"""Tests for drawer near full-screen cards layout and solid styling."""

import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QResizeEvent
import sys

from backend.modules.qt6_gui.ui.main_window import MainWindow


def test_drawer_geometry_near_fullscreen():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.resize(1280, 800)

    # Pass actual QResizeEvent
    event = QResizeEvent(QSize(1280, 800), QSize(1280, 720))
    win.resizeEvent(event)

    cmd_h = win.command_bar.height() if win.command_bar else 56
    draw_h = 800 - cmd_h
    expected_geom = QRect(30, 30, 1280 - 60, draw_h - 60)

    for drawer in (win.bluetooth_drawer, win.settings_drawer, win.logs_drawer, win.diagnostics_drawer):
        assert drawer.geometry() == expected_geom
        assert drawer.testAttribute(Qt.WidgetAttribute.WA_StyledBackground) is True
