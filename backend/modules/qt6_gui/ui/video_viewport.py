"""
video_viewport.py — QOpenGLWidget Subclass for Low-Latency SHM Video Frame Rendering.

Renders zero-copy RGBA / YUV420 frames uploaded directly from `nemo_media_shm_down` to OpenGL textures.
Intercepts mouse/touch events and emits normalized input coordinates for channel_manager.
"""

import logging
import time
from typing import Optional
from PyQt6.QtWidgets import QWidget
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtGui import QEventPoint, QMouseEvent, QTouchEvent, QPainter, QImage, QColor
from PyQt6.QtCore import QEvent, QPointF, Qt, pyqtSignal

logger = logging.getLogger("qt6_gui.video_viewport")


class VideoViewportWidget(QOpenGLWidget):
    """
    High-Performance Video Render Canvas for Android Auto Projected Stream.
    Renders decoded RGBA frames directly from shared memory with multi-touch support.
    """

    # Emits full touch event dict for AA input channel (action, action_index, pointers)
    touch_input_event = pyqtSignal(dict)
    # Legacy signal for backwards compatibility
    user_input_event = pyqtSignal(str, int, int, int)

    def __init__(self, parent=None, sample_interval_ms: int = 30):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self.sample_interval_ms = sample_interval_ms  # Throttling interval for DRAG events (default 30ms / ~33Hz)
        self._last_drag_time = 0.0

        self.frame_width = 1280
        self.frame_height = 720
        self.current_frame_data: Optional[bytes] = None

        self.margin_width: int = 0
        self.margin_height: int = 0
        self.stretch_to_fill: bool = True

    def update_frame(self, frame_bytes: bytes, width: int, height: int):
        """Update active RGBA frame pixel data from SHM reader."""
        if not frame_bytes or width <= 0 or height <= 0:
            return
        self.current_frame_data = frame_bytes
        self.frame_width = width
        self.frame_height = height
        self.update()  # Triggers paintEvent redraw

    def cleanupGL(self):
        """Safely release frame resources."""
        self.current_frame_data = None

    def paintEvent(self, event):
        """High-Performance Painter Rendering for projected video frames."""
        painter = QPainter(self)
        if (
            self.current_frame_data
            and 0 < self.frame_width <= 4096
            and 0 < self.frame_height <= 4096
            and len(self.current_frame_data) == self.frame_width * self.frame_height * 4
        ):
            img = QImage(
                self.current_frame_data,
                self.frame_width,
                self.frame_height,
                self.frame_width * 4,
                QImage.Format.Format_RGBA8888,
            )
            painter.drawImage(self.rect(), img)
        else:
            painter.fillRect(self.rect(), QColor(13, 17, 23))
        painter.end()

    def set_margins(self, margin_width: int = 0, margin_height: int = 0, stretch_to_fill: bool = True) -> None:
        """Configure aspect margins and scaling policy."""
        self.margin_width = margin_width
        self.margin_height = margin_height
        self.stretch_to_fill = stretch_to_fill

    # ------------------------------------------------------------------
    # Input Event Interception (Multi-Touch & Mouse)
    # ------------------------------------------------------------------

    def _map_coords(self, pos) -> tuple[int, int]:
        """Map screen pixel coordinates to video projection coordinates compensating for aspect margins."""
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

        # Determine Android Auto Touch Action:
        # 0 = PRESS, 1 = RELEASE, 2 = DRAG, 5 = POINTER_DOWN, 6 = POINTER_UP
        if not active_pointers:
            # Last pointer released -> RELEASE (1)
            action = 1
            action_index = 0
            pointers_payload = released_points if released_points else [{"x": 0, "y": 0, "pointer_id": 0}]
        elif pressed_indices:
            if len(active_pointers) == 1 and pressed_indices[0] == 0:
                # First pointer pressed -> PRESS (0)
                action = 0
                action_index = 0
            else:
                # Secondary pointer pressed -> POINTER_DOWN (5)
                action = 5
                action_index = pressed_indices[0]
            pointers_payload = active_pointers
        elif released_points:
            # Non-last pointer released -> POINTER_UP (6)
            # Android MotionEvent expects the releasing pointer included at action_index
            action = 6
            releasing_pointer = released_points[0]
            action_index = len(active_pointers)
            pointers_payload = list(active_pointers) + [releasing_pointer]
        else:
            # Moving / Dragging -> DRAG (2)
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


