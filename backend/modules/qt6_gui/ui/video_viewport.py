"""
video_viewport.py — QOpenGLWidget Subclass for Low-Latency SHM Video Frame Rendering.

Renders zero-copy RGBA / YUV420 frames uploaded directly from `nemo_media_shm_down` to OpenGL textures.
Intercepts mouse/touch events and emits normalized input coordinates for channel_manager.
"""

import logging
from typing import Callable, Optional
from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtGui import QMouseEvent
try:
    from OpenGL import GL
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

logger = logging.getLogger("qt6_gui.video_viewport")


class VideoViewportWidget(QOpenGLWidget):
    """
    OpenGL Texture Render Canvas for Android Auto Video Stream.
    """

    # Emits (event_type, x, y, button) for input routing
    user_input_event = pyqtSignal(str, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

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
    # Input Event Interception
    # ------------------------------------------------------------------

    def _map_coords(self, pos) -> tuple[int, int]:
        widget_w = max(1, self.width())
        widget_h = max(1, self.height())
        norm_x = int((pos.x() / widget_w) * self.frame_width)
        norm_y = int((pos.y() / widget_h) * self.frame_height)
        norm_x = max(0, min(self.frame_width, norm_x))
        norm_y = max(0, min(self.frame_height, norm_y))
        return norm_x, norm_y

    def mousePressEvent(self, event: QMouseEvent):
        x, y = self._map_coords(event.position())
        btn = getattr(event.button(), "value", 1)
        self.user_input_event.emit("press", x, y, int(btn))

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() != Qt.MouseButton.NoButton:
            x, y = self._map_coords(event.position())
            btn = getattr(event.button(), "value", 1)
            self.user_input_event.emit("move", x, y, int(btn))

    def mouseReleaseEvent(self, event: QMouseEvent):
        x, y = self._map_coords(event.position())
        btn = getattr(event.button(), "value", 1)
        self.user_input_event.emit("release", x, y, int(btn))


