"""
NemoHeadUnit-Wireless v2 — video_ui module

Standalone PyQt6 window that decodes and displays the Android Auto video stream.
At target integration this QWidget subclass will be embedded into the main window.

Module contract:
  Name        : video_ui
  Priority    : 2  (UI level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                video.frame             {channel_id, ts_us, data_b64, codec, is_config}
                video.state             {state}  IDLE | SETUP | OPEN | PLAYING | STOPPED
                aa.session.active       {}
                aa.session.shutdown     {}
                bluetooth_manager.pairing.completed  {device_address}
  Publishes   : system.module_ready     {name, priority}
                system.ready            {name, priority}
                video.ui.winid          {winid: int}  — for future touch_input module

Decoder strategy (runtime probe, no config required):
  1. vaapih264dec  — VA-API HW decode via i965 driver (Intel Bay Trail)
                     Caricato a runtime da plugin di sistema via scan_path
                     se non disponibile nell'env conda.
                     NB: usiamo vaapih264dec, NON vaapidecodebin.
                     vaapidecodebin usa DMA-buf e triggera un assert nel
                     driver i965 su Bay Trail (i965_check_alloc_surface_bo).
                     vaapih264dec usa superficie YUV420 classica, stabile.
  2. vah264dec     — VA-API HW decode via iHD driver (Broadwell+)
                     Non disponibile su Bay Trail (iHD init failed).
  3. openh264dec   — Cisco openh264 SW decode (gst-plugins-bad + openh264 conda)
                     Più leggero di avdec_h264 su hardware x86 datato.
  4. avdec_h264    — FFmpeg SW decode (gst-libav / conda) — fallback finale
  Se GStreamer non è disponibile, la finestra mostra solo il placeholder.

Rendering:
  Primary  : appsink caps=NV12 → QOpenGLWidget with GLSL Y+UV shader (zero CPU copy)
  Fallback : videoconvert → appsink caps=RGB → QLabel with QImage

Placeholder (no active stream):
  - Digital clock HH:MM:SS, updated every second via QTimer
  - Connection state indicator: coloured dot (●) + text label
    ● red    — In attesa di connessione BT
    ● yellow — Handshake AA in corso
    ● green  — Stream attivo
    ● red    — Stream interrotto  (after STOPPED/IDLE post-session)

State machine (internal _conn_state):
  WAITING_BT   → bluetooth_manager.pairing.completed → HANDSHAKE
  HANDSHAKE    → aa.session.active           → HANDSHAKE  (already set)
  HANDSHAKE    → video.state=PLAYING         → STREAMING
  STREAMING    → video.state=IDLE/STOPPED    → INTERRUPTED
  INTERRUPTED  → aa.session.shutdown         → WAITING_BT
  any          → aa.session.shutdown         → WAITING_BT

Fix notes (2026-05-10 rev2):
  - push_frame / push_nv12 / push_rgb decorated with @pyqtSlot so that
    QMetaObject.invokeMethod actually dispatches them (PyQt6 silently drops
    invocations on plain methods that are not registered Qt slots).
  - set_streaming(True) is now triggered by the first decoded sample arriving
    from GStreamer (_on_new_sample) rather than on video.state=PLAYING.
  - Diagnostic log lines added in push_frame and _on_new_sample.

Fix notes (2026-05-10 rev3):
  - Rimosso vaapidecodebin dalla probe: su Bay Trail con driver i965 triggera
    un assertion fault in i965_check_alloc_surface_bo (DMA-buf format mismatch).
  - Nuovo ordine probe: vaapih264dec (i965 HW) → vah264dec (iHD HW) → avdec_h264 (SW).
  - vaapih264dec non usa DMA-buf, quindi è stabile su i965.

Fix notes (2026-05-10 rev4 — anti-artefatti):
  Obiettivo: nessun artefatto anche in scene con movimento veloce.
  Preferenza esplicita: meglio saltare frame (scena accelerata) che mostrare
  macrobloc corrotti.

  1. Queue leaky=downstream PRIMA del decoder (sui dati H264 compressi).
  2. appsink drop=false, max-buffers=4.
  3. PTS monotono su ogni Gst.Buffer in push_frame.

Fix notes (2026-05-12 rev5 — decoder probe estesa + artefatti residui):
  Obiettivo: decoder HW portabile senza dipendenze conda, artefatti zero
  anche dopo drop della queue leaky.

  1. _try_load_system_vaapi(): carica vaapih264dec dai plugin GStreamer di
     sistema (/usr/lib/*/gstreamer-1.0) via Gst.Registry.scan_path() se
     non disponibile nell'env conda. Nessuna modifica a variabili d'ambiente.
     Su macchine senza VA-API fallisce silenziosamente.

  2. openh264dec aggiunto in _DECODER_CANDIDATES (posizione 3, prima di
     avdec_h264). Più leggero su hardware x86 datato come Bay Trail.

  3. h264parse config-interval=-1: il parser reinserisce automaticamente
     SPS/PPS prima di ogni IDR frame. Dopo qualsiasi drop della queue leaky
     il decoder riceve un IDR completo e si risincronizza immediatamente
     senza produrre macrobloc. Questa è la fix principale degli artefatti
     residui.

  4. Log dettagliato al boot: decoder scelto + path VA-API se caricato
     da sistema, per diagnostica in deploy.
"""

from __future__ import annotations

import base64
import sys
import threading
from datetime import datetime
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

for _p in (_V2, _MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PyQt6.QtCore import (                                              # noqa: E402
    Qt, QTimer, QMetaObject, Q_ARG, pyqtSlot, QSize,
)
from PyQt6.QtGui import QFont, QImage, QPixmap                         # noqa: E402
from PyQt6.QtOpenGLWidgets import QOpenGLWidget                        # noqa: E402
from PyQt6.QtWidgets import (                                           # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLabel, QSizePolicy, QStackedWidget,
)

from shared.bus_client import BusClient   # noqa: E402
from shared.logger import get_logger      # noqa: E402

# ---------------------------------------------------------------------------
# Optional GStreamer import — graceful degradation if not available
# ---------------------------------------------------------------------------
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib  # type: ignore
    Gst.init(None)
    _GST_AVAILABLE = True
except Exception:
    _GST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "video_ui"
PRIORITY    = 2

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# ---------------------------------------------------------------------------
# Connection state constants
# ---------------------------------------------------------------------------

_STATE_WAITING_BT   = "WAITING_BT"
_STATE_HANDSHAKE    = "HANDSHAKE"
_STATE_STREAMING    = "STREAMING"
_STATE_INTERRUPTED  = "INTERRUPTED"

_STATE_LABELS = {
    _STATE_WAITING_BT:  ("#e05252", "●  In attesa di connessione BT"),
    _STATE_HANDSHAKE:   ("#e0b84a", "●  Handshake AA in corso"),
    _STATE_STREAMING:   ("#4caf50", "●  Stream attivo"),
    _STATE_INTERRUPTED: ("#e05252", "●  Stream interrotto"),
}

# ---------------------------------------------------------------------------
# GStreamer pipeline builder
# ---------------------------------------------------------------------------

# Candidati decoder in ordine di preferenza.
# Ogni entry: (element_name, label, needs_h264parse)
#
# vaapidecodebin ESCLUSO: usa DMA-buf internamente e triggera un assertion
# fault nel driver i965 su Bay Trail (i965_check_alloc_surface_bo).
# vaapih264dec usa superficie YUV classica — stabile su i965.
#
# openh264dec: Cisco openh264, più leggero di avdec_h264 su x86 datato.
# Disponibile tramite conda-forge: openh264 + gst-plugins-bad.
_DECODER_CANDIDATES = [
    ("vaapih264dec", "VA-API HW i965 (vaapih264dec)",  True),
    ("vah264dec",    "VA-API HW iHD (vah264dec)",      True),
    ("openh264dec",  "Cisco openh264 SW (openh264dec)", True),
    ("avdec_h264",   "FFmpeg SW (avdec_h264)",          True),
]

# Dimensione della queue leaky prima del decoder.
# 8 buffer @ 30fps = ~267ms di margine per assorbire jitter ZMQ.
# In caso di backpressure si butta il pacchetto compresso più vecchio
# (leaky=downstream) prima che entri nel decoder.
# config-interval=-1 su h264parse garantisce che dopo ogni drop la
# ripresa avvenga sempre su un IDR completo — nessun artefatto.
_LEAKY_QUEUE_BUFFERS = 8

# Framerate nominale assunto per il calcolo del PTS (Android Auto è sempre 30fps).
_FRAME_DURATION_NS = 1_000_000_000 // 30   # 33_333_333 ns

# Path standard dove cercare i plugin GStreamer di sistema per la probe VA-API.
# L'ordine riflette le architetture più comuni su Linux desktop/embedded.
_SYSTEM_GST_PLUGIN_PATHS = [
    "/usr/lib/x86_64-linux-gnu/gstreamer-1.0",
    "/usr/lib/aarch64-linux-gnu/gstreamer-1.0",
    "/usr/lib/arm-linux-gnueabihf/gstreamer-1.0",
    "/usr/lib/i386-linux-gnu/gstreamer-1.0",
]


def _try_load_system_vaapi() -> str | None:
    """
    Prova a caricare vaapih264dec dai plugin GStreamer di sistema via
    Gst.Registry.scan_path(), senza modificare variabili d'ambiente globali.

    Ritorna il path di sistema usato se vaapih264dec è ora disponibile,
    None altrimenti (fallisce silenziosamente — la probe continua con
    i candidati SW successivi).

    Chiamata una sola volta all'avvio di _build_pipeline, prima della
    selezione del decoder.
    """
    if not _GST_AVAILABLE:
        return None

    # Già disponibile nell'env conda — nessuna azione necessaria.
    if Gst.ElementFactory.find("vaapih264dec"):
        return "(conda env)"

    registry = Gst.Registry.get()
    for path in _SYSTEM_GST_PLUGIN_PATHS:
        if not Path(path).exists():
            continue
        registry.scan_path(path)
        if Gst.ElementFactory.find("vaapih264dec"):
            return path

    return None


def _build_pipeline(use_gl: bool) -> "tuple[Gst.Pipeline | None, str]":
    """
    Probe available decoders and build the best pipeline.
    Returns (pipeline, render_format) where render_format is 'NV12' or 'RGB'.
    Returns (None, '') if GStreamer is unavailable or no decoder found.

    Pipeline topology:
      appsrc → h264parse(config-interval=-1) → queue(leaky=downstream)
               → decoder → videoconvert → appsink(drop=false)

    config-interval=-1: h264parse reinserisce SPS/PPS prima di ogni IDR.
    Dopo qualsiasi drop della queue leaky il decoder riceve sempre un IDR
    completo e si risincronizza senza produrre macrobloc.

    La queue leaky scarta compressi in eccesso PRIMA del decoder.
    L'appsink non scarta mai frame già decodificati.
    """
    if not _GST_AVAILABLE:
        return None, ""

    fmt = "NV12" if use_gl else "RGB"

    # Tenta di caricare vaapih264dec dai plugin di sistema se non
    # disponibile nell'env conda.
    vaapi_system_path = _try_load_system_vaapi()
    if vaapi_system_path:
        log.info(
            "VA-API probe: vaapih264dec disponibile (path: %s)", vaapi_system_path
        )
    else:
        log.info("VA-API probe: vaapih264dec non disponibile — uso SW decoder")

    # Log tutti i candidati per diagnostica
    for name, label, _ in _DECODER_CANDIDATES:
        found = Gst.ElementFactory.find(name) is not None
        log.debug("decoder probe: %-20s %s", name, "OK" if found else "not found")

    # Seleziona il primo disponibile
    chosen_name  = None
    chosen_label = None
    for name, label, _ in _DECODER_CANDIDATES:
        if Gst.ElementFactory.find(name):
            chosen_name  = name
            chosen_label = label
            break

    if chosen_name is None:
        log.warning(
            "Nessun decoder H264 trovato — "
            "installa gst-libav o gst-plugins-bad nell'env conda."
        )
        return None, ""

    # Log di avvio: decoder scelto + tipo (HW/SW) + path VA-API se applicabile
    decoder_type = "HW VA-API" if "vaapi" in chosen_name or "vah264" in chosen_name else "SW"
    if decoder_type == "HW VA-API" and vaapi_system_path:
        log.info(
            "[VIDEO DECODER] %s (%s) — caricato da sistema: %s — formato output: %s",
            chosen_label, decoder_type, vaapi_system_path, fmt,
        )
    else:
        log.info(
            "[VIDEO DECODER] %s (%s) — formato output: %s",
            chosen_label, decoder_type, fmt,
        )

    # config-interval=-1: reinserisce SPS/PPS prima di ogni IDR frame.
    # Garantisce risincronizzazione pulita dopo ogni drop della queue leaky.
    pipeline_desc = (
        f"appsrc name=src is-live=true format=time "
        f"caps=video/x-h264,stream-format=byte-stream,alignment=au "
        f"! h264parse config-interval=-1 "
        f"! queue name=predec leaky=downstream "
        f"    max-size-buffers={_LEAKY_QUEUE_BUFFERS} "
        f"    max-size-time=0 max-size-bytes=0 "
        f"! {chosen_name} "
        f"! videoconvert "
        f"! video/x-raw,format={fmt} "
        f"! appsink name=sink emit-signals=true sync=false "
        f"    max-buffers=4 drop=false"
    )

    try:
        pipeline = Gst.parse_launch(pipeline_desc)
        log.info("GStreamer pipeline built: %s", pipeline_desc)
        return pipeline, fmt
    except Exception as exc:
        log.warning("GStreamer pipeline build failed: %s", exc)
        return None, ""

# ---------------------------------------------------------------------------
# OpenGL widget — NV12 renderer via GLSL shader
# ---------------------------------------------------------------------------

_VERT_SRC = """
#version 130
in vec2 position;
out vec2 texCoord;
void main() {
    texCoord    = position * 0.5 + 0.5;
    texCoord.y  = 1.0 - texCoord.y;
    gl_Position = vec4(position, 0.0, 1.0);
}
"""

_FRAG_SRC = """
#version 130
uniform sampler2D texY;
uniform sampler2D texUV;
in vec2 texCoord;
out vec4 fragColor;
void main() {
    float y  = texture(texY,  texCoord).r;
    vec2  uv = texture(texUV, texCoord).rg - vec2(0.5, 0.5);
    float r  = y + 1.5958  * uv.y;
    float g  = y - 0.39173 * uv.x - 0.81290 * uv.y;
    float b  = y + 2.017   * uv.x;
    fragColor = vec4(clamp(vec3(r, g, b), 0.0, 1.0), 1.0);
}
"""


class _NV12GLWidget(QOpenGLWidget):
    """Renders a single NV12 frame using a Y+UV GLSL shader."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame_data: bytes | None = None
        self._width  = 0
        self._height = 0
        self._prog   = None
        self._tex_y  = None
        self._tex_uv = None
        self._vbo    = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    @pyqtSlot(bytes, int, int)
    def push_nv12(self, data: bytes, width: int, height: int) -> None:
        self._frame_data = data
        self._width      = width
        self._height     = height
        self.update()

    def initializeGL(self) -> None:
        from OpenGL import GL  # type: ignore
        import numpy as np     # type: ignore

        self._GL = GL

        prog = GL.glCreateProgram()
        for src, kind in ((_VERT_SRC, GL.GL_VERTEX_SHADER), (_FRAG_SRC, GL.GL_FRAGMENT_SHADER)):
            sh = GL.glCreateShader(kind)
            GL.glShaderSource(sh, src)
            GL.glCompileShader(sh)
            if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
                log.warning("GLSL compile error: %s", GL.glGetShaderInfoLog(sh))
            GL.glAttachShader(prog, sh)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            log.warning("GLSL link error: %s", GL.glGetProgramInfoLog(prog))
        self._prog = prog

        self._tex_y  = GL.glGenTextures(1)
        self._tex_uv = GL.glGenTextures(1)

        import numpy as np  # type: ignore
        verts = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype=np.float32)
        self._vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
        log.info("_NV12GLWidget: OpenGL initialised prog=%s tex_y=%s tex_uv=%s",
                 self._prog, self._tex_y, self._tex_uv)

    def paintGL(self) -> None:
        if self._frame_data is None or self._width == 0:
            return
        GL = self._GL
        import numpy as np  # type: ignore

        w, h    = self._width, self._height
        y_size  = w * h
        y_data  = np.frombuffer(self._frame_data[:y_size], dtype=np.uint8).reshape(h, w)
        uv_data = np.frombuffer(
            self._frame_data[y_size:y_size + y_size // 2], dtype=np.uint8
        ).reshape(h // 2, w // 2, 2)

        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glUseProgram(self._prog)

        for tex, unit, data, fmt in (
            (self._tex_y,  0, y_data,  GL.GL_RED),
            (self._tex_uv, 1, uv_data, GL.GL_RG),
        ):
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, fmt,
                data.shape[1], data.shape[0],
                0, fmt, GL.GL_UNSIGNED_BYTE, data,
            )
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)

        GL.glUniform1i(GL.glGetUniformLocation(self._prog, "texY"),  0)
        GL.glUniform1i(GL.glGetUniformLocation(self._prog, "texUV"), 1)

        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        loc = GL.glGetAttribLocation(self._prog, "position")
        GL.glEnableVertexAttribArray(loc)
        GL.glVertexAttribPointer(loc, 2, GL.GL_FLOAT, False, 0, None)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

    def resizeGL(self, w: int, h: int) -> None:
        if hasattr(self, "_GL"):
            self._GL.glViewport(0, 0, w, h)

# ---------------------------------------------------------------------------
# Fallback: RGB QLabel renderer
# ---------------------------------------------------------------------------

class _RGBLabelWidget(QLabel):
    """Renders a single RGB frame into a QLabel pixmap."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: black;")

    @pyqtSlot(bytes, int, int)
    def push_rgb(self, data: bytes, width: int, height: int) -> None:
        img = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pix)

# ---------------------------------------------------------------------------
# Placeholder widget (clock + connection state)
# ---------------------------------------------------------------------------

class _PlaceholderWidget(QWidget):
    """Shown when no video stream is active."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #0d0d0d;")

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.setSpacing(16)

        self._clock_label = QLabel("00:00:00")
        font = QFont("Monospace", 48, QFont.Weight.Light)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self._clock_label.setFont(font)
        self._clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock_label.setStyleSheet("color: #e8e8e8; letter-spacing: 4px;")
        root.addWidget(self._clock_label)

        self._state_label = QLabel("●  In attesa di connessione BT")
        self._state_label.setFont(QFont("Sans", 13))
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_label.setStyleSheet("color: #e05252;")
        root.addWidget(self._state_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self) -> None:
        self._clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    @pyqtSlot(str, str)
    def set_conn_state(self, color: str, text: str) -> None:
        self._state_label.setStyleSheet(f"color: {color};")
        self._state_label.setText(text)

# ---------------------------------------------------------------------------
# Main video widget
# ---------------------------------------------------------------------------

class VideoWidget(QWidget):
    """
    Root widget. Contains:
      - QStackedWidget with pages: [0] placeholder, [1] video renderer
      - GStreamer pipeline (NV12 GL primary, RGB label fallback)

    set_streaming(True) is called automatically by _on_new_sample when the
    first decoded frame is ready — NOT on video.state=PLAYING — so the
    renderer widget is only shown once pixels are actually available.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(QSize(640, 400))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._render_fmt        = ""
        self._pipeline          = None
        self._appsrc            = None
        self._appsink           = None
        self._gst_thread        = None
        self._gl_widget:  _NV12GLWidget   | None = None
        self._rgb_widget: _RGBLabelWidget | None = None
        self._use_gl            = False
        self._first_frame_shown = False
        self._frames_pushed     = 0
        self._frames_decoded    = 0
        self._pts_counter: int  = 0

        self._stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._placeholder = _PlaceholderWidget()
        self._stack.addWidget(self._placeholder)   # index 0

        self._init_gstreamer()

    # ------------------------------------------------------------------
    # GStreamer setup
    # ------------------------------------------------------------------

    def _init_gstreamer(self) -> None:
        try:
            from OpenGL import GL  # noqa: F401
            import numpy           # noqa: F401
            gl_ok = True
        except ImportError:
            gl_ok = False
            log.warning("PyOpenGL or numpy not available — falling back to RGB QLabel renderer")

        pipeline, fmt = _build_pipeline(use_gl=gl_ok)

        if pipeline is None:
            log.warning("GStreamer pipeline non disponibile — solo placeholder")
            return

        self._pipeline   = pipeline
        self._render_fmt = fmt
        self._use_gl     = (fmt == "NV12" and gl_ok)

        if self._use_gl:
            self._gl_widget = _NV12GLWidget()
            self._stack.addWidget(self._gl_widget)   # index 1
            log.info("Renderer: NV12 → QOpenGLWidget (GLSL)")
        else:
            self._rgb_widget = _RGBLabelWidget()
            self._stack.addWidget(self._rgb_widget)  # index 1
            log.info("Renderer: RGB → QLabel")

        self._appsrc  = pipeline.get_by_name("src")
        self._appsink = pipeline.get_by_name("sink")
        self._appsink.connect("new-sample", self._on_new_sample)

        pipeline.set_state(Gst.State.PLAYING)
        log.info("GStreamer pipeline PLAYING (format=%s use_gl=%s)", fmt, self._use_gl)

        self._glib_loop  = GLib.MainLoop()
        self._gst_thread = threading.Thread(
            target=self._glib_loop.run, daemon=True, name="GstGLib"
        )
        self._gst_thread.start()

        gst_bus = pipeline.get_bus()
        gst_bus.add_signal_watch()
        gst_bus.connect("message::error",         self._on_gst_error)
        gst_bus.connect("message::eos",           self._on_gst_eos)
        gst_bus.connect("message::state-changed", self._on_gst_state_changed)

    def _on_gst_error(self, _bus, message) -> None:
        err, dbg = message.parse_error()
        log.warning("GStreamer error: %s — %s", err.message, dbg)

    def _on_gst_eos(self, _bus, _message) -> None:
        log.info("GStreamer EOS")

    def _on_gst_state_changed(self, _bus, message) -> None:
        if message.src == self._pipeline:
            old, new, _pending = message.parse_state_changed()
            log.info("GStreamer pipeline: %s → %s",
                     Gst.Element.state_get_name(old),
                     Gst.Element.state_get_name(new))

    # ------------------------------------------------------------------
    # Frame ingestion
    # ------------------------------------------------------------------

    @pyqtSlot(str, bool)
    def push_frame(self, data_b64: str, is_config: bool) -> None:
        """Decode base64 payload and push raw H264 bytes into GStreamer appsrc.

        Assegna un PTS monotono ad ogni buffer. Senza PTS vaapih264dec può
        riordinare i frame in modo errato su scene ad alto movimento.
        Il PTS è calcolato dal contatore interno _pts_counter (non dal clock
        di sistema) per evitare drift quando i frame non arrivano a ritmo costante.
        """
        if self._appsrc is None:
            return
        try:
            raw = base64.b64decode(data_b64)
        except Exception as exc:
            log.warning("push_frame: base64 decode error — %s", exc)
            return

        buf = Gst.Buffer.new_wrapped(raw)

        if not is_config:
            buf.pts      = self._pts_counter * _FRAME_DURATION_NS
            buf.duration = _FRAME_DURATION_NS
            self._pts_counter += 1
        else:
            buf.pts      = Gst.CLOCK_TIME_NONE
            buf.duration = Gst.CLOCK_TIME_NONE

        ret = self._appsrc.emit("push-buffer", buf)

        self._frames_pushed += 1
        if is_config or self._frames_pushed <= 5 or self._frames_pushed % 300 == 0:
            log.debug(
                "push_frame #%d is_config=%s len=%d pts=%s appsrc_ret=%s",
                self._frames_pushed, is_config,
                len(raw),
                buf.pts if not is_config else "NONE",
                ret,
            )

        if ret != Gst.FlowReturn.OK:
            log.warning("appsrc push-buffer returned %s (frame #%d)", ret, self._frames_pushed)

    def _on_new_sample(self, sink) -> "Gst.FlowReturn":
        """GStreamer callback: pull decoded frame and schedule Qt repaint."""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        caps   = sample.get_caps()
        struct = caps.get_structure(0)
        width  = struct.get_value("width")
        height = struct.get_value("height")
        buf    = sample.get_buffer()
        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR

        frame_bytes = bytes(mapinfo.data)
        buf.unmap(mapinfo)

        self._frames_decoded += 1
        if self._frames_decoded <= 3 or self._frames_decoded % 300 == 0:
            log.debug(
                "_on_new_sample #%d %dx%d len=%d",
                self._frames_decoded, width, height, len(frame_bytes),
            )

        if not self._first_frame_shown:
            self._first_frame_shown = True
            log.info("First decoded frame ready — switching to renderer widget")
            QMetaObject.invokeMethod(
                self, "set_streaming",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, True),
            )

        if self._use_gl and self._gl_widget is not None:
            QMetaObject.invokeMethod(
                self._gl_widget, "push_nv12",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bytes, frame_bytes),
                Q_ARG(int, width),
                Q_ARG(int, height),
            )
        elif self._rgb_widget is not None:
            QMetaObject.invokeMethod(
                self._rgb_widget, "push_rgb",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bytes, frame_bytes),
                Q_ARG(int, width),
                Q_ARG(int, height),
            )

        return Gst.FlowReturn.OK

    # ------------------------------------------------------------------
    # Stream visibility toggle
    # ------------------------------------------------------------------

    @pyqtSlot(bool)
    def set_streaming(self, active: bool) -> None:
        if not active:
            self._first_frame_shown = False
            self._pts_counter       = 0
        self._stack.setCurrentIndex(1 if (active and self._render_fmt) else 0)
        log.info("set_streaming(%s) → stack index %d", active,
                 1 if (active and self._render_fmt) else 0)

    @pyqtSlot(str, str)
    def set_conn_state(self, color: str, text: str) -> None:
        self._placeholder.set_conn_state(color, text)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
        if hasattr(self, "_glib_loop"):
            self._glib_loop.quit()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Standalone window wrapper (dev / test only)
# ---------------------------------------------------------------------------

class _VideoWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NemoHeadUnit v2 — Video")
        self._video = VideoWidget()
        self.setCentralWidget(self._video)

    @property
    def video(self) -> VideoWidget:
        return self._video


_window: _VideoWindow | None = None
_app:    QApplication | None = None


def _invoke(obj, slot: str, *args):
    if obj is None:
        return
    q_args = [Q_ARG(type(a), a) for a in args]
    QMetaObject.invokeMethod(obj, slot, Qt.ConnectionType.QueuedConnection, *q_args)


# ---------------------------------------------------------------------------
# Connection state machine helper
# ---------------------------------------------------------------------------

_conn_state: str = _STATE_WAITING_BT


def _set_conn_state(new_state: str) -> None:
    global _conn_state
    _conn_state = new_state
    color, text = _STATE_LABELS[new_state]
    if _window:
        _invoke(_window.video, "set_conn_state", color, text)
    log.info("conn_state → %s", new_state)


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info("system.readytostart — announcing priority %d", PRIORITY)
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info("system.start priority=2 — video_ui ready")
    if _window:
        winid = int(_window.video.winId())
        bus.publish("video.ui.winid", {"winid": winid})
        log.info("video.ui.winid published: %d", winid)
    
    bus.subscribe("video.frame",                  on_video_frame)
    bus.subscribe("video.state",                  on_video_state)
    bus.subscribe("aa.session.active",            on_aa_session_active)
    bus.subscribe("aa.session.shutdown",          on_aa_session_shutdown)
    bus.subscribe("bluetooth_manager.pairing.completed",  on_bluetooth_pairing_completed)

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — exiting")
    bus.stop()
    if _app:
        QMetaObject.invokeMethod(_app, "quit", Qt.ConnectionType.QueuedConnection)


def on_video_frame(topic: str, payload: dict) -> None:
    if _window is None:
        return
    data_b64  = payload.get("data_b64", "")
    is_config = bool(payload.get("is_config", False))
    QMetaObject.invokeMethod(
        _window.video, "push_frame",
        Qt.ConnectionType.QueuedConnection,
        Q_ARG(str, data_b64),
        Q_ARG(bool, is_config),
    )


def on_video_state(topic: str, payload: dict) -> None:
    state = payload.get("state", "")
    if state == "PLAYING":
        _set_conn_state(_STATE_STREAMING)
    elif state in ("IDLE", "STOPPED"):
        if _conn_state == _STATE_STREAMING:
            _set_conn_state(_STATE_INTERRUPTED)
        _invoke(_window.video if _window else None, "set_streaming", False)


def on_aa_session_active(topic: str, payload: dict) -> None:
    if _conn_state in (_STATE_WAITING_BT, _STATE_HANDSHAKE):
        _set_conn_state(_STATE_HANDSHAKE)


def on_aa_session_shutdown(topic: str, payload: dict) -> None:
    _set_conn_state(_STATE_WAITING_BT)
    _invoke(_window.video if _window else None, "set_streaming", False)


def on_bluetooth_pairing_completed(topic: str, payload: dict) -> None:
    if _conn_state == _STATE_WAITING_BT:
        _set_conn_state(_STATE_HANDSHAKE)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

import time  # noqa: E402


def run() -> None:
    global _app, _window

    bus.subscribe("system.readytostart",          on_system_readytostart)
    bus.subscribe("system.start",                 on_system_start)
    bus.subscribe("system.stop",                  on_system_stop)

    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()

    _app    = QApplication(sys.argv)
    _window = _VideoWindow()

    screen = _app.primaryScreen().availableGeometry()
    _window.setGeometry(screen.x(), screen.y(), screen.width() // 2, screen.height() // 2)
    _window.show()

    log.info("video_ui window open")
    exit_code = _app.exec()

    bus_thread.join(timeout=2)
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
