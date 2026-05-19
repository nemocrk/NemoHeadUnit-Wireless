"""
NemoHeadUnit-Wireless — bluetooth_manager

Module contract:
  Name        : bluetooth_manager
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                bluetooth_manager.discover              {duration_sec: int}
                bluetooth_manager.pair                  {device_address: str}
                bluetooth_manager.confirm_pairing       {device_address: str, pin: str}
                bluetooth_manager.reject_pairing        {device_address: str}
                bluetooth_manager.rfcomm.connected      {device_address: str}  → stops autoconnect
                bluetooth_manager.try_autoconnect       {}                     → (re)starts autoconnect
                bluetooth_manager.paired.list           {}
                bluetooth_manager.paired.remove         {device_address: str}
                bluetooth_manager.paired.connect        {device_address: str}
                bluetooth_manager.paired.disconnect     {device_address: str}
                config.response                 (filtered by module=bluetooth_manager)
                config.changed                  (filtered by module=bluetooth_manager)
  Publishes   : system.module_ready             {name, priority}
                system.ready                    {name, priority}
                bluetooth_manager.device.found          {address, name, rssi}
                bluetooth_manager.discovery.completed   {devices: [...]}
                bluetooth_manager.pairing.pin           {device_address, pin}
                bluetooth_manager.pairing.completed     {device_address}
                bluetooth_manager.pairing.failed        {device_address, error}
                bluetooth_manager.paired.devices        {devices: [{address, name, connected, trusted}]}
                bluetooth_manager.paired.removed        {device_address}
                bluetooth_manager.paired.connected      {device_address}
                bluetooth_manager.paired.disconnected   {device_address}
                bluetooth_manager.paired.failed         {device_address, error}
                bluetooth_manager.error                 {error}

Configuration keys (config/bluetooth_manager.yaml):
  discoverable                  bool   default: true
  discoverable_timeout          int    default: 0  (seconds, 0 = permanent)
  discovery_duration_sec        int    default: 10
  adapter_name                  str    default: NemoHeadUnit
  autoconnect_enabled           bool   default: true
  autoconnect_connect_timeout_s int    default: 8
  autoconnect_backoff_initial_s int    default: 5
  autoconnect_backoff_cap_s     int    default: 60

Internal helpers (no ZMQ dependency):
  bluez_adapter.py    — D-Bus / BlueZ init, profile registration, set_name
  discovery.py        — timed device discovery
  pairing.py          — agent, PIN, confirm
  paired_devices.py   — list/remove/connect/disconnect for already-paired devices
"""

import sys
import threading
from pathlib import Path
import time

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent   # modules/bluetooth_manager/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient              # noqa: E402
from shared.logger import get_logger                 # noqa: E402
from shared.config_client import ConfigClient        # noqa: E402
from shared.config_schema import field_bool, field_int, field_string  # noqa: E402

from bluetooth_manager.bluez_adapter import BluezAdapter     # noqa: E402
from bluetooth_manager.discovery import DiscoverySession     # noqa: E402
from bluetooth_manager.pairing import PairingAgent           # noqa: E402
import bluetooth_manager.paired_devices as paired_devices    # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "bluetooth_manager"
PRIORITY    = 1

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "discoverable":                  field_bool(default=True),
    "discoverable_timeout":          field_int(default=0, min=0),
    "discovery_duration_sec":        field_int(default=10, min=1, max=120),
    "adapter_name":                  field_string(default="NemoHeadUnit"),
    "autoconnect_enabled":           field_bool(default=True),
    "autoconnect_connect_timeout_s": field_int(default=8, min=2, max=30),
    "autoconnect_backoff_initial_s": field_int(default=5, min=1, max=30),
    "autoconnect_backoff_cap_s":     field_int(default=60, min=10, max=300),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_adapter:   BluezAdapter     | None = None
_discovery: DiscoverySession | None = None
_pairing:   PairingAgent     | None = None
_glib_loop  = None

# ---------------------------------------------------------------------------
# Autoconnect state
# ---------------------------------------------------------------------------

_autoconnect_stop:   threading.Event = threading.Event()
_autoconnect_active: bool            = False
_autoconnect_lock:   threading.Lock  = threading.Lock()


# ---------------------------------------------------------------------------
# GLib mainloop
# ---------------------------------------------------------------------------

def _start_glib_mainloop() -> None:
    global _glib_loop
    try:
        from gi.repository import GLib
        _glib_loop = GLib.MainLoop()
        t = threading.Thread(target=_glib_loop.run, daemon=True, name="glib-dbus")
        t.start()
        log.info("GLib mainloop started (thread: glib-dbus)")
    except Exception as e:
        log.error(f"Failed to start GLib mainloop — agent callbacks will not work: {e}")


def _stop_glib_mainloop() -> None:
    global _glib_loop
    if _glib_loop and _glib_loop.is_running():
        _glib_loop.quit()
        log.info("GLib mainloop stopped")
    _glib_loop = None


# ---------------------------------------------------------------------------
# Autoconnect loop
# ---------------------------------------------------------------------------

def _autoconnect_loop() -> None:
    global _autoconnect_active

    backoff = int(_config["autoconnect_backoff_initial_s"])
    cap     = int(_config["autoconnect_backoff_cap_s"])
    timeout = int(_config["autoconnect_connect_timeout_s"])

    log.info(f"Autoconnect loop started (backoff={backoff}s cap={cap}s timeout={timeout}s)")

    try:
        while not _autoconnect_stop.is_set():
            if _adapter is None or _adapter.bus is None:
                log.warning("Autoconnect: adapter not ready, waiting...")
                _autoconnect_stop.wait(backoff)
                continue

            devices = paired_devices.list_paired(_adapter.bus)
            if not devices:
                log.debug("Autoconnect: no paired/trusted devices found")
            else:
                log.info(f"Autoconnect: trying {len(devices)} device(s)")

            for dev in devices:
                if _autoconnect_stop.is_set():
                    break
                address = dev["address"]
                if dev.get("connected"):
                    log.debug(f"Autoconnect: {address} already connected — skipping")
                    continue

                log.info(f"Autoconnect: attempting connect to {address} ({dev.get('name', '?')})")

                done = threading.Event()

                def _on_ok(addr, _ev=done):
                    log.info(f"Autoconnect: connected to {addr}")
                    _ev.set()

                def _on_err(addr, err, _ev=done):
                    log.debug(f"Autoconnect: {addr} failed — {err}")
                    _ev.set()

                paired_devices.connect(
                    _adapter.bus,
                    address,
                    timeout_s=timeout,
                    on_connected=_on_ok,
                    on_failed=_on_err,
                )
                done.wait(timeout=timeout + 1)

            if _autoconnect_stop.is_set():
                break

            log.debug(f"Autoconnect: round done, sleeping {backoff}s")
            _autoconnect_stop.wait(backoff)
            backoff = min(backoff * 2, cap)

    finally:
        with _autoconnect_lock:
            _autoconnect_active = False
        log.info("Autoconnect loop stopped")


def _start_autoconnect() -> None:
    global _autoconnect_active
    if not _config.get("autoconnect_enabled", True):
        log.info("Autoconnect disabled by config — skipping")
        return
    with _autoconnect_lock:
        if _autoconnect_active:
            log.debug("Autoconnect already active — ignoring request")
            return
        _autoconnect_stop.clear()
        _autoconnect_active = True
    threading.Thread(target=_autoconnect_loop, daemon=True, name="bt-autoconnect").start()
    log.info("Autoconnect thread launched")


def _stop_autoconnect(reason: str = "") -> None:
    _autoconnect_stop.set()
    if reason:
        log.info(f"Autoconnect stopped: {reason}")


# ---------------------------------------------------------------------------
# ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config, _pairing
    if not config:
        log.info("No persisted config found — defaults seeded by config_manager.")
    else:
        merged = {k: v.default for k, v in _SCHEMA.items()}
        merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
        _config = merged
        log.info(f"Config loaded: {_config}")

    _apply_config()

    if _pairing is None and _adapter is not None:
        _pairing = PairingAgent(
            adapter=_adapter,
            on_pin_requested=_on_pin_requested,
            on_pairing_completed=_on_pairing_completed,
            on_pairing_failed=_on_pairing_failed,
        )
        _pairing.register()

    log.info("Bluetooth subsystem ready")
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    _start_autoconnect()


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    if isinstance(value, (dict, list)):
        log.warning(f"config.changed: structural value for '{key}' rejected")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")
    _apply_config()


def _apply_config() -> None:
    if _adapter is None:
        return
    _adapter.set_name(str(_config["adapter_name"]))
    _adapter.set_discoverable(
        bool(_config["discoverable"]),
        timeout=int(_config["discoverable_timeout"]),
    )


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    global _adapter
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — initialising Bluetooth subsystem")
    _start_glib_mainloop()
    _adapter = BluezAdapter()
    if not _adapter.init():
        log.error("D-Bus init failed — Bluetooth unavailable")
        bus.publish("bluetooth_manager.error", {"error": "D-Bus init failed"})
        bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    if not _adapter.register_profiles():
        log.error("Profile registration failed")
        bus.publish("bluetooth_manager.error", {"error": "Profile registration failed"})
        bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    cfg.get(schema=_SCHEMA)


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down Bluetooth")
    _stop_autoconnect("system.stop")
    if _pairing:
        _pairing.unregister()
    if _adapter:
        _adapter.set_discoverable(False)
        _adapter.shutdown()
    _stop_glib_mainloop()
    bus.stop()


# ---------------------------------------------------------------------------
# Topic handlers
# ---------------------------------------------------------------------------

def on_discover(topic: str, payload: dict) -> None:
    global _discovery
    if _adapter is None:
        bus.publish("bluetooth_manager.error", {"error": "Adapter not ready"})
        return
    if _discovery is not None and _discovery.is_running:
        log.warning("Discovery already in progress — ignoring duplicate request")
        return
    duration = int(payload.get("duration_sec", _config["discovery_duration_sec"]))
    log.info(f"Discovery requested for {duration}s")
    _discovery = DiscoverySession(
        adapter=_adapter,
        on_device_cb=_on_device_found,
        on_done_cb=_on_discovery_done,
    )
    _discovery.start(duration_sec=duration)


def on_pair(topic: str, payload: dict) -> None:
    if _pairing is None:
        bus.publish("bluetooth_manager.error", {"error": "Pairing agent not ready"})
        return
    address = payload.get("device_address", "")
    if not address:
        bus.publish("bluetooth_manager.error", {"error": "bluetooth_manager.pair: missing device_address"})
        return
    log.info(f"Pairing requested with {address}")
    _pairing.pair(address)


def on_confirm_pairing(topic: str, payload: dict) -> None:
    if _pairing is None:
        bus.publish("bluetooth_manager.error", {"error": "Pairing agent not ready"})
        return
    address = payload.get("device_address", "")
    pin     = payload.get("pin", "")
    if not address or not pin:
        bus.publish("bluetooth_manager.error", {"error": "bluetooth_manager.confirm_pairing: missing fields"})
        return
    log.info(f"Confirm pairing for {address} pin={pin}")
    _pairing.confirm(address, pin)


def on_reject_pairing(topic: str, payload: dict) -> None:
    if _pairing is None:
        bus.publish("bluetooth_manager.error", {"error": "Pairing agent not ready"})
        return
    address = payload.get("device_address", "")
    if address:
        _pairing.reject(address)


def on_rfcomm_connected(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "unknown")
    log.info(f"RFCOMM connected ({address}) — stopping autoconnect loop")
    _stop_autoconnect(f"rfcomm.connected device={address}")


def on_try_autoconnect(topic: str, payload: dict) -> None:
    log.info("bluetooth_manager.try_autoconnect received")
    _start_autoconnect()


def on_paired_list(topic: str, payload: dict) -> None:
    if _adapter is None:
        bus.publish("bluetooth_manager.error", {"error": "Adapter not ready"})
        return
    devices = paired_devices.list_paired(_adapter.bus)
    bus.publish("bluetooth_manager.paired.devices", {"devices": devices})


def on_paired_remove(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    if not address:
        bus.publish("bluetooth_manager.error", {"error": "bluetooth_manager.paired.remove: missing device_address"})
        return
    if _adapter is None:
        bus.publish("bluetooth_manager.error", {"error": "Adapter not ready"})
        return
    ok = paired_devices.remove(_adapter.bus, address)
    if ok:
        bus.publish("bluetooth_manager.paired.removed", {"device_address": address})
    else:
        bus.publish("bluetooth_manager.paired.failed", {"device_address": address, "error": "Remove failed"})


def on_paired_connect(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    if not address:
        bus.publish("bluetooth_manager.error", {"error": "bluetooth_manager.paired.connect: missing device_address"})
        return
    if _adapter is None:
        bus.publish("bluetooth_manager.error", {"error": "Adapter not ready"})
        return
    timeout = int(_config["autoconnect_connect_timeout_s"])
    paired_devices.connect(
        _adapter.bus, address, timeout_s=timeout,
        on_connected=lambda addr: bus.publish("bluetooth_manager.paired.connected", {"device_address": addr}),
        on_failed=lambda addr, err: bus.publish("bluetooth_manager.paired.failed", {"device_address": addr, "error": err}),
    )


def on_paired_disconnect(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    if not address:
        bus.publish("bluetooth_manager.error", {"error": "bluetooth_manager.paired.disconnect: missing device_address"})
        return
    if _adapter is None:
        bus.publish("bluetooth_manager.error", {"error": "Adapter not ready"})
        return
    paired_devices.disconnect(
        _adapter.bus, address,
        on_disconnected=lambda addr: bus.publish("bluetooth_manager.paired.disconnected", {"device_address": addr}),
        on_failed=lambda addr, err: bus.publish("bluetooth_manager.paired.failed", {"device_address": addr, "error": err}),
    )


# ---------------------------------------------------------------------------
# Internal callbacks → bus events
# ---------------------------------------------------------------------------

def _on_device_found(address: str, name: str, rssi: int) -> None:
    bus.publish("bluetooth_manager.device.found", {"address": address, "name": name, "rssi": rssi})

def _on_discovery_done(devices: list) -> None:
    bus.publish("bluetooth_manager.discovery.completed", {"devices": devices})

def _on_pin_requested(device_address: str, pin: str) -> None:
    bus.publish("bluetooth_manager.pairing.pin", {"device_address": device_address, "pin": pin})

def _on_pairing_completed(device_address: str) -> None:
    bus.publish("bluetooth_manager.pairing.completed", {"device_address": device_address})

def _on_pairing_failed(device_address: str, error: str) -> None:
    bus.publish("bluetooth_manager.pairing.failed", {"device_address": device_address, "error": error})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart",                  on_system_readytostart)
    bus.subscribe("system.start",                         on_system_start)
    bus.subscribe("system.stop",                          on_system_stop)
    bus.subscribe("bluetooth_manager.discover",           on_discover)
    bus.subscribe("bluetooth_manager.pair",               on_pair)
    bus.subscribe("bluetooth_manager.confirm_pairing",    on_confirm_pairing)
    bus.subscribe("bluetooth_manager.reject_pairing",     on_reject_pairing)
    bus.subscribe("bluetooth_manager.rfcomm.connected",   on_rfcomm_connected)
    bus.subscribe("bluetooth_manager.try_autoconnect",    on_try_autoconnect)
    bus.subscribe("bluetooth_manager.paired.list",        on_paired_list)
    bus.subscribe("bluetooth_manager.paired.remove",      on_paired_remove)
    bus.subscribe("bluetooth_manager.paired.connect",     on_paired_connect)
    bus.subscribe("bluetooth_manager.paired.disconnect",  on_paired_disconnect)

    log.info("Module started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
