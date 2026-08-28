"""
main_window.py — Main Application Window Layout and Layering Management with WebClient Focus Parity.

Manages stacked layout:
1. Base Layer: OpenGL Video Viewport Widget (0, 0, w, h)
2. Overlay: Analog Clock / Disconnected Screen Widget (0, 0, w, h)
3. Floating: Bottom Command Bar Widget
4. Floating: Arc Radial FAB Action Menu Widget
5. Floating: Volume Popover Card Widget
6. Floating: Slide-Over Drawers (Bluetooth, Settings, Logs)
7. Floating: Top-Center Toast Notification Banner Widget
"""

import logging
from pathlib import Path
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from .analog_clock import AnalogClockWidget
from .arc_radial_menu import ArcRadialMenuWidget
from .command_bar import CommandBarWidget
from .drawers.bluetooth_drawer import BluetoothDrawerWidget
from .drawers.logs_drawer import LogsDrawerWidget
from .drawers.settings_drawer import SettingsDrawerWidget
from .toast_notification import ToastNotificationWidget
from .video_viewport import VideoViewportWidget
from .volume_popover import VolumePopoverWidget

logger = logging.getLogger("qt6_gui.main_window")


class MainWindow(QMainWindow):
    """
    Main Headunit Window Container with WebClient Video Focus & Toast Parity.
    """

    close_app_requested = pyqtSignal()
    video_focus_toggled = pyqtSignal(str)  # Emits "PROJECTED" or "NATIVE"
    projection_focus_requested = pyqtSignal(str)
    touch_input_requested = pyqtSignal(dict)
    microphone_data_requested = pyqtSignal(bytes)
    bluetooth_scan_requested = pyqtSignal()
    volume_action_requested = pyqtSignal(str)
    settings_load_requested = pyqtSignal()
    settings_save_requested = pyqtSignal(str, dict)

    def __init__(self, parent=None, service_enabled: bool = True):
        super().__init__(parent)
        self.setWindowTitle("NemoHeadUnit — Wireless Android Auto (Qt6)")
        self.resize(1280, 720)
        self.setMinimumSize(800, 480)

        self.isVideoFocused = True
        self._is_connected = False

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

        # 1. Base Layer: Video Viewport OpenGL Canvas
        self.video_viewport = VideoViewportWidget(self.central_widget)
        self.video_viewport.setGeometry(0, 0, 1280, 720)

        # 2. Overlay Disconnected Clock Screen
        self.disconnected_screen = QWidget(self.central_widget)
        self.disconnected_screen.setObjectName("disconnected-screen")
        self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.disconnected_screen.setGeometry(0, 0, 1280, 720)
        self.clock_widget = AnalogClockWidget(self.disconnected_screen)

        # 3. Floating Bottom Command Bar
        self.command_bar = CommandBarWidget(self.central_widget)

        # 4. Arc Radial FAB Action Menu
        self.arc_menu = ArcRadialMenuWidget(self.central_widget)

        # 5. Volume Popover Card
        self.volume_popover = VolumePopoverWidget(self.central_widget, service_enabled=service_enabled)

        # 6. Floating Top-Center Toast Notification Banner
        self.toast_widget = ToastNotificationWidget(self.central_widget)

        # 7. Slide-Over Drawers
        self.bluetooth_drawer = BluetoothDrawerWidget(self.central_widget, service_enabled=service_enabled)
        self.settings_drawer = SettingsDrawerWidget(self.central_widget, service_enabled=service_enabled)
        self.logs_drawer = LogsDrawerWidget(self.central_widget)

        self._connect_signals()

    def _connect_signals(self):
        # Command Bar button signals
        self.command_bar.home_clicked.connect(self._toggle_clock_overlay)
        self.command_bar.volume_clicked.connect(self._toggle_volume_popover)
        self.command_bar.menu_clicked.connect(self.arc_menu.toggle_menu)
        self.command_bar.exit_clicked.connect(self.close_app_requested.emit)
        self.video_viewport.touch_input_event.connect(self.touch_input_requested.emit)
        self.volume_popover.vol_action.connect(self.volume_action_requested.emit)
        self.bluetooth_drawer.scan_requested.connect(self.bluetooth_scan_requested.emit)
        self.settings_drawer.load_config_requested.connect(self.settings_load_requested.emit)
        self.settings_drawer.save_config_requested.connect(self.settings_save_requested.emit)

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
        """Toggle video focus mode between PROJECTED and NATIVE (matching WebClient toggleVideoFocus)."""
        self.isVideoFocused = not self.isVideoFocused
        target_mode = "PROJECTED" if self.isVideoFocused else "NATIVE"

        if self.isVideoFocused:
            self.disconnected_screen.hide()
            self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.toast_widget.show_toast("Resuming Android Auto Video Projection", "info")
        else:
            self.disconnected_screen.show()
            self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.disconnected_screen.raise_()
            self.command_bar.raise_()
            self.arc_menu.raise_()
            self.volume_popover.raise_()
            self.toast_widget.raise_()
            self.toast_widget.show_toast("Switched to Clock / Home (Video Suspended)", "info")

        self.video_focus_toggled.emit(target_mode)
        self.projection_focus_requested.emit(target_mode)

    def _toggle_volume_popover(self):
        self.volume_popover.setVisible(not self.volume_popover.isVisible())
        if self.volume_popover.isVisible():
            self.volume_popover.raise_()

    def _toggle_drawer(self, target_drawer: QWidget):
        for drawer in (self.bluetooth_drawer, self.settings_drawer, self.logs_drawer):
            if drawer == target_drawer:
                drawer.setVisible(not drawer.isVisible())
                if drawer.isVisible():
                    drawer.raise_()
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

        # Position Floating Toast Banner (top center)
        self.toast_widget.setGeometry((w - 380) // 2, 20, 380, 44)

        # Position Slide-Over Drawers (pinned to right edge)
        drawer_w = 380
        self.bluetooth_drawer.setGeometry(w - drawer_w, 0, drawer_w, h)
        self.settings_drawer.setGeometry(w - drawer_w, 0, drawer_w, h)
        self.logs_drawer.setGeometry(w - drawer_w, 0, drawer_w, h)

    def set_connected_state(self, is_connected: bool):
        """Update connection state: hide clock on projection, re-show on disconnect."""
        self._is_connected = is_connected
        self.command_bar.set_online_status(is_connected)
        if is_connected:
            if self.isVideoFocused:
                self.disconnected_screen.hide()
                self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        else:
            self.isVideoFocused = False
            self.disconnected_screen.show()
            self.disconnected_screen.raise_()
            self.command_bar.raise_()
            self.arc_menu.raise_()
            self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def closeEvent(self, event):
        self.close_app_requested.emit()
        super().closeEvent(event)
