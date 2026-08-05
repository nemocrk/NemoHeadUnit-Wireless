"""
command_bar.py — Floating Command Bar Widget.

Bottom command bar featuring Home, Status Dot, Play/Pause, Volume, Exit, Menu Drawer buttons.
"""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from .svg_utils import make_svg_icon


class CommandBarWidget(QWidget):
    """
    Floating Bottom Control Bar Widget.
    """

    home_clicked = pyqtSignal()
    playpause_clicked = pyqtSignal()
    volume_clicked = pyqtSignal()
    exit_clicked = pyqtSignal()
    menu_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("command-bar")

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 4, 12, 4)
        self.layout.setSpacing(8)

        # 1. Home / Clock Button
        self.btn_home = QPushButton(self)
        self.btn_home.setIcon(make_svg_icon("home", color="#8b949e", size=22))
        self.btn_home.setIconSize(QSize(22, 22))
        self.btn_home.setObjectName("btn-home")
        self.btn_home.setProperty("class", "cmd-btn")
        self.btn_home.setToolTip("Home / Clock View")
        self.btn_home.clicked.connect(self.home_clicked.emit)
        self.layout.addWidget(self.btn_home)

        # 2. Status Dot
        self.status_dot = QWidget(self)
        self.status_dot.setObjectName("status-dot-offline")
        self.status_dot.setToolTip("System Connection Status")
        self.layout.addWidget(self.status_dot)

        # 3. Play / Pause Button
        self.btn_playpause = QPushButton(self)
        self.btn_playpause.setIcon(make_svg_icon("play", color="#8b949e", size=22))
        self.btn_playpause.setIconSize(QSize(22, 22))
        self.btn_playpause.setObjectName("btn-playpause")
        self.btn_playpause.setProperty("class", "cmd-btn")
        self.btn_playpause.setToolTip("Play / Pause")
        self.btn_playpause.clicked.connect(self.playpause_clicked.emit)
        self.layout.addWidget(self.btn_playpause)

        # 4. Volume Controls Button
        self.btn_volume = QPushButton(self)
        self.btn_volume.setIcon(make_svg_icon("volume", color="#8b949e", size=22))
        self.btn_volume.setIconSize(QSize(22, 22))
        self.btn_volume.setObjectName("btn-volume")
        self.btn_volume.setProperty("class", "cmd-btn")
        self.btn_volume.setToolTip("Volume Controls")
        self.btn_volume.clicked.connect(self.volume_clicked.emit)
        self.layout.addWidget(self.btn_volume)

        # 5. Exit Button
        self.btn_close = QPushButton(self)
        self.btn_close.setIcon(make_svg_icon("close", color="#8b949e", size=22))
        self.btn_close.setIconSize(QSize(22, 22))
        self.btn_close.setObjectName("btn-close")
        self.btn_close.setProperty("class", "cmd-btn")
        self.btn_close.setToolTip("Close / Exit")
        self.btn_close.clicked.connect(self.exit_clicked.emit)
        self.layout.addWidget(self.btn_close)

        # 6. Menu Drawer Button
        self.btn_menu = QPushButton(self)
        self.btn_menu.setIcon(make_svg_icon("menu", color="#8b949e", size=22))
        self.btn_menu.setIconSize(QSize(22, 22))
        self.btn_menu.setObjectName("btn-menu")
        self.btn_menu.setProperty("class", "cmd-btn")
        self.btn_menu.setToolTip("Main Menu")
        self.btn_menu.clicked.connect(self.menu_clicked.emit)
        self.layout.addWidget(self.btn_menu)

    def set_online_status(self, is_online: bool):
        if is_online:
            self.status_dot.setObjectName("status-dot-online")
        else:
            self.status_dot.setObjectName("status-dot-offline")
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)
