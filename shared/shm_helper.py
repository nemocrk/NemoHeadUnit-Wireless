"""
NemoHeadUnit-Wireless — shared/shm_helper.py

Zero-copy double-buffered shared memory engine for offscreen widget rendering.

Architecture (ported from proof_of_concept/shared_buffer.py):
  - Two POSIX shared memory segments per widget (buf_0, buf_1)
  - Fixed max physical size — never reallocated on resize
  - QImage wraps raw ctypes pointer (true zero-copy)
  - Pitch = max_width * 4  (critical for correct row alignment)
  - Ack-based flow control (swap_buffer → swap_ack)
  - Deterministic naming: nemo_shm_{name}_buf_0 / nemo_shm_{name}_buf_1
  - Resource tracker patch to prevent premature unlink

See proof_of_concept/technical_paper.md for full design rationale.
"""

import ctypes
import os
import time
import sys
import logging
import weakref
from pathlib import Path
from multiprocessing import shared_memory
from typing import Optional

try:
    from PyQt6.QtCore import QPointF, Qt, QEvent
    from PyQt6.QtGui import QImage, QMouseEvent, QKeyEvent, QFocusEvent
    from PyQt6.QtWidgets import QApplication
    _QT_AVAILABLE = True
except (ImportError, AttributeError):
    _QT_AVAILABLE = False

logger = logging.getLogger("shm_helper")
_mouse_grab_targets = weakref.WeakKeyDictionary()

# ---------------------------------------------------------------------------
# Resource tracker patch
# ---------------------------------------------------------------------------
# Python's multiprocessing.resource_tracker treats shared memory as a
# process-local resource and will emit warnings (or prematurely unlink
# segments) when a process exits without calling unlink(). Since we
# intentionally share segments across processes, we patch the tracker
# to ignore shared_memory-type resources.

def _patch_resource_tracker():
    from multiprocessing import resource_tracker

    _orig_register = resource_tracker.register
    _orig_unregister = resource_tracker.unregister

    def patched_register(name, rtype):
        if rtype == "shared_memory":
            return  # Skip tracking
        return _orig_register(name, rtype)

    def patched_unregister(name, rtype):
        if rtype == "shared_memory":
            return  # Skip tracking
        return _orig_unregister(name, rtype)

    resource_tracker.register = patched_register
    resource_tracker.unregister = patched_unregister

_patch_resource_tracker()


# ---------------------------------------------------------------------------
# DoubleSharedBuffer
# ---------------------------------------------------------------------------

class DoubleSharedBuffer:
    """
    Manages two POSIX shared memory buffers for double-buffered rendering.

    The shared memory is allocated at a fixed physical size (max_width × max_height × 4).
    The logical rendering viewport is smaller and changes dynamically.
    QImage is constructed with pitch = max_width * 4 to match the physical row stride.

    :param name: Unique string identifier for the widget.
    :param max_width: Maximum physical width of the buffer.
    :param max_height: Maximum physical height of the buffer.
    :param create: If True, creates the segments (widget side).
                   If False, attaches to existing ones (compositor side).
    """

    def __init__(self, name: str, max_width: int = 1024, max_height: int = 600, create: bool = False):
        self.name = name
        self.max_width = max(1, max_width)
        self.max_height = max(1, max_height)
        self.buffer_size = self.max_width * self.max_height * 4  # 4 bytes per pixel (ARGB32)
        self.create = create

        self.shm_names = [
            f"nemo_shm_{name}_buf_0",
            f"nemo_shm_{name}_buf_1",
        ]

        self.shms: list[shared_memory.SharedMemory] = []
        self.addresses: list[int] = []

        try:
            for shm_name in self.shm_names:
                if create:
                    # Clean up orphaned shared memory left over from previous crashes
                    try:
                        temp_shm = shared_memory.SharedMemory(name=shm_name)
                        temp_shm.close()
                        temp_shm.unlink()
                        logger.info("Cleaned up orphaned shared memory: %s", shm_name)
                    except FileNotFoundError:
                        pass

                    shm = shared_memory.SharedMemory(name=shm_name, create=True, size=self.buffer_size)
                else:
                    shm = shared_memory.SharedMemory(name=shm_name)

                self.shms.append(shm)

                # Get the raw memory address of the shared memory buffer
                address = ctypes.addressof(ctypes.c_char.from_buffer(shm.buf))
                self.addresses.append(address)

        except Exception as e:
            logger.error("Error initializing shared buffer '%s': %s", name, e)
            self.cleanup()
            raise

    def get_image(self, index: int, logical_width: int, logical_height: int) -> "QImage":
        """
        Returns a QImage wrapping the shared memory at the given index (0 or 1),
        configured with active logical dimensions and physical pitch.

        Modifying this QImage writes directly to the shared memory block (zero-copy).

        CRITICAL: pitch (bytesPerLine) MUST be max_width * 4 regardless of logical_width.
        If bytesPerLine does not match the physical row width, pixel rows will be
        misaligned and the image will appear sheared/corrupted.
        """
        if not _QT_AVAILABLE:
            raise RuntimeError("PyQt6 not available")

        if index < 0 or index >= len(self.addresses):
            raise IndexError(f"Buffer index {index} out of range")

        # Ensure logical dimensions don't exceed physical max boundaries
        logical_width = min(logical_width, self.max_width)
        logical_height = min(logical_height, self.max_height)

        address = self.addresses[index]
        pitch = self.max_width * 4  # bytesPerLine must match physical width

        return QImage(address, logical_width, logical_height, pitch, QImage.Format.Format_ARGB32)

    def cleanup(self):
        """Closes and (if creator) unlinks shared memory segments."""
        for shm in self.shms:
            try:
                shm.close()
            except Exception:
                pass
            if self.create:
                try:
                    shm.unlink()
                    logger.info("Unlinked shared memory: %s", shm.name)
                except Exception:
                    pass
        self.shms.clear()
        self.addresses.clear()


# ---------------------------------------------------------------------------
# OffscreenWidgetEngine
# ---------------------------------------------------------------------------

class OffscreenWidgetEngine:
    """
    High-level engine for offscreen widget rendering into shared memory.
    Used by each UI module to render its widget tree into double-buffered SHM.

    Implements ack-based flow control:
      - render_and_swap() gates on pending_ack to prevent overwriting
        a buffer the compositor is still reading
      - on_swap_ack() clears the gate and triggers deferred redraw

    :param name: Widget/module name (used for SHM segment naming)
    :param w: Initial logical width
    :param h: Initial logical height
    :param bus: BusClient instance for publishing frame_ready messages
    :param max_width: Maximum physical width (default 1024)
    :param max_height: Maximum physical height (default 600)
    """

    def __init__(self, name: str, w: int, h: int, bus=None,
                 max_width: int = 1024, max_height: int = 600):
        self.name = name
        self.w = max(1, w)
        self.h = max(1, h)
        self.bus = bus
        self.max_width = max(1, max_width)
        self.max_height = max(1, max_height)

        # Double-buffered shared memory (creator side)
        self.shm_buffer = DoubleSharedBuffer(name, max_width, max_height, create=True)

        # Flow control state
        self.active_idx = 0       # Buffer index last written to
        self.pending_ack = False  # True if waiting for compositor ack
        self.needs_redraw = False # True if render requested while pending_ack

    def resize(self, w: int, h: int) -> None:
        """
        Update logical dimensions. No SHM reallocation needed —
        the fixed max buffer handles any size within bounds.
        """
        self.w = min(w, self.max_width)
        self.h = min(h, self.max_height)

    def get_write_image(self) -> Optional["QImage"]:
        """
        Return a QImage wrapping the back buffer for writing.
        The returned image is zero-copy: painting on it writes directly to SHM.
        """
        if not _QT_AVAILABLE:
            return None
        back_idx = 1 - self.active_idx
        return self.shm_buffer.get_image(back_idx, self.w, self.h)

    def swap_and_notify(self) -> None:
        """
        Swap the active buffer index and publish ui.widget.frame_ready.
        Sets pending_ack = True to gate the next render.
        """
        self.active_idx = 1 - self.active_idx
        self.pending_ack = True
        self.needs_redraw = False

        if self.bus is not None:
            self.bus.publish("ui.widget.frame_ready", {
                "name": self.name,
                "shm_name": self.shm_buffer.shm_names[self.active_idx],
                "buffer_index": self.active_idx,
                "w": self.w,
                "h": self.h,
                "max_width": self.max_width,
                "max_height": self.max_height,
                "format": "ARGB32",
                "timestamp_ms": int(time.time() * 1000),
            })

    def on_swap_ack(self) -> None:
        """
        Called when the compositor acknowledges it has consumed the frame.
        Clears the pending_ack gate. If needs_redraw is True, the caller
        should trigger another render_and_swap() cycle.
        """
        self.pending_ack = False

    def render_and_swap(self, widget) -> None:
        """
        High-level convenience: render a QWidget into the back buffer,
        swap, and notify the compositor.

        Gated by ack flow control — if pending_ack is True, sets
        needs_redraw and returns without rendering.
        """
        if not _QT_AVAILABLE:
            return

        if self.pending_ack:
            self.needs_redraw = True
            return

        img = self.get_write_image()
        if img is None:
            return

        from PyQt6.QtCore import Qt
        img.fill(Qt.GlobalColor.transparent)
        widget.render(img)

        self.swap_and_notify()

    def cleanup(self) -> None:
        """Close and unlink shared memory segments."""
        self.shm_buffer.cleanup()


# ---------------------------------------------------------------------------
# Input event injection (unchanged from original)
# ---------------------------------------------------------------------------

def inject_input_event(root_window, payload: dict) -> None:
    """
    Reconstruct raw ZMQ input events into synthetic Qt events and inject them
    into the widget tree represented by root_window.

    Input messages are already dispatched onto each widget's Qt thread by the
    UI modules, so deliver them synchronously. Queuing drag events behind
    repaint/timer work makes controls such as sliders feel laggy.
    """
    if not _QT_AVAILABLE or root_window is None:
        return

    ev_type = payload.get("type")

    if ev_type in ("press", "move", "release"):
        root_w = max(0, int(root_window.width()))
        root_h = max(0, int(root_window.height()))
        root_x = max(0.0, min(float(payload.get("x", 0)), float(root_w)))
        root_y = max(0.0, min(float(payload.get("y", 0)), float(root_h)))
        root_pos = QPointF(root_x, root_y)

        target = _mouse_grab_targets.get(root_window)
        if ev_type == "press" or target is None:
            target = root_window.childAt(int(root_pos.x()), int(root_pos.y()))
            if target is None:
                target = root_window
            if ev_type == "press":
                _mouse_grab_targets[root_window] = target

        target_pos = target.mapFrom(root_window, root_pos.toPoint())
        local_pos = QPointF(target_pos)
        qt_type = {
            "press": QEvent.Type.MouseButtonPress,
            "move": QEvent.Type.MouseMove,
            "release": QEvent.Type.MouseButtonRelease
        }[ev_type]

        button_value = int(payload.get("button", 0))
        buttons_value = int(payload.get("buttons", button_value if ev_type == "press" else 0))
        if ev_type == "press" and button_value == 0:
            button_value = int(Qt.MouseButton.LeftButton.value)
            buttons_value = int(Qt.MouseButton.LeftButton.value)
        elif ev_type == "release" and button_value == 0:
            button_value = int(Qt.MouseButton.LeftButton.value)

        button = Qt.MouseButton(button_value)
        buttons = Qt.MouseButton(buttons_value)
        modifiers = Qt.KeyboardModifier(payload.get("modifiers", 0))

        if ev_type == "press":
            focus_target = target
            while focus_target is not None and focus_target.focusPolicy() == Qt.FocusPolicy.NoFocus:
                focus_target = focus_target.parentWidget()
            if focus_target is not None:
                root_window.activateWindow()
                focus_target.setFocus(Qt.FocusReason.MouseFocusReason)
                # On WA_DontShowOnScreen windows the OS never activates the window,
                # so setFocus() may not fire focusInEvent.  Send a synthetic
                # QFocusEvent directly so focusInEvent is guaranteed to run.
                if not focus_target.hasFocus():
                    QApplication.sendEvent(
                        focus_target,
                        QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason),
                    )

        qt_event = QMouseEvent(
            qt_type,
            local_pos,
            root_pos,
            QPointF(root_window.mapToGlobal(root_pos.toPoint())),
            button,
            buttons,
            modifiers
        )
        QApplication.sendEvent(target, qt_event)
        if ev_type == "release":
            _mouse_grab_targets.pop(root_window, None)

    elif ev_type in ("key", "key_press", "key_release"):
        key = payload.get("key", 0)
        text = payload.get("text", "")
        is_auto_repeat = bool(payload.get("is_auto_repeat", False))
        qt_type = QEvent.Type.KeyRelease if ev_type == "key_release" else QEvent.Type.KeyPress
        modifiers = Qt.KeyboardModifier(payload.get("modifiers", 0))
        target = root_window.focusWidget() or root_window
        if target is not root_window and not root_window.isAncestorOf(target):
            target = root_window

        qt_event = QKeyEvent(
            qt_type,
            Qt.Key(key),
            modifiers,
            text,
            is_auto_repeat
        )
        QApplication.sendEvent(target, qt_event)
