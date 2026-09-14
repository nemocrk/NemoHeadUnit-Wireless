import unittest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

class TestDiagnosticsDrawer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drawer_creation(self):
        from backend.modules.qt6_gui.ui.drawers.diagnostics_drawer import DiagnosticsDrawerWidget
        drawer = DiagnosticsDrawerWidget(host_port="127.0.0.1:8000")
        self.assertIsNotNone(drawer)
        self.assertEqual(drawer.vu_bar.value(), 0)
        self.assertEqual(drawer.combo_transport.count(), 5)
        self.assertEqual(drawer.combo_decoder.count(), 6)

    def test_dict_sink_capabilities(self):
        from backend.modules.qt6_gui.ui.drawers.diagnostics_drawer import DiagnosticsDrawerWidget
        drawer = DiagnosticsDrawerWidget(host_port="127.0.0.1:8000")
        sinks = [
            {"id": "alsa_output.pci", "name": "Built-in Audio Analog Stereo"},
            {"id": "bluez_sink", "name": "Bluetooth Headset"},
            "raw_string_sink"
        ]
        drawer.sink_combo.clear()
        drawer.sink_combo.addItem("Default Sink", "default")
        for s in sinks:
            if isinstance(s, dict):
                label = s.get("name") or s.get("id") or "Unknown"
                val = s.get("id") or label
            else:
                label = str(s)
                val = str(s)
            drawer.sink_combo.addItem(label, val)
        self.assertEqual(drawer.sink_combo.count(), 4)
        self.assertEqual(drawer.sink_combo.itemText(1), "Built-in Audio Analog Stereo")
        self.assertEqual(drawer.sink_combo.itemData(1), "alsa_output.pci")

    def test_diagnostics_ws_messages_and_test_triggers(self):
        import json
        from unittest.mock import patch, MagicMock
        from backend.modules.qt6_gui.ui.drawers.diagnostics_drawer import DiagnosticsDrawerWidget
        drawer = DiagnosticsDrawerWidget(host_port="127.0.0.1:8000")

        # 1. WS message parsing
        drawer._on_ws_message(json.dumps({"type": "test_started", "test_type": "audio_tone"}))
        self.assertIn("Started: audio_tone", drawer.console.toPlainText())

        drawer._on_ws_message(json.dumps({
            "type": "test_completed",
            "results": {"test_type": "audio_tone", "status": "passed", "elapsed_sec": 1.2}
        }))
        self.assertIn("Completed: audio_tone", drawer.console.toPlainText())

        drawer._on_ws_message(json.dumps({"type": "mic_level", "len": 512}))
        self.assertEqual(drawer.vu_bar.value(), 50)

        drawer._on_ws_message(json.dumps({"type": "audio_frame_injected", "format": "PCM", "len": 640}))
        self.assertIn("Injected PCM audio", drawer.console.toPlainText())

        # 2. Trigger tests
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"{}"
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            drawer._run_video_benchmark()
            mock_urlopen.assert_called()

            drawer._apply_audio_sink()
            mock_urlopen.assert_called()


if __name__ == "__main__":
    unittest.main()

