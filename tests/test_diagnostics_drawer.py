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


if __name__ == "__main__":
    unittest.main()
