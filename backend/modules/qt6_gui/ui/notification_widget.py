"""
notification_widget.py — Dedicated Notification Toast and Dashboard Card Widgets for Qt6 GUI.

Provides animated Heads-Up floating alerts and persistent dashboard notification cards.
"""

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal, QPropertyAnimation, QPoint
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
)

from .svg_utils import make_svg_icon


class NotificationToast(QFrame):
    """
    Floating animated slide-down toast for incoming notifications.
    """

    dismissed = pyqtSignal(str)  # notif_id
    _show_notification_signal = pyqtSignal(str, str, str, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notification-toast")
        self.notif_id = ""

        self._show_notification_signal.connect(self._do_show_notification, Qt.ConnectionType.QueuedConnection)
        self.setFixedWidth(420)
        self.setFixedHeight(72)
        self.setStyleSheet("""
            QFrame#notification-toast {
                background: rgba(22, 27, 34, 0.95);
                border: 1px solid #30363d;
                border-radius: 12px;
            }
        """)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(14, 10, 14, 10)
        self.layout.setSpacing(12)

        self.icon_lbl = QLabel(self)
        self.icon_lbl.setPixmap(make_svg_icon("alert", color="#58a6ff", size=22).pixmap(22, 22))
        self.layout.addWidget(self.icon_lbl)

        self.text_layout = QVBoxLayout()
        self.text_layout.setSpacing(2)

        self.lbl_title = QLabel("WhatsApp • John", self)
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #f0f6fc;")
        self.text_layout.addWidget(self.lbl_title)

        self.lbl_text = QLabel("Hey, are you on your way?", self)
        self.lbl_text.setStyleSheet("font-size: 12px; color: #8b949e;")
        self.text_layout.addWidget(self.lbl_text)

        self.layout.addLayout(self.text_layout)
        self.layout.addStretch()

        self.btn_dismiss = QPushButton(self)
        self.btn_dismiss.setIcon(make_svg_icon("close", color="#8b949e", size=16))
        self.btn_dismiss.setIconSize(QSize(16, 16))
        self.btn_dismiss.setStyleSheet("background: transparent; border: none; padding: 4px;")
        self.btn_dismiss.clicked.connect(self._on_dismiss_clicked)
        self.layout.addWidget(self.btn_dismiss)

        self.auto_timer = QTimer(self)
        self.auto_timer.setSingleShot(True)
        self.auto_timer.timeout.connect(self.hide)

        self.hide()

    def show_notification(self, notif_id: str, title: str, text: str, app_name: str = "Alert", duration_ms: int = 6000):
        self._show_notification_signal.emit(notif_id, title, text, app_name, duration_ms)

    def _do_show_notification(self, notif_id: str, title: str, text: str, app_name: str = "Alert", duration_ms: int = 6000):
        self.notif_id = notif_id
        display_title = f"{app_name} • {title}" if app_name and app_name != title else title
        self.lbl_title.setText(display_title)
        self.lbl_text.setText(text)

        # Center at top of parent window
        if self.parent():
            pw = self.parent().width()
            self.move((pw - self.width()) // 2, 16)

        self.show()
        self.raise_()
        self.auto_timer.start(duration_ms)

    def _on_dismiss_clicked(self):
        self.auto_timer.stop()
        self.hide()
        self.dismissed.emit(self.notif_id)


class NotificationCardWidget(QFrame):
    """
    Persistent notification feed card on the Clock / Dashboard screen.
    """

    action_triggered = pyqtSignal(str, str)  # (notif_id, action_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notification-card")
        self.setProperty("class", "dash-card")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(10)

        # Header
        self.header_layout = QHBoxLayout()
        self.icon_bell = QLabel(self)
        self.icon_bell.setPixmap(make_svg_icon("alert", color="#58a6ff", size=18).pixmap(18, 18))
        self.header_layout.addWidget(self.icon_bell)

        self.lbl_card_title = QLabel("Notifications", self)
        self.lbl_card_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #f0f6fc;")
        self.header_layout.addWidget(self.lbl_card_title)
        self.header_layout.addStretch()

        self.btn_clear = QPushButton("Clear", self)
        self.btn_clear.setStyleSheet("font-size: 11px; color: #8b949e; background: transparent; border: none;")
        self.btn_clear.clicked.connect(self.clear_all)
        self.header_layout.addWidget(self.btn_clear)

        self.layout.addLayout(self.header_layout)

        # Notifications List
        self.list_widget = QListWidget(self)
        self.list_widget.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                color: #c9d1d9;
            }
            QListWidget::item {
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                padding: 8px 4px;
            }
            QListWidget::item:hover {
                background: rgba(56, 189, 248, 0.15);
                border-radius: 6px;
            }
        """)
        self.layout.addWidget(self.list_widget)

        # Empty State Placeholder Label
        self.lbl_empty = QLabel("No new notifications\nAll systems nominal", self)
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 500; padding: 24px;")
        self.layout.addWidget(self.lbl_empty)
        self._update_empty_state()

    def _update_empty_state(self):
        has_items = self.list_widget.count() > 0
        self.list_widget.setVisible(has_items)
        self.lbl_empty.setVisible(not has_items)
        self.btn_clear.setVisible(has_items)

    def add_notification(self, notif_id: str, title: str, text: str, app_name: str = "Alert"):
        item = QListWidgetItem()
        item.setText(f"🔔 [{app_name}] {title}\n    {text}")
        item.setData(Qt.ItemDataRole.UserRole, notif_id)
        self.list_widget.insertItem(0, item)

        while self.list_widget.count() > 10:
            self.list_widget.takeItem(self.list_widget.count() - 1)
        self._update_empty_state()

    def remove_notification(self, notif_id: str):
        for idx in range(self.list_widget.count()):
            item = self.list_widget.item(idx)
            if item and item.data(Qt.ItemDataRole.UserRole) == notif_id:
                self.list_widget.takeItem(idx)
                break
        self._update_empty_state()

    def clear_all(self):
        self.list_widget.clear()
        self._update_empty_state()
