"""
nav_card_widget.py — Dedicated Turn-by-Turn Navigation Widget for Connected Home/Clock Screen.

Renders vector turn maneuver icons, next street/road label, distance to turn, and ETA.
"""

import base64
from typing import Optional
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QPixmap, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class NavCardWidget(QFrame):
    """
    Glassmorphism Turn-by-Turn Navigation Widget.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("nav-card-widget")
        self.setStyleSheet("""
            QFrame#nav-card-widget {
                background: rgba(22, 27, 34, 0.75);
                border: 1px solid rgba(0, 230, 118, 0.25);
                border-radius: 16px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # 1. Turn Maneuver Icon View
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(90, 90)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            background: rgba(16, 185, 129, 0.15);
            border-radius: 12px;
            border: 1px solid rgba(0, 230, 118, 0.3);
        """)
        self._draw_maneuver_icon(0, 0)
        layout.addWidget(self.icon_label)

        # 2. Maneuver & Next Street Details
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Distance & Unit
        self.distance_label = QLabel("—", self)
        self.distance_label.setStyleSheet("""
            color: #00e676;
            font-size: 24px;
            font-weight: 700;
        """)
        text_layout.addWidget(self.distance_label)

        # Next Road / Street Name
        self.road_label = QLabel("Follow route", self)
        self.road_label.setStyleSheet("""
            color: #f0f6fc;
            font-size: 16px;
            font-weight: 600;
        """)
        self.road_label.setWordWrap(True)
        text_layout.addWidget(self.road_label)

        # ETA / Extra Status
        self.eta_label = QLabel("", self)
        self.eta_label.setStyleSheet("""
            color: #8b949e;
            font-size: 13px;
            font-weight: 500;
        """)
        text_layout.addWidget(self.eta_label)

        layout.addLayout(text_layout)
        layout.addStretch()

    def update_navigation(
        self,
        road: str,
        distance_meters: float,
        maneuver_type: int = 0,
        turn_side: int = 0,
        turn_icon_b64: str = "",
        eta_seconds: int = 0,
    ):
        """Update navigation turn details."""
        if not road and distance_meters < 0:
            self.road_label.setText("No Route Active")
            self.distance_label.setText("—")
            self.eta_label.setText("")
            self._draw_maneuver_icon(0, 0)
            return

        self.road_label.setText(road or "Follow Route")

        # Format Distance
        if distance_meters >= 0:
            if distance_meters >= 1000:
                self.distance_label.setText(f"{distance_meters / 1000.0:.1f} km")
            else:
                self.distance_label.setText(f"{int(distance_meters)} m")
        else:
            self.distance_label.setText("—")

        # Format ETA
        if eta_seconds > 0:
            mins = eta_seconds // 60
            hrs = mins // 60
            if hrs > 0:
                self.eta_label.setText(f"ETA: {hrs}h {mins % 60}m")
            else:
                self.eta_label.setText(f"ETA: {mins} min")
        else:
            self.eta_label.setText("")

        # Render Icon
        if turn_icon_b64 and "base64," in turn_icon_b64:
            try:
                raw_b64 = turn_icon_b64.split("base64,")[1]
                img_data = base64.b64decode(raw_b64)
                img = QImage.fromData(img_data)
                if not img.isNull():
                    pix = QPixmap.fromImage(img).scaled(
                        70, 70,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self.icon_label.setPixmap(pix)
                    return
            except Exception:
                pass

        self._draw_maneuver_icon(maneuver_type, turn_side)

    def _draw_maneuver_icon(self, maneuver_type: int, turn_side: int):
        """Draw aesthetic vector arrow based on maneuver type and turn side."""
        size = 90
        target = QPixmap(size, size)
        target.fill(Qt.GlobalColor.transparent)

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(16, 185, 129, 35))
        painter.drawRoundedRect(0, 0, size, size, 12, 12)

        # Arrow rendering
        painter.setPen(QPen(QColor(0, 230, 118), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

        cx, cy = size // 2, size // 2
        # turn_side: 1 = LEFT, 2 = RIGHT, 0 = STRAIGHT
        if turn_side == 1:  # LEFT
            painter.drawLine(cx + 12, cy + 20, cx + 12, cy - 6)
            painter.drawLine(cx + 12, cy - 6, cx - 14, cy - 6)
            painter.drawLine(cx - 14, cy - 6, cx - 6, cy - 14)
            painter.drawLine(cx - 14, cy - 6, cx - 6, cy + 2)
        elif turn_side == 2:  # RIGHT
            painter.drawLine(cx - 12, cy + 20, cx - 12, cy - 6)
            painter.drawLine(cx - 12, cy - 6, cx + 14, cy - 6)
            painter.drawLine(cx + 14, cy - 6, cx + 6, cy - 14)
            painter.drawLine(cx + 14, cy - 6, cx + 6, cy + 2)
        else:  # STRAIGHT
            painter.drawLine(cx, cy + 20, cx, cy - 16)
            painter.drawLine(cx, cy - 16, cx - 10, cy - 6)
            painter.drawLine(cx, cy - 16, cx + 10, cy - 6)

        painter.end()
        self.icon_label.setPixmap(target)
