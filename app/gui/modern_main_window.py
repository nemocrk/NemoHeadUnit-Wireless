"""
Modern Main Application Window for NemoHeadUnit-Wireless

Provides a modern, car-head unit inspired GUI with PyQt6.
"""

import sys
import logging
from typing import Optional
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QStackedWidget, QFrame, QPushButton, QSlider, QMenu, QWidgetAction, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, QTime, QPoint, QRect, QSize
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QPolygon


from app.connection import ConnectionManager
from app.logger import LoggerManager
from app.message_bus import MessageBus
from app.gui.components.connection_tab import ConnectionTab
from app.gui.components.media_tab import MediaTab
from app.gui.components.config_tab import ConfigTab
from app.gui.components.status_tab import StatusTab
from app.gui.components.equalizer_tab import EqualizerTab

class AnalogClock(QWidget):
    """Custom stylized analog clock for the principal tab."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(1000)
        self.setMinimumSize(400, 400)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        side = min(self.width(), self.height())
        painter.scale(side / 200.0, side / 200.0)

        time = QTime.currentTime()

        # Hour hand
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#4caf50"))
        painter.save()
        painter.rotate(30.0 * ((time.hour() + time.minute() / 60.0)))
        painter.drawConvexPolygon(QPolygon([QPoint(6, 7), QPoint(-6, 7), QPoint(0, -50)]))
        painter.restore()

        # Minute hand
        painter.setBrush(QColor("#2196f3"))
        painter.save()
        painter.rotate(6.0 * (time.minute() + time.second() / 60.0))
        painter.drawConvexPolygon(QPolygon([QPoint(4, 7), QPoint(-4, 7), QPoint(0, -80)]))
        painter.restore()

        # Second hand
        painter.setPen(QPen(QColor("#ff6b6b"), 1))
        painter.save()
        painter.rotate(6.0 * time.second())
        painter.drawLine(0, 10, 0, -90)
        painter.restore()

        # Marks
        painter.setPen(QPen(QColor("#ffffff"), 2))
        for i in range(12):
            painter.drawLine(88, 0, 96, 0)
            painter.rotate(30.0)


def create(message_bus: Optional[MessageBus] = None,
           connection_manager: Optional[ConnectionManager] = None,
           run_event_loop: bool = False):
    """Utility function to launch the main window
    
    Args:
        message_bus: Message bus instance for event communication
        connection_manager: Connection manager instance
        run_event_loop: If True, start Qt event loop (blocks); if False, just create window
    
    Returns:
        If run_event_loop=True: event loop exit code
        If run_event_loop=False: MainWindow instance
    """
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(message_bus=message_bus,
                        connection_manager=connection_manager,
                        app=app)
    window.show()
    
    if run_event_loop:
        # Start event loop (blocks until window is closed)
        return app.exec()
    else:
        # Just return window for initialization phase
        return window


class MainWindow(QMainWindow):
    """Main application window with modern car-inspired design"""

    def __init__(self, message_bus: Optional[MessageBus] = None,
                 connection_manager: Optional[ConnectionManager] = None,
                 app: Optional[QApplication] = None):
        super().__init__()
        self.logger = LoggerManager.get_logger('app.gui.main_window')
        self.logger.info("Initializing Modern MainWindow")

        # Store dependencies
        self._bus = message_bus
        self._connection = connection_manager
        self._app = app

        # Subscribe to bus events
        self._subscribe_to_events()

        # Initialize modern UI
        self._init_ui()

    def _subscribe_to_events(self):
        """Subscribe to message bus events"""
        self.logger.debug("Subscribing to message bus events")
        if self._bus:
            self._bus.on("connection.status", self._on_connection_status_changed)
            self._bus.on("media.status", self._on_media_status_changed)
            self._bus.on("config.settings", self._on_config_changed)
            self.logger.info("Subscribed to message bus events")

    def _on_connection_status_changed(self, payload: dict):
        """Handle connection status changes from message bus"""
        status = payload.get("status") if isinstance(payload, dict) else payload
        method = payload.get("method", "Bluetooth") if isinstance(payload, dict) else "Bluetooth"

        self.logger.debug(f"Connection status changed: {status}, method: {method}")
        self.set_connection_status(status, method)

    def _on_media_status_changed(self, payload: dict):
        """Handle media status changes from message bus"""
        if isinstance(payload, dict):
            status = payload.get("status")
            channel = payload.get("channel")
            self.logger.debug(f"Media status changed: {status} for channel: {channel}")
            self.set_media_status(status, channel)

    def _on_config_changed(self, settings: dict):
        """Handle configuration changes from message bus"""
        self.logger.debug(f"Configuration changed: {settings}")
        self.logger.info("Updating UI with new configuration")

    def _init_ui(self):
        """Initialize the modern main UI components"""
        self.logger.debug("Setting up modern UI components")
        self.setWindowTitle("NemoHeadUnit-Wireless")
        # Target 10-inch 1080p resolution
        self.setMinimumSize(1280, 800) 
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f0f1b;
                color: #ffffff;
                font-size: 20px;
            }
            QLabel { font-size: 20px; }
            QPushButton { 
                border-radius: 12px; 
                background-color: #2d2d44; 
                color: #ffffff;
                font-size: 28px; 
                font-weight: bold;
            }
            QMenu {
                background-color: #1e1e2e;
                border: 1px solid #4caf50;
                border-radius: 15px;
                padding: 10px;
            }
            QSlider::groove:horizontal {
                height: 12px;
                background: #2d2d44;
                border-radius: 6px;
            }
            QSlider::groove:vertical {
                width: 12px;
                background: #2d2d44;
                border-radius: 6px;
            }
            QSlider::handle:horizontal {
                background: #4caf50;
                width: 48px;
                height: 48px;
                margin: -18px 0;
                border-radius: 24px;
            }
            QSlider::handle:vertical {
                background: #4caf50;
                width: 48px;
                height: 48px;
                margin: 0 -18px;
                border-radius: 24px;
            }
        """)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Content Stack
        self.content_stack = QStackedWidget()
        
        # 1. Principal Tab (Home)
        self.home_page = QWidget()
        home_layout = QVBoxLayout(self.home_page)
        self.clock = AnalogClock()
        home_layout.addWidget(self.clock, alignment=Qt.AlignmentFlag.AlignCenter)
        
        self.home_status = QLabel("System Ready | Disconnected")
        self.home_status.setStyleSheet("font-size: 24px; color: #888; margin-top: 20px;")
        home_layout.addWidget(self.home_status, alignment=Qt.AlignmentFlag.AlignCenter)
        
        self.content_stack.addWidget(self.home_page)

        # 2. Settings Stack (Integrated Pages)
        self.settings_container = QTabWidget()
        self.settings_container.setStyleSheet("QTabBar::tab { height: 60px; width: 150px; font-size: 16px; }")
        
        self.tab_config = ConfigTab(message_bus=self._bus)
        self.tab_connection = ConnectionTab(message_bus=self._bus)
        self.tab_media = MediaTab(message_bus=self._bus)
        self.tab_status = StatusTab(message_bus=self._bus)
        self.tab_eq = EqualizerTab()

        self.settings_container.addTab(self.tab_config, "Display")
        self.settings_container.addTab(self.tab_connection, "Connection")
        self.settings_container.addTab(self.tab_media, "Media")
        self.settings_container.addTab(self.tab_status, "System")
        self.settings_container.addTab(self.tab_eq, "Equalizer")
        
        self.content_stack.addWidget(self.settings_container)
        main_layout.addWidget(self.content_stack, 1) # Pushes nav bar to bottom

        # Navigation Bar
        nav_bar = QFrame()
        nav_bar.setFixedHeight(100) # Taller for touch
        nav_bar.setStyleSheet("background-color: #1e1e2e; border-top: 1px solid #333;")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(20, 10, 20, 10)
        nav_layout.setSpacing(15)

        # 1. Volume Button
        self.volume_btn = QPushButton("🔊")
        self.volume_btn.setFixedSize(80, 80)
        self.volume_btn.clicked.connect(self._show_volume_popover)
        nav_layout.addWidget(self.volume_btn)

        # 2. Brightness Button
        self.brightness_btn = QPushButton("🔆")
        self.brightness_btn.setFixedSize(80, 80)
        self.brightness_btn.clicked.connect(self._show_brightness_popover)
        nav_layout.addWidget(self.brightness_btn)

        # 3. Reproduction Status
        self.reproduction_status = QLabel("No Media Playing")
        self.reproduction_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reproduction_status.setStyleSheet("font-size: 22px; font-weight: bold; color: #4caf50;")
        nav_layout.addWidget(self.reproduction_status, 1)

        # 4. Gear (Settings Toggle)
        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setFixedSize(80, 80)
        self.gear_btn.clicked.connect(self._toggle_settings)
        nav_layout.addWidget(self.gear_btn)

        main_layout.addWidget(nav_bar)

        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1000)

    def _show_volume_popover(self):
        self._show_slider_popover("Volume", self.volume_btn)

    def _show_brightness_popover(self):
        self._show_slider_popover("Brightness", self.brightness_btn)

    def _show_slider_popover(self, title, button):
        menu = QMenu(self)
        container = QWidget()
        layout = QVBoxLayout(container)
        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)
        slider = QSlider(Qt.Orientation.Vertical)
        slider.setFixedSize(80, 300) # Touch-friendly popover slider
        layout.addWidget(slider)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        menu.exec(button.mapToGlobal(QPoint(0, -280)))

    def _toggle_settings(self):
        new_idx = 1 if self.content_stack.currentIndex() == 0 else 0
        self.content_stack.setCurrentIndex(new_idx)
        self.gear_btn.setStyleSheet("background-color: #4caf50; color: white;" if new_idx == 1 else "")

    def update_status(self):
        """Update status indicators"""
        self.logger.debug("Updating status indicators")
        import random
        cpu = random.randint(0, 30)
        mem = random.randint(10, 40)

        # Update Home page status info
        self.home_status.setText(f"System Active | CPU: {cpu}% | RAM: {mem}%")

        # Update system status
        self.tab_status.system_status.setText("Active")
        self.tab_status.system_status.setStyleSheet("font-size: 24px; font-weight: bold; color: #4caf50; padding: 10px;")
        self.tab_status.cpu_usage.setText(f"{cpu}%")
        self.tab_status.memory_usage.setText(f"{mem}%")
        self.logger.debug("Status update complete")

    def set_connection_status(self, status: str, method: str = "Bluetooth"):
        """Set connection status and update UI"""
        self.logger.debug(f"Setting connection status: {status}, method: {method}")
        self.tab_connection.set_connection_status(status, method)
        self.logger.debug(f"Connection status updated: {status}")

    def set_media_status(self, status: str, channel: str):
        """Set media channel status"""
        self.logger.debug(f"Setting media status: {status} for channel: {channel}")
        
        # Update the main navigation bar reproduction status
        if status == "Active":
            self.reproduction_status.setText(f"Playing: {channel}")
            self.reproduction_status.setStyleSheet("font-size: 22px; font-weight: bold; color: #4caf50;")
        else:
            self.reproduction_status.setText("No Media Playing")
            self.reproduction_status.setStyleSheet("font-size: 22px; font-weight: bold; color: #888;")

        # Update the status in the Media tab labels
        attr_name = f"{channel.lower()}_status"
        if not hasattr(self.tab_media, attr_name):
            return

        label = getattr(self.tab_media, attr_name)
        label.setText(status)
        label.setStyleSheet(
            f"font-size: 24px; font-weight: bold; color: {'#4caf50' if status == 'Active' else '#ff6b6b'};"
        )
        self.logger.debug(f"Media status updated: {status} for {channel}")