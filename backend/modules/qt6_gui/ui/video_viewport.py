"""
video_viewport.py — QQuickWidget-based Zero-Copy Video Viewport for Android Auto Projected Stream.

Uses QQuickWidget (single native window compatible with EGLFS and Wayland) embedding GstGLQt6VideoItem.
Avoids createWindowContainer() which is forbidden on EGLFS (causes 'OpenGL windows cannot be mixed with others').
"""

import os
import sys
import time
import logging
import tempfile
import ctypes
import ctypes.util
from typing import Optional, Callable

from PyQt6.QtWidgets import QWidget
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtCore import QEvent, QPointF, Qt, QUrl, QObject, pyqtSignal
from PyQt6.QtGui import QEventPoint, QMouseEvent, QTouchEvent, QPainter, QImage
from PyQt6.QtQuick import QQuickItem, QQuickWindow, QSGRendererInterface, QQuickImageProvider

logger = logging.getLogger("qt6_gui.video_viewport")

QML_ZERO_COPY_CODE = """
import QtQuick 2.15
import org.freedesktop.gstreamer.Qt6GLVideoItem 1.0

Rectangle {
    id: root
    anchors.fill: parent
    color: "#000000"

    GstGLQt6VideoItem {
        id: videoItem
        objectName: "videoItem"
        anchors.fill: parent
        smooth: true
        antialiasing: false
        opacity: 1.0
        visible: true
    }

    Image {
        id: fallbackImage
        objectName: "fallbackImage"
        anchors.fill: parent
        visible: false
        fillMode: Image.PreserveAspectFit
        cache: false
        source: "image://nemo_video/frame"
    }
}
"""

QML_FALLBACK_CODE = """
import QtQuick 2.15

Rectangle {
    id: root
    anchors.fill: parent
    color: "#000000"

    Image {
        id: fallbackImage
        objectName: "fallbackImage"
        anchors.fill: parent
        visible: true
        fillMode: Image.PreserveAspectFit
        cache: false
        source: "image://nemo_video/frame"
    }
}
"""


class FrameImageProvider(QQuickImageProvider):
    """Fallback QML Image Provider serving software/appsink RGBA video frames."""
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.image = QImage(1280, 720, QImage.Format.Format_RGBA8888)
        self.image.fill(0)

    def requestImage(self, *args):
        # PyQt6 passes (id_str, requestedSize) and expects Tuple[QImage, QSize]
        # PySide6 passes (id_str, size, requestedSize) and expects QImage
        size = self.image.size()
        if len(args) == 2:
            return self.image, size
        return self.image


class VideoViewportWidget(QQuickWidget):
    """
    High-Performance Zero-Copy & Fallback Video Render Canvas for Android Auto Projected Stream.
    Inherits from QQuickWidget (compatible with EGLFS single-window constraint).
    """

    touch_input_event = pyqtSignal(dict)
    user_input_event = pyqtSignal(str, int, int, int)

    def __init__(self, parent=None, sample_interval_ms: int = 30):
        super().__init__(parent)
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self.sample_interval_ms = sample_interval_ms
        self._last_drag_time = 0.0

        self.frame_width = 1280
        self.frame_height = 720
        self.current_frame_data: Optional[bytes] = None

        self.margin_width: int = 0
        self.margin_height: int = 0
        self.stretch_to_fill: bool = False

        self._gst_sink = None
        self._gl_decoder = None
        self._qml_temp_path = None
        self._attached = False
        self._sg_initialized = False
        self._video_item: Optional[QObject] = None
        self._fallback_image: Optional[QObject] = None
        self._sink_bound_callback: Optional[Callable[[], None]] = None
        self._is_fallback_mode = False
        self._frame_seq = 0

        # Register ImageProvider for non-zero-copy frames
        self._image_provider = FrameImageProvider()
        self.engine().addImageProvider("nemo_video", self._image_provider)

        self.statusChanged.connect(self._on_qml_status_changed)
        self.sceneGraphError.connect(self._on_scenegraph_error)

        # Hook Scene Graph initialization
        qw = self.quickWindow()
        if qw:
            qw.sceneGraphInitialized.connect(self._on_scenegraph_initialized)

        # On Windows or non-GL systems, directly load fallback Image QML without GstGL plugin warnings
        if sys.platform == "win32":
            self._load_fallback_qml()
        else:
            self._load_qml(QML_ZERO_COPY_CODE)
        logger.info("🎬 [VideoViewport] QQuickWidget initialized (Dual-Mode EGLFS/Wayland/Windows Compliant)")

    def _load_qml(self, qml_content: str):
        if self._qml_temp_path and os.path.exists(self._qml_temp_path):
            try:
                os.unlink(self._qml_temp_path)
            except Exception:
                pass
        with tempfile.NamedTemporaryFile("w", suffix=".qml", delete=False) as f:
            f.write(qml_content)
            self._qml_temp_path = f.name
        self.setSource(QUrl.fromLocalFile(self._qml_temp_path))

    def _load_fallback_qml(self):
        self._is_fallback_mode = True
        logger.info("🎬 [VideoViewport] Switching to fallback Image viewport QML")
        self._load_qml(QML_FALLBACK_CODE)

    def _on_scenegraph_error(self, error, message):
        logger.error(f"❌ [VideoViewport] SceneGraph Error: {error} - {message}")

    def _on_qml_status_changed(self, status):
        logger.info(f"🎬 [VideoViewport] QML Status: {status}")
        if status == QQuickWidget.Status.Ready:
            root = self.rootObject()
            if root:
                self._video_item = root.findChild(QObject, "videoItem")
                self._fallback_image = root.findChild(QObject, "fallbackImage")
                logger.info(f"🎬 [VideoViewport] Elements ready: videoItem={self._video_item}, fallbackImage={self._fallback_image}")
                if self._sg_initialized:
                    self._try_bind()
        elif status == QQuickWidget.Status.Error:
            for err in self.errors():
                logger.warning(f"⚠️ [VideoViewport] QML Error: {err.toString()}")
            if not self._is_fallback_mode:
                self._load_fallback_qml()

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
        logger.info(f"🎬 [VideoViewport] attach_gstreamer_sink (sg_initialized={self._sg_initialized})")
        if self._sg_initialized:
            self._try_bind()

    def _on_scenegraph_initialized(self) -> None:
        logger.info("🎬 [VideoViewport] Scene Graph initialized signal received on render thread")
        self._sg_initialized = True
        self._try_bind()

    def _try_bind(self) -> None:
        if self._attached or not self._gst_sink or not self._sg_initialized:
            return

        if not self._video_item:
            root = self.rootObject()
            if root:
                self._video_item = root.findChild(QObject, "videoItem")

        if not self._video_item:
            return

        try:
            cpp_ptr = None
            try:
                import PyQt6.sip as sip
                cpp_ptr = sip.unwrapinstance(self._video_item)
            except Exception:
                pass

            if not cpp_ptr:
                try:
                    from shiboken6 import Shiboken
                    cpp_ptr = Shiboken.getCppPointer(self._video_item)[0]
                except Exception:
                    pass

            if cpp_ptr and self._gst_sink:
                if sys.platform == "win32":
                    libgobject_path = ctypes.util.find_library("gobject-2.0") or "gobject-2.0-0.dll"
                else:
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
                if self._video_item:
                    self._video_item.setProperty("visible", True)
                if self._fallback_image:
                    self._fallback_image.setProperty("visible", False)
                logger.info(
                    f"🎬 [VideoViewport] qml6glsink successfully attached to GstGLQt6VideoItem (ptr={hex(cpp_ptr)}) — Zero-Copy ACTIVE!"
                )
                if self._sink_bound_callback:
                    self._sink_bound_callback()
        except Exception as exc:
            logger.error(f"❌ [VideoViewport] Error binding widget to sink: {exc}")

    def update_frame(self, frame_bytes: bytes, width: int, height: int):
        """Update active frame dimensions and render fallback buffer if zero-copy is inactive."""
        if width > 0 and height > 0:
            self.frame_width = width
            self.frame_height = height
        gl_active = self._attached or (self._gl_decoder is not None and getattr(self._gl_decoder, "is_available", False))
        if not gl_active and frame_bytes:
            self.current_frame_data = frame_bytes
            img = QImage(frame_bytes, width, height, width * 4, QImage.Format.Format_RGBA8888)
            self._image_provider.image = img
            if self._fallback_image:
                self._fallback_image.setProperty("visible", True)
                self._frame_seq = (self._frame_seq + 1) % 1000000
                self._fallback_image.setProperty("source", f"image://nemo_video/frame?seq={self._frame_seq}")

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
    # Input Event Interception (Multi-Touch & Mouse)
    # ------------------------------------------------------------------

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

    def event(self, event: QEvent) -> bool:
        if event.type() in (
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TouchCancel,
        ):
            self.touchEvent(event)
            return True
        return super().event(event)

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

        for idx, pt in enumerate(points):
            pos = pt.position()
            if pos.isNull() and not pt.scenePosition().isNull():
                pos = pt.scenePosition()
            px, py = self._map_coords(pos)
            pid = int(pt.id()) if (pt.id() != 0 or idx == 0) else idx
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
