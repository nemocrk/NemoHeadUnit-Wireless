"""
phone_call_widget.py — Dedicated In-Call & Incoming Call Card Widget for Connected Home/Clock Screen.

Displays active caller name, phone number, call duration timer, and glowing
Accept (Green), Decline / End Call (Red), and Mute (Yellow) action buttons.
"""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .svg_utils import make_svg_icon


class PhoneCallWidget(QFrame):
    """
    In-Call Interactive Card Widget for the Clock / Dashboard screen.
    """

    action_triggered = pyqtSignal(str)  # "answer", "hangup", "mute"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("phone-call-card")
        self.setProperty("class", "dash-card")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(10)

        # Header with Call State
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(8)

        self.icon_phone = QLabel(self)
        self.icon_phone.setPixmap(make_svg_icon("phone", color="#58a6ff", size=20).pixmap(20, 20))
        self.header_layout.addWidget(self.icon_phone)

        self.lbl_state = QLabel("Incoming Call...", self)
        self.lbl_state.setObjectName("phone-state-label")
        self.lbl_state.setStyleSheet("font-size: 13px; font-weight: bold; color: #58a6ff; text-transform: uppercase;")
        self.header_layout.addWidget(self.lbl_state)
        self.header_layout.addStretch()

        self.lbl_duration = QLabel("00:00", self)
        self.lbl_duration.setObjectName("phone-duration-label")
        self.lbl_duration.setStyleSheet("font-size: 13px; font-family: monospace; color: #8b949e;")
        self.header_layout.addWidget(self.lbl_duration)

        self.layout.addLayout(self.header_layout)

        # Caller Info (Name & Number)
        self.info_layout = QVBoxLayout()
        self.info_layout.setSpacing(2)

        self.lbl_caller_name = QLabel("John Doe", self)
        self.lbl_caller_name.setObjectName("caller-name")
        self.lbl_caller_name.setStyleSheet("font-size: 18px; font-weight: bold; color: #f0f6fc;")
        self.info_layout.addWidget(self.lbl_caller_name)

        self.lbl_caller_number = QLabel("+1 (555) 019-2834", self)
        self.lbl_caller_number.setObjectName("caller-number")
        self.lbl_caller_number.setStyleSheet("font-size: 13px; color: #8b949e;")
        self.info_layout.addWidget(self.lbl_caller_number)

        self.layout.addLayout(self.info_layout)
        self.layout.addStretch()

        # Action Buttons Layout
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(12)

        # 1. Answer (Green)
        self.btn_answer = QPushButton(" Answer", self)
        self.btn_answer.setObjectName("btn-call-answer")
        self.btn_answer.setIcon(make_svg_icon("phone", color="#ffffff", size=18))
        self.btn_answer.setIconSize(QSize(18, 18))
        self.btn_answer.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: #ffffff;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 16px;
            }
            QPushButton:hover { background: #2ea043; }
        """)
        self.btn_answer.clicked.connect(lambda: self.action_triggered.emit("answer"))
        self.btn_layout.addWidget(self.btn_answer)

        # 2. Decline / Hangup (Red)
        self.btn_hangup = QPushButton(" End Call", self)
        self.btn_hangup.setObjectName("btn-call-hangup")
        self.btn_hangup.setIcon(make_svg_icon("close", color="#ffffff", size=18))
        self.btn_hangup.setIconSize(QSize(18, 18))
        self.btn_hangup.setStyleSheet("""
            QPushButton {
                background: #da3633;
                color: #ffffff;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 16px;
            }
            QPushButton:hover { background: #f85149; }
        """)
        self.btn_hangup.clicked.connect(lambda: self.action_triggered.emit("hangup"))
        self.btn_layout.addWidget(self.btn_hangup)

        self.layout.addLayout(self.btn_layout)

    def update_call_state(
        self,
        is_in_call: bool,
        call_state: str = "IDLE",
        caller_name: str = "",
        caller_number: str = "",
        duration_seconds: int = 0,
    ):
        """Update the call card contents and state."""
        self.lbl_caller_name.setText(caller_name or caller_number or "Unknown Caller")
        self.lbl_caller_number.setText(caller_number or "")

        mins = duration_seconds // 60
        secs = duration_seconds % 60
        self.lbl_duration.setText(f"{mins:02d}:{secs:02d}")

        if call_state == "RINGING":
            self.lbl_state.setText("Incoming Call...")
            self.lbl_state.setStyleSheet("font-size: 13px; font-weight: bold; color: #58a6ff;")
            self.btn_answer.setVisible(True)
            self.btn_hangup.setText(" Decline")
        elif call_state in ("ACTIVE", "CONNECTING", "DIALING"):
            self.lbl_state.setText("In Call")
            self.lbl_state.setStyleSheet("font-size: 13px; font-weight: bold; color: #3fb950;")
            self.btn_answer.setVisible(False)
            self.btn_hangup.setText(" End Call")
        elif call_state == "HOLD":
            self.lbl_state.setText("Call on Hold")
            self.lbl_state.setStyleSheet("font-size: 13px; font-weight: bold; color: #d29922;")
            self.btn_answer.setVisible(False)
            self.btn_hangup.setText(" End Call")
        else:
            self.lbl_state.setText("Call Ended")
            self.lbl_state.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e;")
            self.btn_answer.setVisible(False)
            self.btn_hangup.setText(" Close")
