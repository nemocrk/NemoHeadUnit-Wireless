import os
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication, QComboBox, QSpinBox
from backend.modules.qt6_gui.ui.drawers.settings_drawer import SettingsDrawerWidget, DragScrollArea


class TestSettingsDrawer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_scroll_area(self):
        drawer = SettingsDrawerWidget()
        self.assertIsInstance(drawer.form_scroll, DragScrollArea)
        self.assertIsInstance(drawer.tabs_scroll, DragScrollArea)

    def test_channels_interactive_rendering(self):
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
                    ],
                }
            }
        }
        drawer._on_config_loaded(mock_data)
        self.assertTrue(hasattr(drawer, "channels_data"))
        self.assertEqual(len(drawer.channels_data), 4)

        # Verify channels_data video config mutation
        vcfg = drawer.channels_data[2]["av_channel"]["video_configs"][0]
        self.assertEqual(vcfg["video_resolution"], "VIDEO_1280x720")
        self.assertEqual(vcfg["video_fps"], "_30")
        self.assertEqual(vcfg["dpi"], 140)


if __name__ == "__main__":
    unittest.main()
