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


def test_hardware_volume_listener_run_loop_with_events():
    app = get_app()
    listener = HardwareVolumeListener()
    r_fd, w_fd = os.pipe()

    fmt, size = get_input_event_format()
    raw_up = struct.pack(fmt, 100, 200, EV_KEY, KEY_VOLUMEUP, 1)
    os.write(w_fd, raw_up)

    listener._fds = {r_fd: "/dev/input/event0"}
    action_received = []
    listener.volume_action.connect(action_received.append)

    with patch.object(listener, "_open_devices", return_value=[r_fd]):
        # Run one iteration of listener
        listener._running = True
        import threading
        t = threading.Thread(target=listener._run_loop)
        t.start()
        import time
        time.sleep(0.1)
        listener._running = False
        os.write(w_fd, b"stop")
        t.join(timeout=1.0)

    try:
        os.close(w_fd)
    except OSError:
        pass

    app.processEvents()
    assert "up" in action_received




from unittest.mock import patch, MagicMock

def test_main_window_hardware_volume_actions():
    app = get_app()
    with patch("backend.modules.qt6_gui.ui.volume_popover.MediaStatusStreamThread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        win = MainWindow()
        win.show()

        assert not win.volume_popover.isVisible()

        # Simulate hardware volume key press
        win._on_hardware_volume_key("up")
        assert not win.volume_popover.isHidden()
        assert win._volume_hud_timer.isActive() is True

        win._on_hardware_volume_key("down")
        assert not win.volume_popover.isHidden()
        win.volume_popover.stop_media_stream()
        win.close()

