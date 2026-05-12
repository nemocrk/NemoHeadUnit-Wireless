"""
paired_devices.py — BlueZ D-Bus helpers for already-paired/trusted devices.

Responsibilities:
  - List all paired or trusted devices from BlueZ
  - Remove a device (Adapter1.RemoveDevice)
  - Connect to a device (Device1.Connect) with explicit timeout watchdog
  - Disconnect from a device (Device1.Disconnect)
  - Get info dict for a single device

Design notes:
  - No local state — BlueZ is the single source of truth.
  - connect() is fully async: Device1.Connect() is dispatched on the GLib
    mainloop (reply_handler / error_handler). A daemon watchdog thread fires
    error_handler after `timeout_s` seconds if BlueZ has not replied yet,
    preventing the ~25 s native BlueZ timeout from stalling the caller.
  - All functions accept a dbus.SystemBus instance so they share the bus
    already opened by BluezAdapter (no extra D-Bus connections).
"""

import threading
from typing import Callable

from shared.logger import get_logger

log = get_logger("bluetooth.paired_devices")

_BLUEZ_SERVICE = "org.bluez"
_IFACE_DEVICE  = "org.bluez.Device1"
_IFACE_ADAPTER = "org.bluez.Adapter1"
_IFACE_PROPS   = "org.freedesktop.DBus.Properties"
_IFACE_OBJMGR  = "org.freedesktop.DBus.ObjectManager"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_managed_objects(bus) -> dict:
    import dbus
    manager = dbus.Interface(
        bus.get_object(_BLUEZ_SERVICE, "/"),
        _IFACE_OBJMGR,
    )
    return manager.GetManagedObjects()


def _resolve_device_path(bus, address: str) -> str | None:
    """Return the D-Bus object path for a device by MAC address, or None."""
    try:
        for path, ifaces in _get_managed_objects(bus).items():
            dev = ifaces.get(_IFACE_DEVICE)
            if dev and str(dev.get("Address", "")) == address:
                return path
    except Exception as e:
        log.error(f"_resolve_device_path({address}): {e}")
    return None


def _resolve_adapter_path(bus) -> str | None:
    """Return the D-Bus object path of the first Adapter1, or None."""
    try:
        for path, ifaces in _get_managed_objects(bus).items():
            if _IFACE_ADAPTER in ifaces:
                return path
    except Exception as e:
        log.error(f"_resolve_adapter_path: {e}")
    return None


def _device_to_dict(props: dict) -> dict:
    return {
        "address":   str(props.get("Address", "")),
        "name":      str(props.get("Name", props.get("Alias", "Unknown"))),
        "connected": bool(props.get("Connected", False)),
        "trusted":   bool(props.get("Trusted", False)),
        "paired":    bool(props.get("Paired", False)),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_paired(bus) -> list[dict]:
    """
    Return all devices with Paired=True or Trusted=True.
    Order is arbitrary (as returned by GetManagedObjects).
    """
    result = []
    try:
        for _path, ifaces in _get_managed_objects(bus).items():
            dev = ifaces.get(_IFACE_DEVICE)
            if dev is None:
                continue
            if dev.get("Paired") or dev.get("Trusted"):
                result.append(_device_to_dict(dev))
    except Exception as e:
        log.error(f"list_paired: {e}")
    return result


def get_info(bus, address: str) -> dict | None:
    """
    Return info dict for a single device, or None if not found.
    """
    try:
        for _path, ifaces in _get_managed_objects(bus).items():
            dev = ifaces.get(_IFACE_DEVICE)
            if dev and str(dev.get("Address", "")) == address:
                return _device_to_dict(dev)
    except Exception as e:
        log.error(f"get_info({address}): {e}")
    return None


def remove(bus, address: str) -> bool:
    """
    Remove a paired device via Adapter1.RemoveDevice.
    Returns True on success, False on error.
    """
    try:
        import dbus
        adapter_path = _resolve_adapter_path(bus)
        if not adapter_path:
            log.error(f"remove({address}): no adapter found")
            return False
        device_path = _resolve_device_path(bus, address)
        if not device_path:
            log.warning(f"remove({address}): device not found in BlueZ")
            return False
        adapter = dbus.Interface(
            bus.get_object(_BLUEZ_SERVICE, adapter_path),
            _IFACE_ADAPTER,
        )
        adapter.RemoveDevice(device_path)
        log.info(f"Device {address} removed from BlueZ")
        return True
    except Exception as e:
        log.error(f"remove({address}): {e}")
        return False


def connect(
    bus,
    address: str,
    timeout_s: int = 8,
    on_connected: Callable[[str], None] | None = None,
    on_failed: Callable[[str, str], None] | None = None,
) -> None:
    """
    Connect to an already-paired device asynchronously.

    Device1.Connect() is dispatched on the GLib mainloop via
    reply_handler/error_handler. A daemon watchdog thread fires on_failed
    after `timeout_s` seconds if BlueZ has not replied, preventing the
    native ~25 s BlueZ timeout from stalling callers.

    Callbacks (both optional):
        on_connected(address)
        on_failed(address, error_str)
    """
    device_path = _resolve_device_path(bus, address)
    if not device_path:
        log.warning(f"connect({address}): device not found in BlueZ")
        if on_failed:
            on_failed(address, "Device not found in BlueZ")
        return

    # Shared flag: whichever side fires first (BlueZ or watchdog) wins.
    _replied = threading.Event()

    def _reply_handler():
        if _replied.is_set():
            return
        _replied.set()
        log.info(f"Device1.Connect() succeeded for {address}")
        if on_connected:
            on_connected(address)

    def _error_handler(err):
        if _replied.is_set():
            return
        _replied.set()
        err_str = str(err)
        # AlreadyConnected is a success condition
        if "AlreadyConnected" in err_str:
            log.info(f"Device {address} already connected — treating as success")
            if on_connected:
                on_connected(address)
            return
        log.warning(f"Device1.Connect() failed for {address}: {err_str}")
        if on_failed:
            on_failed(address, err_str)

    def _watchdog():
        if not _replied.wait(timeout=timeout_s):
            log.warning(f"connect({address}): timeout after {timeout_s}s — cancelling")
            _replied.set()
            if on_failed:
                on_failed(address, f"Timeout after {timeout_s}s")

    try:
        import dbus
        device_iface = dbus.Interface(
            bus.get_object(_BLUEZ_SERVICE, device_path),
            _IFACE_DEVICE,
        )
        device_iface.Connect(
            reply_handler=_reply_handler,
            error_handler=_error_handler,
        )
        threading.Thread(
            target=_watchdog,
            daemon=True,
            name=f"bt-connect-watchdog-{address}",
        ).start()
        log.debug(f"Device1.Connect() dispatched for {address} (timeout={timeout_s}s)")
    except Exception as e:
        if not _replied.is_set():
            _replied.set()
            log.error(f"connect({address}): dispatch failed: {e}")
            if on_failed:
                on_failed(address, str(e))


def disconnect(
    bus,
    address: str,
    on_disconnected: Callable[[str], None] | None = None,
    on_failed: Callable[[str, str], None] | None = None,
) -> None:
    """
    Disconnect a connected device asynchronously via Device1.Disconnect().
    """
    device_path = _resolve_device_path(bus, address)
    if not device_path:
        log.warning(f"disconnect({address}): device not found in BlueZ")
        if on_failed:
            on_failed(address, "Device not found in BlueZ")
        return

    def _reply_handler():
        log.info(f"Device1.Disconnect() succeeded for {address}")
        if on_disconnected:
            on_disconnected(address)

    def _error_handler(err):
        err_str = str(err)
        if "NotConnected" in err_str:
            log.info(f"Device {address} was not connected — treating as success")
            if on_disconnected:
                on_disconnected(address)
            return
        log.warning(f"Device1.Disconnect() failed for {address}: {err_str}")
        if on_failed:
            on_failed(address, err_str)

    try:
        import dbus
        device_iface = dbus.Interface(
            bus.get_object(_BLUEZ_SERVICE, device_path),
            _IFACE_DEVICE,
        )
        device_iface.Disconnect(
            reply_handler=_reply_handler,
            error_handler=_error_handler,
        )
        log.debug(f"Device1.Disconnect() dispatched for {address}")
    except Exception as e:
        log.error(f"disconnect({address}): dispatch failed: {e}")
        if on_failed:
            on_failed(address, str(e))
