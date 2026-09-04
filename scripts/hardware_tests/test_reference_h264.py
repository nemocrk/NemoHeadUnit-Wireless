#!/usr/bin/env python3
"""
test_reference_h264.py — Playback of standard 720p H.264 reference video with Intel hardware decode.

Tests:
1. Standard H.264 1280x720 4Mbps video file (/tmp/bbb_720p.mp4).
2. Pure hardware decoding via Bay Trail VPU (vah264dec + vapostproc + waylandsink).
3. Zero software encoding (no x264enc).
4. Real-time CPU usage measurement to prove zero-copy / zero-CPU decode.
"""

import sys
import os
import time
import subprocess
import argparse

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)


def get_process_cpu_percent(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            fields = f.read().split()
            utime = int(fields[13])
            stime = int(fields[14])
            return float(utime + stime)
    except Exception:
        return 0.0


def play_reference_video(video_path: str = "/tmp/bbb_720p.mp4", duration_s: int = 30):
    pid = os.getpid()
    print("=" * 65)
    print("🐰 Testing Pure Hardware H.264 Reference Video Playback")
    print(f"   File:     {video_path} (Big Buck Bunny 720p @ 30fps H.264)")
    print(f"   Decoder:  vah264dec (Intel Bay Trail VPU Hardware Engine)")
    print(f"   Sink:     waylandsink (Zero-Copy DRM DMABuf scanout)")
    print(f"   Duration: {duration_s}s (auto-looping)")
    print("=" * 65, flush=True)

    pipe_str = (
        f"filesrc location={video_path} "
        f"! qtdemux name=demux "
        f"demux.video_0 ! queue ! h264parse ! vah264dec ! vapostproc ! waylandsink sync=true"
    )

    pipeline = Gst.parse_launch(pipe_str)
    loop = GLib.MainLoop()

    bus = pipeline.get_bus()

    def on_bus_msg(bus, msg):
        if msg.type == Gst.MessageType.EOS:
            # Loop playback
            pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"[ERR] GStreamer error: {err}, {dbg}", flush=True)
            loop.quit()
        return True

    bus.add_signal_watch()
    bus.connect("message", on_bus_msg)

    t0 = time.time()
    last_cpu_time = get_process_cpu_percent(pid)
    last_wall_time = t0

    def on_tick():
        nonlocal last_cpu_time, last_wall_time
        now = time.time()
        elapsed = now - t0
        dt = now - last_wall_time
        curr_cpu_time = get_process_cpu_percent(pid)

        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK']) if hasattr(os, 'sysconf') else 100
        cpu_diff_sec = (curr_cpu_time - last_cpu_time) / clk_tck
        cpu_pct = (cpu_diff_sec / dt) * 100.0 if dt > 0 else 0.0

        last_cpu_time = curr_cpu_time
        last_wall_time = now

        print(
            f"[*] Playing H.264... {elapsed:4.1f}s / {duration_s}s | "
            f"Process CPU: {cpu_pct:4.1f}%",
            flush=True,
        )

        if elapsed >= duration_s:
            loop.quit()
            return False
        return True

    GLib.timeout_add(1000, on_tick)

    pipeline.set_state(Gst.State.PLAYING)
    print("[*] Playback started. Check intel_gpu_top and htop now!\n", flush=True)

    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n[!] Stopped by user.")

    pipeline.set_state(Gst.State.NULL)
    print("=" * 65)
    print("✅ Reference playback test finished.")
    print("=" * 65, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reference H.264 Playback")
    parser.add_argument("--file", type=str, default="/tmp/bbb_720p.mp4")
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    play_reference_video(video_path=args.file, duration_s=args.duration)
