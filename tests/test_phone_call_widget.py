"""Tests for Phone Call Widget and Phone Drawer."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import pytest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.ui.phone_call_widget import PhoneCallWidget
from backend.modules.qt6_gui.ui.drawers.phone_drawer import PhoneDrawerWidget


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_call_widget_state_updates():
    app = get_app()
    widget = PhoneCallWidget()
    widget.show()

    widget.update_call_state(
        is_in_call=True,
        call_state="INCOMING",
        caller_name="Alice Smith",
        caller_number="+1234567890",
        duration_seconds=0,
    )

    assert widget.lbl_caller_name.text() == "Alice Smith"
    assert widget.lbl_caller_number.text() == "+1234567890"
    assert not widget.btn_answer.isHidden()
    assert not widget.btn_hangup.isHidden()
    assert hasattr(widget, "btn_mute")
    assert not widget.btn_mute.isHidden()


def test_call_widget_action_emits():
    app = get_app()
    widget = PhoneCallWidget()
    emitted = []
    widget.action_triggered.connect(lambda act: emitted.append(act))

    widget.btn_answer.click()
    assert "answer" in emitted

    widget.btn_hangup.click()
    assert "hangup" in emitted

    widget.btn_mute.click()
    assert "mute" in emitted


def test_phone_drawer_structure():
    app = get_app()
    drawer = PhoneDrawerWidget()
    assert hasattr(drawer, "tabs")
    tab_names = [drawer.tabs.tabText(i) for i in range(drawer.tabs.count())]
    assert "Recents" in tab_names
    assert "Contacts" in tab_names
    assert "Keypad" in tab_names
