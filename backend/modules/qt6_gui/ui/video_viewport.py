"""
video_viewport.py — QOpenGLWidget Subclass for Low-Latency SHM Video Frame Rendering.

Renders zero-copy RGBA / YUV420 frames uploaded directly from `nemo_media_shm_down` to OpenGL textures.
Intercepts mouse/touch events and emits normalized input coordinates for channel_manager.
"""

import logging
import time
from typing import Callable, Optional
from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtGui import QEventPoint, QMouseEvent, QTouchEvent
try:
    from OpenGL import GL
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

logger = logging.getLogger("qt6_gui.video_viewport")


class VideoViewportWidget(QOpenGLWidget):
    """
    OpenGL Texture Render Canvas for Android Auto Video Stream with Multi-Touch Support.
    """

    # Emits full touch event dict for AA input channel (action, action_index, pointers)
    touch_input_event = pyqtSignal(dict)
    # Legacy signal for backwards compatibility
    user_input_event = pyqtSignal(str, int, int, int)

    def __init__(self, parent=None, sample_interval_ms: int = 30):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

        self.sample_interval_ms = sample_interval_ms  # Throttling interval for DRAG events (default 30ms / ~33Hz)
        self._last_drag_time = 0.0

        self.texture_id = 0
        self.frame_width = 1280
        self.frame_height = 720
        self.current_frame_data: Optional[bytes] = None
        self.has_new_frame = False

    def update_frame(self, frame_bytes: bytes, width: int, height: int):
        """Update active RGBA frame pixel data from SHM reader."""
        if not frame_bytes or width <= 0 or height <= 0:
            return
        self.current_frame_data = frame_bytes
        self.frame_width = width
        self.frame_height = height
        self.has_new_frame = True
        self.update()  # Triggers paintGL redraw

    def initializeGL(self):
        logger.info("🔍 [Video Viewport Trace] Entering initializeGL()...")
        if not HAS_OPENGL:
            logger.warning("OpenGL Python bindings not found — falling back to QPainter software render")
            return

        GL.glClearColor(0.05, 0.07, 0.09, 1.0)
        GL.glEnable(GL.GL_TEXTURE_2D)
        self.texture_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        logger.info("🔍 [Video Viewport Trace] initializeGL() completed cleanly!")

    def cleanupGL(self):
        """Safely delete OpenGL texture while context is valid on the main GUI thread."""
        if HAS_OPENGL and self.texture_id:
            try:
                if self.isValid():
                    self.makeCurrent()
                    GL.glDeleteTextures([self.texture_id])
                    self.doneCurrent()
            except Exception as exc:
                logger.debug("cleanupGL notice: %s", exc)
            self.texture_id = 0
            self.current_frame_data = None

    def resizeGL(self, w: int, h: int):
        if HAS_OPENGL:
            GL.glViewport(0, 0, w, h)

    def paintGL(self):
        if not HAS_OPENGL or not self.texture_id:
            return

        GL.glClear(GL.GL_COLOR_BUFFER_BIT)

        if (
            self.current_frame_data
            and self.has_new_frame
            and 0 < self.frame_width <= 4096
            and 0 < self.frame_height <= 4096
            and len(self.current_frame_data) == self.frame_width * self.frame_height * 4
        ):
            try:
                GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
                GL.glTexImage2D(
                    GL.GL_TEXTURE_2D,
                    0,
                    GL.GL_RGBA,
                    self.frame_width,
                    self.frame_height,
                    0,
                    GL.GL_RGBA,
                    GL.GL_UNSIGNED_BYTE,
                    self.current_frame_data,
                )
                self.has_new_frame = False
            except Exception as exc:
                logger.debug("OpenGL glTexImage2D exception: %s", exc)
                self.has_new_frame = False

        if self.texture_id and self.current_frame_data:
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
            GL.glBegin(GL.GL_QUADS)
            GL.glTexCoord2f(0.0, 0.0); GL.glVertex2f(-1.0, 1.0)
            GL.glTexCoord2f(1.0, 0.0); GL.glVertex2f(1.0, 1.0)
            GL.glTexCoord2f(1.0, 1.0); GL.glVertex2f(1.0, -1.0)
            GL.glTexCoord2f(0.0, 1.0); GL.glVertex2f(-1.0, -1.0)
            GL.glEnd()

    # ------------------------------------------------------------------
    # Input Event Interception (Multi-Touch & Mouse)
    # ------------------------------------------------------------------

    def _map_coords(self, pos) -> tuple[int, int]:
        """Map screen pixel coordinates to video projection coordinates compensating for letterboxing/pillarboxing."""
        widget_w = max(1.0, float(self.width()))
        widget_h = max(1.0, float(self.height()))
        target_w = max(1.0, float(self.frame_width))
        target_h = max(1.0, float(self.frame_height))

        target_ratio = target_w / target_h
        view_ratio = widget_w / widget_h

        # Compute letterbox bounds
        if view_ratio > target_ratio:
            displayed_w = widget_h * target_ratio
            displayed_h = widget_h
        else:
            displayed_w = widget_w
            displayed_h = widget_w / target_ratio

        ui_left = (widget_w - displayed_w) / 2.0
        ui_top = (widget_h - displayed_h) / 2.0

        local_x = float(pos.x()) - ui_left
        local_y = float(pos.y()) - ui_top

        norm_x = int((local_x / displayed_w) * target_w)
        norm_y = int((local_y / displayed_h) * target_h)

        norm_x = max(0, min(self.frame_width, norm_x))
        norm_y = max(0, min(self.frame_height, norm_y))
        return norm_x, norm_y

    def touchEvent(self, event: QTouchEvent):
        """Native Qt Multi-Touch Event Interception."""
        points = event.points()
        if not points:
            return super().touchEvent(event)

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
            action = 6
            action_index = 0
            pointers_payload = active_pointers
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


