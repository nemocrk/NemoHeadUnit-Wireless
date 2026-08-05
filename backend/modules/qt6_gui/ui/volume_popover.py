"""
volume_popover.py — Volume Popover Menu Card Widget with Real-Time Media Server SSE Stream.

Displays Mute toggle, Volume Down, Volume Percentage level label, and Volume Up buttons.
"""

import json
import urllib.request
from PyQt6.QtCore import QSize, Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .svg_utils import make_svg_icon


class MediaStatusStreamThread(QThread):
    """Asynchronous thread connecting to /api/media/stream_status SSE stream."""
    media_status_updated = pyqtSignal(dict)

    def __init__(self, host_port="127.0.0.1:8000"):
        super().__init__()
        self.host_port = host_port
        self._running = True

    def run(self):
        url = f"http://{self.host_port}/api/media/stream_status"
        while self._running:
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=12.0) as resp:
                    for line in resp:
                        if not self._running:
                            break
                        line_str = line.decode("utf-8", errors="ignore").strip()
                        if line_str.startswith("data:"):
                            json_str = line_str[5:].strip()
                            if json_str:
                                data = json.loads(json_str)
                                self.media_status_updated.emit(data)
            except Exception:
                self.msleep(1000)

    def stop(self):
        self._running = False


class VolumeActionThread(QThread):
    """Asynchronous thread for posting volume changes to media_server REST API."""
    volume_updated = pyqtSignal(int, bool)

    def __init__(self, action: str):
        super().__init__()
        self.action = action

    def run(self):
        try:
            url = f"http://127.0.0.1:8000/api/media/volume?action={self.action}"
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                vol = data.get("volume", 80)
                muted = data.get("muted", False)
                self.volume_updated.emit(vol, muted)
        except Exception:
            pass


class VolumePopoverWidget(QWidget):
    """
    Volume Popover Control Widget with Live Media SSE Stream Sync.
    """

    vol_action = pyqtSignal(str)  # "up", "down", "mute"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("volume-popover")
        self.setStyleSheet("""
            #volume-popover {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 20px;
            }
            .vol-btn {
                background-color: #21262d;
                color: #e6edf3;
                border: 1px solid #30363d;
                border-radius: 14px;
                min-width: 32px;
                min-height: 32px;
                font-weight: bold;
                font-size: 16px;
            }
            .vol-btn:hover {
                background-color: #30363d;
            }
            #vol-level-display {
                color: #58a6ff;
                font-weight: 600;
                font-size: 14px;
                padding: 0 6px;
            }
        """)

        self.current_volume = 80
        self.is_muted = False
        self.media_stream = None

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 6, 12, 6)
        self.layout.setSpacing(8)

        # Mute button
        self.btn_mute = QPushButton(self)
        self.btn_mute.setIcon(make_svg_icon("mute", color="#e6edf3", size=20))
        self.btn_mute.setIconSize(QSize(20, 20))
        self.btn_mute.setProperty("class", "vol-btn")
        self.btn_mute.setToolTip("Toggle Mute")
        self.btn_mute.clicked.connect(lambda: self._on_vol_click("mute"))
        self.layout.addWidget(self.btn_mute)

        # Vol Down button
        self.btn_down = QPushButton("-", self)
        self.btn_down.setProperty("class", "vol-btn")
        self.btn_down.setToolTip("Volume Down")
        self.btn_down.clicked.connect(lambda: self._on_vol_click("down"))
        self.layout.addWidget(self.btn_down)

        # Vol Level text display
        self.lbl_level = QLabel("80%", self)
        self.lbl_level.setObjectName("vol-level-display")
        self.layout.addWidget(self.lbl_level)

        # Vol Up button
        self.btn_up = QPushButton("+", self)
        self.btn_up.setProperty("class", "vol-btn")
        self.btn_up.setToolTip("Volume Up")
        self.btn_up.clicked.connect(lambda: self._on_vol_click("up"))
        self.layout.addWidget(self.btn_up)

        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self.start_media_stream()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop_media_stream()

    def start_media_stream(self):
        if not self.media_stream:
            self.media_stream = MediaStatusStreamThread()
            self.media_stream.media_status_updated.connect(self._on_media_status_updated)
            self.media_stream.start()

    def stop_media_stream(self):
        if self.media_stream:
            self.media_stream.stop()
            self.media_stream.quit()
            self.media_stream = None

    def _on_media_status_updated(self, data: dict):
        vol = data.get("volume", self.current_volume)
        muted = data.get("muted", self.is_muted)
        self.update_volume(vol, muted)

    def _on_vol_click(self, action: str):
        if action == "up":
            self.current_volume = min(100, self.current_volume + 5)
        elif action == "down":
            self.current_volume = max(0, self.current_volume - 5)
        elif action == "mute":
            self.is_muted = not self.is_muted

        self.update_volume(self.current_volume, self.is_muted)
        self.vol_action.emit(action)

        self.action_thread = VolumeActionThread(action)
        self.action_thread.volume_updated.connect(self.update_volume)
        self.action_thread.start()

    def update_volume(self, level: int, muted: bool):
        self.current_volume = level
        self.is_muted = muted
        self.lbl_level.setText(f"{level}%" if not muted else "Muted")
        self.btn_mute.setIcon(make_svg_icon("mute" if muted else "volume", color="#e6edf3", size=20))
