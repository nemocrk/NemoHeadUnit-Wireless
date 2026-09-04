#!/usr/bin/env python3
"""
test_ponytail_qml6.py — Minimalist zero-copy Qt6/GStreamer player (/ponytail style).

No buttons, no bridges, no boilerplate. 55 lines.
1. QML ApplicationWindow + GstGLQt6VideoItem
2. Pipeline: vah264dec -> vapostproc -> DMABuf -> glupload -> qml6glsink
3. On sceneGraphInitialized: pass C++ pointer via g_object_set and PLAY.
"""

import sys
import os
import ctypes
import ctypes.util
import gi

os.environ["LIBVA_DRIVER_NAME"] = "i965"
os.environ["QT_MULTIMEDIA_FORCE_GL_TEXTURE_EXTERNAL_OES"] = "1"

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtCore import QTimer, QObject
from shiboken6 import Shiboken

Gst.init(None)

QML = b"""
import QtQuick
import QtQuick.Controls
import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0

ApplicationWindow {
    width: 1280
    height: 720
    visible: true
    color: "black"

    GstGLQt6VideoItem {
        objectName: "video"
        anchors.fill: parent
    }
}
"""


def main():
    video = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bbb_720p.mp4"
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    QQuickWindow.setGraphicsApi(QSGRendererInterface.OpenGL)
    app = QGuiApplication(sys.argv)

    # 1. Pipeline hardware decode
    pipe = Gst.parse_launch(
        f"filesrc location=\"{video}\" ! qtdemux ! h264parse ! vah264dec ! "
        f"vapostproc ! video/x-raw(memory:DMABuf) ! glupload ! qml6glsink name=sink"
    )
    sink = pipe.get_by_name("sink")

    # 2. QML Window & Video Item
    engine = QQmlApplicationEngine()
    engine.loadData(QML)
    if not engine.rootObjects():
        sys.exit(1)
    window = engine.rootObjects()[0]
    item = window.findChild(QObject, "video")

    # 3. Aggancio C++ pointer a qml6glsink quando lo scene graph e' pronto
    libgobject = ctypes.CDLL(ctypes.util.find_library("gobject-2.0") or "libgobject-2.0.so.0")
    libgobject.g_object_set.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]

    def on_ready():
        ptr = Shiboken.getCppPointer(item)[0]
        libgobject.g_object_set(hash(sink), b"widget", ctypes.c_void_p(ptr), None)
        pipe.set_state(Gst.State.PLAYING)
        print("▶️ [ponytail] Playback zero-copy avviato con successo!", flush=True)

    window.sceneGraphInitialized.connect(on_ready)

    QTimer.singleShot(duration * 1000, app.quit)
    app.exec()
    pipe.set_state(Gst.State.NULL)


if __name__ == "__main__":
    main()
