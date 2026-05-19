"""
NemoHeadUnit-Wireless — zmq_trace module

A normal Nemo module that collects instrumentation events emitted by BusClient
through shared.bus_trace.BusTracer.

Place as:
  modules/zmq_trace/main.py

It auto-starts like other modules via main.py autodiscovery.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import zmq

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent   # modules/zmq_trace/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.config_schema import field_bool, field_float, field_int, field_string  # noqa: E402
from shared.logger import get_logger           # noqa: E402

MODULE_NAME = "zmq_trace"
PRIORITY = 0
TRACE_ADDR = os.getenv("BUS_TRACE_ADDR", "ipc:///tmp/nemobus_v2.trace")

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

_SCHEMA = {
    "enabled": field_bool(default=True),
    "console_enabled": field_bool(default=True),
    "jsonl_enabled": field_bool(default=True),
    "jsonl_path": field_string(default="/tmp/nemobus_trace.jsonl"),
    "report_interval_sec": field_float(default=1.0, min=0.1, max=60.0),
    "top_n": field_int(default=12, min=3, max=100),
    "max_samples_per_key": field_int(default=2000, min=100, max=10000),
    "publish_summary_on_bus": field_bool(default=False),
    "blacklist_prefixes": field_string(default=""),  # comma-separated, e.g. "log.,debug."
}
_config = {k: v.default for k, v in _SCHEMA.items()}

stop_event = threading.Event()


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.total = Counter()
        self.window = Counter()
        self.topic_pub = Counter()
        self.topic_recv = Counter()
        self.topic_bytes = Counter()
        self.module_pub = Counter()
        self.module_recv = Counter()
        self.pub_drop = Counter()
        self.seq_gap = Counter()
        self.duplicates = Counter()
        self.callback_error = Counter()
        self.subscriptions = defaultdict(set)  # module → set(topic)
        self.lat_us = defaultdict(lambda: deque(maxlen=_config["max_samples_per_key"]))
        self.cb_us = defaultdict(lambda: deque(maxlen=_config["max_samples_per_key"]))
        self.last_seq = {}  # (src_module, topic, dst_module) → seq
        self.started = time.time()

    def add(self, ev: dict[str, Any]) -> None:
        et = ev.get("type", "unknown")
        topic = ev.get("topic", "") or ""
        module = ev.get("module", "") or ""
        key = topic or module or et

        with self.lock:
            self.total[et] += 1
            self.window[et] += 1
            self.window["events"] += 1

            if et == "publish_ok":
                self.topic_pub[topic] += 1
                self.module_pub[module] += 1
                self.topic_bytes[topic] += int(ev.get("bytes", 0) or 0)
            elif et in {"publish_drop", "publish_error"}:
                self.pub_drop[topic] += 1
            elif et == "recv_ok":
                self.topic_recv[topic] += 1
                self.module_recv[module] += 1
                self.topic_bytes[topic] += int(ev.get("bytes", 0) or 0)
                if ev.get("latency_us") is not None:
                    self.lat_us[topic].append(float(ev["latency_us"]))
                if ev.get("callback_us") is not None:
                    self.cb_us[f"{module}:{topic}"].append(float(ev["callback_us"]))
                gap = int(ev.get("seq_gap", 0) or 0)
                if gap > 0:
                    self.seq_gap[topic] += gap
                if ev.get("duplicate"):
                    self.duplicates[topic] += 1
            elif et == "callback_error":
                self.callback_error[f"{module}:{topic}"] += 1
            elif et == "subscribe":
                self.subscriptions[module].add(topic)

    def snapshot_and_reset_window(self) -> dict[str, Any]:
        with self.lock:
            snap = {
                "total": dict(self.total),
                "window": dict(self.window),
                "topic_pub": dict(self.topic_pub),
                "topic_recv": dict(self.topic_recv),
                "topic_bytes": dict(self.topic_bytes),
                "module_pub": dict(self.module_pub),
                "module_recv": dict(self.module_recv),
                "pub_drop": dict(self.pub_drop),
                "seq_gap": dict(self.seq_gap),
                "duplicates": dict(self.duplicates),
                "callback_error": dict(self.callback_error),
                "subscriptions": {m: sorted(v) for m, v in self.subscriptions.items()},
                "lat_us": {k: list(v) for k, v in self.lat_us.items()},
                "cb_us": {k: list(v) for k, v in self.cb_us.items()},
                "uptime_sec": time.time() - self.started,
            }
            self.window.clear()
            return snap


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int((len(values) - 1) * p)))
    return values[idx]


def _rate(count: int, interval: float) -> float:
    if interval <= 0:
        return 0.0
    return count / interval


def _blacklisted(topic: str) -> bool:
    prefixes = [p.strip() for p in str(_config.get("blacklist_prefixes", "")).split(",") if p.strip()]
    return any(topic.startswith(p) for p in prefixes)


def collector_loop(metrics: Metrics) -> None:
    ctx = zmq.Context()
    pull = ctx.socket(zmq.PULL)
    pull.setsockopt(zmq.RCVHWM, 100000)
    pull.setsockopt(zmq.LINGER, 0)
    try:
        # Clean stale IPC file if present. ZeroMQ usually handles it, but this helps after hard kills.
        if TRACE_ADDR.startswith("ipc://"):
            ipc_path = TRACE_ADDR.replace("ipc://", "")
            try:
                Path(ipc_path).unlink(missing_ok=True)
            except Exception:
                pass
        pull.bind(TRACE_ADDR)
        log.info(f"Trace collector bound on {TRACE_ADDR}")
    except Exception as e:
        log.error(f"Cannot bind trace collector on {TRACE_ADDR}: {e!r}")
        return

    while not stop_event.is_set():
        try:
            raw = pull.recv(flags=zmq.NOBLOCK)
        except zmq.Again:
            time.sleep(0.001)
            continue
        except Exception as e:
            log.warning(f"Trace recv error: {e!r}")
            continue

        try:
            ev = json.loads(raw.decode("utf-8"))
        except Exception:
            metrics.add({"type": "collector_parse_error"})
            continue

        if not _config.get("enabled", True):
            continue
        if _blacklisted(ev.get("topic", "") or ""):
            continue

        metrics.add(ev)

        if _config.get("jsonl_enabled", True):
            try:
                with open(str(_config["jsonl_path"]), "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, separators=(",", ":"), default=str) + "\n")
            except Exception as e:
                # Avoid spamming: count only.
                metrics.add({"type": "jsonl_write_error", "module": MODULE_NAME, "topic": ""})

    pull.close(linger=0)
    ctx.term()


def reporter_loop(metrics: Metrics) -> None:
    last = time.time()
    last_topic_pub = Counter()
    last_topic_recv = Counter()
    last_topic_bytes = Counter()
    last_module_pub = Counter()
    last_module_recv = Counter()
    last_pub_drop = Counter()
    last_seq_gap = Counter()

    while not stop_event.is_set():
        interval = float(_config.get("report_interval_sec", 1.0) or 1.0)
        time.sleep(interval)
        now = time.time()
        elapsed = max(0.001, now - last)
        last = now

        snap = metrics.snapshot_and_reset_window()
        topic_pub = Counter(snap["topic_pub"])
        topic_recv = Counter(snap["topic_recv"])
        topic_bytes = Counter(snap["topic_bytes"])
        module_pub = Counter(snap["module_pub"])
        module_recv = Counter(snap["module_recv"])
        pub_drop = Counter(snap["pub_drop"])
        seq_gap = Counter(snap["seq_gap"])

        d_pub = topic_pub - last_topic_pub
        d_recv = topic_recv - last_topic_recv
        d_bytes = topic_bytes - last_topic_bytes
        d_mod_pub = module_pub - last_module_pub
        d_mod_recv = module_recv - last_module_recv
        d_pub_drop = pub_drop - last_pub_drop
        d_seq_gap = seq_gap - last_seq_gap

        last_topic_pub = topic_pub
        last_topic_recv = topic_recv
        last_topic_bytes = topic_bytes
        last_module_pub = module_pub
        last_module_recv = module_recv
        last_pub_drop = pub_drop
        last_seq_gap = seq_gap

        total_pub = sum(d_pub.values())
        total_recv = sum(d_recv.values())
        total_bytes = sum(d_bytes.values())
        total_drops = sum(d_pub_drop.values())
        total_gaps = sum(d_seq_gap.values())

        summary = {
            "interval_sec": elapsed,
            "publish_per_sec": _rate(total_pub, elapsed),
            "receive_per_sec": _rate(total_recv, elapsed),
            "bytes_per_sec": _rate(total_bytes, elapsed),
            "publish_drops": total_drops,
            "seq_gaps": total_gaps,
            "events_per_sec": _rate(int(snap["window"].get("events", 0)), elapsed),
        }

        if _config.get("console_enabled", True):
            print_report(summary, d_pub, d_recv, d_bytes, d_mod_pub, d_mod_recv, d_pub_drop, d_seq_gap, snap)

        if _config.get("publish_summary_on_bus", False):
            bus.publish("zmq_trace.summary", summary)


def print_report(summary, d_pub, d_recv, d_bytes, d_mod_pub, d_mod_recv, d_pub_drop, d_seq_gap, snap) -> None:
    top_n = int(_config.get("top_n", 12) or 12)
    lat_us = snap["lat_us"]
    cb_us = snap["cb_us"]

    print("\n=== NemoBus Trace ===")
    print(
        f"traffic: pub={summary['publish_per_sec']:.1f}/s "
        f"recv={summary['receive_per_sec']:.1f}/s "
        f"bw={summary['bytes_per_sec']/1024:.1f} KB/s "
        f"pub_drop={summary['publish_drops']} seq_gap={summary['seq_gaps']} "
        f"trace_events={summary['events_per_sec']:.1f}/s"
    )

    print("\nTop topics:")
    topics = set(d_pub) | set(d_recv) | set(d_bytes) | set(d_pub_drop) | set(d_seq_gap)
    ranked = sorted(topics, key=lambda t: (d_pub[t] + d_recv[t] + d_bytes[t] / 1024), reverse=True)[:top_n]
    for t in ranked:
        p99 = _pct(lat_us.get(t, []), 0.99)
        p95 = _pct(lat_us.get(t, []), 0.95)
        lat = "-"
        if p99 is not None:
            lat = f"p95={p95/1000:.2f}ms p99={p99/1000:.2f}ms"
        print(
            f"  {t[:44]:44} "
            f"pub={d_pub[t]:5d} recv={d_recv[t]:5d} "
            f"kb={d_bytes[t]/1024:8.1f} "
            f"drop={d_pub_drop[t]:3d} gap={d_seq_gap[t]:3d} {lat}"
        )

    print("\nTop modules:")
    modules = set(d_mod_pub) | set(d_mod_recv)
    ranked_m = sorted(modules, key=lambda m: d_mod_pub[m] + d_mod_recv[m], reverse=True)[:top_n]
    for m in ranked_m:
        print(f"  {m[:32]:32} pub={d_mod_pub[m]:5d} recv={d_mod_recv[m]:5d}")

    slow = []
    for k, vals in cb_us.items():
        p99 = _pct(vals, 0.99)
        if p99 is not None:
            slow.append((p99, k))
    slow.sort(reverse=True)
    if slow[:5]:
        print("\nSlow callbacks p99:")
        for p99, k in slow[:5]:
            print(f"  {k[:54]:54} {p99/1000:.2f}ms")
    print("=====================\n")


def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        log.info("No persisted config found — defaults seeded by config_manager.")
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    if isinstance(value, (dict, list)):
        log.warning(f"config.changed: structural value for '{key}' rejected")
        return
    _config[key] = value
    log.info(f"Config changed: {key}={value!r}")


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} received — initialising...")
    cfg.get(schema=_SCHEMA)
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info(f"system.ready published (priority={PRIORITY})")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — stopping trace collector")
    stop_event.set()
    bus.stop()


def run() -> None:
    cfg.on_config_loaded = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    metrics = Metrics()
    threading.Thread(target=collector_loop, args=(metrics,), daemon=True, name="trace-collector").start()
    threading.Thread(target=reporter_loop, args=(metrics,), daemon=True, name="trace-reporter").start()

    log.info("Module started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
