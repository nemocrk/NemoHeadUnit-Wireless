"""
phone_card_widget.py — Dedicated Phone & Telephony Card Widget for Dashboard.
Replaces notification card with live/synthetic cellular status, quick actions,
and a dedicated button to open the Phone Drawer.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .svg_utils import make_svg_icon


class PhoneCardWidget(QFrame):
    """
    Dashboard card displaying phone connectivity status, cellular telemetry,
    quick contact call action, and an open drawer trigger.
    """

    open_drawer_requested = pyqtSignal()
    call_requested = pyqtSignal(str)
    call_action_triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("phone-telephony-card")
        self.setProperty("class", "dash-card")

        self._device_name = "Pixel 7"
        self._carrier = "Vodafone 5G"
        self._signal_bars = 4
        self._battery_pct = 85
        self._is_connected = True
        self._quick_name = "Sarah Connor"
        self._quick_number = "+39 347 9876543"

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(10)

        # 1. Header: Phone Icon + Title + Status Pill
        header = QHBoxLayout()
        header.setSpacing(8)

        self.icon_phone = QLabel(self)
        self.icon_phone.setPixmap(make_svg_icon("phone", color="#38bdf8", size=20).pixmap(20, 20))
        header.addWidget(self.icon_phone)

        self.lbl_title = QLabel("Phone Telephony", self)
        self.lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f0f6fc;")
        header.addWidget(self.lbl_title)
        header.addStretch()

        self.lbl_status_pill = QLabel("CONNECTED", self)
        self.lbl_status_pill.setStyleSheet("""
            QLabel {
                background: rgba(35, 134, 54, 0.25);
                color: #3fb950;
                font-size: 11px;
                font-weight: 700;
                padding: 3px 8px;
                border: 1px solid rgba(63, 185, 80, 0.4);
                border-radius: 10px;
            }
        """)
        header.addWidget(self.lbl_status_pill)
        self.layout.addLayout(header)

        # 2. Telemetry Row: Device & Carrier + Signal + Battery
        telemetry_frame = QFrame(self)
        telemetry_frame.setStyleSheet("""
            QFrame {
                background: rgba(22, 27, 34, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                padding: 4px;
            }
        """)
        t_layout = QHBoxLayout(telemetry_frame)
        t_layout.setContentsMargins(10, 8, 10, 8)
        t_layout.setSpacing(12)

        # Left info: Device & Carrier
        v_info = QVBoxLayout()
        v_info.setSpacing(2)
        self.lbl_device = QLabel(f"📱 {self._device_name}", telemetry_frame)
        self.lbl_device.setStyleSheet("font-size: 13px; font-weight: 600; color: #f0f6fc;")
        v_info.addWidget(self.lbl_device)

        self.lbl_carrier = QLabel(self._carrier, telemetry_frame)
        self.lbl_carrier.setStyleSheet("font-size: 11px; color: #8b949e;")
        v_info.addWidget(self.lbl_carrier)
        t_layout.addLayout(v_info)
        t_layout.addStretch()

        # Right indicators: Signal & Battery
        v_stats = QVBoxLayout()
        v_stats.setSpacing(2)
        self.lbl_signal = QLabel(f"📶 {self._signal_bars}/5", telemetry_frame)
        self.lbl_signal.setStyleSheet("font-size: 12px; font-weight: 600; color: #38bdf8;")
        v_stats.addWidget(self.lbl_signal)

        self.lbl_battery = QLabel(f"🔋 {self._battery_pct}%", telemetry_frame)
        self.lbl_battery.setStyleSheet("font-size: 12px; font-weight: 600; color: #3fb950;")
        v_stats.addWidget(self.lbl_battery)
        t_layout.addLayout(v_stats)

        self.layout.addWidget(telemetry_frame)

        # 3. Quick Action Row: Recent / Favorite Dial
        quick_frame = QFrame(self)
        quick_frame.setStyleSheet("""
            QFrame {
                background: rgba(33, 38, 45, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 8px;
            }
        """)
        q_layout = QHBoxLayout(quick_frame)
        q_layout.setContentsMargins(10, 8, 10, 8)
        q_layout.setSpacing(10)

        v_quick = QVBoxLayout()
        v_quick.setSpacing(2)
        lbl_q_header = QLabel("QUICK CALL", quick_frame)
        lbl_q_header.setStyleSheet("font-size: 10px; font-weight: 700; color: #8b949e; letter-spacing: 0.5px;")
        v_quick.addWidget(lbl_q_header)

        self.lbl_quick_contact = QLabel(f"{self._quick_name} ({self._quick_number})", quick_frame)
        self.lbl_quick_contact.setStyleSheet("font-size: 12px; font-weight: 600; color: #c9d1d9;")
        v_quick.addWidget(self.lbl_quick_contact)
        q_layout.addLayout(v_quick)
        q_layout.addStretch()

        self.btn_quick_call = QPushButton(" Call", quick_frame)
        self.btn_quick_call.setIcon(make_svg_icon("phone", color="#ffffff", size=14))
        self.btn_quick_call.setIconSize(QSize(14, 14))
        self.btn_quick_call.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: #ffffff;
                font-size: 12px;
                font-weight: 600;
                border-radius: 6px;
                padding: 6px 12px;
                border: none;
            }
            QPushButton:hover { background: #2ea043; }
        """)
        self.btn_quick_call.clicked.connect(self._on_quick_call_clicked)
        q_layout.addWidget(self.btn_quick_call)

        self.layout.addWidget(quick_frame)

        # 4. Open Drawer Button (Prominent Action)
        self.btn_open_drawer = QPushButton("Open Phone Drawer", self)
        self.btn_open_drawer.setIcon(make_svg_icon("phone", color="#58a6ff", size=16))
        self.btn_open_drawer.setIconSize(QSize(16, 16))
        self.btn_open_drawer.setStyleSheet("""
            QPushButton {
                background: #21262d;
                color: #58a6ff;
                font-size: 13px;
                font-weight: bold;
                border: 1px solid rgba(88, 166, 255, 0.4);
                border-radius: 8px;
                padding: 10px;
            }
            QPushButton:hover {
                background: #30363d;
                border-color: #58a6ff;
            }
            QPushButton:pressed {
                background: #1f6feb;
                color: #ffffff;
            }
        """)
        self.btn_open_drawer.clicked.connect(self.open_drawer_requested.emit)
        self.layout.addWidget(self.btn_open_drawer)

    def _on_quick_call_clicked(self):
        if self._quick_number:
            self.call_requested.emit(self._quick_number)
            self.call_action_triggered.emit(f"dial:{self._quick_number}")

    def update_telemetry(
        self,
        device_name: str = "",
        carrier: str = "",
        signal_bars: int = -1,
        battery_pct: int = -1,
        is_connected: bool = True,
    ):
        if device_name:
            self._device_name = device_name
            self.lbl_device.setText(f"📱 {device_name}")
        if carrier:
            self._carrier = carrier
            self.lbl_carrier.setText(carrier)
        if signal_bars >= 0:
            self._signal_bars = max(0, min(5, signal_bars))
            self.lbl_signal.setText(f"📶 {self._signal_bars}/5")
        if battery_pct >= 0:
            self._battery_pct = max(0, min(100, battery_pct))
            self.lbl_battery.setText(f"🔋 {self._battery_pct}%")

        self._is_connected = is_connected
        if is_connected:
            self.lbl_status_pill.setText("CONNECTED")
            self.lbl_status_pill.setStyleSheet("""
                QLabel {
                    background: rgba(35, 134, 54, 0.25);
                    color: #3fb950;
                    font-size: 11px;
                    font-weight: 700;
                    padding: 3px 8px;
                    border: 1px solid rgba(63, 185, 80, 0.4);
                    border-radius: 10px;
                }
            """)
        else:
            self.lbl_status_pill.setText("SEARCHING")
            self.lbl_status_pill.setStyleSheet("""
                QLabel {
                    background: rgba(187, 128, 9, 0.25);
                    color: #d29922;
                    font-size: 11px;
                    font-weight: 700;
                    padding: 3px 8px;
                    border: 1px solid rgba(210, 153, 34, 0.4);
                    border-radius: 10px;
                }
            """)

    def set_quick_contact(self, name: str, number: str):
        self._quick_name = name
        self._quick_number = number
        self.lbl_quick_contact.setText(f"{name} ({number})")
