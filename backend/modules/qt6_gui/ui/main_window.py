"""
main_window.py — Master QMainWindow Staging Canvas, Overlay Views, Drawers, and Floating Bars.

Graphically identical layout staging for NemoHeadUnit-Wireless Qt6 Frontend.
"""

import logging
from pathlib import Path
from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QMainWindow, QStackedLayout, QWidget

from .analog_clock import AnalogClockWidget
from .arc_radial_menu import ArcRadialMenuWidget
from .command_bar import CommandBarWidget
from .video_viewport import VideoViewportWidget
from .volume_popover import VolumePopoverWidget
from .drawers.bluetooth_drawer import BluetoothDrawerWidget
from .drawers.settings_drawer import SettingsDrawerWidget
from .drawers.logs_drawer import LogsDrawerWidget

logger = logging.getLogger("qt6_gui.main_window")


class MainWindow(QMainWindow):
    """
    Main Qt6 Window Staging Canvas & GUI Panels.
    """

    close_app_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NemoHeadUnit — Wireless Android Auto (Qt6)")
        self.resize(1280, 720)
        self.setMinimumSize(800, 480)

        # Set Window Icon
        try:
            icon_path = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "favicon.png"
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
        except Exception as exc:
            logger.debug("Failed to set window icon: %s", exc)


        # Central Master Container
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        # Stacked / Layered Container
        # 1. Base Layer: Video Viewport OpenGL Canvas
        self.video_viewport = VideoViewportWidget(self.central_widget)
        self.video_viewport.setGeometry(0, 0, 1280, 720)

        # 2. Overlay Disconnected Clock Screen
        self.disconnected_screen = QWidget(self.central_widget)
        self.disconnected_screen.setGeometry(0, 0, 1280, 720)
        self.clock_widget = AnalogClockWidget(self.disconnected_screen)

        # 3. Floating Bottom Command Bar
        self.command_bar = CommandBarWidget(self.central_widget)

        # 4. Arc Radial FAB Action Menu
        self.arc_menu = ArcRadialMenuWidget(self.central_widget)

        # 5. Volume Popover Card
        self.volume_popover = VolumePopoverWidget(self.central_widget)

        # 6. Slide-Over Drawers
        self.bluetooth_drawer = BluetoothDrawerWidget(self.central_widget)
        self.settings_drawer = SettingsDrawerWidget(self.central_widget)
        self.logs_drawer = LogsDrawerWidget(self.central_widget)

        self._connect_signals()

    def _connect_signals(self):
        # Command Bar button signals
        self.command_bar.home_clicked.connect(self._toggle_clock_overlay)
        self.command_bar.volume_clicked.connect(self._toggle_volume_popover)
        self.command_bar.menu_clicked.connect(self.arc_menu.toggle_menu)
        self.command_bar.exit_clicked.connect(self.close_app_requested.emit)

        # Arc Menu drawer toggle signals
        self.arc_menu.bluetooth_clicked.connect(lambda: self._toggle_drawer(self.bluetooth_drawer))
        self.arc_menu.settings_clicked.connect(lambda: self._toggle_drawer(self.settings_drawer))
        self.arc_menu.logs_clicked.connect(lambda: self._toggle_drawer(self.logs_drawer))
        self.arc_menu.fullscreen_clicked.connect(self._toggle_fullscreen)

        # Drawer close signals
        self.bluetooth_drawer.close_clicked.connect(self.bluetooth_drawer.hide)
        self.settings_drawer.close_clicked.connect(self.settings_drawer.hide)
        self.logs_drawer.close_clicked.connect(self.logs_drawer.hide)

    def _toggle_clock_overlay(self):
        is_vis = not self.disconnected_screen.isVisible()
        self.disconnected_screen.setVisible(is_vis)
        self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not is_vis)
        if is_vis:
            self.disconnected_screen.raise_()
            self.command_bar.raise_()
            self.arc_menu.raise_()
            self.volume_popover.raise_()


    def _toggle_volume_popover(self):
        self.volume_popover.setVisible(not self.volume_popover.isVisible())

    def _toggle_drawer(self, target_drawer: QWidget):
        for drawer in (self.bluetooth_drawer, self.settings_drawer, self.logs_drawer):
            if drawer == target_drawer:
                drawer.setVisible(not drawer.isVisible())
            else:
                drawer.hide()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        h = self.height()

        # Resize video canvas and disconnected clock screen
        self.video_viewport.setGeometry(0, 0, w, h)
        self.disconnected_screen.setGeometry(0, 0, w, h)
        self.clock_widget.setGeometry((w - 320) // 2, (h - 360) // 2, 320, 360)

        # Position Command Bar (centered at bottom)
        bar_w = 440
        bar_h = 64
        self.command_bar.setGeometry((w - bar_w) // 2, h - bar_h - 20, bar_w, bar_h)

        # Position Arc Radial FAB menu (bottom right)
        self.arc_menu.setGeometry(w - 280, h - 340, 260, 260)

        # Position Volume Popover (above command bar)
        self.volume_popover.setGeometry((w - 240) // 2, h - bar_h - 85, 240, 52)

        # Position Slide-Over Drawers (pinned to right edge)
        drawer_w = 380
        self.bluetooth_drawer.setGeometry(w - drawer_w, 0, drawer_w, h)
        self.settings_drawer.setGeometry(w - drawer_w, 0, drawer_w, h)
        self.logs_drawer.setGeometry(w - drawer_w, 0, drawer_w, h)

    def set_connected_state(self, is_connected: bool):
        self.command_bar.set_online_status(is_connected)
        if is_connected:
            self.disconnected_screen.hide()
            self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        else:
            self.disconnected_screen.show()
            self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def closeEvent(self, event):
        self.close_app_requested.emit()
        super().closeEvent(event)

