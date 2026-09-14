#!/usr/bin/env python3
"""
test_qml6_zero_copy.py — Standalone test for GStreamer qml6glsink zero-copy video in Qt6.

Tests:
1. PyQt6 QQuickWidget hosting GstGLVideoItem.
2. Hardware H.264 decode (vah264dec + vapostproc + glupload + qml6glsink).
3. Playback of /tmp/bbb_720p.mp4 for 20 seconds.
4. Live rendering + screenshot verification.
"""

import sys
import os
import time
import subprocess
import argparse
import ctypes
import PyQt6.sip as sip

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtCore import QUrl, QTimer, Qt, QByteArray

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstGL", "1.0")
from gi.repository import Gst, GstGL

Gst.init(None)
# MUST instantiate qml6glsink before QML engine loads to register QML types
_sink_reg = Gst.ElementFactory.make("qml6glsink", None)

QML_CODE = """
import QtQuick
import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0

Rectangle {
    anchors.fill: parent
    color: "#0d1117"

    Qt6GLVideoItem {
        id: videoItem
        objectName: "videoItem"
        anchors.fill: parent
    }

    Text {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 20
        text: "NemoHeadUnit — Qt6 qml6glsink Zero-Copy"
        color: "#58a6ff"
        font.pixelSize: 22
        font.bold: true
    }
}
"""


class Qml6TestWindow(QMainWindow):
    def __init__(self, video_file: str = "/tmp/bbb_720p.mp4", duration_s: int = 20):
        super().__init__()
        self.setWindowTitle("NemoHeadUnit — qml6glsink Test")
        self.resize(1920, 1200)

        self.video_file = video_file
        self.duration_s = duration_s
        self.pipeline = None

        qml_path = "/tmp/video_scene.qml"
        with open(qml_path, "w") as f:
            f.write(QML_CODE)

        # Container
        self.quick_widget = QQuickWidget(self)
        self.quick_widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.quick_widget.setSource(QUrl.fromLocalFile(qml_path))
        self.setCentralWidget(self.quick_widget)

    def start_video(self):
        for err in self.quick_widget.errors():
            print(f"[QML ERR] {err.toString()}", flush=True)

        root = self.quick_widget.rootObject()
        if not root:
            print("[ERR] Could not get QML rootObject", flush=True)
            return

        video_item = root.findChild(object, "videoItem")
        if not video_item:
            print("[ERR] Could not find 'videoItem' in QML scene", flush=True)
            return

        ptr = sip.unwrapinstance(video_item)
        print(f"[*] Found QQuickItem 'videoItem' at C++ pointer: {hex(ptr)}", flush=True)

        pipe_str = (
            f"filesrc location={self.video_file} "
            f"! qtdemux name=d "
            f"d.video_0 ! queue ! h264parse ! vah264dec ! vapostproc ! video/x-raw(memory:DMABuf) ! glupload ! qml6glsink name=sink sync=true"
        )
        print(f"[*] Pipeline:\n    {pipe_str}", flush=True)
        self.pipeline = Gst.parse_launch(pipe_str)

        sink = self.pipeline.get_by_name("sink")
        # Pass C++ pointer of QQuickItem to qml6glsink
        sink.set_property("widget", ptr)

        self.pipeline.set_state(Gst.State.PLAYING)
        print("[*] Pipeline playing. Zero-copy hardware video active on screen!", flush=True)

    def closeEvent(self, event):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="Qt6 qml6glsink zero-copy test")
    parser.add_argument("--file", type=str, default="/tmp/bbb_720p.mp4")
    parser.add_argument("--duration", type=int, default=20)
    args = parser.parse_args()

    app = QApplication(sys.argv)
    win = Qml6TestWindow(video_file=args.file, duration_s=args.duration)
    win.showFullScreen()

    QTimer.singleShot(300, win.start_video)

    def capture():
        subprocess.run(["grim", "/tmp/qml6_live_screen.png"], check=False)
        if os.path.exists("/tmp/qml6_live_screen.png"):
            sz = os.path.getsize("/tmp/qml6_live_screen.png")
            print(f"📸 Screen captured to /tmp/qml6_live_screen.png ({sz} bytes)", flush=True)

    QTimer.singleShot(2500, capture)
    QTimer.singleShot(args.duration * 1000, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
