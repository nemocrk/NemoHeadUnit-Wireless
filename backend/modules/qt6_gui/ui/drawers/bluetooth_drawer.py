"""
bluetooth_drawer.py — Bluetooth Connections Slide-Over Drawer with Real-Time SSE Stream Integration.

Displays real-time Bluetooth device discovery scan, paired devices, and connection controls via connectivity_manager SSE stream.
"""

import json
import urllib.request
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BluetoothStreamThread(QThread):
    """Asynchronous thread connecting to /api/connectivity/stream_status SSE stream."""
    status_updated = pyqtSignal(dict)

    def __init__(self, host_port="127.0.0.1:8000"):
        super().__init__()
        self.host_port = host_port
        self._running = True

    def run(self):
        url = f"http://{self.host_port}/api/connectivity/stream_status"
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
                                self.status_updated.emit(data)
            except Exception:
                # Brief sleep before reconnecting
                self.msleep(1000)

    def stop(self):
        self._running = False


class BluetoothScanThread(QThread):
    """Asynchronous thread for posting discovery trigger to /api/connectivity/discover."""
    scan_finished = pyqtSignal()

    def run(self):
        try:
            url = "http://127.0.0.1:8000/api/connectivity/discover"
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                pass
        except Exception:
            pass
        self.scan_finished.emit()


class BluetoothDrawerWidget(QWidget):
    """
    Bluetooth Manager Drawer with Real-Time SSE Stream Integration.
    """

    close_clicked = pyqtSignal()
    scan_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(340)

        self.stream_thread = None

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)

        # Drawer Header
        header_layout = QHBoxLayout()
        title_label = QLabel("Bluetooth Connections", self)
        title_label.setProperty("class", "drawer-title")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("×", self)
        close_btn.setProperty("class", "close-btn")
        close_btn.clicked.connect(self.close_clicked.emit)
        header_layout.addWidget(close_btn)
        self.layout.addLayout(header_layout)

        # Controls & Scan Button
        self.scan_btn = QPushButton("🔍 Scan Bluetooth Devices", self)
        self.scan_btn.setStyleSheet("""
            QPushButton {
                background-color: #1f6feb;
                color: #ffffff;
                border: 1px solid #388bfd;
                border-radius: 6px;
                padding: 8px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #388bfd;
            }
        """)
        self.scan_btn.clicked.connect(self._on_scan_clicked)
        self.layout.addWidget(self.scan_btn)

        # Status Label
        self.lbl_status = QLabel("Connecting to status stream...", self)
        self.lbl_status.setStyleSheet("color: #58a6ff; font-weight: 500;")
        self.layout.addWidget(self.lbl_status)

        # Device List
        self.device_list = QListWidget(self)
        self.device_list.setStyleSheet("""
            QListWidget {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #21262d;
                color: #e6edf3;
            }
            QListWidget::item:hover {
                background-color: #161b22;
            }
        """)
        self.layout.addWidget(self.device_list)

        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self.start_stream()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop_stream()

    def start_stream(self):
        if not self.stream_thread:
            self.stream_thread = BluetoothStreamThread()
            self.stream_thread.status_updated.connect(self._on_status_updated)
            self.stream_thread.start()

    def stop_stream(self):
        if self.stream_thread:
            self.stream_thread.stop()
            self.stream_thread.quit()
            self.stream_thread = None

    def _on_scan_clicked(self):
        self.lbl_status.setText("Initiating Bluetooth discovery scan...")
        self.scan_thread = BluetoothScanThread()
        self.scan_thread.start()

    def _on_status_updated(self, data: dict):
        discovering = data.get("discovering", False)
        rfcomm_connected = data.get("rfcomm_connected", False)
        stage_label = data.get("stage_label", "Bluetooth Ready")

        if discovering:
            self.lbl_status.setText("🔍 Scanning for nearby Bluetooth devices...")
        elif rfcomm_connected:
            self.lbl_status.setText(f"🟢 {stage_label}")
        else:
            self.lbl_status.setText(f"🔵 {stage_label}")

        toast_msg = data.get("toast_message")
        pairing_pin = data.get("pairing_pin")
        if pairing_pin:
            dev_addr = data.get("pairing_device", "Phone")
            toast_msg = f"🔑 Pairing Request from {dev_addr}: PIN {pairing_pin}"

        if toast_msg and hasattr(self.window(), "toast_widget") and self.window().toast_widget:
            self.window().toast_widget.show_toast(toast_msg, icon="📶")


        paired = data.get("paired_devices", [])
        discovered = data.get("discovered_devices", [])


        # Combine paired and discovered devices, marking connection state
        all_devices = []
        for dev in paired:
            dev["connected"] = True
            all_devices.append(dev)

        for dev in discovered:
            if not any(d.get("address") == dev.get("address") for d in all_devices):
                dev["connected"] = False
                all_devices.append(dev)

        self.device_list.clear()
        if not all_devices:
            self.device_list.addItem(QListWidgetItem("No Bluetooth devices found."))
            return

        for dev in all_devices:
            name = dev.get("name", "Unknown Device")
            addr = dev.get("address", "")
            connected = dev.get("connected", False)
            status_text = " 🟢 Connected" if connected else " ⚪ Paired / Discovered"
            item = QListWidgetItem(f"📱 {name}\n   [{addr}]{status_text}")
            self.device_list.addItem(item)
