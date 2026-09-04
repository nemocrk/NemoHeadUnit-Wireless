"""
test_hardware_volume_keys.py — Tests for Linux hardware volume buttons monitoring.
"""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import struct
import sys
import pytest
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.media.hardware_volume_listener import (
    HardwareVolumeListener,
    get_input_event_format,
    decode_input_event,
    KEY_MAP,
    EV_KEY,
    KEY_VOLUMEUP,
    KEY_VOLUMEDOWN,
    KEY_MUTE,
)
from backend.modules.qt6_gui.ui.main_window import MainWindow


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_input_event_format_and_decoding():
    fmt, size = get_input_event_format()
    assert size in (16, 24)

    # 1. Volume Up press (value=1)
    raw_up = struct.pack(fmt, 100, 200, EV_KEY, KEY_VOLUMEUP, 1)
    ev = decode_input_event(raw_up, fmt)
    assert ev == (1, KEY_VOLUMEUP, 1)
    assert KEY_MAP.get(ev[1]) == "up"

    # 2. Volume Down press (value=1)
    raw_down = struct.pack(fmt, 100, 200, EV_KEY, KEY_VOLUMEDOWN, 1)
    ev = decode_input_event(raw_down, fmt)
    assert ev == (1, KEY_VOLUMEDOWN, 1)
    assert KEY_MAP.get(ev[1]) == "down"

    # 3. Mute press (value=1)
    raw_mute = struct.pack(fmt, 100, 200, EV_KEY, KEY_MUTE, 1)
    ev = decode_input_event(raw_mute, fmt)
    assert ev == (1, KEY_MUTE, 1)
    assert KEY_MAP.get(ev[1]) == "mute"

    # 4. Invalid corrupt bytes return None
    assert decode_input_event(b"bad", fmt) is None


def test_hardware_volume_listener_lifecycle():
    app = get_app()
    listener = HardwareVolumeListener()
    # Graceful start and stop without errors even if /dev/input is absent
    listener.start()
    listener.stop()


def test_main_window_hardware_volume_actions():
    app = get_app()
    win = MainWindow()
    win.show()

    assert not win.volume_popover.isVisible()

    # Simulate hardware volume key press
    win._on_hardware_volume_key("up")
    assert not win.volume_popover.isHidden()
    assert win._volume_hud_timer.isActive() is True

    win._on_hardware_volume_key("down")
    assert not win.volume_popover.isHidden()
