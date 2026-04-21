"""
NemoHeadUnit-Wireless — QtBusBridge
Thread-safe bridge between the ZMQ bus recv thread and the Qt main thread.

Usage:
    Call install(bus) once after QApplication is created.
    Then use bus.subscribe(topic, callback, thread="main") and
    the callback will always be invoked on the Qt main thread
    via QueuedConnection — safe for any widget operation.

How it works:
    BusClient._dispatch() calls QtBusBridge.dispatch(payload) from the
    ZMQ recv thread. dispatch() emits a pyqtSignal, which Qt automatically
    delivers to the connected slot in the main event loop thread.
"""

import logging
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, Qt

log = logging.getLogger("app.gui.qt_bridge")


class QtBusBridge(QObject):
    """
    One instance per (topic, callback) pair that requires main-thread dispatch.

    The signal is connected with Qt.ConnectionType.QueuedConnection (default
    when emitter and receiver live in different threads), so the slot runs
    safely inside the Qt event loop.
    """

    _signal = pyqtSignal(object)

    def __init__(self, callback: Callable, parent: QObject = None):
        super().__init__(parent)
        self._callback = callback
        # QueuedConnection is implicit when threads differ, but explicit is safer
        self._signal.connect(self._callback, Qt.ConnectionType.QueuedConnection)
        log.debug(f"QtBusBridge created for {callback.__qualname__}")

    def dispatch(self, payload) -> None:
        """
        Called from the ZMQ recv thread.
        Emits the signal — Qt will deliver it on the main thread.
        """
        self._signal.emit(payload)


def _bridge_factory(callback: Callable) -> QtBusBridge:
    """Factory function passed to BusClient.set_qt_bridge_factory()."""
    return QtBusBridge(callback)


def install(bus) -> None:
    """
    Wire the Qt bridge factory into a BusClient instance.

    Must be called after QApplication is instantiated and before
    any subscribe(..., thread='main') calls are dispatched.

    Args:
        bus: BusClient instance
    """
    bus.set_qt_bridge_factory(_bridge_factory)
    log.info("QtBusBridge installed on BusClient")
