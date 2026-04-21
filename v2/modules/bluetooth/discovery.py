"""
discovery.py — Bluetooth device discovery with configurable duration.

Responsibilities:
  - Start/stop BlueZ discovery via Adapter1
  - Collect devices found during the scan window
  - Return results to caller (main.py publishes them on the bus)

No ZMQ dependency — all bus publishing is handled by main.py.
"""

import logging
import threading
import time
from typing import Callable

log = logging.getLogger("bluetooth.discovery")


class DiscoverySession:
    """
    Manages a timed Bluetooth discovery session.

    Usage:
        def on_device(address, name, rssi):
            ...

        def on_done(devices):
            ...

        session = DiscoverySession(adapter, on_device_cb=on_device, on_done_cb=on_done)
        session.start(duration_sec=10)
    """

    def __init__(
        self,
        adapter,  # BluezAdapter instance
        on_device_cb: Callable[[str, str, int], None] | None = None,
        on_done_cb: Callable[[list], None] | None = None,
    ):
        self._adapter = adapter
        self._on_device_cb = on_device_cb
        self._on_done_cb = on_done_cb
        self._devices: dict[str, dict] = {}  # address -> {name, rssi}
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, duration_sec: int = 10) -> None:
        """Begin discovery; stops automatically after duration_sec."""
        if self._running:
            log.warning("Discovery already running, ignoring start request")
            return
        self._devices.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(duration_sec,), daemon=True
        )
        self._thread.start()
        log.info(f"Discovery started for {duration_sec}s")

    def stop(self) -> None:
        """Abort discovery early."""
        self._running = False
        log.info("Discovery stop requested")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self, duration_sec: int) -> None:
        try:
            self._start_discovery()
            deadline = time.monotonic() + duration_sec
            while self._running and time.monotonic() < deadline:
                new_devices = self._poll_devices()
                for dev in new_devices:
                    addr = dev["address"]
                    if addr not in self._devices:
                        self._devices[addr] = dev
                        log.info(f"Device found: {addr} ({dev.get('name', '?')}), rssi={dev.get('rssi', 0)}")
                        if self._on_device_cb:
                            self._on_device_cb(addr, dev.get("name", ""), dev.get("rssi", 0))
                time.sleep(1)
        except Exception as e:
            log.error(f"Discovery error: {e}")
        finally:
            self._stop_discovery()
            self._running = False
            devices_list = list(self._devices.values())
            log.info(f"Discovery completed. Found {len(devices_list)} device(s)")
            if self._on_done_cb:
                self._on_done_cb(devices_list)

    def _start_discovery(self) -> None:
        """Call Adapter1.StartDiscovery via D-Bus."""
        try:
            # adapter._adapter is the dbus.Interface for Adapter1
            self._adapter._adapter.StartDiscovery()
            log.debug("Adapter1.StartDiscovery called")
        except Exception as e:
            log.error(f"StartDiscovery failed: {e}")

    def _stop_discovery(self) -> None:
        """Call Adapter1.StopDiscovery via D-Bus."""
        try:
            self._adapter._adapter.StopDiscovery()
            log.debug("Adapter1.StopDiscovery called")
        except Exception as e:
            log.warning(f"StopDiscovery failed (may already be stopped): {e}")

    def _poll_devices(self) -> list[dict]:
        """
        Poll BlueZ ObjectManager for Device1 objects found so far.
        Returns list of {address, name, rssi}.
        """
        found = []
        try:
            import dbus
            bus = self._adapter._bus
            manager = dbus.Interface(
                bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            objects = manager.GetManagedObjects()
            for path, ifaces in objects.items():
                dev = ifaces.get("org.bluez.Device1")
                if dev:
                    found.append({
                        "address": str(dev.get("Address", "")),
                        "name": str(dev.get("Name", "")),
                        "rssi": int(dev.get("RSSI", 0)),
                    })
        except Exception as e:
            log.error(f"_poll_devices error: {e}")
        return found
