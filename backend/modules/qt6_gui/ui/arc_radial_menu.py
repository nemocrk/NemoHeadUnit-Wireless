"""
arc_radial_menu.py — Arc Radial FAB Action Menu Widget.

Arc radial action menu providing fast access to Settings, Bluetooth, WiFi, Live Logs, and Fullscreen.
"""

import math
from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QPushButton, QWidget

from .svg_utils import make_svg_icon


class ArcRadialMenuWidget(QWidget):
    """
    Arc Radial FAB Action Menu Widget.
    Positions items along a curved radial arc anchored to bottom-right.
    """

    phone_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    bluetooth_clicked = pyqtSignal()
    wifi_clicked = pyqtSignal()
    logs_clicked = pyqtSignal()
    diagnostics_clicked = pyqtSignal()
    fullscreen_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(280, 280)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Fab Buttons with Material Vector SVG Icons
        self.fabs = [
            self._create_fab("phone", "Phone & Contacts", self.phone_clicked),
            self._create_fab("settings", "Settings", self.settings_clicked),
            self._create_fab("bluetooth", "Bluetooth Connections", self.bluetooth_clicked),
            self._create_fab("wifi", "WiFi Hotspot", self.wifi_clicked),
            self._create_fab("logs", "Live Logs", self.logs_clicked),
            self._create_fab("diagnostic", "Multimedia Diagnostics", self.diagnostics_clicked),
            self._create_fab("fullscreen", "Fullscreen Toggle", self.fullscreen_clicked),
        ]

        self.is_expanded = False
        self.hide()
        self._arrange_arc()

    def _create_fab(self, icon_name: str, tooltip: str, signal: pyqtSignal) -> QPushButton:
        btn = QPushButton(self)
        btn.setIcon(make_svg_icon(icon_name, color="#58a6ff", size=22))
        btn.setIconSize(QSize(22, 22))
        btn.setProperty("class", "arc-fab-item")
        btn.setToolTip(tooltip)
        btn.setFixedSize(52, 52)
        btn.clicked.connect(signal.emit)
        return btn

    def _arrange_arc(self):
        """Position 5 FAB buttons along a 90-degree curved radial arc."""
        radius = 145.0
        center_x = 210.0  # Anchor bottom right
        center_y = 210.0
        num_items = len(self.fabs)

        for i, btn in enumerate(self.fabs):
            # Spread angle from 180 (left) to 270 (top)
            angle_deg = 180 + (i / max(1, num_items - 1)) * 90.0
            angle_rad = math.radians(angle_deg)
            x = center_x + radius * math.cos(angle_rad) - 26
            y = center_y + radius * math.sin(angle_rad) - 26
            btn.move(int(x), int(y))

    def collapse(self):
        self.is_expanded = False
        self.hide()

    def expand(self):
        self.is_expanded = True
        self.show()
        self.raise_()

    def toggle_menu(self):
        if self.is_expanded:
            self.collapse()
        else:
            self.expand()
