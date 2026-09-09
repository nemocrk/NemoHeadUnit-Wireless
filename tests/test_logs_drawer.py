import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import unittest
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.ui.drawers.logs_drawer import LogsDrawerWidget


class TestLogsDrawer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_logs_drawer_ui_and_filters(self):
        drawer = LogsDrawerWidget(host_port="127.0.0.1:8000")
        drawer.show()

        # Update modules dropdown
        drawer._update_modules_dropdown(["channel_manager", "tcp_server", "media_server"])
        assert drawer.module_combo.count() == 4  # All + 3 modules

        # Test append log entry
        drawer.append_log_entry("2026-09-08 | INFO | Test log line 1")
        assert "Test log line 1" in drawer.console.toPlainText()

        # Test filter change signal
        filters = []
        drawer.filter_changed.connect(lambda m, l: filters.append((m, l)))
        drawer.module_combo.setCurrentIndex(1)
        assert len(filters) == 1

        # Reconnect stream with specific module
        drawer.reconnect_stream()
        assert len(drawer.log_sockets) > 0

        # Close all sockets
        drawer._close_all_sockets()
        assert len(drawer.log_sockets) == 0

        # Close clicked signal
        closed = []
        drawer.close_clicked.connect(lambda: closed.append(True))
        drawer.close_clicked.emit()
        assert closed == [True]
