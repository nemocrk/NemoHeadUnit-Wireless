#!/usr/bin/env python3
"""
test_qt_waylandsink_overlay.py — Embed waylandsink directly into a Qt6 QWidget via winId().

Tests:
1. PyQt6 QWidget window with title / border / status bar.
2. GStreamer H.264 HW decode pipeline (vah264dec + vapostproc + waylandsink).
3. Embedding video subsurface directly into QWidget.winId().
4. 1280x720 @ 60 FPS playback for 20 seconds.
"""

import sys
import os
import time
import subprocess
import argparse

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import QTimer, Qt

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo, GLib

Gst.init(None)


class HeadUnitVideoWindow(QMainWindow):
    def __init__(self, width: int = 1280, height: int = 720, fps: int = 60, duration_s: int = 20, pattern: str = "ball"):
        super().__init__()
        self.setWindowTitle("NemoHeadUnit — Zero-Copy Wayland Video Canvas")
        self.resize(1920, 1200)
        self.setStyleSheet("background-color: #0d1117;")

        # Central container
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Video viewport widget (plain QWidget acting as Wayland subsurface anchor)
        self.video_widget = QWidget(central)
        self.video_widget.setFixedSize(width, height)
        self.video_widget.setStyleSheet("background-color: #000000;")
        layout.addWidget(self.video_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        # Bottom status bar (proves Qt UI sits around the video)
        self.status_bar = QLabel("⚡ NemoHeadUnit | Hardware VPU 0-Copy Direct Scanout | 1280x720 @ 60 FPS", central)
        self.status_bar.setFixedHeight(50)
        self.status_bar.setStyleSheet("background-color: #161b22; color: #58a6ff; font-size: 16px; font-weight: bold; padding-left: 20px;")
        layout.addWidget(self.status_bar)

        self.width = width
        self.height = height
        self.fps = fps
        self.duration_s = duration_s
        self.pattern = pattern
        self.pipeline = None

    def start_pipeline(self):
        win_id = int(self.video_widget.winId())
        print(f"[*] Target QWidget Wayland winId: {win_id}", flush=True)

        pipe_str = (
            f"videotestsrc pattern={self.pattern} is-live=true "
            f"! video/x-raw,width={self.width},height={self.height},framerate={self.fps}/1 "
            f"! x264enc tune=zerolatency speed-preset=ultrafast bitrate=4000 key-int-max={self.fps} "
            f"! h264parse config-interval=1 "
            f"! vah264dec "
            f"! vapostproc "
            f"! waylandsink name=sink sync=false"
        )
        print(f"[*] Pipeline:\n    {pipe_str}", flush=True)
        self.pipeline = Gst.parse_launch(pipe_str)

        sink = self.pipeline.get_by_name("sink")
        bus = self.pipeline.get_bus()

        # Set window handle
        if isinstance(sink, GstVideo.VideoOverlay):
            sink.set_window_handle(win_id)

        def on_sync(bus, msg):
            if GstVideo.is_video_overlay_prepare_window_handle_message(msg):
                msg.src.set_window_handle(win_id)

        bus.enable_sync_message_emission()
        bus.connect("sync-message::element", on_sync)

        self.pipeline.set_state(Gst.State.PLAYING)
        print("[*] GStreamer pipeline set to PLAYING", flush=True)

        # In Wayland, subsurfaces are synchronized to parent surface commits.
        # Pump GLib events for waylandsink and trigger video_widget update at 60fps.
        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump_and_update)
        self._pump_timer.start(16)

    def _pump_and_update(self):
        while GLib.MainContext.default().iteration(False):
            pass
        self.video_widget.update()

    def closeEvent(self, event):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="Qt6 Wayland Video Overlay Test")
    parser.add_argument("--duration", type=int, default=20, help="Duration in seconds")
    parser.add_argument("--fps", type=int, default=60, help="Framerate")
    parser.add_argument("--pattern", type=str, default="ball", help="videotestsrc pattern (ball, snow, smpte)")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    win = HeadUnitVideoWindow(width=1280, height=720, fps=args.fps, duration_s=args.duration, pattern=args.pattern)
    win.showFullScreen()

    # Start pipeline after window is mapped and winId is valid
    QTimer.singleShot(200, win.start_pipeline)

    # Capture screenshot after 3 seconds
    def capture():
        subprocess.run(["grim", "/tmp/qt_wayland_overlay.png"], check=False)
        if os.path.exists("/tmp/qt_wayland_overlay.png"):
            sz = os.path.getsize("/tmp/qt_wayland_overlay.png")
            print(f"📸 Framebuffer captured to /tmp/qt_wayland_overlay.png ({sz} bytes)", flush=True)

    QTimer.singleShot(3000, capture)
    QTimer.singleShot(args.duration * 1000, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
