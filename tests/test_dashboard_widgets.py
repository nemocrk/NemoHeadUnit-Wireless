import unittest
from backend.modules.qt6_gui.ui.media_card_widget import MediaCardWidget
from backend.modules.qt6_gui.ui.nav_card_widget import NavCardWidget
from PyQt6.QtWidgets import QApplication
import sys

class TestDashboardWidgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_media_card_creation(self):
        widget = MediaCardWidget()
        widget.update_metadata("Test Song", "Test Artist", "Test Album", "", 2)
        self.assertEqual(widget.title_label.text(), "Test Song")
        self.assertEqual(widget.artist_label.text(), "Test Artist")
        self.assertEqual(widget.badge_label.text(), "NOW PLAYING")

    def test_nav_card_creation(self):
        widget = NavCardWidget()
        widget.update_navigation("Main Street", 450.0, 1, 1, "", 180)
        self.assertEqual(widget.road_label.text(), "Main Street")
        self.assertEqual(widget.distance_label.text(), "450 m")
        self.assertEqual(widget.eta_label.text(), "ETA: 3 min")

if __name__ == "__main__":
    unittest.main()
