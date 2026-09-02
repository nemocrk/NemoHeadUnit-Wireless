"""
video_viewport.py — QOpenGLWidget (or QWidget fallback) for Low-Latency SHM Video Frame Rendering.

Renders decoded RGBA frames using one of two paths:
  - GL path  : GlImageSinkDecoder shares EGL context -> glimagesink renders via paintGL() shader quad (zero CPU)
  - RGBA path: GStreamerHwDecoder / PyAV bytes -> QPainter.drawImage (fallback when GL unavailable)

Intercepts mouse/touch events and emits normalized input coordinates for channel_manager.
"""

import logging
import time
from typing import Optional
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QEventPoint, QMouseEvent, QTouchEvent, QPainter, QImage, QColor
from PyQt6.QtCore import QEvent, QPointF, Qt, pyqtSignal

logger = logging.getLogger("qt6_gui.video_viewport")

# ---------------------------------------------------------------------------
# GL availability probe
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget as _QOpenGLWidgetBase
    from PyQt6.QtGui import QOpenGLContext
    from OpenGL import GL as _GL
    _HAS_QOPENGL = True
except ImportError:
    _QOpenGLWidgetBase = None
    _HAS_QOPENGL = False


# ---------------------------------------------------------------------------
# _GLMixin — mixed in only when QOpenGLWidget is importable
# ---------------------------------------------------------------------------

class _GLMixin:
    """
    OpenGL render methods mixed into VideoViewportWidget when QOpenGLWidget is available.
    paintGL renders the GlImageSinkDecoder texture via a minimal fullscreen shader quad.
    Falls back to QPainter (RGBA bytes) when no GL texture is ready.
    """

    # Inline GLSL — RGBA passthrough, Y-flipped for GL convention
    # ponytail: minimal passthrough; upgrade to BT.601 YUV shader if color accuracy needed
    _VERT_SRC = (
        "attribute vec2 a_pos; varying vec2 v_uv;\n"
        "void main(){\n"
        "  gl_Position=vec4(a_pos,0.,1.);\n"
        "  v_uv=(a_pos+1.)*.5;\n"
        "  v_uv.y=1.-v_uv.y;\n"
        "}"
    )
    _FRAG_SRC = (
        "uniform sampler2D u_tex; varying vec2 v_uv;\n"
        "void main(){ gl_FragColor=texture2D(u_tex,v_uv); }"
    )

    def initializeGL(self):
        """Share Qt EGL context with GlImageSinkDecoder and set GL clear color."""
        from OpenGL import GL
        GL.glClearColor(0.05, 0.067, 0.09, 1.0)
        if not (self._gl_decoder and self._gl_decoder.is_available):
            return
        try:
            from PyQt6.QtGui import QOpenGLContext
            ctx = QOpenGLContext.currentContext()
            native = ctx.nativeInterface()
            if native and hasattr(native, "nativeContext") and hasattr(native, "display"):
                self._gl_decoder.set_gl_context(int(native.display()), int(native.nativeContext()))
                logger.debug("[VideoViewport] EGL context shared with GlImageSinkDecoder")
            else:
                logger.warning("[VideoViewport] No EGL native interface — GL decoder disabled")
                self._gl_decoder = None
        except Exception as exc:
            logger.warning(f"[VideoViewport] initializeGL failed: {exc} — GL decoder disabled")
            self._gl_decoder = None

    def _compile_shader(self):
        """Compile fullscreen quad shader. Called lazily on first paintGL with a valid texture."""
        from OpenGL import GL
        import numpy as np
        vert = GL.glCreateShader(GL.GL_VERTEX_SHADER)
        GL.glShaderSource(vert, self._VERT_SRC)
        GL.glCompileShader(vert)
        frag = GL.glCreateShader(GL.GL_FRAGMENT_SHADER)
        GL.glShaderSource(frag, self._FRAG_SRC)
        GL.glCompileShader(frag)
        prog = GL.glCreateProgram()
        GL.glAttachShader(prog, vert)
        GL.glAttachShader(prog, frag)
        GL.glLinkProgram(prog)
        GL.glDeleteShader(vert)
        GL.glDeleteShader(frag)
        verts = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype=np.float32)
        vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self._shader_program = prog
        self._quad_vbo = vbo

    def paintGL(self):
        """
        GL render path: pull texture from GlImageSinkDecoder and draw with shader quad.
        Falls back to QPainter RGBA path if no texture is available yet.
        """
        from OpenGL import GL
        tex_id = 0
        if self._gl_decoder and self._gl_decoder.is_available:
            tex_id = self._gl_decoder.get_latest_texture_id()
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if tex_id:
            if self._shader_program is None:
                self._compile_shader()
            GL.glUseProgram(self._shader_program)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
            GL.glUniform1i(GL.glGetUniformLocation(self._shader_program, "u_tex"), 0)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._quad_vbo)
            loc = GL.glGetAttribLocation(self._shader_program, "a_pos")
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, 2, GL.GL_FLOAT, False, 0, None)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
            GL.glDisableVertexAttribArray(loc)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glUseProgram(0)
        elif (
            self.current_frame_data
            and 0 < self.frame_width <= 4096
            and 0 < self.frame_height <= 4096
            and len(self.current_frame_data) == self.frame_width * self.frame_height * 4
        ):
            painter = QPainter(self)
            img = QImage(
                self.current_frame_data,
                self.frame_width, self.frame_height,
                self.frame_width * 4,
                QImage.Format.Format_RGBA8888,
            )
            painter.drawImage(self.rect(), img)
            painter.end()

    def resizeGL(self, w: int, h: int):
        from OpenGL import GL
        GL.glViewport(0, 0, w, h)


# ---------------------------------------------------------------------------
# VideoViewportWidget — QOpenGLWidget + _GLMixin when GL available, QWidget otherwise
# ---------------------------------------------------------------------------

_bases = (_GLMixin, _QOpenGLWidgetBase) if _HAS_QOPENGL else (QWidget,)


class VideoViewportWidget(*_bases):
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

        # GL decoder — set via attach_gl_decoder() after SHM engine init in main.py
        self._gl_decoder = None
        self._shader_program = None  # compiled lazily on first paintGL with texture
        self._quad_vbo = None

    def attach_gl_decoder(self, decoder) -> None:
        """Wire a GlImageSinkDecoder to this viewport. Called from main.py after SHM engine init."""
        self._gl_decoder = decoder

    def update_frame(self, frame_bytes: bytes, width: int, height: int):
        """Update active frame. No-op when GL decoder is active (GStreamer renders directly)."""
        if self._gl_decoder and self._gl_decoder.is_available:
            return  # GL path: glimagesink renders; no bytes needed
        if not frame_bytes or width <= 0 or height <= 0:
            return
        self.current_frame_data = frame_bytes
        self.frame_width = width
        self.frame_height = height
        self.update()  # Triggers paintEvent / paintGL redraw

    def cleanupGL(self):
        """Safely release frame resources."""
        self.current_frame_data = None

    def paintEvent(self, event):
        """
        RGBA fallback render path — only used when base class is QWidget (no GL available).
        When QOpenGLWidget is the base class, paintGL() handles rendering instead.
        """
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

    def cleanupGL(self):
        """Safely release frame and GL resources."""
        self.current_frame_data = None
        self._shader_program = None
        self._quad_vbo = None

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


