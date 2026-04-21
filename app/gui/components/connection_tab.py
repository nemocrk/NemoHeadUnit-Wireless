"""
Connection tab component for NemoHeadUnit-Wireless

All bus subscriptions use thread='main' to ensure Qt widget
manipulation happens on the main thread via QtBusBridge.

Event Topics (subscribed):
- connection.pairing.requested
- connection.status

Event Topics (published):
- connection.pairing.requested
"""

from PyQt6.QtWidgets import (
    QGroupBox, QVBoxLayout, QLabel, QComboBox, QPushButton, QProgressBar
)
from PyQt6.QtCore import Qt
from app.bus_client import BusClient
from app.logger import LoggerManager


class ConnectionTab(QGroupBox):
    """
    Connection tab component.

    Displays connection status, pairing controls, and recent connections.
    """

    def __init__(self, message_bus: BusClient = None):
        super().__init__("Connection")
        self._connection_status = "Disconnected"
        self._signal_level = 0
        self._recent_connections = []
        self._bus = message_bus
        self._logger = LoggerManager.get_logger('app.gui.connection_tab')
        self._setup_ui()
        self._subscribe()

    def _setup_ui(self):
        layout = QVBoxLayout()

        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #ff6b6b")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        self.method_combo = QComboBox()
        self.method_combo.addItem("Bluetooth")
        self.method_combo.addItem("WiFi AP")
        layout.addWidget(QLabel("Connection Method:"))
        layout.addWidget(self.method_combo)

        self.pairing_btn = QPushButton("Pair Device")
        self.pairing_btn.setMinimumHeight(60)
        self.pairing_btn.clicked.connect(self._on_pairing_btn_clicked)
        layout.addWidget(self.pairing_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(30)
        layout.addWidget(self.progress_bar)

        self.recent_connections_label = QLabel("No connections yet")
        self.recent_connections_label.setWordWrap(True)
        self.recent_connections_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(QLabel("Recent Connections:"))
        layout.addWidget(self.recent_connections_label)

        layout.addStretch()
        self.setLayout(layout)

    def _subscribe(self):
        """Subscribe to bus topics. thread='main' ensures Qt-safe callbacks."""
        if not self._bus:
            return
        self._bus.on("connection.pairing.requested", self._on_pairing_requested, thread="main")
        self._bus.on("connection.status",            self._on_connection_status,  thread="main")

    def _on_pairing_requested(self, payload: dict) -> None:
        """Handle pairing request — runs on Qt main thread."""
        self._logger.debug(f"Pairing requested: {payload}")
        self.status_label.setText("Searching for devices...")

    def _on_connection_status(self, payload: dict) -> None:
        """Handle connection status updates — runs on Qt main thread."""
        status = payload.get("status", "Disconnected") if isinstance(payload, dict) else str(payload)
        method = payload.get("method", "Bluetooth") if isinstance(payload, dict) else "Bluetooth"
        self._logger.info(f"Connection status updated: {status} via {method}")
        self.set_connection_status(status, method)

    def _on_pairing_btn_clicked(self) -> None:
        """Publish pairing request to bus."""
        self._logger.info("Pairing button clicked")
        if self._bus:
            self._bus.publish("connection.pairing.requested", "connection_tab", {
                "method": self.method_combo.currentText(),
                "device": None,
            })

    def set_connection_status(self, status: str, method: str = "Bluetooth"):
        """Update connection status UI."""
        self._connection_status = status
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f"font-weight: bold; color: {'#4caf50' if status == 'Connected' else '#ff6b6b'}"
        )
        self.pairing_btn.setDisabled(status == "Connected")

    def set_signal_level(self, level: int):
        self._signal_level = level
        self.progress_bar.setValue(level)

    def add_recent_connection(self, device_name: str):
        self._recent_connections.append(device_name)
        self.recent_connections_label.setText(
            "\n".join(self._recent_connections[-5:])
        )
