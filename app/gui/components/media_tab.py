from typing import Optional
from PyQt6.QtWidgets import QGroupBox, QFormLayout, QLabel, QComboBox, QSlider, QCheckBox, QVBoxLayout
from PyQt6.QtCore import Qt
from app.message_bus import MessageBus
from app.logger import LoggerManager


class MediaTab(QGroupBox):
    def __init__(self, message_bus: Optional[MessageBus] = None):
        super().__init__("Media Streaming")
        self._bus = message_bus
        self._logger = LoggerManager.get_logger('app.gui.media_tab')
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()

        # Video channel
        self.video_status = QLabel("Not Active")
        self.video_status.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        form.addRow("Video Status:", self.video_status)

        self.video_source = QComboBox()
        self.video_source.addItem("System Display")
        form.addRow("Video Source:", self.video_source)

        self.video_scale = QSlider(Qt.Orientation.Horizontal)
        self.video_scale.setRange(50, 150)
        self.video_scale.setValue(100)
        self.video_scale.setMinimumHeight(80)
        form.addRow("Scale:", self.video_scale)

        # Audio channel
        self.audio_status = QLabel("Not Active")
        self.audio_status.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        form.addRow("Audio Status:", self.audio_status)

        self.audio_source = QComboBox()
        self.audio_source.addItems(["Media Player", "Navigation", "Phone Calls"])
        form.addRow("Audio Source:", self.audio_source)

        self.audio_volume = QSlider(Qt.Orientation.Horizontal)
        self.audio_volume.setValue(75)
        self.audio_volume.setMinimumHeight(80)
        form.addRow("Channel Volume:", self.audio_volume)

        self.audio_muted = QCheckBox("Muted")
        form.addRow(self.audio_muted)

        # Microphone
        self.mic_status = QLabel("Not Active")
        form.addRow("Microphone:", self.mic_status)
        
        self.mic_routing = QCheckBox("Route to Head Unit")
        form.addRow(self.mic_routing)

        layout.addLayout(form)
        self.setLayout(layout)