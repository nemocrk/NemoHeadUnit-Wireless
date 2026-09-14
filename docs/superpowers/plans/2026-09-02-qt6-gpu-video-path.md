# Qt6 GPU Video Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the double CPU↔GPU memory crossing in Qt6 GUI video rendering with a GStreamer `glimagesink` path that shares the `QOpenGLWidget` OpenGL context, reducing Render/3D GPU load from ~99% to near zero on Bay Trail.

**Architecture:** `vah264dec ! vapostproc ! glupload ! glimagesink` pipeline with shared EGL context from `QOpenGLWidget.initializeGL()`. On first frame, `paintGL()` samples the `glimagesink` texture via a minimal GLSL shader quad. Full fallback chain: if `QOpenGLWidget` unavailable at import → existing `GStreamerHwDecoder` (RGBA bytes); if pipeline fails → same; if no GStreamer → PyAV CPU.

**Tech Stack:** Python 3.13, PyQt6, GStreamer 1.0 Python bindings (`gi.repository.Gst`, `GstGL`, `GstVideo`), OpenGL (PyOpenGL), micromamba `NemoHeadUnit-Wireless` env.

**Spec:** `docs/superpowers/specs/2026-09-02-qt6-gpu-video-path-design.md`

## Global Constraints

- All code runs in micromamba env `NemoHeadUnit-Wireless`; test with `micromamba run -n NemoHeadUnit-Wireless`
- Cross-platform: use `sys.platform` guards; no hardcoded `/tmp` or POSIX-only paths
- `BaseBackendModule` lifecycle unchanged; `main.py` must not be modified
- Smoke test after every task: `micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py` must boot clean
- Commit after each task with conventional commit message

---

### Task 1: `GlImageSinkDecoder` — GStreamer GL pipeline with EGL context sharing

**Files:**
- Modify: `backend/modules/qt6_gui/media/shm_media_engine.py`

**Interfaces:**
- Consumes: nothing from other tasks (standalone class)
- Produces:
  - `class GlImageSinkDecoder` with:
    - `__init__(on_frame_callback: Callable[[bytes, int, int, int], None])` — callback only used in RGBA fallback branch
    - `is_available: bool`
    - `set_gl_context(egl_display_handle: int, egl_context_handle: int) -> None`
    - `decode_nal(nal_data: bytes, ts_us: int = 0) -> bool`
    - `get_latest_texture_id() -> int` — returns 0 if no frame yet
    - `close() -> None`
  - `_HAS_QOPENGL: bool` module-level flag
  - `QtSHMMediaEngine._hw_decoder` is now `GlImageSinkDecoder` when `_HAS_QOPENGL` is True

- [ ] **Step 1: Add `_HAS_QOPENGL` flag at the top of `shm_media_engine.py`**

  Open `backend/modules/qt6_gui/media/shm_media_engine.py`. After the existing imports, add:

  ```python
  # GL availability probe — checked once at module load
  try:
      from PyQt6.QtOpenGLWidgets import QOpenGLWidget as _probe_ogl  # noqa: F401
      _HAS_QOPENGL = True
  except ImportError:
      _HAS_QOPENGL = False
  ```

- [ ] **Step 2: Write failing test for `_HAS_QOPENGL` flag and `GlImageSinkDecoder` availability**

  Create `tests/test_gl_image_sink_decoder.py`:

  ```python
  """Tests for GlImageSinkDecoder and _HAS_QOPENGL flag in shm_media_engine."""
  import sys
  import importlib


  def test_has_qopengl_flag_is_bool():
      mod = importlib.import_module("backend.modules.qt6_gui.media.shm_media_engine")
      assert isinstance(mod._HAS_QOPENGL, bool)


  def test_gl_image_sink_decoder_class_exists():
      mod = importlib.import_module("backend.modules.qt6_gui.media.shm_media_engine")
      assert hasattr(mod, "GlImageSinkDecoder")


  def test_gl_image_sink_decoder_interface():
      """GlImageSinkDecoder must expose the required interface regardless of GL availability."""
      mod = importlib.import_module("backend.modules.qt6_gui.media.shm_media_engine")
      dec = mod.GlImageSinkDecoder(on_frame_callback=lambda *a: None)
      assert hasattr(dec, "is_available")
      assert hasattr(dec, "set_gl_context")
      assert hasattr(dec, "decode_nal")
      assert hasattr(dec, "get_latest_texture_id")
      assert hasattr(dec, "close")
      assert isinstance(dec.is_available, bool)
      assert dec.get_latest_texture_id() == 0


  def test_shm_engine_uses_gl_decoder_when_available(monkeypatch):
      """QtSHMMediaEngine picks GlImageSinkDecoder when _HAS_QOPENGL is True."""
      mod = importlib.import_module("backend.modules.qt6_gui.media.shm_media_engine")
      monkeypatch.setattr(mod, "_HAS_QOPENGL", True)
      engine = mod.QtSHMMediaEngine.__new__(mod.QtSHMMediaEngine)
      engine.__init__()
      assert isinstance(engine._hw_decoder, mod.GlImageSinkDecoder)


  def test_shm_engine_uses_hw_decoder_when_gl_unavailable(monkeypatch):
      """QtSHMMediaEngine falls back to GStreamerHwDecoder when _HAS_QOPENGL is False."""
      mod = importlib.import_module("backend.modules.qt6_gui.media.shm_media_engine")
      monkeypatch.setattr(mod, "_HAS_QOPENGL", False)
      engine = mod.QtSHMMediaEngine.__new__(mod.QtSHMMediaEngine)
      engine.__init__()
      assert isinstance(engine._hw_decoder, mod.GStreamerHwDecoder)
  ```

- [ ] **Step 3: Run test to confirm failure**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_gl_image_sink_decoder.py -v
  ```

  Expected: FAIL — `GlImageSinkDecoder` not yet defined.

- [ ] **Step 4: Implement `GlImageSinkDecoder` in `shm_media_engine.py`**

  Add the following class **before** `QtSHMMediaEngine`. Insert after `GStreamerHwDecoder.close()`:

  ```python
  class GlImageSinkDecoder:
      """
      Hardware-accelerated H.264 decoder via GStreamer glimagesink with shared EGL context.

      Pipeline: appsrc ! h264parse ! vah264dec ! vapostproc ! glupload ! glimagesink
      Renders directly into the QOpenGLWidget GL context — zero CPU copies.

      Falls back to is_available=False if any required GStreamer element is missing.
      On failure, QtSHMMediaEngine stays on GStreamerHwDecoder (RGBA bytes path).
      """

      def __init__(self, on_frame_callback: Callable[[bytes, int, int, int], None]):
          self.on_frame_callback = on_frame_callback  # unused in GL path; kept for interface parity
          self.is_available = False
          self._pipeline = None
          self._appsrc = None
          self._glimagesink = None
          self._Gst = None
          self._GstGL = None
          self._latest_texture_id: int = 0
          self._gl_context_set = False
          self._egl_display: int = 0
          self._egl_context: int = 0

          self._try_init_pipeline()

      def _try_init_pipeline(self) -> None:
          """Build GStreamer pipeline. Sets is_available=True on success."""
          try:
              import gi
              gi.require_version("Gst", "1.0")
              gi.require_version("GstGL", "1.0")
              from gi.repository import Gst, GstGL
              Gst.init(None)
              self._Gst = Gst
              self._GstGL = GstGL

              # Scan system GStreamer plugin paths (micromamba isolation)
              registry = Gst.Registry.get()
              for path in [
                  "/usr/lib/gstreamer-1.0",
                  "/usr/lib/x86_64-linux-gnu/gstreamer-1.0",
                  "/usr/lib64/gstreamer-1.0",
              ]:
                  import os as _os
                  if _os.path.isdir(path):
                      registry.scan_path(path)

              # Verify required elements are present
              required = ["vah264dec", "vapostproc", "glupload", "glimagesink", "h264parse"]
              missing = [e for e in required if Gst.ElementFactory.find(e) is None]
              if missing:
                  logger.info(f"ℹ️ [GlImageSinkDecoder] Missing GStreamer elements {missing} — using RGBA fallback")
                  return

              pipe_str = (
                  "appsrc name=src is-live=true format=bytes "
                  "! h264parse config-interval=-1 "
                  "! vah264dec "
                  "! vapostproc "
                  "! video/x-raw(memory:DMABuf),format=RGBA "
                  "! glupload "
                  "! glimagesink name=sink sync=false qos=false"
              )
              self._pipeline = Gst.parse_launch(pipe_str)
              self._appsrc = self._pipeline.get_by_name("src")
              self._glimagesink = self._pipeline.get_by_name("sink")

              if not self._appsrc or not self._glimagesink:
                  logger.warning("[GlImageSinkDecoder] Pipeline element lookup failed")
                  return

              self.is_available = True
              logger.info("🎬 [GlImageSinkDecoder] Pipeline built — awaiting GL context from QOpenGLWidget.initializeGL()")

          except Exception as exc:
              logger.warning(f"[GlImageSinkDecoder] Init failed: {exc}")

      def set_gl_context(self, egl_display_handle: int, egl_context_handle: int) -> None:
          """
          Called from QOpenGLWidget.initializeGL() to share the Qt EGL context with glimagesink.
          Must be called before decode_nal() is first used.
          """
          if not self.is_available or not self._pipeline:
              return
          try:
              import gi
              gi.require_version("GstGL", "1.0")
              from gi.repository import GstGL
              self._egl_display = egl_display_handle
              self._egl_context = egl_context_handle

              # Wrap Qt's EGL context into a GstGLContext
              gl_display = GstGL.GLDisplayEGL.new_with_egl_display(egl_display_handle)
              gl_context = GstGL.GLContext.new_wrapped(
                  gl_display,
                  egl_context_handle,
                  GstGL.GLPlatform.EGL,
                  GstGL.GLAPI.GLES2 | GstGL.GLAPI.OPENGL,
              )
              gl_context.activate(True)

              # Propagate context to all pipeline elements
              gst_context = self._Gst.Context.new(GstGL.GL_DISPLAY_CONTEXT_TYPE, True)
              GstGL.context_set_gl_display(gst_context, gl_display)
              self._pipeline.set_context(gst_context)

              gst_context2 = self._Gst.Context.new("gst.gl.app_context", True)
              gst_context2.get_structure().set_value("context", gl_context)
              self._pipeline.set_context(gst_context2)

              # Start pipeline now that context is shared
              ret = self._pipeline.set_state(self._Gst.State.PLAYING)
              if ret == self._Gst.StateChangeReturn.FAILURE:
                  logger.error("[GlImageSinkDecoder] Pipeline failed to reach PLAYING")
                  self.is_available = False
                  return

              self._gl_context_set = True
              logger.info("🎬 [GlImageSinkDecoder] GL context shared — pipeline PLAYING, zero-CPU path active")

          except Exception as exc:
              logger.warning(f"[GlImageSinkDecoder] set_gl_context failed: {exc} — falling back to RGBA path")
              self.is_available = False

      def decode_nal(self, nal_data: bytes, ts_us: int = 0) -> bool:
          """Push a NAL unit into the pipeline. Returns False if not ready."""
          if not self.is_available or not self._appsrc or not self._gl_context_set:
              return False
          try:
              buf = self._Gst.Buffer.new_wrapped(nal_data)
              if ts_us > 0:
                  buf.pts = ts_us * 1000  # microseconds → nanoseconds
              self._appsrc.emit("push-buffer", buf)
              return True
          except Exception:
              return False

      def get_latest_texture_id(self) -> int:
          """
          Pull the latest GL texture ID from glimagesink for use in paintGL().
          Returns 0 if no frame is ready yet.
          """
          if not self.is_available or not self._glimagesink or not self._gl_context_set:
              return 0
          try:
              sample = self._glimagesink.emit("pull-preroll") if self._glimagesink else None
              if sample is None:
                  return self._latest_texture_id
              buf = sample.get_buffer()
              if buf is None:
                  return self._latest_texture_id
              import gi
              gi.require_version("GstGL", "1.0")
              from gi.repository import GstGL
              mem = buf.peek_memory(0)
              if mem and GstGL.is_gl_memory(mem):
                  gl_mem = GstGL.GLMemory(mem)
                  self._latest_texture_id = gl_mem.get_texture_id()
          except Exception:
              pass
          return self._latest_texture_id

      def close(self) -> None:
          if self._pipeline:
              try:
                  self._pipeline.set_state(self._Gst.State.NULL)
              except Exception:
                  pass
              self._pipeline = None
              self._appsrc = None
              self._glimagesink = None
          self.is_available = False
          self._gl_context_set = False
  ```

- [ ] **Step 5: Update `QtSHMMediaEngine.__init__` to select `GlImageSinkDecoder` when `_HAS_QOPENGL`**

  In `QtSHMMediaEngine.__init__`, replace:

  ```python
  # 1. Initialize Hardware-Accelerated GStreamer VA-API Video Decoder
  self._hw_decoder = GStreamerHwDecoder(self._on_hw_decoded_frame)
  ```

  With:

  ```python
  # 1. Initialize best available video decoder
  if _HAS_QOPENGL:
      self._hw_decoder = GlImageSinkDecoder(self._on_hw_decoded_frame)
      if not self._hw_decoder.is_available:
          logger.info("ℹ️ [SHM Engine] GlImageSinkDecoder not available — falling back to GStreamerHwDecoder")
          self._hw_decoder = GStreamerHwDecoder(self._on_hw_decoded_frame)
  else:
      self._hw_decoder = GStreamerHwDecoder(self._on_hw_decoded_frame)
  ```

- [ ] **Step 6: Run tests**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_gl_image_sink_decoder.py -v
  ```

  Expected: all 5 tests PASS.

- [ ] **Step 7: Smoke test backend boot**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py &
  sleep 5 && kill %1
  ```

  Expected: clean boot, no import errors. Look for `GlImageSinkDecoder` or `GStreamerHwDecoder` active line.

- [ ] **Step 8: Commit**

  ```bash
  git add backend/modules/qt6_gui/media/shm_media_engine.py tests/test_gl_image_sink_decoder.py
  git commit -m "feat(qt6_gui): add GlImageSinkDecoder with EGL context sharing, _HAS_QOPENGL fallback"
  ```

---

### Task 2: `VideoViewportWidget` — `paintGL()` shader quad + `QOpenGLWidget` failsafe

**Files:**
- Modify: `backend/modules/qt6_gui/ui/video_viewport.py`

**Interfaces:**
- Consumes: `GlImageSinkDecoder` (from Task 1) — accessed via `self._gl_decoder` set from `main_window` after engine init
- Produces:
  - `VideoViewportWidget` class with same external API as before (`update_frame`, `touch_input_event`, `user_input_event`, `set_margins`)
  - New method: `attach_gl_decoder(decoder: GlImageSinkDecoder) -> None`
  - `initializeGL()` calls `decoder.set_gl_context(egl_display, egl_context)`
  - `paintGL()` renders shader quad when GL decoder active, else falls back to QPainter path

- [ ] **Step 1: Write failing test for `attach_gl_decoder` interface**

  Create `tests/test_video_viewport_gl.py`:

  ```python
  """Tests for VideoViewportWidget GL path integration."""
  import sys
  import types


  def _make_mock_decoder(is_available=True, texture_id=42):
      dec = types.SimpleNamespace(
          is_available=is_available,
          set_gl_context=lambda *a: None,
          get_latest_texture_id=lambda: texture_id,
      )
      return dec


  def test_attach_gl_decoder_method_exists():
      """VideoViewportWidget must expose attach_gl_decoder()."""
      import importlib
      mod = importlib.import_module("backend.modules.qt6_gui.ui.video_viewport")
      assert hasattr(mod.VideoViewportWidget, "attach_gl_decoder")


  def test_attach_gl_decoder_sets_decoder():
      """attach_gl_decoder stores decoder on the widget."""
      import importlib
      mod = importlib.import_module("backend.modules.qt6_gui.ui.video_viewport")
      # Instantiate without QApplication — just check attribute wiring
      widget = object.__new__(mod.VideoViewportWidget)
      widget._gl_decoder = None
      decoder = _make_mock_decoder()
      widget.attach_gl_decoder(decoder)
      assert widget._gl_decoder is decoder


  def test_update_frame_no_op_when_gl_active():
      """update_frame() must be a no-op (not store bytes) when GL decoder is active."""
      import importlib
      mod = importlib.import_module("backend.modules.qt6_gui.ui.video_viewport")
      widget = object.__new__(mod.VideoViewportWidget)
      widget._gl_decoder = _make_mock_decoder(is_available=True)
      widget.current_frame_data = None
      widget.update_frame(b"fake_rgba", 1280, 720)
      assert widget.current_frame_data is None  # bytes not stored, GL path active


  def test_update_frame_stores_bytes_when_gl_unavailable():
      """update_frame() stores rgba_bytes when GL decoder is not active."""
      import importlib
      mod = importlib.import_module("backend.modules.qt6_gui.ui.video_viewport")
      widget = object.__new__(mod.VideoViewportWidget)
      widget._gl_decoder = None
      widget.current_frame_data = None
      widget.frame_width = 0
      widget.frame_height = 0
      # Patch update() to a no-op
      widget.update = lambda: None
      widget.update_frame(b"x" * (1280 * 720 * 4), 1280, 720)
      assert widget.current_frame_data is not None
  ```

- [ ] **Step 2: Run test to confirm failure**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_video_viewport_gl.py -v
  ```

  Expected: FAIL — `attach_gl_decoder` not yet defined.

- [ ] **Step 3: Add `_HAS_QOPENGL` try-import and base class selection at top of `video_viewport.py`**

  Replace the existing imports block at the top of `video_viewport.py`. Add **before** the class definition:

  ```python
  # GL availability probe
  try:
      from PyQt6.QtOpenGLWidgets import QOpenGLWidget as _QOpenGLWidget
      from PyQt6.QtGui import QOpenGLContext
      from OpenGL import GL as _GL
      _HAS_QOPENGL = True
  except ImportError:
      _QOpenGLWidget = None
      _HAS_QOPENGL = False

  _VideoViewportBase = _QOpenGLWidget if _HAS_QOPENGL else QWidget
  ```

  Change the class definition line from:

  ```python
  class VideoViewportWidget(QOpenGLWidget):
  ```

  To:

  ```python
  class VideoViewportWidget(_VideoViewportBase):
  ```

- [ ] **Step 4: Add `_gl_decoder`, `attach_gl_decoder`, and update `__init__`**

  In `VideoViewportWidget.__init__`, add after `self.stretch_to_fill = True`:

  ```python
  self._gl_decoder = None       # Set via attach_gl_decoder() after SHM engine init
  self._shader_program = None   # Compiled GLSL program (lazy, first paintGL)
  self._quad_vbo = None         # Fullscreen quad vertex buffer
  ```

  Add new method after `__init__`:

  ```python
  def attach_gl_decoder(self, decoder) -> None:
      """Wire a GlImageSinkDecoder to this viewport. Called from main.py after SHM engine init."""
      self._gl_decoder = decoder
  ```

- [ ] **Step 5: Override `update_frame` to be no-op when GL active**

  Replace existing `update_frame` method:

  ```python
  def update_frame(self, frame_bytes: bytes, width: int, height: int):
      """Update active frame. No-op when GL decoder is active (GStreamer renders directly)."""
      if self._gl_decoder and self._gl_decoder.is_available:
          return  # GL path: glimagesink renders; no bytes needed
      if not frame_bytes or width <= 0 or height <= 0:
          return
      self.current_frame_data = frame_bytes
      self.frame_width = width
      self.frame_height = height
      self.update()  # Triggers paintEvent (RGBA fallback path)
  ```

- [ ] **Step 6: Add GL methods — `initializeGL`, `paintGL`, `resizeGL` — guarded by `_HAS_QOPENGL`**

  Add the following block after `update_frame`. These methods only activate when the base class is `QOpenGLWidget`:

  ```python
  if _HAS_QOPENGL:
      # Inline GLSL shaders for fullscreen textured quad
      _VERT_SRC = """
  attribute vec2 a_pos;
  varying vec2 v_uv;
  void main() {
      gl_Position = vec4(a_pos, 0.0, 1.0);
      v_uv = (a_pos + 1.0) * 0.5;
      v_uv.y = 1.0 - v_uv.y;
  }
  """
      _FRAG_SRC = """
  uniform sampler2D u_tex;
  varying vec2 v_uv;
  void main() { gl_FragColor = texture2D(u_tex, v_uv); }
  """

      def initializeGL(self):
          """Share Qt EGL context with GlImageSinkDecoder and compile shader."""
          from OpenGL import GL
          GL.glClearColor(0.05, 0.067, 0.09, 1.0)

          if self._gl_decoder and self._gl_decoder.is_available:
              try:
                  ctx = QOpenGLContext.currentContext()
                  native = ctx.nativeInterface()  # QNativeInterface.QEGLContext on EGL
                  if native and hasattr(native, "nativeContext") and hasattr(native, "display"):
                      egl_ctx = int(native.nativeContext())
                      egl_dpy = int(native.display())
                      self._gl_decoder.set_gl_context(egl_dpy, egl_ctx)
                      logger.debug("[VideoViewport] EGL context shared with GlImageSinkDecoder")
                  else:
                      logger.warning("[VideoViewport] Cannot obtain EGL handles — GL decoder disabled")
                      self._gl_decoder = None
              except Exception as exc:
                  logger.warning(f"[VideoViewport] initializeGL context share failed: {exc}")
                  self._gl_decoder = None

      def _compile_shader(self):
          """Compile and link the fullscreen quad shader program. Called lazily on first paintGL."""
          from OpenGL import GL
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

          # Fullscreen quad: two triangles covering NDC [-1, 1]
          import numpy as np
          verts = np.array([-1,-1, 1,-1, -1,1, 1,1], dtype=np.float32)
          vbo = GL.glGenBuffers(1)
          GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
          GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
          GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

          self._shader_program = prog
          self._quad_vbo = vbo

      def paintGL(self):
          """Render frame via GL texture (zero-CPU) or fall back to QPainter."""
          from OpenGL import GL

          tex_id = 0
          if self._gl_decoder and self._gl_decoder.is_available:
              tex_id = self._gl_decoder.get_latest_texture_id()

          if tex_id:
              if self._shader_program is None:
                  self._compile_shader()

              GL.glClear(GL.GL_COLOR_BUFFER_BIT)
              GL.glUseProgram(self._shader_program)

              GL.glActiveTexture(GL.GL_TEXTURE0)
              GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
              loc_tex = GL.glGetUniformLocation(self._shader_program, "u_tex")
              GL.glUniform1i(loc_tex, 0)

              GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._quad_vbo)
              loc_pos = GL.glGetAttribLocation(self._shader_program, "a_pos")
              GL.glEnableVertexAttribArray(loc_pos)
              GL.glVertexAttribPointer(loc_pos, 2, GL.GL_FLOAT, False, 0, None)

              GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

              GL.glDisableVertexAttribArray(loc_pos)
              GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
              GL.glUseProgram(0)
          else:
              # Fallback: QPainter path (RGBA bytes from GStreamerHwDecoder/PyAV)
              GL.glClear(GL.GL_COLOR_BUFFER_BIT)
              if (
                  self.current_frame_data
                  and 0 < self.frame_width <= 4096
                  and 0 < self.frame_height <= 4096
                  and len(self.current_frame_data) == self.frame_width * self.frame_height * 4
              ):
                  from PyQt6.QtGui import QPainter, QImage
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

  # Inject GL methods into class only when GL is available
  # (methods defined inside `if _HAS_QOPENGL` block above are not class methods by default)
  # Use direct assignment to attach them to the class:
  if _HAS_QOPENGL:
      VideoViewportWidget.initializeGL = VideoViewportWidget.initializeGL  # already bound
      VideoViewportWidget.paintGL = VideoViewportWidget.paintGL
      VideoViewportWidget.resizeGL = VideoViewportWidget.resizeGL
      VideoViewportWidget._compile_shader = VideoViewportWidget._compile_shader
  ```

  > **Note:** The `if _HAS_QOPENGL:` block with method definitions inside a class body is not valid Python. Use a mixin pattern instead — define an `_GLMixin` class with the GL methods, then use it in `_VideoViewportBase`:

  **Correct pattern to use instead of the above:**

  ```python
  # At module level, after try-import block:

  class _GLMixin:
      """GL render methods — mixed into VideoViewportWidget only when QOpenGLWidget is available."""

      _VERT_SRC = """
  attribute vec2 a_pos; varying vec2 v_uv;
  void main(){ gl_Position=vec4(a_pos,0.,1.); v_uv=(a_pos+1.)*.5; v_uv.y=1.-v_uv.y; }
  """
      _FRAG_SRC = """
  uniform sampler2D u_tex; varying vec2 v_uv;
  void main(){ gl_FragColor=texture2D(u_tex,v_uv); }
  """

      def initializeGL(self):
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
                  logger.debug("[VideoViewport] EGL context shared")
              else:
                  logger.warning("[VideoViewport] No EGL native interface — GL decoder disabled")
                  self._gl_decoder = None
          except Exception as exc:
              logger.warning(f"[VideoViewport] initializeGL failed: {exc}")
              self._gl_decoder = None

      def _compile_shader(self):
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
          verts = np.array([-1,-1, 1,-1, -1,1, 1,1], dtype=np.float32)
          vbo = GL.glGenBuffers(1)
          GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
          GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
          GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
          self._shader_program = prog
          self._quad_vbo = vbo

      def paintGL(self):
          from OpenGL import GL
          tex_id = self._gl_decoder.get_latest_texture_id() if (self._gl_decoder and self._gl_decoder.is_available) else 0
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
          elif self.current_frame_data and 0 < self.frame_width <= 4096 and 0 < self.frame_height <= 4096:
              from PyQt6.QtGui import QPainter, QImage
              p = QPainter(self)
              img = QImage(self.current_frame_data, self.frame_width, self.frame_height,
                           self.frame_width * 4, QImage.Format.Format_RGBA8888)
              p.drawImage(self.rect(), img)
              p.end()

      def resizeGL(self, w, h):
          from OpenGL import GL
          GL.glViewport(0, 0, w, h)


  # Class definition uses mixin when GL is available, plain QWidget otherwise
  if _HAS_QOPENGL:
      class VideoViewportWidget(_GLMixin, _QOpenGLWidget):
          ...
  else:
      class VideoViewportWidget(QWidget):
          ...
  ```

  Implement `VideoViewportWidget` body once using this pattern. The existing `paintEvent` stays in the non-GL `QWidget` branch; `paintGL` is in `_GLMixin`.

- [ ] **Step 7: Run tests**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_video_viewport_gl.py -v
  ```

  Expected: all 4 tests PASS.

- [ ] **Step 8: Smoke test backend**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py &
  sleep 5 && kill %1
  ```

  Expected: clean boot.

- [ ] **Step 9: Commit**

  ```bash
  git add backend/modules/qt6_gui/ui/video_viewport.py tests/test_video_viewport_gl.py
  git commit -m "feat(qt6_gui): paintGL shader quad + QOpenGLWidget failsafe via _GLMixin"
  ```

---

### Task 3: Wire `attach_gl_decoder` in `main.py` + integration verification

**Files:**
- Modify: `backend/modules/qt6_gui/main.py`

**Interfaces:**
- Consumes: `VideoViewportWidget.attach_gl_decoder(decoder)` (Task 2), `GlImageSinkDecoder` (Task 1)
- Produces: working zero-CPU video path on target; `intel_gpu_top` shows Render/3D < 10%

- [ ] **Step 1: Wire `attach_gl_decoder` after SHM engine init in `main.py`**

  In `Qt6GuiModule.setup()`, after:

  ```python
  self.shm_engine.connect_shm()
  ```

  Add:

  ```python
  # Wire GL decoder to viewport if active
  if (
      self.main_window
      and hasattr(self.shm_engine, "_hw_decoder")
      and hasattr(self.main_window, "video_viewport")
      and hasattr(self.main_window.video_viewport, "attach_gl_decoder")
  ):
      self.main_window.video_viewport.attach_gl_decoder(self.shm_engine._hw_decoder)
      self.log.info(
          f"[Qt6Gui] GL decoder wired to viewport: "
          f"{type(self.shm_engine._hw_decoder).__name__} "
          f"(available={self.shm_engine._hw_decoder.is_available})"
      )
  ```

- [ ] **Step 2: Verify `PyOpenGL` is installed in env**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python -c "from OpenGL import GL; print('PyOpenGL OK')"
  ```

  If missing:
  ```bash
  micromamba install -n NemoHeadUnit-Wireless pyopengl -c conda-forge -y
  ```
  Then add `pyopengl` to `environment.yml` and `web-browser-head-unit/environment.yml`.

- [ ] **Step 3: Full backend smoke test**

  ```bash
  micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py &
  sleep 8 && kill %1
  ```

  Expected log lines (in order):
  1. `GlImageSinkDecoder Pipeline built — awaiting GL context`  OR `GStreamerHwDecoder` active (if GL unavailable on dev machine — normal)
  2. `GL decoder wired to viewport`
  3. No `AttributeError`, no `ImportError`, no pipeline `FAILURE`

- [ ] **Step 4: Deploy and verify on Bay Trail target**

  On target (`192.168.1.105`):

  ```bash
  # Restart the service / run manually
  micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py &

  # In a second terminal, watch GPU usage
  intel_gpu_top
  ```

  Connect Android Auto. Expected:
  - `Render/3D`: drops from ~99% → < 10%
  - `Video`: rises from ~4% to 20-40% (VPU actively decoding)
  - `[GlImageSinkDecoder] GL context shared — pipeline PLAYING` in log

- [ ] **Step 5: Test headless fallback**

  On dev machine (no VAAPI, no physical display):

  ```bash
  QT_QPA_PLATFORM=offscreen micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py &
  sleep 5 && kill %1
  ```

  Expected: `GStreamerHwDecoder` active log line, no crash, clean shutdown.

- [ ] **Step 6: Commit**

  ```bash
  git add backend/modules/qt6_gui/main.py
  git commit -m "feat(qt6_gui): wire attach_gl_decoder in setup() — zero-CPU GL video path end-to-end"
  ```

- [ ] **Step 7: Update README**

  In `web-browser-head-unit/README.md`, under the `qt6_gui` section, add:

  ```markdown
  **Video Rendering:** On Linux with VAAPI (Intel Bay Trail / Braswell), the `qt6_gui` module
  uses a zero-CPU GStreamer `glimagesink` path with shared EGL context (`GlImageSinkDecoder`).
  Falls back automatically to `GStreamerHwDecoder` (RGBA bytes) when OpenGL or VAAPI is unavailable.
  Required GStreamer elements: `vah264dec`, `vapostproc`, `glupload`, `glimagesink`.
  ```

  ```bash
  git add web-browser-head-unit/README.md
  git commit -m "docs(qt6_gui): document zero-CPU GL video path and fallback chain"
  ```
