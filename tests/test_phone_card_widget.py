"""
test_phone_card_widget.py — Tests for PhoneCardWidget and its integration with MainWindow.
"""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from backend.modules.qt6_gui.ui.phone_card_widget import PhoneCardWidget
from backend.modules.qt6_gui.ui.main_window import MainWindow


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_phone_card_widget_creation_and_telemetry():
    app = get_app()
    card = PhoneCardWidget()

    # Initial default synthetic state
    assert "Pixel 7" in card.lbl_device.text()
    assert "Vodafone 5G" in card.lbl_carrier.text()
    assert "4/5" in card.lbl_signal.text()
    assert "85%" in card.lbl_battery.text()
    assert card.lbl_status_pill.text() == "CONNECTED"

    # Update telemetry
    card.update_telemetry(
        device_name="iPhone 15",
        carrier="TIM LTE",
        signal_bars=5,
        battery_pct=92,
        is_connected=True,
    )
    assert "iPhone 15" in card.lbl_device.text()
    assert "TIM LTE" in card.lbl_carrier.text()
    assert "5/5" in card.lbl_signal.text()
    assert "92%" in card.lbl_battery.text()

    # Disconnected state
    card.update_telemetry(is_connected=False)
    assert card.lbl_status_pill.text() == "SEARCHING"


def test_phone_card_quick_call_and_open_drawer_signals():
    app = get_app()
    card = PhoneCardWidget()

    calls = []
    actions = []
    drawer_opened = []

    card.call_requested.connect(lambda num: calls.append(num))
    card.call_action_triggered.connect(lambda act: actions.append(act))
    card.open_drawer_requested.connect(lambda: drawer_opened.append(True))

    card.set_quick_contact("Mario Rossi", "+390123456")
    assert "Mario Rossi" in card.lbl_quick_contact.text()

    # Click Quick Call
    card.btn_quick_call.click()
    assert calls == ["+390123456"]
    assert actions == ["dial:+390123456"]

    # Click Open Phone Drawer
    card.btn_open_drawer.click()
    assert drawer_opened == [True]


def test_phone_card_main_window_integration():
    app = get_app()
    win = MainWindow()

    # Check that phone_card exists on disconnected screen
    assert hasattr(win, "phone_card")
    assert win.phone_card is not None
    assert win.notification_card is win.phone_card  # Alias preserved

    # Drawer starts hidden
    assert win.phone_drawer.isHidden()

    # Trigger open_drawer_requested from phone_card
    win.phone_card.open_drawer_requested.emit()
    assert not win.phone_drawer.isHidden()

    # Trigger again to toggle close
    win.phone_card.open_drawer_requested.emit()
    assert win.phone_drawer.isHidden()
