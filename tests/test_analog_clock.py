import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtCore import QSize

from backend.modules.qt6_gui.ui.analog_clock import AnalogClockWidget


class TestAnalogClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_clock_widget_initialization_and_tick(self):
        clock = AnalogClockWidget()
        clock.resize(250, 250)
        assert clock.date_label.text() != ""

        # Trigger tick
        clock._on_tick()
        assert clock.date_label.text() != ""

        # Trigger paint event by rendering to offscreen pixmap
        pixmap = QPixmap(QSize(250, 250))
        clock.render(pixmap)
        assert not pixmap.isNull()

        # Connect button click
        clicked = []
        clock.connect_phone_clicked.connect(lambda: clicked.append(True))
        clock.btn_connect_phone.click()
        assert len(clicked) == 1
