"""
analog_clock.py — QWidget rendering Analog Clock & Clean Date View.

Graphically identical to frontend/js/analog_clock.js. Shown on the disconnected screen.
"""

import math
import datetime
from PyQt6.QtCore import QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class AnalogClockWidget(QWidget):
    """
    Analog Clock Canvas & Date View Widget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.addStretch()

        # Date Display
        self.date_label = QLabel(self)
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_label.setStyleSheet("""
            color: #8b949e;
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 2px;
        """)
        self.layout.addWidget(self.date_label)

        # Media Playback Card (When connected without video focus)
        self.media_card = QLabel(self)
        self.media_card.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_card.setStyleSheet("""
            color: #58a6ff;
            font-size: 15px;
            font-weight: 500;
            margin-top: 6px;
            padding: 4px 12px;
            background: rgba(30, 41, 59, 0.7);
            border-radius: 8px;
        """)
        self.media_card.hide()
        self.layout.addWidget(self.media_card)

        # Navigation Card (When connected without video focus)
        self.nav_card = QLabel(self)
        self.nav_card.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nav_card.setStyleSheet("""
            color: #00e676;
            font-size: 14px;
            font-weight: 500;
            margin-top: 4px;
            padding: 4px 12px;
            background: rgba(16, 185, 129, 0.15);
            border-radius: 8px;
        """)
        self.nav_card.hide()
        self.layout.addWidget(self.nav_card)

        # 1 Hz update timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1000)
        self._update_date()

    def update_media_info(self, title: str, artist: str):
        """Update media info display on disconnected/clock screen."""
        if title or artist:
            text = f"🎵 {title}"
            if artist:
                text += f" — {artist}"
            self.media_card.setText(text)
            self.media_card.show()
        else:
            self.media_card.hide()

    def update_nav_info(self, road: str, distance_meters: float):
        """Update navigation turn/road display on disconnected/clock screen."""
        if road or distance_meters >= 0:
            parts = []
            if road:
                parts.append(f"🧭 {road}")
            if distance_meters >= 0:
                if distance_meters >= 1000:
                    dist_str = f"{distance_meters / 1000.0:.1f} km"
                else:
                    dist_str = f"{int(distance_meters)} m"
                parts.append(f"({dist_str})")
            self.nav_card.setText(" ".join(parts))
            self.nav_card.show()
        else:
            self.nav_card.hide()

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
        radius = side * 0.35
        center = QPointF(width / 2.0, height / 2.0 - 20)

        # Clock dial background circle
        painter.setPen(QPen(QColor(48, 54, 61), 2))
        painter.setBrush(QColor(22, 27, 34, 180))
        painter.drawEllipse(center, radius, radius)

        now = datetime.datetime.now()
        hours = now.hour % 12 + now.minute / 60.0
        minutes = now.minute + now.second / 60.0
        seconds = now.second

        # Tick marks
        for i in range(12):
            angle = math.radians(i * 30 - 90)
            inner_r = radius * 0.85
            outer_r = radius * 0.95
            p1 = QPointF(center.x() + inner_r * math.cos(angle), center.y() + inner_r * math.sin(angle))
            p2 = QPointF(center.x() + outer_r * math.cos(angle), center.y() + outer_r * math.sin(angle))
            painter.setPen(QPen(QColor(139, 148, 158), 2))
            painter.drawLine(p1, p2)

        # Hour Hand
        h_angle = math.radians(hours * 30 - 90)
        h_len = radius * 0.5
        h_end = QPointF(center.x() + h_len * math.cos(h_angle), center.y() + h_len * math.sin(h_angle))
        painter.setPen(QPen(QColor(230, 237, 243), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, h_end)

        # Minute Hand
        m_angle = math.radians(minutes * 6 - 90)
        m_len = radius * 0.75
        m_end = QPointF(center.x() + m_len * math.cos(m_angle), center.y() + m_len * math.sin(m_angle))
        painter.setPen(QPen(QColor(88, 166, 255), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, m_end)

        # Second Hand
        s_angle = math.radians(seconds * 6 - 90)
        s_len = radius * 0.85
        s_end = QPointF(center.x() + s_len * math.cos(s_angle), center.y() + s_len * math.sin(s_angle))
        painter.setPen(QPen(QColor(255, 77, 77), 1.5))
        painter.drawLine(center, s_end)

        # Center pin
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 77, 77))
        painter.drawEllipse(center, 4, 4)
