#!/usr/bin/env python3
"""
test_qt_opengl_zero_copy.py — PyQt6 QOpenGLWidget + GStreamer Zero-Copy HW Decode Test.

Tests:
1. Sharing Qt EGL context with GStreamer.
2. Hardware H.264 decode (vah264dec + vapostproc + glupload + glcolorconvert).
3. Extracting real GL texture ID via ctypes PyGObject pointer resolution.
4. Rendering texture directly in QOpenGLWidget.paintGL() via fullscreen shader quad.
5. Capturing screen with grim to prove 0-copy rendering in Qt6.
"""

import sys
import os
import ctypes
import subprocess
import time

from PyQt6.QtWidgets import QApplication
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore import QTimer, Qt
from OpenGL import GL
import numpy as np

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstGL", "1.0")
gi.require_version("GstGLEGL", "1.0")
from gi.repository import Gst, GstGL, GstGLEGL

Gst.init(None)

# PyGObject C struct layout to extract raw C pointer from Python wrapper
class PyGObject(ctypes.Structure):
    _fields_ = [
        ("ob_refcnt", ctypes.c_ssize_t),
        ("ob_type", ctypes.c_void_p),
        ("pointer", ctypes.c_void_p),
    ]

# libgstgl function binding
_libgstgl = ctypes.CDLL("libgstgl-1.0.so.0")
_libgstgl.gst_gl_memory_get_texture_id.restype = ctypes.c_uint
_libgstgl.gst_gl_memory_get_texture_id.argtypes = [ctypes.c_void_p]


def get_gl_texture_id(mem) -> int:
    """Extract OpenGL texture ID from a Gst.Memory object via its underlying GstGLMemory pointer."""
    try:
        py_obj = PyGObject.from_address(id(mem))
        c_ptr = py_obj.pointer
        if c_ptr:
            return int(_libgstgl.gst_gl_memory_get_texture_id(ctypes.c_void_p(c_ptr)))
    except Exception as e:
        print("[ERR] get_gl_texture_id exception:", e)
    return 0


class TestGLVideoWidget(QOpenGLWidget):
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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Qt6 QOpenGLWidget Zero-Copy Test")
        self.resize(1280, 720)
        self._shader_program = None
        self._quad_vbo = None
        self._current_tex_id = 0
        self._current_sample = None
        self._frames_rendered = 0
        self._pipeline = None

    def initializeGL(self):
        GL.glClearColor(0.1, 0.1, 0.1, 1.0)
        self._compile_shader()

        # Share Qt's current EGL context with GStreamer
        libegl = ctypes.CDLL("libEGL.so.1")
        libegl.eglGetCurrentDisplay.restype = ctypes.c_void_p
        libegl.eglGetCurrentContext.restype = ctypes.c_void_p
        egl_dpy = libegl.eglGetCurrentDisplay()
        egl_ctx = libegl.eglGetCurrentContext()
        print(f"[*] Qt EGL Display: {hex(egl_dpy or 0)}, Context: {hex(egl_ctx or 0)}", flush=True)

        if not egl_dpy or not egl_ctx:
            print("[ERR] Failed to get current EGL display or context!", flush=True)
            return

        self._init_gstreamer_pipeline(egl_dpy, egl_ctx)

    def _compile_shader(self):
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

    def _init_gstreamer_pipeline(self, egl_dpy: int, egl_ctx: int):
        pipe_str = (
            "videotestsrc pattern=smpte is-live=true "
            "! video/x-raw,width=1280,height=720,framerate=30/1 "
            "! x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=30 "
            "! h264parse config-interval=1 "
            "! vah264dec "
            "! vapostproc "
            "! glupload "
            "! glcolorconvert "
            "! appsink name=sink emit-signals=true max-buffers=1 drop=true caps=video/x-raw(memory:GLMemory),format=RGBA"
        )
        print(f"[*] Building GStreamer GL Pipeline:\n    {pipe_str}", flush=True)
        self._pipeline = Gst.parse_launch(pipe_str)

        # Wrap Qt EGL context into GStreamer GLContext
        gl_display = GstGLEGL.GLDisplayEGL.new_with_egl_display(egl_dpy)
        gl_context = GstGL.GLContext.new_wrapped(
            gl_display,
            egl_ctx,
            GstGL.GLPlatform.EGL,
            GstGL.GLAPI.GLES2 | GstGL.GLAPI.OPENGL,
        )
        gl_context.activate(True)

        gst_ctx_display = Gst.Context.new(GstGL.GL_DISPLAY_CONTEXT_TYPE, True)
        GstGL.context_set_gl_display(gst_ctx_display, gl_display)
        self._pipeline.set_context(gst_ctx_display)

        gst_ctx_app = Gst.Context.new("gst.gl.app_context", True)
        gst_ctx_app.get_structure().set_value("context", gl_context)
        self._pipeline.set_context(gst_ctx_app)

        sink = self._pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_new_sample)

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        print(f"[*] GStreamer pipeline state set to PLAYING: {ret}", flush=True)

    def _on_new_sample(self, sink):
        try:
            sample = sink.emit("pull-sample")
            if sample:
                buf = sample.get_buffer()
                if buf:
                    mem = buf.peek_memory(0)
                    if mem and GstGL.is_gl_memory(mem):
                        tex_id = get_gl_texture_id(mem)
                        if tex_id > 0:
                            self._current_tex_id = tex_id
                            self._current_sample = sample
                            # Request paintGL redraw on Qt main thread
                            QTimer.singleShot(0, self.update)
        except Exception as e:
            print("[ERR] _on_new_sample:", e, flush=True)
        return Gst.FlowReturn.OK

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        tex_id = self._current_tex_id
        if tex_id > 0 and self._shader_program:
            is_tex = GL.glIsTexture(tex_id)
            GL.glUseProgram(self._shader_program)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glUniform1i(GL.glGetUniformLocation(self._shader_program, "u_tex"), 0)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._quad_vbo)
            loc = GL.glGetAttribLocation(self._shader_program, "a_pos")
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, 2, GL.GL_FLOAT, False, 0, None)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
            GL.glDisableVertexAttribArray(loc)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glUseProgram(0)

            err = GL.glGetError()
            self._frames_rendered += 1
            if self._frames_rendered == 1 or self._frames_rendered % 30 == 0:
                print(f"🎨 [paintGL] Frame #{self._frames_rendered} | Tex ID: {tex_id} | glIsTexture: {is_tex} | GL Error: {err}", flush=True)

    def resizeGL(self, w: int, h: int):
        GL.glViewport(0, 0, w, h)

    def closeEvent(self, event):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        super().closeEvent(event)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Qt6 OpenGL Zero-Copy Test")
    parser.add_argument("--duration", type=int, default=20, help="Duration in seconds (default: 20)")
    parser.add_argument("--fullscreen", action="store_true", help="Show fullscreen")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    widget = TestGLVideoWidget()
    if args.fullscreen:
        widget.showFullScreen()
    else:
        widget.show()

    # Capture screenshot after 3 seconds, then quit after duration
    def capture_and_check():
        subprocess.run(["grim", "/tmp/qt_opengl_screen.png"], check=False)
        if os.path.exists("/tmp/qt_opengl_screen.png"):
            sz = os.path.getsize("/tmp/qt_opengl_screen.png")
            print(f"📸 Framebuffer captured to /tmp/qt_opengl_screen.png ({sz} bytes)", flush=True)

    QTimer.singleShot(3000, capture_and_check)
    if args.duration > 0:
        QTimer.singleShot(args.duration * 1000, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
