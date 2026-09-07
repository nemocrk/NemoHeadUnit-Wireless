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

    # Initial default clean state (no synthetic mocks)
    assert "No Device" in card.lbl_device.text()
    assert card.lbl_carrier.isHidden()
    assert card.lbl_signal.isHidden()
    assert card.lbl_battery.isHidden()
    assert card.lbl_status_pill.text() == "DISCONNECTED"
    assert card.quick_frame.isHidden()

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

    # Routine telemetry update without is_connected argument must preserve device name and CONNECTED state
    card.update_telemetry(signal_bars=3)
    assert "iPhone 15" in card.lbl_device.text()
    assert card.lbl_status_pill.text() == "CONNECTED"
    assert "3/5" in card.lbl_signal.text()

    # Disconnected state
    card.update_telemetry(is_connected=False)
    assert card.lbl_status_pill.text() == "SEARCHING"
    assert "No Device" in card.lbl_device.text()


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


def test_phone_card_telemetry_cache_preservation():
    app = get_app()
    card = PhoneCardWidget()

    card.update_telemetry(device_name="Pixel 7", signal_bars=4, battery_pct=85, is_connected=True)
    assert "4/5" in card.lbl_signal.text()
    assert "85%" in card.lbl_battery.text()

    # Inbound partial packet with signal_bars=-1 and battery_pct=-1 must NOT clobber cached values
    card.update_telemetry(signal_bars=-1, battery_pct=-1)
    assert "4/5" in card.lbl_signal.text()
    assert "85%" in card.lbl_battery.text()

    # Explicit disconnect clears values
    card.update_telemetry(is_connected=False)
    assert card.lbl_signal.isHidden()
    assert card.lbl_battery.isHidden()


def test_command_bar_phone_status_pill_caching():
    from backend.modules.qt6_gui.ui.command_bar import CommandBarWidget
    app = get_app()
    cmd = CommandBarWidget()

    cmd.update_phone_status(signal=4, battery=75, operator_name="Vodafone IT")
    assert cmd.phone_pill.lbl_battery.text() == "75%"
    assert "Signal: 4/5" in cmd.phone_pill.toolTip()
    assert "Vodafone IT" in cmd.phone_pill.toolTip()

    # Partial update without battery or signal must not reset to --%
    cmd.update_phone_status(signal=None, battery=None)
    assert cmd.phone_pill.lbl_battery.text() == "75%"
    assert "Signal: 4/5" in cmd.phone_pill.toolTip()

    # Update with new battery only
    cmd.update_phone_status(battery=80)
    assert cmd.phone_pill.lbl_battery.text() == "80%"
    assert "Signal: 4/5" in cmd.phone_pill.toolTip()

    # Disconnection resets to --%
    cmd.update_phone_status(is_connected=False)
    assert cmd.phone_pill.lbl_battery.text() == "--%"
    assert "Signal: --" in cmd.phone_pill.toolTip()
