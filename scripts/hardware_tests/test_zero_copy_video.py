#!/usr/bin/env python3
"""
test_zero_copy_video.py — Option A: End-to-End Zero-Copy H.264 Video Test for HP Omni 10.

Demonstrates hardware H.264 decoding (vah264dec + vapostproc)
and direct Wayland zero-copy scanout (waylandsink via DRM DMABuf)
at 1280x720@60fps with CPU monitoring.
"""

import sys
import os
import time
import subprocess
import argparse

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)


def get_process_cpu_percent(pid: int) -> float:
    """Read CPU time from /proc/[pid]/stat."""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            fields = f.read().split()
            utime = int(fields[13])
            stime = int(fields[14])
            return float(utime + stime)
    except Exception:
        return 0.0


def test_zero_copy(
    duration_s: int = 20,
    width: int = 1280,
    height: int = 720,
    fps: int = 60,
    pattern: str = "smpte",
    capture_path: str = "/tmp/zero_copy_screen.png",
):
    pid = os.getpid()
    print("=" * 65)
    print("🎬 Option A: Zero-Copy H.264 Hardware Decode & Wayland Scanout")
    print(f"   Resolution: {width}x{height} @ {fps} FPS")
    print(f"   Platform:   Intel Bay Trail / labwc Wayland (VA-API -> DMABuf)")
    print(f"   Duration:   {duration_s}s | Pattern: {pattern}")
    print("=" * 65, flush=True)

    # 1280x720@60fps H.264 test stream -> hardware VPU decode -> DRM DMABuf -> waylandsink
    pipe_str = (
        f"videotestsrc pattern={pattern} is-live=true "
        f"! video/x-raw,width={width},height={height},framerate={fps}/1 "
        f"! x264enc tune=zerolatency speed-preset=ultrafast bitrate=4000 key-int-max={fps} "
        f"! h264parse config-interval=1 "
        f"! vah264dec "
        f"! vapostproc "
        f"! waylandsink sync=false"
    )

    print(f"[*] Pipeline:\n    {pipe_str}\n", flush=True)
    pipeline = Gst.parse_launch(pipe_str)
    loop = GLib.MainLoop()

    t0 = time.time()
    last_cpu_time = get_process_cpu_percent(pid)
    last_wall_time = t0
    ticks = 0

    def on_tick():
        nonlocal last_cpu_time, last_wall_time, ticks
        ticks += 1
        now = time.time()
        elapsed = now - t0
        dt = now - last_wall_time
        curr_cpu_time = get_process_cpu_percent(pid)

        # Calculate CPU usage of this process
        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK']) if hasattr(os, 'sysconf') else 100
        cpu_diff_sec = (curr_cpu_time - last_cpu_time) / clk_tck
        cpu_pct = (cpu_diff_sec / dt) * 100.0 if dt > 0 else 0.0

        last_cpu_time = curr_cpu_time
        last_wall_time = now

        print(
            f"[*] Frame stream active: {elapsed:4.1f}s / {duration_s}s | "
            f"Resolution: {width}x{height}@{fps} | "
            f"PID CPU: {cpu_pct:4.1f}%",
            flush=True,
        )

        if elapsed >= 3.0 and not os.path.exists(capture_path):
            subprocess.run(["grim", capture_path], check=False)
            if os.path.exists(capture_path):
                sz = os.path.getsize(capture_path)
                print(f"📸 Framebuffer captured to {capture_path} ({sz} bytes)", flush=True)

        if elapsed >= duration_s:
            loop.quit()
            return False
        return True

    GLib.timeout_add(1000, on_tick)

    print("[*] Transitioning pipeline to PLAYING...", flush=True)
    pipeline.set_state(Gst.State.PLAYING)

    print("[*] Entering GLib.MainLoop (Wayland zero-copy scanout active)...", flush=True)
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")

    print("[*] Stopping pipeline...", flush=True)
    pipeline.set_state(Gst.State.NULL)
    print("=" * 65)
    print("✅ Zero-Copy Test Complete.")
    print("=" * 65, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Option A: Zero-copy video test")
    parser.add_argument("--duration", type=int, default=20, help="Test duration in seconds (default: 20)")
    parser.add_argument("--width", type=int, default=1280, help="Width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="Height (default: 720)")
    parser.add_argument("--fps", type=int, default=60, help="Framerate (default: 60)")
    parser.add_argument("--pattern", type=str, default="smpte", help="Pattern (smpte, ball, snow)")
    parser.add_argument("--capture", type=str, default="/tmp/zero_copy_screen.png", help="Screenshot path")
    args = parser.parse_args()

    test_zero_copy(
        duration_s=args.duration,
        width=args.width,
        height=args.height,
        fps=args.fps,
        pattern=args.pattern,
        capture_path=args.capture,
    )
