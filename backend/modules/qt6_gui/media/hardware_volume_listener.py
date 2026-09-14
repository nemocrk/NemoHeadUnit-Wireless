"""
hardware_volume_listener.py — Cross-platform hardware volume keys monitor.

Monitors physical hardware volume keys (VolumeUp, VolumeDown, Mute) on Linux
via /dev/input/event* (soc_button_array, gpio-keys, ACPI) using native stdlib,
and emits Qt signals to control the head unit audio volume and OSD popover.
"""

import glob
import os
import platform
import select
import struct
import sys
import threading
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from shared.logger import get_logger

logger = get_logger("qt6_gui.volume_listener")

# Linux Input Event Constants (linux/input-event-codes.h)
EV_KEY = 1
KEY_MUTE = 113
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115

KEY_MAP = {
    KEY_VOLUMEUP: "up",
    KEY_VOLUMEDOWN: "down",
    KEY_MUTE: "mute",
}


def get_input_event_format() -> tuple[str, int]:
    """Return struct format and size for struct input_event based on architecture."""
    # 64-bit: timeval (8B sec, 8B usec), uint16 type, uint16 code, int32 value = 24 bytes
    # 32-bit: timeval (4B sec, 4B usec), uint16 type, uint16 code, int32 value = 16 bytes
    is_64bit = sys.maxsize > 2**32
    if is_64bit:
        return ("qqHHi", 24)
    else:
        return ("iiHHi", 16)


def decode_input_event(raw_bytes: bytes, fmt: str) -> Optional[tuple[int, int, int]]:
    """Decode a single Linux input_event into (type, code, value)."""
    try:
        _, _, ev_type, ev_code, ev_value = struct.unpack(fmt, raw_bytes)
        return (ev_type, ev_code, ev_value)
    except struct.error:
        return None


class HardwareVolumeListener(QObject):
    """
    Background listener for physical volume buttons on Linux systems.
    Cross-platform safe: gracefully no-ops on Windows or when /dev/input is absent.
    """

    volume_action = pyqtSignal(str)  # "up", "down", "mute"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._fds: Dict[int, str] = {}
        self._fmt, self._event_size = get_input_event_format()

    def start(self):
        """Start listening for hardware volume events in a background thread."""
        if sys.platform != "linux" or not os.path.exists("/dev/input"):
            logger.debug("Hardware volume listener: /dev/input not present on this platform, skipping.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="hw_vol_listener", daemon=True)
        self._thread.start()
        logger.info("Hardware volume listener started.")

    def stop(self):
        """Stop background listener and close all device descriptors."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._close_all_fds()

    def _close_all_fds(self):
        for fd in list(self._fds.keys()):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()

    def _open_devices(self) -> List[int]:
        """Open all readable /dev/input/event* devices."""
        self._close_all_fds()
        candidates = sorted(glob.glob("/dev/input/event*"))
        for path in candidates:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self._fds[fd] = path
            except (OSError, PermissionError):
                continue
        return list(self._fds.keys())

    def _run_loop(self):
        """Event poll loop using select()."""
        fds = self._open_devices()
        if not fds:
            logger.debug("No readable /dev/input/event* devices found for volume listener.")

        while self._running:
            if not fds:
                # Re-scan every 5 seconds if devices weren't available at startup
                threading.Event().wait(5.0)
                if not self._running:
                    break
                fds = self._open_devices()
                continue

            try:
                readable, _, _ = select.select(fds, [], [], 1.0)
            except (ValueError, OSError):
                fds = self._open_devices()
                continue

            for fd in readable:
                try:
                    chunk = os.read(fd, self._event_size * 8)
                except (OSError, BlockingIOError):
                    continue

                if not chunk:
                    continue

                offset = 0
                while offset + self._event_size <= len(chunk):
                    ev = decode_input_event(chunk[offset : offset + self._event_size], self._fmt)
                    offset += self._event_size
                    if ev is None:
                        continue

                    ev_type, ev_code, ev_value = ev
                    # EV_KEY: 1 = KeyPress, 2 = KeyRepeat/Hold
                    if ev_type == EV_KEY and ev_value in (1, 2):
                        action = KEY_MAP.get(ev_code)
                        if action:
                            logger.info(f"Hardware volume key triggered: {action} (code={ev_code})")
                            self.volume_action.emit(action)

        self._close_all_fds()
