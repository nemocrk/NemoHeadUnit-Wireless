"""
analog_clock.py — Premium Automotive Analog Clock & Date Card Widget for Qt6.

Features multi-layer gradient dial, luminous watch markers, glowing second hand,
and sleek date badge pill.
"""

import math
import datetime
from PyQt6.QtCore import QPointF, QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QLinearGradient,
    QBrush,
)
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget, QHBoxLayout


class AnalogClockWidget(QFrame):
    """
    Luxury Automotive Analog Clock & Date Card Widget.
    """

    connect_phone_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("clock-card-widget")
        self.setMinimumSize(220, 220)
        self.setStyleSheet("""
            QFrame#clock-card-widget {
                background: rgba(22, 27, 34, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 20px;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(10)
        self.layout.addStretch()

        # Date Display Pill
        self.date_container = QHBoxLayout()
        self.date_container.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.date_label = QLabel(self)
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_label.setStyleSheet("""
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 6px 16px;
            color: #58a6ff;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 1.5px;
        """)
        self.date_container.addWidget(self.date_label)
        self.layout.addLayout(self.date_container)

        # Connect / Pair Phone Button
        self.btn_connect_phone = QPushButton("📱 Connect Phone / Bluetooth", self)
        self.btn_connect_phone.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_connect_phone.setStyleSheet("""
            QPushButton {
                background-color: rgba(56, 189, 248, 0.12);
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 14px;
                padding: 8px 18px;
                color: #38bdf8;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: rgba(56, 189, 248, 0.28);
                border-color: #38bdf8;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: rgba(56, 189, 248, 0.45);
            }
        """)
        self.btn_connect_phone.clicked.connect(self.connect_phone_clicked.emit)
        self.layout.addWidget(self.btn_connect_phone, alignment=Qt.AlignmentFlag.AlignCenter)

        # 1 Hz update timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1000)
        self._update_date()

    def _on_tick(self):
        self._update_date()
        self.update()

    def _update_date(self):
        now = datetime.datetime.now()
        date_str = now.strftime("%a, %b %d").upper()
        self.date_label.setText(date_str)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        side = min(width, height)
        radius = side * 0.36
        center = QPointF(width / 2.0, height / 2.0 - 24)

        # Outer Bezel Glow
        glow_grad = QRadialGradient(center, radius + 12)
        glow_grad.setColorAt(0.7, QColor(56, 189, 248, 25))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow_grad))
        painter.drawEllipse(center, radius + 12, radius + 12)

        # Dial Bezel Ring
        bezel_grad = QLinearGradient(center.x() - radius, center.y() - radius, center.x() + radius, center.y() + radius)
        bezel_grad.setColorAt(0.0, QColor(88, 166, 255, 140))
        bezel_grad.setColorAt(0.5, QColor(30, 41, 59, 180))
        bezel_grad.setColorAt(1.0, QColor(56, 189, 248, 140))
        painter.setPen(QPen(QBrush(bezel_grad), 2.5))

        # Dial Face Background
        dial_grad = QRadialGradient(center, radius)
        dial_grad.setColorAt(0.0, QColor(15, 23, 42, 240))
        dial_grad.setColorAt(0.85, QColor(11, 15, 25, 250))
        dial_grad.setColorAt(1.0, QColor(30, 41, 59, 255))
        painter.setBrush(QBrush(dial_grad))
        painter.drawEllipse(center, radius, radius)

        now = datetime.datetime.now()
        hours = now.hour % 12 + now.minute / 60.0
        minutes = now.minute + now.second / 60.0
        seconds = now.second

        # Hour & Minute Tick Marks
        for i in range(60):
            angle = math.radians(i * 6 - 90)
            is_major = (i % 5 == 0)
            inner_r = radius * (0.80 if is_major else 0.88)
            outer_r = radius * 0.93

            p1 = QPointF(center.x() + inner_r * math.cos(angle), center.y() + inner_r * math.sin(angle))
            p2 = QPointF(center.x() + outer_r * math.cos(angle), center.y() + outer_r * math.sin(angle))

            if is_major:
                painter.setPen(QPen(QColor(240, 246, 252, 220), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            else:
                painter.setPen(QPen(QColor(100, 116, 139, 100), 1.0))
            painter.drawLine(p1, p2)

        # Hour Hand (Luminous Neon White)
        h_angle = math.radians(hours * 30 - 90)
        h_len = radius * 0.52
        h_end = QPointF(center.x() + h_len * math.cos(h_angle), center.y() + h_len * math.sin(h_angle))
        painter.setPen(QPen(QColor(248, 250, 252), 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, h_end)

        # Minute Hand (Cyan Accent)
        m_angle = math.radians(minutes * 6 - 90)
        m_len = radius * 0.74
        m_end = QPointF(center.x() + m_len * math.cos(m_angle), center.y() + m_len * math.sin(m_angle))
        painter.setPen(QPen(QColor(56, 189, 248), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, m_end)

        # Second Hand (Glowing Coral Red with Counterweight)
        s_angle = math.radians(seconds * 6 - 90)
        s_len = radius * 0.86
        tail_len = radius * 0.20
        s_end = QPointF(center.x() + s_len * math.cos(s_angle), center.y() + s_len * math.sin(s_angle))
        s_tail = QPointF(center.x() - tail_len * math.cos(s_angle), center.y() - tail_len * math.sin(s_angle))
        painter.setPen(QPen(QColor(248, 81, 73), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(s_tail, s_end)

        # Center Cap & Accent Dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42))
        painter.drawEllipse(center, 6, 6)
        painter.setBrush(QColor(248, 81, 73))
        painter.drawEllipse(center, 3.5, 3.5)
