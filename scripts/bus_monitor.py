#!/usr/bin/env python3
"""
NemoHeadUnit-Wireless — bus_monitor.py

Live terminal dashboard for diagnosing bus traffic, SHM frame flow,
and callback latency without modifying any module code.

Usage:
    python3 scripts/bus_monitor.py [--interval N] [--window N] [--top N] [--filter PREFIX]

Data sources:
    1. Raw ZMQ bus (SUB *) on ipc:///tmp/nemobus_v2.sub
       → per-topic message rates, payload sizes
    2. Trace JSONL tail: /tmp/nemobus_trace.jsonl (written by zmq_trace module)
       → callback durations, latency percentiles, publish drops, seq gaps

What it shows:
    Panel 1  Top Topics by Rate
             message/s, sparkbar, total count, drops, seq gaps, latency p99
    Panel 2  Slow Callbacks
             p50 and p99 handler durations per module:topic pair
    Panel 3  SHM Frame / Ack Flow
             per widget: frame_ready count, frame_ack count, pending delta,
             round-trip p50/p99, age of last ack.
             STALLED (red) = frame_ready sent > 500 ms ago with no ack back.

Keys:
    q / Ctrl-C   quit
    r            reset all counters
    f            toggle frame-flow panel
"""

from __future__ import annotations

import argparse
import json
import os
import select
import sys
import termios
import threading
import time
import tty
from collections import Counter, defaultdict, deque
from pathlib import Path

import zmq

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BROKER_SUB_ADDR = os.getenv("BROKER_SUB_ADDR", "ipc:///tmp/nemobus_v2.sub")
TRACE_JSONL     = Path(os.getenv("BUS_TRACE_JSONL", "/tmp/nemobus_trace.jsonl"))

WIDGET_MODULES = [
    "config_ui", "navbar_ui", "floating_menu_ui",
    "video_ui",  "log_viewer_ui", "bluetooth_ui",
]

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
_R  = "\033[0m"
_B  = "\033[1m"
_DM = "\033[2m"
_RD = "\033[91m"
_YL = "\033[93m"
_GN = "\033[92m"
_CY = "\033[96m"
_WH = "\033[97m"
_MG = "\033[95m"

def c(text, *codes):  return "".join(codes) + str(text) + _R

def bar(v, mx, w=12):
    if mx <= 0: return " " * w
    filled = min(w, int(v / mx * w))
    colour = _RD if filled >= w * 0.8 else (_YL if filled >= w * 0.5 else _GN)
    return c("█" * filled + "░" * (w - filled), colour)

def fmt_us(us):
    if us is None: return c("    —   ", _DM)
    ms = us / 1000
    col = _RD if ms >= 100 else (_YL if ms >= 10 else _GN)
    return c(f"{ms:7.1f}ms", col)

def fmt_rate(r):
    col = _RD if r >= 100 else (_YL if r >= 20 else _GN)
    return c(f"{r:7.1f}/s", col)

def pct(values, p):
    if not values: return None
    sv = sorted(values)
    return sv[max(0, min(len(sv)-1, int((len(sv)-1)*p)))]

# ---------------------------------------------------------------------------
# Shared stats
# ---------------------------------------------------------------------------

class BusStats:
    def __init__(self, window_sec=3.0):
        self.lk = threading.Lock()
        self.window_sec = window_sec
        self.started = self.reset_ts = time.monotonic()

        self._pub_t: dict[str, deque] = defaultdict(lambda: deque(maxlen=5000))
        self._bw_t:  dict[str, deque] = defaultdict(lambda: deque(maxlen=5000))

        self.tot_pub   = Counter()
        self.tot_bytes = Counter()
        self.tot_drop  = Counter()
        self.tot_gap   = Counter()
        self.cb_errors = Counter()

        self.cb_us:  dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self.lat_us: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))

        self.fr_ts:    dict[str, float] = {}   # name → ts of last frame_ready
        self.fr_count: Counter = Counter()
        self.fa_ts:    dict[str, float] = {}   # name → ts of last frame_ack
        self.fa_count: Counter = Counter()
        self.rtt_us:   dict[str, deque] = defaultdict(lambda: deque(maxlen=300))

    # ---- raw bus recording ----
    def record_msg(self, topic: str, size: int, payload_b: bytes):
        now = time.monotonic()
        with self.lk:
            self._pub_t[topic].append(now)
            self._bw_t[topic].append((now, size))
            self.tot_pub[topic]   += 1
            self.tot_bytes[topic] += size

            if topic == "ui.widget.frame_ready":
                try:
                    p = json.loads(payload_b)
                    name = p.get("name", "")
                    if name:
                        self.fr_ts[name]    = now
                        self.fr_count[name] += 1
                except Exception:
                    pass
            elif topic.startswith("ui.widget.frame_ack."):
                name = topic[len("ui.widget.frame_ack."):]
                prev = self.fr_ts.get(name)
                self.fa_ts[name]    = now
                self.fa_count[name] += 1
                if prev is not None:
                    self.rtt_us[name].append((now - prev) * 1_000_000)

    # ---- trace side-channel ----
    def record_trace(self, ev: dict):
        et     = ev.get("type", "")
        topic  = ev.get("topic", "") or ""
        module = ev.get("module", "") or ""
        with self.lk:
            if et == "publish_drop":
                self.tot_drop[topic] += 1
            elif et == "recv_ok":
                if ev.get("latency_us") is not None:
                    self.lat_us[topic].append(float(ev["latency_us"]))
                if ev.get("callback_us") is not None:
                    self.cb_us[f"{module}:{topic}"].append(float(ev["callback_us"]))
                gap = int(ev.get("seq_gap") or 0)
                if gap > 0:
                    self.tot_gap[topic] += gap
            elif et == "callback_error":
                self.cb_errors[f"{module}:{topic}"] += 1

    # ---- read helpers ----
    def rate(self, topic):
        now, cutoff = time.monotonic(), time.monotonic() - self.window_sec
        with self.lk:
            dq = self._pub_t.get(topic)
            return sum(1 for t in (dq or []) if t >= cutoff) / self.window_sec

    def bw(self, topic):
        cutoff = time.monotonic() - self.window_sec
        with self.lk:
            dq = self._bw_t.get(topic)
            return sum(b for t, b in (dq or []) if t >= cutoff) / self.window_sec

    def topics(self):
        with self.lk: return list(self.tot_pub.keys())

    def snap(self):
        with self.lk:
            return {
                "tot_pub":   dict(self.tot_pub),
                "tot_drop":  dict(self.tot_drop),
                "tot_gap":   dict(self.tot_gap),
                "cb_us":     {k: list(v) for k, v in self.cb_us.items()},
                "lat_us":    {k: list(v) for k, v in self.lat_us.items()},
                "fr_count":  dict(self.fr_count),
                "fa_count":  dict(self.fa_count),
                "fr_ts":     dict(self.fr_ts),
                "fa_ts":     dict(self.fa_ts),
                "rtt_us":    {k: list(v) for k, v in self.rtt_us.items()},
            }

    def reset(self):
        with self.lk:
            for d in (self._pub_t, self._bw_t, self.cb_us, self.lat_us, self.rtt_us):
                d.clear()
            for ctr in (self.tot_pub, self.tot_bytes, self.tot_drop, self.tot_gap,
                        self.cb_errors, self.fr_count, self.fa_count):
                ctr.clear()
            self.fr_ts.clear(); self.fa_ts.clear()
            self.reset_ts = time.monotonic()


# ---------------------------------------------------------------------------
# Listener threads
# ---------------------------------------------------------------------------

def bus_listener(stats: BusStats, stop: threading.Event):
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVHWM, 20000)
    sub.setsockopt(zmq.LINGER, 0)
    sub.connect(BROKER_SUB_ADDR)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    while not stop.is_set():
        if not sub.poll(300): continue
        try:
            frames = sub.recv_multipart(flags=zmq.NOBLOCK)
        except zmq.Again:
            continue
        if len(frames) < 2: continue
        topic = frames[0].decode("utf-8", errors="replace")
        size  = len(frames[0]) + len(frames[1])
        stats.record_msg(topic, size, frames[1])
    sub.close(linger=0); ctx.term()


def jsonl_tailer(stats: BusStats, stop: threading.Event):
    """Tail the trace JSONL file for callback/latency data."""
    while not stop.is_set():
        if TRACE_JSONL.exists(): break
        time.sleep(1.0)
    if stop.is_set(): return
    try:
        fh = open(TRACE_JSONL, "r", encoding="utf-8", errors="replace")
        fh.seek(0, 2)
    except Exception:
        return
    while not stop.is_set():
        line = fh.readline()
        if not line:
            time.sleep(0.04)
            continue
        line = line.strip()
        if not line: continue
        try:
            stats.record_trace(json.loads(line))
        except Exception:
            pass
    fh.close()


# ---------------------------------------------------------------------------
# Keyboard (raw mode, non-blocking)
# ---------------------------------------------------------------------------

class RawKey:
    def __init__(self):
        self._fd  = sys.stdin.fileno()
        self._old = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)

    def read(self):
        r, _, _ = select.select([self._fd], [], [], 0)
        if r:
            return os.read(self._fd, 1).decode("utf-8", errors="replace")
        return None

    def restore(self):
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(stats: BusStats, args, snap: dict, tick: int):
    try:
        cols = os.get_terminal_size().columns
    except Exception:
        cols = 120

    out = []
    W = lambda s: out.append(s + "\033[K")
    now = time.monotonic()

    # ── header ──
    uptime = now - stats.started
    since  = now - stats.reset_ts
    W(c("━" * cols, _CY))
    W(c(" ◉ NemoBus Monitor ", _B, _CY)
      + c(f"  uptime {uptime:.0f}s  rate-window {stats.window_sec:.1f}s  "
          f"reset {since:.0f}s ago", _DM)
      + c("  [r]=reset  [q]=quit", _DM))
    W(c("━" * cols, _CY))

    # ── panel 1: top topics ──
    topics  = stats.topics()
    rated   = sorted(topics, key=lambda t: stats.rate(t), reverse=True)
    if args.filter:
        rated = [t for t in rated if args.filter in t]
    rated   = rated[:args.top]
    max_r   = max((stats.rate(t) for t in rated), default=1.0)

    tot_pub  = snap["tot_pub"]
    tot_drop = snap["tot_drop"]
    tot_gap  = snap["tot_gap"]

    W("")
    W(c(f"  {'TOPIC':<44} {'RATE':>9}  {'BAR':12}  {'TOTAL':>7}  {'DROP':>5}  {'GAP':>5}  {'LAT p99':>9}", _B))
    W(c("─" * cols, _DM))

    for t in rated:
        r      = stats.rate(t)
        total  = tot_pub.get(t, 0)
        drops  = tot_drop.get(t, 0)
        gaps   = tot_gap.get(t, 0)
        lat99  = pct(snap["lat_us"].get(t, []), 0.99)

        d_str = c(f"{drops:5d}", _RD) if drops else c(f"{drops:5d}", _DM)
        g_str = c(f"{gaps:5d}",  _YL) if gaps  else c(f"{gaps:5d}",  _DM)

        W(f"  {c(t[:44], _WH):<44}  {fmt_rate(r):}  {bar(r, max_r)}  "
          f"{total:>7d}  {d_str}  {g_str}  {fmt_us(lat99)}")

    # ── panel 2: slow callbacks ──
    slow = []
    for k, vals in snap["cb_us"].items():
        p99 = pct(vals, 0.99)
        p50 = pct(vals, 0.50)
        if p99 is not None:
            slow.append((p99, p50, k))
    slow.sort(reverse=True)

    if slow:
        W("")
        W(c(f"  {'SLOW CALLBACKS':<52}  {'p50':>9}  {'p99':>9}", _B))
        W(c("─" * cols, _DM))
        for p99, p50, k in slow[:8]:
            W(f"  {c(k[:52], _YL):<52}  {fmt_us(p50)}  {fmt_us(p99)}")

    # ── panel 3: SHM frame / ack flow ──
    W("")
    W(c(f"  {'SHM FRAME FLOW':<28}  {'READY':>7}  {'ACK':>7}  {'PENDING':^11}  "
        f"{'RTT p50':>9}  {'RTT p99':>9}  {'LAST ACK AGE':>13}", _B))
    W(c("─" * cols, _DM))

    fr_count = snap["fr_count"]; fa_count = snap["fa_count"]
    fr_ts    = snap["fr_ts"];    fa_ts    = snap["fa_ts"]
    rtt_us   = snap["rtt_us"]

    seen = set(fr_count) | set(fa_count)
    for name in sorted(set(WIDGET_MODULES) | seen):
        rc = fr_count.get(name, 0)
        ac = fa_count.get(name, 0)
        pending = rc - ac

        last_fr = fr_ts.get(name)
        last_fa = fa_ts.get(name)

        stalled = False
        stall_s = 0.0
        if last_fr is not None and (last_fa is None or last_fr > last_fa):
            stall_s = now - last_fr
            stalled = stall_s > 0.5

        # ack age string
        if last_fa is not None:
            age = now - last_fa
            col = _GN if age < 1 else (_YL if age < 5 else _RD)
            ack_age = c(f"{age*1000:6.0f}ms" if age < 1 else f"{age:6.1f}s ", col)
        else:
            ack_age = c("  —     ", _DM)

        vals = rtt_us.get(name, [])
        p50  = pct(vals, 0.50)
        p99  = pct(vals, 0.99)

        if rc == 0:
            nm = c(f"  {name:<26}", _DM); pend_s = c("  inactive  ", _DM)
        elif stalled:
            nm = c(f"  {name:<26}", _RD, _B)
            pend_s = c(f" STALLED {stall_s:.1f}s  ", _RD, _B)
        elif pending > 0:
            nm = c(f"  {name:<26}", _YL)
            pend_s = c(f" +{pending:<9d}  ", _YL)
        else:
            nm = c(f"  {name:<26}", _GN)
            pend_s = c(f" {pending:<11d}", _DM)

        W(f"{nm}  {c(rc,_WH):>7}  {c(ac,_WH):>7}  {pend_s}  {fmt_us(p50)}  {fmt_us(p99)}  {ack_age}")

    # ── footer ──
    W("")
    W(c("━" * cols, _CY))
    spin = "⣾⣽⣻⢿⡿⣟⣯⣷"[tick % 8]
    W(c(f" {spin} ", _CY)
      + c(f"bus={BROKER_SUB_ADDR}  trace={TRACE_JSONL}  "
          f"topics seen={len(topics)}", _DM))

    sys.stdout.write("\033[2J\033[H" + "\n".join(out) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="NemoBus live monitor")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="Refresh interval seconds (default: 1.0)")
    ap.add_argument("--window",   type=float, default=3.0,
                    help="Rate window seconds (default: 3.0)")
    ap.add_argument("--top",      type=int,   default=20,
                    help="Max topics shown (default: 20)")
    ap.add_argument("--filter",   type=str,   default="",
                    help="Only show topics containing this string")
    args = ap.parse_args()

    stats = BusStats(window_sec=args.window)
    stop  = threading.Event()

    threading.Thread(target=bus_listener, args=(stats, stop),
                     daemon=True, name="bus-mon").start()
    threading.Thread(target=jsonl_tailer, args=(stats, stop),
                     daemon=True, name="jsonl-tail").start()

    tick = 0
    try:
        kb = RawKey()
    except Exception:
        kb = None

    try:
        while True:
            if kb:
                ch = kb.read()
                if ch in ("q", "Q", "\x03", "\x04"): break
                elif ch in ("r", "R"): stats.reset()
            snap = stats.snap()
            render(stats, args, snap, tick)
            tick += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if kb: kb.restore()
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        print("bus_monitor stopped.")

if __name__ == "__main__":
    main()
