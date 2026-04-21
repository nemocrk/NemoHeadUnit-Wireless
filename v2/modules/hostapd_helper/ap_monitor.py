"""
ap_monitor.py — Poll the wireless interface until the AP is confirmed active.

Responsibilities:
  - Repeatedly check that:
      1. hostapd process is still alive
      2. The interface is UP and has an IP assigned
      3. At least one client association OR the interface is in AP mode (iw)
  - Call on_ready_cb(params) once all checks pass
  - Call on_failed_cb(reason) if timeout expires

No ZMQ dependency — callbacks notify main.py.
"""

import logging
import subprocess
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("hostapd_helper.ap_monitor")

DEFAULT_POLL_INTERVAL = 1.0   # seconds between checks
DEFAULT_TIMEOUT       = 30.0  # seconds before giving up


class APMonitor:
    """
    Polls the system until the WiFi AP is confirmed active.

    Usage:
        def on_ready(params: dict):
            # params from APManager.get_params()
            ...

        def on_failed(reason: str):
            ...

        monitor = APMonitor(
            ap_manager=mgr,
            on_ready_cb=on_ready,
            on_failed_cb=on_failed,
        )
        monitor.start()
    """

    def __init__(
        self,
        ap_manager,                              # APManager instance
        on_ready_cb: Callable[[dict], None],
        on_failed_cb: Callable[[str], None],
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._ap = ap_manager
        self._on_ready = on_ready_cb
        self._on_failed = on_failed_cb
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin polling in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info(f"APMonitor started (timeout={self._timeout}s)")

    def stop(self) -> None:
        self._running = False
        log.info("APMonitor stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        deadline = time.monotonic() + self._timeout
        iface = self._ap._cfg.interface

        while self._running and time.monotonic() < deadline:
            if self._is_ap_active(iface):
                log.info(f"AP confirmed active on {iface}")
                self._on_ready(self._ap.get_params())
                return
            log.debug(f"AP not yet active on {iface}, retrying...")
            time.sleep(self._poll_interval)

        if self._running:
            reason = f"AP did not become active within {self._timeout}s"
            log.error(reason)
            self._on_failed(reason)

    def _is_ap_active(self, iface: str) -> bool:
        """
        Returns True when ALL of these conditions hold:
          1. hostapd subprocess is alive
          2. Interface has the gateway IP assigned
          3. Interface is in AP mode according to `iw`
        """
        return (
            self._ap.is_running()
            and self._has_ip(iface)
            and self._is_in_ap_mode(iface)
        )

    @staticmethod
    def _has_ip(iface: str) -> bool:
        """Check /sys/class/net/<iface>/operstate == 'up' and ip addr shows inet."""
        try:
            result = subprocess.run(
                ["ip", "addr", "show", iface],
                capture_output=True, text=True, timeout=3,
            )
            return "inet " in result.stdout
        except Exception as e:
            log.debug(f"_has_ip check failed: {e}")
            return False

    @staticmethod
    def _is_in_ap_mode(iface: str) -> bool:
        """Use `iw dev <iface> info` and check type is 'AP'."""
        try:
            result = subprocess.run(
                ["iw", "dev", iface, "info"],
                capture_output=True, text=True, timeout=3,
            )
            return "type AP" in result.stdout
        except FileNotFoundError:
            # iw not available — fall back to just checking IP
            log.debug("iw not found, skipping AP mode check")
            return True
        except Exception as e:
            log.debug(f"_is_in_ap_mode check failed: {e}")
            return False
