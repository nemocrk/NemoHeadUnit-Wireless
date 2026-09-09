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

    def test_media_card_creation_and_states(self):
        widget = MediaCardWidget()
        action_mock = unittest.mock.MagicMock()
        widget.media_action_requested.connect(action_mock)

        # 1. Active playing state with position (Android Auto proto: 2 = PLAYING)
        widget.update_metadata("Test Song", "Test Artist", "Test Album", "", 2, "spotify", 125)
        self.assertEqual(widget.title_label.text(), "Test Song")
        self.assertEqual(widget.artist_label.text(), "Test Artist")
        self.assertIn("NOW PLAYING", widget.badge_label.text())
        self.assertIn("2:05", widget.album_label.text())

        # 2. Paused state (Android Auto proto: 3 = PAUSED)
        widget.update_metadata(playback_state=3)
        self.assertIn("PAUSED", widget.badge_label.text())

        # 3. Idle / empty state
        widget.update_metadata("", "", "", "", 0)
        self.assertEqual(widget.title_label.text(), "No Active Media")
        self.assertEqual(widget.badge_label.text(), "IDLE")

        # 4. Button clicks (85=play/pause, 87=next, 88=prev)
        widget.btn_playpause.click()
        action_mock.assert_called_with(85)
        widget.btn_next.click()
        action_mock.assert_called_with(87)
        widget.btn_prev.click()
        action_mock.assert_called_with(88)


    def test_nav_card_creation_and_maneuvers(self):
        widget = NavCardWidget()

        # 1. Meters & min ETA
        widget.update_navigation("Main Street", 450.0, 1, 1, "", 180)
        self.assertEqual(widget.road_label.text(), "Main Street")
        self.assertEqual(widget.distance_label.text(), "450 m")
        self.assertEqual(widget.eta_label.text(), "ETA: 3 min")

        # 2. Km distance and hour+min ETA
        widget.update_navigation("Highway 101", 15400.0, 6, 2, "", 3900)
        self.assertEqual(widget.distance_label.text(), "15.4 km")
        self.assertEqual(widget.eta_label.text(), "ETA: 1h 5m")

        # 3. Inactive / no route
        widget.update_navigation("", -1.0, 0, 0, "", 0)
        self.assertEqual(widget.road_label.text(), "No Route Active")
        self.assertEqual(widget.distance_label.text(), "—")

        # 4. Vector arrow types (U-turn, roundabout, destination, left, right)
        widget._draw_maneuver_icon(11, 1)  # U-turn
        widget._draw_maneuver_icon(30, 0)  # Roundabout
        widget._draw_maneuver_icon(39, 0)  # Destination
        widget._draw_maneuver_icon(5, 1)   # Left
        widget._draw_maneuver_icon(6, 2)   # Right
        widget._draw_maneuver_icon(0, 0)   # Straight fallback


if __name__ == "__main__":
    unittest.main()

