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


class BluetoothActionThread(QThread):
    """Asynchronous thread for posting action requests to /api/connectivity."""
    action_finished = pyqtSignal()

    def __init__(self, endpoint: str, payload: dict, host_port: str = "127.0.0.1:8000"):
        super().__init__()
        self.endpoint = endpoint
        self.payload = payload
        self.host_port = host_port

    def run(self):
        try:
            url = f"http://{self.host_port}/api/connectivity/{self.endpoint.lstrip('/')}"
            data_bytes = json.dumps(self.payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                pass
        except Exception:
            pass
        self.action_finished.emit()


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
        self.action_thread = None
        self.known_devices = []
        self.ignored_devices = []

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
        btn_top_layout = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 Scan Devices", self)
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
        btn_top_layout.addWidget(self.scan_btn)

        self.connect_pair_btn = QPushButton("🔗 Pair / Connect", self)
        self.connect_pair_btn.setStyleSheet("""
            QPushButton {
                background-color: #238636;
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                padding: 8px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #2ea043;
            }
        """)
        self.connect_pair_btn.clicked.connect(self._on_connect_pair_clicked)
        btn_top_layout.addWidget(self.connect_pair_btn)
        self.layout.addLayout(btn_top_layout)

        # Status Label
        self.lbl_status = QLabel("Connecting to status stream...", self)
        self.lbl_status.setStyleSheet("color: #58a6ff; font-weight: 500;")
        self.layout.addWidget(self.lbl_status)

        # Pairing PIN Box
        self.active_pairing_device = None
        self.pairing_card = QWidget(self)
        self.pairing_card.setObjectName("bt-pairing-card")
        self.pairing_card.setStyleSheet("""
            QWidget#bt-pairing-card {
                background-color: rgba(30, 41, 59, 0.95);
                border: 2px solid #38bdf8;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        p_layout = QVBoxLayout(self.pairing_card)
        p_layout.setContentsMargins(10, 10, 10, 10)
        p_layout.setSpacing(6)
        self.lbl_pairing_title = QLabel("🔑 Pairing Request", self.pairing_card)
        self.lbl_pairing_title.setStyleSheet("font-weight: bold; color: #38bdf8; font-size: 13px;")
        p_layout.addWidget(self.lbl_pairing_title)

        self.lbl_pairing_pin = QLabel("PIN: ------", self.pairing_card)
        self.lbl_pairing_pin.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff; letter-spacing: 3px;")
        self.lbl_pairing_pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p_layout.addWidget(self.lbl_pairing_pin)

        btn_box = QHBoxLayout()
        self.btn_confirm_pin = QPushButton("✅ Confirm & Pair", self.pairing_card)
        self.btn_confirm_pin.setStyleSheet("background-color: #238636; color: white; border-radius: 6px; padding: 6px 12px; font-weight: bold;")
        self.btn_confirm_pin.clicked.connect(self._on_confirm_pin_clicked)
        btn_box.addWidget(self.btn_confirm_pin)

        self.btn_reject_pin = QPushButton("❌ Reject", self.pairing_card)
        self.btn_reject_pin.setStyleSheet("background-color: #da3633; color: white; border-radius: 6px; padding: 6px 12px; font-weight: bold;")
        self.btn_reject_pin.clicked.connect(self._on_reject_pin_clicked)
        btn_box.addWidget(self.btn_reject_pin)
        p_layout.addLayout(btn_box)

        self.layout.addWidget(self.pairing_card)
        self.pairing_card.hide()

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
            QListWidget::item:selected {
                background-color: #1f6feb;
                color: #ffffff;
            }
            QListWidget::item:hover {
                background-color: #161b22;
            }
        """)
        self.device_list.itemDoubleClicked.connect(lambda item: self._on_connect_pair_clicked())
        self.layout.addWidget(self.device_list)

        # Action Buttons Layout
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        self.toggle_ignore_btn = QPushButton("🚫 Toggle Ignore", self)
        self.toggle_ignore_btn.setStyleSheet("""
            QPushButton {
                background-color: #21262d;
                color: #e6edf3;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 8px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #30363d;
            }
        """)
        self.toggle_ignore_btn.clicked.connect(self._on_toggle_ignore_clicked)
        actions_layout.addWidget(self.toggle_ignore_btn)

        self.remove_btn = QPushButton("🗑️ Forget", self)
        self.remove_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(239, 68, 68, 0.15);
                color: #f87171;
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 6px;
                padding: 8px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.3);
            }
        """)
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        actions_layout.addWidget(self.remove_btn)

        self.layout.addLayout(actions_layout)

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

    def _on_connect_pair_clicked(self):
        selected_item = self.device_list.currentItem()
        if not selected_item:
            self.lbl_status.setText("⚠️ Select a device to pair/connect")
            return
        addr = selected_item.data(Qt.ItemDataRole.UserRole)
        if not addr:
            return
        is_paired = selected_item.data(Qt.ItemDataRole.UserRole + 1)
        if is_paired:
            self.lbl_status.setText(f"Connecting to {addr}...")
            self.action_thread = BluetoothActionThread("connect", {"device_address": addr})
        else:
            self.lbl_status.setText(f"Initiating pairing with {addr}...")
            self.action_thread = BluetoothActionThread("pair", {"device_address": addr})
        self.action_thread.start()

    def _on_confirm_pin_clicked(self):
        if not self.active_pairing_device:
            return
        self.lbl_status.setText(f"Confirming pairing with {self.active_pairing_device}...")
        self.action_thread = BluetoothActionThread("pair/confirm", {"device_address": self.active_pairing_device})
        self.action_thread.start()
        self.pairing_card.hide()

    def _on_reject_pin_clicked(self):
        if not self.active_pairing_device:
            return
        self.lbl_status.setText(f"Rejecting pairing with {self.active_pairing_device}...")
        self.action_thread = BluetoothActionThread("pair/reject", {"device_address": self.active_pairing_device})
        self.action_thread.start()
        self.pairing_card.hide()

    def _on_toggle_ignore_clicked(self):
        selected_item = self.device_list.currentItem()
        if not selected_item:
            self.lbl_status.setText("⚠️ Select a device to toggle ignore")
            return
        addr = selected_item.data(Qt.ItemDataRole.UserRole)
        if not addr:
            return

        is_currently_ignored = addr in self.ignored_devices
        endpoint = "devices/unignore" if is_currently_ignored else "devices/ignore"
        self.lbl_status.setText(f"{'Enabling' if is_currently_ignored else 'Ignoring'} {addr}...")
        self.action_thread = BluetoothActionThread(endpoint, {"device_address": addr})
        self.action_thread.start()

    def _on_remove_clicked(self):
        selected_item = self.device_list.currentItem()
        if not selected_item:
            self.lbl_status.setText("⚠️ Select a device to forget/remove")
            return
        addr = selected_item.data(Qt.ItemDataRole.UserRole)
        if not addr:
            return

        self.lbl_status.setText(f"Removing device {addr}...")
        self.action_thread = BluetoothActionThread("paired/remove", {"device_address": addr})
        self.action_thread.start()

    def _on_status_updated(self, data: dict):
        discovering = data.get("discovering", False)
        rfcomm_connected = data.get("rfcomm_connected", False)
        stage_label = data.get("stage_label", "Bluetooth Ready")
        self.known_devices = data.get("known_aa_devices", [])
        self.ignored_devices = data.get("ignored_devices", [])

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
            self.active_pairing_device = dev_addr
            self.lbl_pairing_title.setText(f"🔑 Pairing Request: {dev_addr}")
            self.lbl_pairing_pin.setText(f"PIN: {pairing_pin}")
            self.pairing_card.show()
            toast_msg = f"🔑 Pairing Request from {dev_addr}: PIN {pairing_pin}"
        else:
            self.pairing_card.hide()

        if toast_msg and hasattr(self.window(), "toast_widget") and self.window().toast_widget:
            self.window().toast_widget.show_toast(toast_msg, icon="📶")

        paired = data.get("paired_devices", [])
        discovered = data.get("discovered_devices", [])

        # Combine paired and discovered devices, marking connection state
        all_devices = []
        for dev in paired:
            dev["connected"] = True
            dev["is_paired"] = True
            all_devices.append(dev)

        for dev in discovered:
            if not any(d.get("address") == dev.get("address") for d in all_devices):
                dev["connected"] = False
                dev["is_paired"] = False
                all_devices.append(dev)

        current_selected_addr = None
        if self.device_list.currentItem():
            current_selected_addr = self.device_list.currentItem().data(Qt.ItemDataRole.UserRole)

        self.device_list.clear()
        if not all_devices:
            self.device_list.addItem(QListWidgetItem("No Bluetooth devices found."))
            return

        for dev in all_devices:
            name = dev.get("name", "Unknown Device")
            addr = dev.get("address", "")
            connected = dev.get("connected", False)
            is_paired = dev.get("is_paired", False)
            is_known = addr in self.known_devices
            is_ignored = addr in self.ignored_devices

            tag = " [⭐ AA Ready]" if is_known else (" [🚫 Ignored]" if is_ignored else "")
            status_text = " 🟢 Connected" if connected else (" 🔵 Paired" if is_paired else " ⚪ Discovered (Tap to Pair)")
            item = QListWidgetItem(f"📱 {name}{tag}\n   [{addr}]{status_text}")
            item.setData(Qt.ItemDataRole.UserRole, addr)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_paired)
            self.device_list.addItem(item)
            if current_selected_addr and addr == current_selected_addr:
                self.device_list.setCurrentItem(item)
