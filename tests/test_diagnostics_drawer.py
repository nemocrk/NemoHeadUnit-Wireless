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


if __name__ == "__main__":
    unittest.main()
