from typing import Optional
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QSlider, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from app.message_bus import MessageBus
from app.logger import LoggerManager


class EqualizerTab(QGroupBox):
    def __init__(self, message_bus: Optional[MessageBus] = None):
        super().__init__("Audio Equalizer")
        self._bus = message_bus
        self._logger = LoggerManager.get_logger('app.gui.equalizer_tab')
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout()
        layout.setSpacing(40) # More spacing between bands
        bands = ["60Hz", "230Hz", "910Hz", "4kHz", "14kHz"]
        
        for band in bands:
            band_layout = QVBoxLayout()
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(-12, 12)
            slider.setValue(0)
            slider.setMinimumHeight(300)
            slider.setMinimumWidth(64) # Wider track for touch
            band_layout.addWidget(slider, alignment=Qt.AlignmentFlag.AlignCenter)
            band_lbl = QLabel(band)
            band_lbl.setStyleSheet("font-weight: bold; font-size: 18px;")
            band_layout.addWidget(band_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addLayout(band_layout)
            
        self.setLayout(layout)