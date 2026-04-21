from typing import Optional
from PyQt6.QtWidgets import QGroupBox, QFormLayout, QSlider, QComboBox, QCheckBox, QVBoxLayout
from PyQt6.QtCore import Qt
from app.message_bus import MessageBus
from app.logger import LoggerManager


class ConfigTab(QGroupBox):
    def __init__(self, message_bus: Optional[MessageBus] = None):
        super().__init__("System Configuration")
        self._bus = message_bus
        self._logger = LoggerManager.get_logger('app.gui.config_tab')
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()
        form.setVerticalSpacing(35) # Increased for 10-inch 1080p
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Display settings
        self.display_brightness = QSlider(Qt.Orientation.Horizontal)
        self.display_brightness.setRange(0, 100)
        self.display_brightness.setValue(80)
        self.display_brightness.setMinimumHeight(64)
        form.addRow("Brightness:", self.display_brightness)

        self.display_orientation = QComboBox()
        self.display_orientation.setMinimumHeight(50)
        self.display_orientation.addItems(["Landscape", "Portrait", "Auto"])
        form.addRow("Orientation:", self.display_orientation)

        self.audio_output = QComboBox()
        self.audio_output.addItems(["Bluetooth", "Line Out", "Internal Speaker"])
        self.audio_output.setMinimumHeight(50)
        form.addRow("Audio Output:", self.audio_output)

        # Feature toggles
        self.navigation_enabled = QCheckBox("Enable Navigation")
        self.navigation_enabled.setChecked(True)
        self.navigation_enabled.setStyleSheet("QCheckBox::indicator { width: 30px; height: 30px; }")
        form.addRow(self.navigation_enabled)

        self.touch_mode = QCheckBox("Touch Mode")
        self.touch_mode.setChecked(True)
        self.touch_mode.setStyleSheet("QCheckBox::indicator { width: 30px; height: 30px; }")
        form.addRow(self.touch_mode)

        self.auto_connect = QCheckBox("Auto-connect on Startup")
        self.auto_connect.setStyleSheet("QCheckBox::indicator { width: 30px; height: 30px; }")
        form.addRow(self.auto_connect)

        self.low_latency = QCheckBox("Low Latency Mode")
        self.low_latency.setStyleSheet("QCheckBox::indicator { width: 30px; height: 30px; }")
        form.addRow(self.low_latency)

        self.debug_mode = QCheckBox("Debug Mode")
        self.debug_mode.setStyleSheet("QCheckBox::indicator { width: 30px; height: 30px; }")
        form.addRow(self.debug_mode)

        self.log_level = QComboBox()
        self.log_level.setMinimumHeight(50)
        self.log_level.addItems(["Debug", "Info", "Warning"])
        form.addRow("Logging Level:", self.log_level)

        layout.addLayout(form)
        layout.addStretch()
        self.setLayout(layout)