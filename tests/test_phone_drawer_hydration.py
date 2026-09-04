"""
test_phone_drawer_hydration.py — Tests for PhoneDrawerWidget real data hydration, search filter and DTMF.
"""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from backend.modules.qt6_gui.ui.drawers.phone_drawer import PhoneDrawerWidget


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_phone_drawer_hydration_and_filter():
    app = get_app()
    drawer = PhoneDrawerWidget()

    # Hydrate with custom test data
    contacts = [
        {"name": "Alice Smith", "primary_phone": "+1234567"},
        {"name": "Bob Jones", "primary_phone": "+7654321"},
        {"name": "Charlie Brown", "primary_phone": "+9998888"},
    ]
    drawer.set_contacts(contacts)
    assert drawer.contacts_list.count() == 3

    # Filter contacts
    drawer.search_input.setText("Bob")
    assert drawer.contacts_list.count() == 1
    assert "Bob Jones" in drawer.contacts_list.item(0).text()

    # Clear filter
    drawer.search_input.setText("")
    assert drawer.contacts_list.count() == 3


def test_phone_drawer_recents_and_favorites():
    app = get_app()
    drawer = PhoneDrawerWidget()

    favs = [{"name": "Mom", "primary_phone": "123"}]
    drawer.set_favorites(favs)
    assert drawer.favorites_list.count() == 1
    assert "Mom" in drawer.favorites_list.item(0).text()

    recents = [{"name": "Dad", "number": "456", "timestamp": "10:00", "call_type": "MISSED"}]
    drawer.set_recents(recents)
    assert drawer.recents_list.count() == 1
    assert "Dad" in drawer.recents_list.item(0).text()


def test_phone_drawer_keypad_dial_and_dtmf():
    app = get_app()
    drawer = PhoneDrawerWidget()

    calls = []
    dtmfs = []
    drawer.call_requested.connect(lambda num: calls.append(num))
    drawer.dtmf_requested.connect(lambda tone: dtmfs.append(tone))

    # Dial digits when idle
    drawer._append_digit("1")
    drawer._append_digit("2")
    drawer._append_digit("3")
    assert drawer.dial_display.text() == "123"
    assert len(dtmfs) == 0  # No DTMF emitted when idle

    drawer._trigger_call()
    assert calls == ["123"]

    # When in call: pressing digits emits DTMF
    drawer.set_in_call(True)
    drawer._append_digit("4")
    assert dtmfs == ["4"]
