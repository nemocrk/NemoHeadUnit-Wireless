import os
import json
import unittest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QSpinBox, QLineEdit, QPushButton
from backend.modules.qt6_gui.ui.drawers.settings_drawer import (
    SettingsDrawerWidget,
    DragScrollArea,
    ConfigFetchThread,
    ConfigSaveThread,
)


class TestSettingsDrawer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_scroll_area(self):
        drawer = SettingsDrawerWidget()
        self.assertIsInstance(drawer.form_scroll, DragScrollArea)
        self.assertIsInstance(drawer.tabs_scroll, DragScrollArea)

        # Test wheelEvent
        ev = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        drawer.form_scroll.wheelEvent(ev)

        # Test eventFilter mouse press, move, release
        btn = QPushButton("Test", drawer)
        drawer.form_scroll.register_child(btn)

        press_ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        drawer.form_scroll.eventFilter(btn, press_ev)

        move_ev = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(10, 50),
            QPointF(10, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        drawer.form_scroll.eventFilter(btn, move_ev)

        release_ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(10, 50),
            QPointF(10, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        drawer.form_scroll.eventFilter(btn, release_ev)

    def test_channels_interactive_rendering_and_forms(self):
        drawer = SettingsDrawerWidget()
        mock_data = {
            "channel_manager": {
                "config": {
                    "head_unit_name": "NemoTest",
                    "driver_position": "LEFT",
                    "channels": [
                        {
                            "channel_id": 1,
                            "input_channel": {
                                "touch_screen_configs": [{"width": 1280, "height": 720}],
                                "supported_keycodes": [85, 87, 88],
                            },
                        },
                        {
                            "channel_id": 2,
                            "sensor_channel": {
                                "sensors": [{"type": "NIGHT_DATA"}, {"type": "DRIVING_STATUS"}],
                            },
                        },
                        {
                            "channel_id": 3,
                            "av_channel": {
                                "codec": "MEDIA_CODEC_VIDEO_H264_BP",
                                "video_configs": [
                                    {
                                        "video_resolution": "VIDEO_1280x720",
                                        "video_fps": "_30",
                                        "dpi": 140,
                                    }
                                ],
                            },
                        },
                        {
                            "channel_id": 4,
                            "av_channel": {
                                "audio_type": "MEDIA",
                                "sample_rate": 48000,
                                "number_of_channels": 2,
                            },
                        },
                        {
                            "channel_id": 5,
                            "av_input_channel": {},
                        },
                        {
                            "channel_id": 6,
                            "bluetooth_channel": {},
                        },
                        {
                            "channel_id": 7,
                            "wifi_channel": {},
                        },
                        {
                            "channel_id": 8,
                            "navigation_channel": {},
                        },
                        {
                            "channel_id": 9,
                            "media_info_channel": {},
                        },
                        {
                            "channel_id": 10,
                            "custom_field": "test",
                        },
                    ],
                }
            },
            "media_server": {
                "config": {
                    "audio_output_sink": "alsa_output.pci",
                    "audio_input_source": "alsa_input.pci",
                    "transport_mode": "auto",
                    "fullscreen": False,
                    "jpeg_quality": 80,
                    "custom_text": "hello",
                },
                "schema": {
                    "transport_mode": {"type": "enum", "choices": ["auto", "shm", "tcp"]},
                    "fullscreen": {"type": "bool"},
                    "jpeg_quality": {"type": "int", "min": 10, "max": 100},
                    "custom_text": {"type": "str"},
                },
            },
        }
        audio_devices = {
            "sinks": [{"id": "alsa_output.pci", "name": "Built-in Speaker"}],
            "sources": [{"id": "alsa_input.pci", "name": "Built-in Mic"}],
        }

        drawer._on_config_loaded(mock_data, audio_devices)
        self.assertEqual(len(drawer.channels_data), 10)

        # Verify channels_data video config mutation
        vcfg = drawer.channels_data[2]["av_channel"]["video_configs"][0]
        self.assertEqual(vcfg["video_resolution"], "VIDEO_1280x720")
        self.assertEqual(vcfg["video_fps"], "_30")
        self.assertEqual(vcfg["dpi"], 140)

        # Switch to media_server tab
        drawer._select_module("media_server")
        self.assertEqual(drawer.active_module, "media_server")
        self.assertIn("audio_output_sink", drawer.field_inputs)
        self.assertIn("audio_input_source", drawer.field_inputs)
        self.assertIn("transport_mode", drawer.field_inputs)
        self.assertIn("fullscreen", drawer.field_inputs)
        self.assertIn("jpeg_quality", drawer.field_inputs)
        self.assertIn("custom_text", drawer.field_inputs)

        # Test save button click
        with patch("backend.modules.qt6_gui.ui.drawers.settings_drawer.ConfigSaveThread") as mock_save_cls:
            mock_save_thread = MagicMock()
            mock_save_cls.return_value = mock_save_thread

            drawer._on_save_clicked()
            mock_save_cls.assert_called_once()
            mock_save_thread.start.assert_called_once()

        drawer._on_save_finished(True, "Saved")
        self.assertIn("saved", drawer.lbl_status.text().lower())

        drawer._on_save_finished(False, "Failed")
        self.assertIn("error", drawer.lbl_status.text().lower())

        drawer._on_config_failed("Network unreachable")
        self.assertIn("unreachable", drawer.lbl_status.text().lower())

    def test_config_threads_mocked(self):
        # 1. ConfigFetchThread
        fetch_thread = ConfigFetchThread()
        loaded_cb = MagicMock()
        fetch_thread.config_loaded.connect(loaded_cb)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"test": {"config": {}}}).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            fetch_thread.run()
            loaded_cb.assert_called_once()

        # 2. ConfigSaveThread
        save_thread = ConfigSaveThread("test_module", {"port": 5000})
        saved_cb = MagicMock()
        save_thread.save_finished.connect(saved_cb)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            save_thread.run()
            saved_cb.assert_called_once_with(True, "Settings saved for test_module")


if __name__ == "__main__":
    unittest.main()

