import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import unittest
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.ui.notification_widget import (
    NotificationToast,
    NotificationCardWidget,
)


class TestNotificationWidgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_notification_toast(self):
        toast = NotificationToast()
        toast.show_notification("notif_1", "Test Title", "Test Text", "App", duration_ms=1000)
        # Verify dismissal
        dismissed = []
        toast.dismissed.connect(lambda nid: dismissed.append(nid))
        toast.notif_id = "notif_1"
        toast._on_dismiss_clicked()
        assert dismissed == ["notif_1"]
        assert not toast.isVisible()

    def test_notification_card(self):
        card = NotificationCardWidget()
        card.show()
        assert card.lbl_empty.isVisible()
        assert not card.list_widget.isVisible()

        # Add notifications
        card.add_notification("n1", "Hello", "World", "System")
        card.add_notification("n2", "Alert", "Warning", "Car")
        assert card.list_widget.count() == 2
        assert not card.lbl_empty.isVisible()
        assert card.list_widget.isVisible()

        # Remove single notification
        card.remove_notification("n1")
        assert card.list_widget.count() == 1

        # Clear all
        card.clear_all()
        assert card.list_widget.count() == 0
        assert card.lbl_empty.isVisible()

    def test_toast_notification_widget(self):
        from backend.modules.qt6_gui.ui.toast_notification import ToastNotificationWidget
        toast = ToastNotificationWidget()
        toast.show_toast("Info Message", "info", 1000)
        self.app.processEvents()
        self.assertEqual(toast.lbl_text.text(), "Info Message")

        toast.show_toast("Success Message", "success", 1000)
        self.app.processEvents()
        self.assertEqual(toast.lbl_text.text(), "Success Message")

        toast.show_toast("Warning Message", "warning", 1000)
        self.app.processEvents()
        self.assertEqual(toast.lbl_text.text(), "Warning Message")

        toast.show_toast("Error Message", "error", 1000)
        self.app.processEvents()
        self.assertEqual(toast.lbl_text.text(), "Error Message")

