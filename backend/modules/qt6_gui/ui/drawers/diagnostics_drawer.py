"""
diagnostics_drawer.py — Interactive Multimedia Diagnostics Drawer for Qt6 GUI.
Point-by-point audio testing, device routing, and video HW acceleration benchmarks.
"""

import json
import urllib.parse
import urllib.request
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..svg_utils import make_svg_icon


class DiagnosticsDrawerWidget(QWidget):
    """
    Multimedia Diagnostics Slide-Over Drawer.
    """

    close_clicked = pyqtSignal()

    def __init__(self, parent=None, host_port="127.0.0.1:8000"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(440)
        self.host_port = host_port
        self.ws: QWebSocket = None

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(12)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("🛠 Multimedia Diagnostics", self)
        title_label.setProperty("class", "drawer-title")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("×", self)
        close_btn.setProperty("class", "close-btn")
        close_btn.clicked.connect(self.close_clicked.emit)
        header_layout.addWidget(close_btn)
        self.main_layout.addLayout(header_layout)

        # Scroll Area for diagnostic controls
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # --- SECTION 1: Audio Diagnostics ---
        audio_group = QFrame(self)
        audio_group.setStyleSheet("QFrame { background-color: rgba(22, 27, 34, 0.7); border: 1px solid #30363d; border-radius: 8px; padding: 10px; }")
        audio_layout = QVBoxLayout(audio_group)

        lbl_audio = QLabel("🔊 Audio Pipeline Tests", self)
        lbl_audio.setStyleSheet("color: #58a6ff; font-weight: bold; font-size: 13px;")
        audio_layout.addWidget(lbl_audio)

        # Audio Test Buttons
        audio_btn_grid = QGridLayout()
        self.btn_pcm_440 = QPushButton("▶ PCM Tone (440Hz)", self)
        self.btn_pcm_1000 = QPushButton("▶ PCM Tone (1kHz)", self)
        self.btn_aac_chime = QPushButton("▶ AAC Chime", self)
        self.btn_mic_test = QPushButton("🎤 Mic Level Test", self)
        self.btn_cli_proc = QPushButton("⚙️ Subprocess Tone", self)
        self.btn_in_proc = QPushButton("⚡ In-Process Tone", self)

        for btn in (self.btn_pcm_440, self.btn_pcm_1000, self.btn_aac_chime, self.btn_mic_test, self.btn_cli_proc, self.btn_in_proc):
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #21262d;
                    color: #c9d1d9;
                    border: 1px solid #30363d;
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #30363d;
                    color: #58a6ff;
                    border-color: #58a6ff;
                }
            """)

        self.btn_pcm_440.clicked.connect(lambda: self._run_test("audio_pcm", {"tone_hz": 440, "duration_ms": 1500}))
        self.btn_pcm_1000.clicked.connect(lambda: self._run_test("audio_pcm", {"tone_hz": 1000, "duration_ms": 1500}))
        self.btn_aac_chime.clicked.connect(lambda: self._run_test("audio_aac", {"duration_ms": 1500}))
        self.btn_mic_test.clicked.connect(lambda: self._run_test("audio_mic", {"duration_ms": 3000}))
        self.btn_cli_proc.clicked.connect(lambda: self._run_test("audio_standalone_proc", {"freq": 440, "duration_sec": 2.0}))
        self.btn_in_proc.clicked.connect(lambda: self._run_test("audio_in_process", {"freq": 440, "duration_sec": 2.0, "push": False}))

        audio_btn_grid.addWidget(self.btn_pcm_440, 0, 0)
        audio_btn_grid.addWidget(self.btn_pcm_1000, 0, 1)
        audio_btn_grid.addWidget(self.btn_aac_chime, 1, 0)
        audio_btn_grid.addWidget(self.btn_mic_test, 1, 1)
        audio_btn_grid.addWidget(self.btn_cli_proc, 2, 0)
        audio_btn_grid.addWidget(self.btn_in_proc, 2, 1)
        audio_layout.addLayout(audio_btn_grid)

        # VU / Mic Meter
        vu_layout = QHBoxLayout()
        lbl_vu = QLabel("Mic Level:", self)
        lbl_vu.setStyleSheet("color: #8b949e; font-size: 11px;")
        vu_layout.addWidget(lbl_vu)
        self.vu_bar = QProgressBar(self)
        self.vu_bar.setRange(0, 100)
        self.vu_bar.setValue(0)
        self.vu_bar.setTextVisible(False)
        self.vu_bar.setFixedHeight(10)
        self.vu_bar.setStyleSheet("""
            QProgressBar {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3fb950, stop:0.7 #d29922, stop:1.0 #f85149);
                border-radius: 4px;
            }
        """)
        vu_layout.addWidget(self.vu_bar)
        audio_layout.addLayout(vu_layout)

        # Audio Device Selector
        dev_layout = QHBoxLayout()
        self.sink_combo = QComboBox(self)
        self.sink_combo.setStyleSheet("background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 4px; padding: 4px;")
        self.sink_combo.addItem("Default Sink", "default")
        dev_layout.addWidget(self.sink_combo, 1)

        self.btn_set_sink = QPushButton("Apply Sink", self)
        self.btn_set_sink.setStyleSheet("background-color: #238636; color: white; border: none; border-radius: 4px; padding: 4px 8px; font-size: 11px;")
        self.btn_set_sink.clicked.connect(self._apply_audio_sink)
        dev_layout.addWidget(self.btn_set_sink)
        audio_layout.addLayout(dev_layout)

        layout.addWidget(audio_group)

        # --- SECTION 2: Video & HW Acceleration ---
        video_group = QFrame(self)
        video_group.setStyleSheet("QFrame { background-color: rgba(22, 27, 34, 0.7); border: 1px solid #30363d; border-radius: 8px; padding: 10px; }")
        video_layout = QVBoxLayout(video_group)

        lbl_video = QLabel("🎬 Video & HW Acceleration", self)
        lbl_video.setStyleSheet("color: #58a6ff; font-weight: bold; font-size: 13px;")
        video_layout.addWidget(lbl_video)

        # Transport & Decoder dropdowns
        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("Transport:", self), 0, 0)
        self.combo_transport = QComboBox(self)
        self.combo_transport.setStyleSheet("background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 4px; padding: 4px;")
        for m in ("mjpeg", "webp", "yuv420", "rgba", "h264"):
            self.combo_transport.addItem(m.upper(), m)
        form_layout.addWidget(self.combo_transport, 0, 1)

        form_layout.addWidget(QLabel("Decoder:", self), 1, 0)
        self.combo_decoder = QComboBox(self)
        self.combo_decoder.setStyleSheet("background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 4px; padding: 4px;")
        self.combo_decoder.addItem("Auto (Negotiated)", "auto")
        self.combo_decoder.addItem("Hardware (V4L2)", "forced_v4l2")
        self.combo_decoder.addItem("Hardware (VAAPI)", "forced_vaapi")
        self.combo_decoder.addItem("Hardware (Direct3D 11)", "forced_d3d11va")
        self.combo_decoder.addItem("Hardware (NVDEC)", "forced_nvdec")
        self.combo_decoder.addItem("Software (FFmpeg)", "sw")
        form_layout.addWidget(self.combo_decoder, 1, 1)
        video_layout.addLayout(form_layout)

        # Benchmark Button
        self.btn_benchmark = QPushButton("🚀 Run Video Pipeline Benchmark (2s)", self)
        self.btn_benchmark.setStyleSheet("""
            QPushButton {
                background-color: #1f6feb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #388bfd;
            }
        """)
        self.btn_benchmark.clicked.connect(self._run_video_benchmark)
        video_layout.addWidget(self.btn_benchmark)

        layout.addWidget(video_group)

        # --- SECTION 3: Diagnostic Output Logs ---
        self.console = QTextEdit(self)
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(200)
        self.console.setFixedHeight(120)
        self.console.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #58a6ff;
                font-family: Consolas, Monaco, monospace;
                font-size: 10px;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        self.console.setPlainText("Diagnostic system ready.\n")
        layout.addWidget(self.console)

        scroll.setWidget(container)
        self.main_layout.addWidget(scroll)

        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self._fetch_capabilities()
        self._connect_ws()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.ws:
            self.ws.close()
            self.ws = None

    def _fetch_capabilities(self):
        """Fetch audio sinks and decoder list from /api/media/diagnostic/capabilities."""
        try:
            url = f"http://{self.host_port}/api/media/diagnostic/capabilities"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                sinks = data.get("audio", {}).get("sinks", [])
                if sinks:
                    self.sink_combo.clear()
                    self.sink_combo.addItem("Default Sink", "default")
                    for s in sinks:
                        self.sink_combo.addItem(s, s)
        except Exception as exc:
            self._log(f"Notice: Failed to fetch capabilities: {exc}")

    def _connect_ws(self):
        """Connect to /api/diagnostic/ws for live test metrics."""
        if self.ws:
            return
        self.ws = QWebSocket()
        self.ws.textMessageReceived.connect(self._on_ws_message)
        ws_url = QUrl(f"ws://{self.host_port}/api/diagnostic/ws")
        self.ws.open(ws_url)

    @pyqtSlot(str)
    def _on_ws_message(self, message: str):
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type == "test_started":
                self._log(f"▶ Started: {data.get('test_type')}")
            elif msg_type == "test_completed":
                res = data.get("results", {})
                self._log(f"✔ Completed: {res.get('test_type')} ({res.get('status')}) in {res.get('elapsed_sec')}s")
            elif msg_type == "mic_level":
                length = data.get("len", 0)
                level = min(100, int((length / 1024.0) * 100))
                self.vu_bar.setValue(level)
            elif msg_type == "audio_frame_injected":
                self._log(f"♫ Injected {data.get('format')} audio ({data.get('len')} bytes)")
        except Exception:
            pass

    def _run_test(self, test_type: str, params: dict):
        self._log(f"Triggering {test_type}...")
        try:
            url = f"http://{self.host_port}/api/diagnostic/run"
            payload = json.dumps({"test_type": test_type, "params": params}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3.0)
        except Exception as exc:
            self._log(f"Error triggering test: {exc}")

    def _run_video_benchmark(self):
        transport = self.combo_transport.currentData()
        decoder = self.combo_decoder.currentData()
        self._run_test("video_benchmark", {"transport": transport, "decoder": decoder, "duration_sec": 2.0, "fps": 30})

    def _apply_audio_sink(self):
        sink = self.sink_combo.currentData()
        self._run_test("audio_device_select", {"sink": sink})

    def _log(self, text: str):
        self.console.append(text)
