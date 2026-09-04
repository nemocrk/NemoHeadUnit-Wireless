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
from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from .analog_clock import AnalogClockWidget
from .arc_radial_menu import ArcRadialMenuWidget
from .command_bar import CommandBarWidget
from .drawers.bluetooth_drawer import BluetoothDrawerWidget
from .drawers.diagnostics_drawer import DiagnosticsDrawerWidget
from .drawers.logs_drawer import LogsDrawerWidget
from .drawers.phone_drawer import PhoneDrawerWidget
from .drawers.settings_drawer import SettingsDrawerWidget
from .media_card_widget import MediaCardWidget
from .nav_card_widget import NavCardWidget
from .notification_widget import NotificationToast, NotificationCardWidget
from .phone_call_widget import PhoneCallWidget
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
    focus_toggle_requested = video_focus_toggled
    phone_action_requested = pyqtSignal(str)
    media_playpause_requested = pyqtSignal()
    fullscreen_change_requested = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowTitle("NemoHeadUnit — Wireless Android Auto (Qt6)")
        self.resize(1280, 720)
        self.setMinimumSize(800, 480)
        self.fullscreen_change_requested.connect(self.set_fullscreen)

        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

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
        self.central_widget.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setCentralWidget(self.central_widget)

        # 1. Base Layer: Video Viewport OpenGL Canvas
        self.video_viewport = VideoViewportWidget(self.central_widget)
        self.video_viewport.setGeometry(0, 0, 1280, 656)

        # 2. Overlay Disconnected Clock Screen with 2x2 Grid Layout
        self.disconnected_screen = QWidget(self.central_widget)
        self.disconnected_screen.setObjectName("disconnected-screen")
        self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.disconnected_screen.setGeometry(0, 0, 1280, 656)

        # Grid Widgets inside disconnected_screen
        self.clock_widget = AnalogClockWidget(self.disconnected_screen)
        self.nav_widget = NavCardWidget(self.disconnected_screen)
        self.media_widget = MediaCardWidget(self.disconnected_screen)
        self.phone_call_widget = PhoneCallWidget(self.disconnected_screen)
        self.notification_card = NotificationCardWidget(self.disconnected_screen)

        self.phone_call_widget.hide()
        self.notification_card.hide()

        self.has_active_nav = False
        self.has_active_media = False
        self.has_active_call = False
        self.has_notifications = False

        # 3. Floating Bottom Command Bar
        self.command_bar = CommandBarWidget(self.central_widget)

        # 4. Arc Radial FAB Action Menu
        self.arc_menu = ArcRadialMenuWidget(self.central_widget)

        # 5. Volume Popover Card
        self.volume_popover = VolumePopoverWidget(self.central_widget)

        # 6. Floating Top-Center Toast Notification Banner
        self.toast_widget = ToastNotificationWidget(self.central_widget)
        self.notification_toast = NotificationToast(self.central_widget)

        # 7. Slide-Over Drawers
        self.phone_drawer = PhoneDrawerWidget(self.central_widget)
        self.bluetooth_drawer = BluetoothDrawerWidget(self.central_widget)
        self.settings_drawer = SettingsDrawerWidget(self.central_widget)
        self.logs_drawer = LogsDrawerWidget(self.central_widget)
        self.diagnostics_drawer = DiagnosticsDrawerWidget(self.central_widget)

        self._connect_signals()

        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def _connect_signals(self):
        # Command Bar button signals
        self.command_bar.home_clicked.connect(self._toggle_clock_overlay)
        self.command_bar.playpause_clicked.connect(self.media_playpause_requested.emit)
        self.command_bar.volume_clicked.connect(self._toggle_volume_popover)
        self.command_bar.menu_clicked.connect(self.arc_menu.toggle_menu)
        self.command_bar.exit_clicked.connect(self.close_app_requested.emit)

        # Arc Menu drawer toggle signals
        self.arc_menu.phone_clicked.connect(lambda: self._toggle_drawer(self.phone_drawer))
        self.arc_menu.bluetooth_clicked.connect(lambda: self._toggle_drawer(self.bluetooth_drawer))
        self.arc_menu.settings_clicked.connect(lambda: self._toggle_drawer(self.settings_drawer))
        self.arc_menu.wifi_clicked.connect(self._on_wifi_restart)
        self.arc_menu.logs_clicked.connect(lambda: self._toggle_drawer(self.logs_drawer))
        self.arc_menu.diagnostics_clicked.connect(lambda: self._toggle_drawer(self.diagnostics_drawer))
        self.arc_menu.fullscreen_clicked.connect(self._toggle_fullscreen)

        # Drawer close signals
        self.phone_drawer.close_clicked.connect(self.phone_drawer.hide)
        self.bluetooth_drawer.close_clicked.connect(self.bluetooth_drawer.hide)
        self.settings_drawer.close_clicked.connect(self.settings_drawer.hide)
        self.logs_drawer.close_clicked.connect(self.logs_drawer.hide)
        self.diagnostics_drawer.close_clicked.connect(self.diagnostics_drawer.hide)

        # Phone signals
        self.phone_drawer.call_action_triggered.connect(self.phone_action_requested.emit)
        self.phone_call_widget.action_triggered.connect(self.phone_action_requested.emit)
        self.command_bar.call_action_triggered.connect(self.phone_action_requested.emit)

        # Clock Home Screen signals
        self.clock_widget.connect_phone_clicked.connect(lambda: self._toggle_drawer(self.bluetooth_drawer))

    def _on_wifi_restart(self):
        """Trigger WiFi hotspot restart via connectivity_manager."""
        self.toast_widget.show_toast("Restarting WiFi Hotspot AP...", "info")
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8000/api/connectivity/wifi/start", method="POST")
            urllib.request.urlopen(req, timeout=3.0)
        except Exception as exc:
            logger.debug("WiFi restart notice: %s", exc)

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

        self.focus_toggle_requested.emit(target_mode)

    def _toggle_volume_popover(self):
        self.volume_popover.setVisible(not self.volume_popover.isVisible())
        if self.volume_popover.isVisible():
            self.volume_popover.raise_()

    def _on_hardware_volume_key(self, action: str):
        """Handle hardware physical volume buttons (VolumeUp, VolumeDown, VolumeMute) with auto-hiding OSD."""
        if hasattr(self, "volume_popover") and self.volume_popover:
            self.volume_popover._on_vol_click(action)
            self.volume_popover.show()
            self.volume_popover.raise_()
            if not hasattr(self, "_volume_hud_timer") or self._volume_hud_timer is None:
                self._volume_hud_timer = QTimer(self)
                self._volume_hud_timer.setSingleShot(True)
                self._volume_hud_timer.timeout.connect(self.volume_popover.hide)
            self._volume_hud_timer.start(2500)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_VolumeUp:
                self._on_hardware_volume_key("up")
                return True
            elif key == Qt.Key.Key_VolumeDown:
                self._on_hardware_volume_key("down")
                return True
            elif key == Qt.Key.Key_VolumeMute:
                self._on_hardware_volume_key("mute")
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_VolumeUp:
            self._on_hardware_volume_key("up")
            event.accept()
            return
        elif key == Qt.Key.Key_VolumeDown:
            self._on_hardware_volume_key("down")
            event.accept()
            return
        elif key == Qt.Key.Key_VolumeMute:
            self._on_hardware_volume_key("mute")
            event.accept()
            return
        super().keyPressEvent(event)

    def _toggle_drawer(self, target_drawer: QWidget):
        if self.arc_menu:
            self.arc_menu.collapse()
        for drawer in (self.phone_drawer, self.bluetooth_drawer, self.settings_drawer, self.logs_drawer, self.diagnostics_drawer):
            if drawer == target_drawer:
                drawer.setVisible(not drawer.isVisible())
                if drawer.isVisible():
                    drawer.raise_()
            else:
                drawer.hide()

    def set_fullscreen(self, enabled: bool):
        if enabled:
            if not self.isFullScreen():
                self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
                self.showFullScreen()
        else:
            if self.isFullScreen():
                self.setWindowFlags(Qt.WindowType.Window)
                self.showNormal()
            elif not self.isVisible():
                self.show()

    def _toggle_fullscreen(self):
        self.set_fullscreen(not self.isFullScreen())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        h = self.height()
        cmd_h = 64
        draw_h = max(100, h - cmd_h)

        # Resize video canvas and disconnected clock screen strictly above the bottom bar
        self.video_viewport.setGeometry(0, 0, w, draw_h)
        self.video_viewport.set_margins(margin_width=0, margin_height=0, stretch_to_fill=False)
        self.disconnected_screen.setGeometry(0, 0, w, draw_h)
        self._relayout_dashboard(w, draw_h)

        # Position Command Bar (full-width docked bottom bar)
        self.command_bar.setGeometry(0, h - cmd_h, w, cmd_h)

        # Position Arc Radial FAB menu (anchored above the bottom right menu button)
        arc_w = 280
        arc_h = 280
        self.arc_menu.setGeometry(w - arc_w - 6, h - cmd_h - arc_h + 36, arc_w, arc_h)

        # Position Volume Popover (above command bar)
        vol_w = 260
        vol_h = 160
        self.volume_popover.setGeometry((w - vol_w) // 2, h - cmd_h - vol_h - 16, vol_w, vol_h)

        # Position Floating Toast Banner (top center)
        self.toast_widget.setGeometry((w - 380) // 2, 20, 380, 44)
        self.notification_toast.setGeometry((w - 420) // 2, 20, 420, 72)

        # Position Slide-Over Drawers (almost full-screen cards: 30px margin on all sides, strictly above bottom command bar)
        margin = 30
        drawer_x = margin
        drawer_y = margin
        drawer_w = max(100, w - (margin * 2))
        drawer_h = max(100, draw_h - (margin * 2))
        for drawer in (self.phone_drawer, self.bluetooth_drawer, self.settings_drawer, self.logs_drawer, self.diagnostics_drawer):
            drawer.setGeometry(drawer_x, drawer_y, drawer_w, drawer_h)

    def _relayout_dashboard(self, w: int, h: int):
        """Cockpit 2x2 Grid Layout for Connected/Clock Screen ensuring all widgets are displayed."""
        self.disconnected_screen.setGeometry(0, 0, w, h)
        margin_x = 36
        margin_y = 24
        avail_w = w - (margin_x * 2)
        avail_h = h - (margin_y * 2)

        has_nav = self.has_active_nav
        has_call = self.has_active_call
        has_media = self.has_active_media

        if not self._is_connected and not has_media and not has_nav and not has_call:
            # Idle Disconnected Mode: Center the Clock Card prominently
            clock_w = min(560, avail_w)
            self.clock_widget.show()
            self.clock_widget.setGeometry((w - clock_w) // 2, margin_y, clock_w, avail_h)
            self.media_widget.hide()
            self.nav_widget.hide()
            self.notification_card.hide()
            self.phone_call_widget.hide()
            return

        col_w = (avail_w - 24) // 2
        row_h = (avail_h - 24) // 2

        # 1. Right Column: Media Card (Top) & Notification/Nav Card (Bottom)
        self.media_widget.show()
        self.media_widget.setGeometry(margin_x + col_w + 24, margin_y, col_w, row_h)

        if has_nav:
            self.nav_widget.show()
            self.notification_card.hide()
            self.nav_widget.setGeometry(margin_x + col_w + 24, margin_y + row_h + 24, col_w, row_h)
        else:
            self.nav_widget.hide()
            self.notification_card.show()
            self.notification_card.setGeometry(margin_x + col_w + 24, margin_y + row_h + 24, col_w, row_h)

        # 2. Left Column: Clock Card (Full or Top) & Phone Call Card
        if has_call:
            self.phone_call_widget.show()
            self.clock_widget.show()
            self.clock_widget.setGeometry(margin_x, margin_y, col_w, row_h)
            self.phone_call_widget.setGeometry(margin_x, margin_y + row_h + 24, col_w, row_h)
        else:
            self.phone_call_widget.hide()
            self.clock_widget.show()
            self.clock_widget.setGeometry(margin_x, margin_y, col_w, avail_h)

    def update_dashboard_state(self, has_nav: bool = False, has_media: bool = False, has_call: bool = False):
        """Update active flags and trigger dashboard relayout."""
        self.has_active_nav = has_nav
        self.has_active_media = has_media
        self.has_active_call = has_call
        cmd_h = self.command_bar.height() if hasattr(self, "command_bar") else 64
        draw_h = max(100, self.height() - cmd_h)
        self._relayout_dashboard(self.width(), draw_h)

    def set_connected_state(self, is_connected: bool, is_disconnect: bool = False):
        """Update connection state: hide clock on projection, re-show on disconnect."""
        self._is_connected = is_connected
        self.command_bar.set_online_status(is_connected)
        if is_connected:
            if self.isVideoFocused:
                self.disconnected_screen.hide()
                self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        elif is_disconnect:
            self.isVideoFocused = False
            self.disconnected_screen.show()
            self.disconnected_screen.raise_()
            self.command_bar.raise_()
            self.arc_menu.raise_()
            self.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def closeEvent(self, event):
        if hasattr(self, "video_viewport") and hasattr(self.video_viewport, "cleanupGL"):
            try:
                self.video_viewport.cleanupGL()
            except Exception:
                pass
        self.close_app_requested.emit()
        super().closeEvent(event)
