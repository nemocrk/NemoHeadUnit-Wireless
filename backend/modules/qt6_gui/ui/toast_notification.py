"""
toast_notification.py — Floating Top-Center Toast Notification Banner Widget for Qt6 GUI.

Provides single-toast stack notification banners floating at top-center, matching HTML5 WebClient:
- Automatic dismissal after 3.5s
- Single active toast at a time (clears previous toast immediately on new arrival)
- Color badges: success 🟢, warning 🟡, error 🔴, info 🔵
"""

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

COLOR_THEMES = {
    "info": {"bg": "#161b22", "border": "#388bfd", "icon": "🔵", "text": "#58a6ff"},
    "success": {"bg": "#161b22", "border": "#2ea043", "icon": "🟢", "text": "#3fb950"},
    "warning": {"bg": "#161b22", "border": "#d29922", "icon": "🟡", "text": "#d29922"},
    "error": {"bg": "#161b22", "border": "#f85149", "icon": "🔴", "text": "#f85149"},
}


class ToastNotificationWidget(QWidget):
    """
    Single-Toast Stack Floating Notification Banner Widget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast-notification")

        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.hide)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(16, 8, 16, 8)
        self.layout.setSpacing(10)

        # Icon Badge Label
        self.lbl_icon = QLabel("🔵", self)
        self.lbl_icon.setStyleSheet("font-size: 14px;")
        self.layout.addWidget(self.lbl_icon)

        # Message Text Label
        self.lbl_text = QLabel("System Notification", self)
        self.lbl_text.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.layout.addWidget(self.lbl_text, 1)

        self.hide()

    def show_toast(self, message: str, toast_type: str = "info", duration_ms: int = 3500, icon: str = None):
        """
        Display floating top-center toast banner.
        Every new toast immediately clears previous active toasts (matching WebClient showToast).
        """
        if self.dismiss_timer.isActive():
            self.dismiss_timer.stop()

        theme = COLOR_THEMES.get(toast_type, COLOR_THEMES["info"])

        self.setStyleSheet(f"""
            #toast-notification {{
                background-color: {theme['bg']};
                border: 1px solid {theme['border']};
                border-radius: 20px;
            }}
            QLabel {{
                color: {theme['text']};
            }}
        """)

        self.lbl_icon.setText(icon if icon else theme["icon"])
        self.lbl_text.setText(message)


        self.adjustSize()
        self.show()
        self.raise_()

        if duration_ms > 0:
            self.dismiss_timer.start(duration_ms)
