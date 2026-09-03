"""
video_viewport.py — QQuickView Container for Zero-Copy Video Viewport with Full pyside6_gstreamer_player Parity.

Uses QQuickView embedded via QWidget.createWindowContainer() for direct DRM/KMS scanout
(no offscreen FBO blitting), and attaches qml6glsink on sceneGraphInitialized.
"""

import os
import sys
import time
import logging
import tempfile
import ctypes
import ctypes.util
from typing import Optional, Callable

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import QEvent, QPointF, Qt, QUrl, QObject, pyqtSignal
from PyQt6.QtGui import QEventPoint, QMouseEvent, QTouchEvent, QPainter, QImage
from PyQt6.QtQuick import QQuickView, QQuickItem, QQuickWindow, QSGRendererInterface

logger = logging.getLogger("qt6_gui.video_viewport")

QML_VIEWPORT_CODE = """
import QtQuick 2.15
import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0

Item {
    id: root
    anchors.fill: parent

    GstGLQt6VideoItem {
        id: videoItem
        objectName: "videoItem"
        anchors.fill: parent
        smooth: true
        antialiasing: false
        opacity: 1.0
    }
}
"""


class VideoViewportWidget(QWidget):
    """
    High-Performance Zero-Copy Video Render Canvas for Android Auto Projected Stream.
    Embeds native QQuickView via createWindowContainer for direct KMS hardware scanout.
    """

    touch_input_event = pyqtSignal(dict)
    user_input_event = pyqtSignal(str, int, int, int)

    def __init__(self, parent=None, sample_interval_ms: int = 30):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self.sample_interval_ms = sample_interval_ms
        self._last_drag_time = 0.0

        self.frame_width = 1280
        self.frame_height = 800
        self.current_frame_data: Optional[bytes] = None

        self.margin_width: int = 0
        self.margin_height: int = 0
        self.stretch_to_fill: bool = True

        self._gst_sink = None
        self._gl_decoder = None
        self._qml_temp_path = None
        self._attached = False
        self._sink_bound_callback: Optional[Callable[[], None]] = None

        # 1. Initialize native QQuickView
        self._quick_view = QQuickView()
        self._quick_view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._quick_view.setColor(Qt.GlobalColor.black)

        # 2. Write and load QML scene
        with tempfile.NamedTemporaryFile("w", suffix=".qml", delete=False) as f:
            f.write(QML_VIEWPORT_CODE)
            self._qml_temp_path = f.name

        self._quick_view.setSource(QUrl.fromLocalFile(self._qml_temp_path))

        # 3. Embed QQuickView window into QWidget hierarchy via native container
        self._container = QWidget.createWindowContainer(self._quick_view, self)
        self._container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._container.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._container)

        # Filter events on container and quick view for touch/mouse dispatch
        self._container.installEventFilter(self)
        self._quick_view.installEventFilter(self)

        # 4. Connect sceneGraphInitialized
        self._quick_view.sceneGraphInitialized.connect(self._on_scenegraph_initialized)
        logger.info("🎬 [VideoViewport] Native QQuickView container initialized")

    def set_sink_bound_callback(self, cb: Callable[[], None]) -> None:
        """Set callback invoked once qml6glsink has been successfully bound to GstGLQt6VideoItem."""
        self._sink_bound_callback = cb
        if self._attached and self._sink_bound_callback:
            self._sink_bound_callback()

    def attach_gl_decoder(self, decoder) -> None:
        """Wire a video decoder to this viewport. Called from main.py after SHM engine init."""
        self._gl_decoder = decoder
        if hasattr(decoder, "attach_viewport"):
            decoder.attach_viewport(self)

    def attach_gstreamer_sink(self, sink) -> None:
        """Attach a GStreamer qml6glsink element."""
        self._gst_sink = sink
        if hasattr(self._quick_view, "isSceneGraphInitialized") and self._quick_view.isSceneGraphInitialized():
            self._on_scenegraph_initialized()

    def _on_scenegraph_initialized(self) -> None:
        """Executed inside Qt's OpenGL render thread when Scene Graph is ready."""
        if self._attached or not self._gst_sink:
            return

        root = self._quick_view.rootObject()
        if not root:
            logger.warning("[VideoViewport] sceneGraphInitialized but rootObject is None")
            return

        video_item = root.findChild(QObject, "videoItem")
        if not video_item:
            logger.warning("[VideoViewport] sceneGraphInitialized but videoItem not found")
            return

        try:
            cpp_ptr = None
            try:
                import PyQt6.sip as sip
                cpp_ptr = sip.unwrapinstance(video_item)
            except Exception:
                pass

            if not cpp_ptr:
                try:
                    from shiboken6 import Shiboken
                    cpp_ptr = Shiboken.getCppPointer(video_item)[0]
                except Exception:
                    pass

            if cpp_ptr and self._gst_sink:
                libgobject_path = ctypes.util.find_library("gobject-2.0") or "libgobject-2.0.so.0"
                libgobject = ctypes.CDLL(libgobject_path)
                libgobject.g_object_set.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_char_p,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ]
                libgobject.g_object_set.restype = None
                libgobject.g_object_set(hash(self._gst_sink), b"widget", ctypes.c_void_p(cpp_ptr), None)
                self._attached = True
                logger.info(
                    f"🎬 [VideoViewport] qml6glsink attached to GstGLQt6VideoItem (ptr={hex(cpp_ptr)}) — Zero-Copy ACTIVE!"
                )
                if self._sink_bound_callback:
                    self._sink_bound_callback()
        except Exception as exc:
            logger.error(f"[VideoViewport] Error in _on_scenegraph_initialized: {exc}")

    def update_frame(self, frame_bytes: bytes, width: int, height: int):
        """Update active frame dimensions."""
        if width > 0 and height > 0:
            self.frame_width = width
            self.frame_height = height

    def cleanupGL(self) -> None:
        """Release temporary QML resources."""
        self._attached = False
        self._gst_sink = None
        if self._qml_temp_path and os.path.exists(self._qml_temp_path):
            try:
                os.unlink(self._qml_temp_path)
            except Exception:
                pass
            self._qml_temp_path = None

    def set_margins(self, margin_width: int = 0, margin_height: int = 0, stretch_to_fill: bool = True) -> None:
        """Configure aspect margins and scaling policy."""
        self.margin_width = margin_width
        self.margin_height = margin_height
        self.stretch_to_fill = stretch_to_fill

    # ------------------------------------------------------------------
    # Input Event Interception (Multi-Touch & Mouse via Event Filter)
    # ------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype in (
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TouchCancel,
        ):
            self.touchEvent(event)
            return True
        elif etype == QEvent.Type.MouseButtonPress:
            self.mousePressEvent(event)
            return True
        elif etype == QEvent.Type.MouseMove:
            self.mouseMoveEvent(event)
            return True
        elif etype == QEvent.Type.MouseButtonRelease:
            self.mouseReleaseEvent(event)
            return True
        return super().eventFilter(watched, event)

    def _map_coords(self, pos) -> tuple[int, int]:
        """Map screen pixel coordinates to video projection coordinates."""
        from shared.touch_mapper import TouchCoordinateMapper
        pt = TouchCoordinateMapper.map_coordinate(
            raw_x=float(pos.x()),
            raw_y=float(pos.y()),
            surface_width=float(self.width()),
            surface_height=float(self.height()),
            negotiated_width=self.frame_width,
            negotiated_height=self.frame_height,
            margin_width=self.margin_width,
            margin_height=self.margin_height,
            stretch_to_fill=self.stretch_to_fill,
        )
        return pt.x, pt.y

    def touchEvent(self, event: QTouchEvent):
        """Native Qt Multi-Touch Event Interception."""
        if event.type() == QEvent.Type.TouchCancel:
            self.touch_input_event.emit({
                "action": 1,
                "action_index": 0,
                "pointers": [{"x": 0, "y": 0, "pointer_id": 0}],
            })
            event.accept()
            return

        points = event.points()
        if not points:
            event.accept()
            return

        active_pointers = []
        pressed_indices = []
        released_points = []

        for pt in points:
            px, py = self._map_coords(pt.position())
            pid = int(pt.id())
            state = pt.state()
            if state != QEventPoint.State.Released:
                active_pointers.append({"x": px, "y": py, "pointer_id": pid})
                if state == QEventPoint.State.Pressed:
                    pressed_indices.append(len(active_pointers) - 1)
            else:
                released_points.append({"x": px, "y": py, "pointer_id": pid})

        if not active_pointers:
            action = 1
            action_index = 0
            pointers_payload = released_points if released_points else [{"x": 0, "y": 0, "pointer_id": 0}]
        elif pressed_indices:
            if len(active_pointers) == 1 and pressed_indices[0] == 0:
                action = 0
                action_index = 0
            else:
                action = 5
                action_index = pressed_indices[0]
            pointers_payload = active_pointers
        elif released_points:
            action = 6
            releasing_pointer = released_points[0]
            action_index = len(active_pointers)
            pointers_payload = list(active_pointers) + [releasing_pointer]
        else:
            now = time.monotonic()
            if (now - self._last_drag_time) * 1000.0 < self.sample_interval_ms:
                event.accept()
                return
            self._last_drag_time = now
            action = 2
            action_index = 0
            pointers_payload = active_pointers

        self.touch_input_event.emit({
            "action": action,
            "action_index": action_index,
            "pointers": pointers_payload,
        })
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        x, y = self._map_coords(event.position())
        btn = getattr(event.button(), "value", 1)
        self.user_input_event.emit("press", x, y, int(btn))
        self.touch_input_event.emit({
            "action": 0,
            "action_index": 0,
            "pointers": [{"x": x, "y": y, "pointer_id": 0}],
        })

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() != Qt.MouseButton.NoButton:
            now = time.monotonic()
            if (now - self._last_drag_time) * 1000.0 < self.sample_interval_ms:
                return
            self._last_drag_time = now
            x, y = self._map_coords(event.position())
            btn = getattr(event.button(), "value", 1)
            self.user_input_event.emit("move", x, y, int(btn))
            self.touch_input_event.emit({
                "action": 2,
                "action_index": 0,
                "pointers": [{"x": x, "y": y, "pointer_id": 0}],
            })

    def mouseReleaseEvent(self, event: QMouseEvent):
        x, y = self._map_coords(event.position())
        btn = getattr(event.button(), "value", 1)
        self.user_input_event.emit("release", x, y, int(btn))
        self.touch_input_event.emit({
            "action": 1,
            "action_index": 0,
            "pointers": [{"x": x, "y": y, "pointer_id": 0}],
        })
