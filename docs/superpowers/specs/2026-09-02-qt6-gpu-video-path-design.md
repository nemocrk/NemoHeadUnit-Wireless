# Qt6 GUI — Zero-CPU GPU Video Rendering Path

**Date:** 2026-09-02  
**Target:** Intel Bay Trail (ValleyView Gen7), Linux/Wayland, Qt6 + GStreamer 1.0  
**Status:** Approved for implementation

---

## Problem

Current render path in `qt6_gui`:

```
vah264dec (GPU VPU)
  → bytes(map_info.data)            ← CPU readback #1: VA surface → system RAM
  → QImage(rgba_bytes, ...)         ← CPU wrapper
  → QPainter.drawImage(rect, img)   ← Qt RHI re-upload #2: system RAM → GPU texture
```

Two GPU↔CPU memory crossings per frame at 30 fps on a bandwidth-constrained Bay Trail GPU
→ Render/3D engine at **99.42%**, Video engine at 4% (VPU underused).

## Goal

Replace the two-crossing path with a single GL-context-shared path:

```
vah264dec (GPU VPU)
  → vapostproc                  ← stays on VA/DMA-BUF surface
  → glimagesink                 ← imports DMA-BUF as EGL image, renders into shared GL context
```

Zero CPU copies. Render/3D drops to near 0%. Video engine utilization rises (correct).

---

## Architecture

### GL Context Sharing

`glimagesink` accepts an external `GstGLContext` wrapping an existing EGL/GLX context.
`QOpenGLWidget` exposes its OpenGL context via `context()` and `makeCurrent()`.

Flow:
1. `VideoViewportWidget` (subclass of `QOpenGLWidget`) calls `initializeGL()` → creates a `GstGLContext` wrapping Qt's EGL context handle.
2. `GlImageSinkDecoder` receives this context → passes it to the GStreamer pipeline as `GstGLDisplay` + `GstGLContext` via `gst_element_set_context()` before PLAYING.
3. `glimagesink` renders into the shared context. `paintGL()` pulls the latest `GstSample` and renders the GL texture via a minimal shader quad.

### Pipeline

```
appsrc name=src is-live=true format=bytes
  ! h264parse config-interval=-1
  ! vah264dec
  ! vapostproc
  ! video/x-raw(memory:DMABuf),format=RGBA
  ! glupload
  ! glimagesink name=sink sync=false qos=false
```

`vapostproc` handles VA-surface → DMA-BUF color conversion on the GPU.  
`glupload` imports DMA-BUF as EGL image into the shared GL context.  
`glimagesink` exposes the GL texture for `paintGL()` to sample.

### Fallback Chain

```
QOpenGLWidget available? (try-import at module load)
  YES → GlImageSinkDecoder (GPU path, zero CPU)
          Pipeline fails → GStreamerHwDecoder (RGBA bytes, single copy)
  NO  → GStreamerHwDecoder (existing RGBA bytes path — works headless)
          GStreamer unavailable → PyAV CPU decoder
          PyAV unavailable → black frame
```

`_HAS_QOPENGL` flag set once at module load:
```python
try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget as _OGLWidget
    _HAS_QOPENGL = True
except ImportError:
    _HAS_QOPENGL = False
```

---

## Files Changed

### `shm_media_engine.py`

**Add** `GlImageSinkDecoder` class:
- Same public interface as `GStreamerHwDecoder`: `decode_nal(nal_data, ts_us)`, `close()`, `is_available: bool`
- `set_gl_context(egl_display, egl_context)` — called once from `VideoViewportWidget.initializeGL()`
- `get_latest_texture_id() -> int` — returns current GL texture ID for `paintGL()`
- `on_frame_callback` only used by fallback path (GL path renders without Python callback)

**Modify** `QtSHMMediaEngine.__init__`:
- `_HAS_QOPENGL` → `GlImageSinkDecoder`, else → `GStreamerHwDecoder` (unchanged)

**No other changes to `QtSHMMediaEngine`.**

### `video_viewport.py`

**Try-import** `QOpenGLWidget` at top.

**If GL available:** `VideoViewportWidget(QOpenGLWidget)` gains:
- `initializeGL()`: grab Qt EGL context handles, call `shm_engine.hw_decoder.set_gl_context(...)`
- `paintGL()`: if `GlImageSinkDecoder` active → pull texture → render fullscreen quad; else → existing QPainter path  
- `resizeGL(w, h)`: `glViewport(0, 0, w, h)`

**If GL not available:** `VideoViewportWidget(QWidget)` — existing `paintEvent` + `QPainter` unchanged.

Touch/mouse code: **zero changes** — `_map_coords`, `touchEvent`, `mousePressEvent`, `mouseMoveEvent`, `mouseReleaseEvent` all preserved verbatim.

### `main.py`

**No changes required.** `_on_video_frame_from_shm` → `video_viewport.update_frame(rgba_bytes, w, h)` — when GL path is active this is a no-op; when fallback is active it follows existing path.

---

## Shader (minimal, inline in `video_viewport.py`)

```glsl
// vertex
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
    gl_Position = vec4(a_pos, 0.0, 1.0);
    v_uv = (a_pos + 1.0) * 0.5;
    v_uv.y = 1.0 - v_uv.y;  // flip Y for GL convention
}

// fragment
uniform sampler2D u_tex;
varying vec2 v_uv;
void main() { gl_FragColor = texture2D(u_tex, v_uv); }
```

Inlined as strings, no external shader files.

> ponytail: RGBA passthrough shader — upgrade to BT.601 YUV→RGB if color accuracy becomes a requirement.

---

## Fallback Behavior Matrix

| Condition | Active decoder | Render path | CPU cost |
|---|---|---|---|
| GL + `vah264dec` + `vapostproc` + `glimagesink` all available | `GlImageSinkDecoder` | `paintGL()` shader quad | ~0% |
| GL available but GStreamer pipeline fails | `GStreamerHwDecoder` | `paintGL()` → QPainter | medium |
| GL not importable (headless, no OpenGL) | `GStreamerHwDecoder` | `paintEvent()` → QPainter | medium |
| No GStreamer at all | `PyAV` CPU | `paintEvent()` → QPainter | high |
| Nothing available | black frame | `paintEvent()` fill | 0% |

---

## Cross-Platform Notes

| OS / Display | Decoder | GL path | Notes |
|---|---|---|---|
| Linux/Wayland + Bay Trail | `vah264dec` | EGL shared context | Confirmed: `vah264dec`, `vapostproc`, `glimagesink`, `glupload` present |
| Linux/X11 | `vah264dec` or `avdec_h264` | GLX shared context | Same pipeline, `glimagesink` selects GLX automatically |
| Windows | `d3d11h264dec` | ANGLE/EGL or D3D11 | Separate branch in `GlImageSinkDecoder.__init__`; falls back to RGBA bytes if D3D11 fails |

---

## Verification Plan

### Automated
```bash
micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_media_server_diagnostic.py -x
```

### Manual on Target
1. `micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py`
2. Boot log: confirm `GlImageSinkDecoder` active, no pipeline parse error
3. Connect Android Auto phone → confirm video plays without artifacts
4. Run `intel_gpu_top` on target: Render/3D < 10%, Video > 0%
5. Headless fallback test: `QT_QPA_PLATFORM=offscreen python ...` → confirm `GStreamerHwDecoder` activates, no crash
