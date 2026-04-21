"""
NemoHeadUnit-Wireless v2 — bluetooth module

Module contract:
  Name        : bluetooth
  Subscribes  : system.start
                system.stop
                bluetooth.discover          {duration_sec: int}
                bluetooth.pair              {device_address: str}
                bluetooth.confirm_pairing   {device_address: str, pin: str}
                config.response             (filtered by module=bluetooth)
                config.changed              (filtered by module=bluetooth)
  Publishes   : bluetooth.device.found         {address, name, rssi}
                bluetooth.discovery.completed  {devices: [...]}
                bluetooth.pairing.pin          {device_address, pin}
                bluetooth.pairing.completed    {device_address}
                bluetooth.pairing.failed       {device_address, error}
                bluetooth.rfcomm.connected     {device_address}
                bluetooth.error                {error}
                config.get                     (on startup, to load persisted config)
                config.set                     (when writing defaults for the first time)

Configuration keys (v2/config/bluetooth.yaml):
  discoverable         bool   default: true
  discoverable_timeout int    default: 0  (seconds, 0 = permanent)
  discovery_duration_sec int  default: 10
  adapter_name         str    default: NemoHeadUnit

Internal helpers (no ZMQ dependency):
  bluez_adapter.py  — D-Bus / BlueZ init, profile registration, set_name
  discovery.py      — timed device discovery
  pairing.py        — agent, PIN, confirm
  rfcomm.py         — RFCOMM channel 8 accept loop
"""

import sys
from pathlib import Path

_HERE    = Path(__file__).parent   # v2/modules/bluetooth/
_MODULES = _HERE.parent            # v2/modules/
_V2      = _MODULES.parent         # v2/

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient        # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402

from bluetooth.bluez_adapter import BluezAdapter   # noqa: E402
from bluetooth.discovery import DiscoverySession   # noqa: E402
from bluetooth.pairing import PairingAgent         # noqa: E402
from bluetooth.rfcomm import RfcommListener        # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "bluetooth"

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "discoverable":          True,
    "discoverable_timeout":  0,
    "discovery_duration_sec": 10,
    "adapter_name":          "NemoHeadUnit",
}

# Live config — starts from defaults, overwritten when config.response arrives
_config: dict = dict(_DEFAULTS)

# ---------------------------------------------------------------------------
# Module-level singletons (created on system.start)
# ---------------------------------------------------------------------------

_adapter:   BluezAdapter    | None = None
_discovery: DiscoverySession | None = None
_pairing:   PairingAgent    | None = None
_rfcomm:    RfcommListener  | None = None

# ---------------------------------------------------------------------------
# ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    """Received full config from config_manager. Apply to live state."""
    global _config
    # Merge: persisted values override defaults; unknown keys are ignored
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in config.items() if k in _DEFAULTS})
    _config = merged
    log.info(f"Config loaded: {_config}")
    _apply_config()


def _on_config_changed(key: str, value) -> None:
    """A single key was updated by an external caller — apply immediately."""
    if key not in _DEFAULTS:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")
    _apply_config()


def _apply_config() -> None:
    """Push current _config values onto the live adapter (if ready)."""
    if _adapter is None:
        return
    _adapter.set_name(str(_config["adapter_name"]))
    _adapter.set_discoverable(
        bool(_config["discoverable"]),
        timeout=int(_config["discoverable_timeout"]),
    )


# ---------------------------------------------------------------------------
# system.start / system.stop
# ---------------------------------------------------------------------------

def on_system_start(topic: str, payload: dict) -> None:
    global _adapter, _pairing, _rfcomm

    log.info("system.start received — initialising Bluetooth subsystem")

    # Request persisted config; _on_config_loaded will call _apply_config
    # once config_manager responds. _DEFAULTS are used in the meantime.
    cfg.get()

    _adapter = BluezAdapter()
    if not _adapter.init():
        bus.publish("bluetooth.error", {"error": "D-Bus init failed"})
        return

    if not _adapter.register_profiles():
        bus.publish("bluetooth.error", {"error": "Profile registration failed"})
        return

    # Apply defaults immediately; config.response will override when it arrives
    _apply_config()

    _pairing = PairingAgent(
        adapter=_adapter,
        on_pin_requested=_on_pin_requested,
        on_pairing_completed=_on_pairing_completed,
        on_pairing_failed=_on_pairing_failed,
    )
    _pairing.register()

    _rfcomm = RfcommListener(on_connected_cb=_on_rfcomm_connected)
    if not _rfcomm.start():
        bus.publish("bluetooth.error", {"error": "RFCOMM listener failed to start"})
        return

    log.info("Bluetooth subsystem ready")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — shutting down Bluetooth")
    if _rfcomm:
        _rfcomm.stop()
    if _pairing:
        _pairing.unregister()
    if _adapter:
        _adapter.set_discoverable(False)
        _adapter.shutdown()
    bus.stop()


# ---------------------------------------------------------------------------
# bluetooth.discover
# ---------------------------------------------------------------------------

def on_discover(topic: str, payload: dict) -> None:
    global _discovery
    if _adapter is None:
        bus.publish("bluetooth.error", {"error": "Adapter not ready"})
        return
    # Caller may override duration; fallback to configured default
    duration = int(payload.get("duration_sec", _config["discovery_duration_sec"]))
    log.info(f"Discovery requested for {duration}s")
    _discovery = DiscoverySession(
        adapter=_adapter,
        on_device_cb=_on_device_found,
        on_done_cb=_on_discovery_done,
    )
    _discovery.start(duration_sec=duration)


# ---------------------------------------------------------------------------
# bluetooth.pair
# ---------------------------------------------------------------------------

def on_pair(topic: str, payload: dict) -> None:
    if _pairing is None:
        bus.publish("bluetooth.error", {"error": "Pairing agent not ready"})
        return
    address = payload.get("device_address", "")
    if not address:
        bus.publish("bluetooth.error", {"error": "bluetooth.pair: missing device_address"})
        return
    log.info(f"Pairing requested with {address}")
    _pairing.pair(address)


# ---------------------------------------------------------------------------
# bluetooth.confirm_pairing
# ---------------------------------------------------------------------------

def on_confirm_pairing(topic: str, payload: dict) -> None:
    if _pairing is None:
        bus.publish("bluetooth.error", {"error": "Pairing agent not ready"})
        return
    address = payload.get("device_address", "")
    pin     = payload.get("pin", "")
    if not address or not pin:
        bus.publish("bluetooth.error", {"error": "bluetooth.confirm_pairing: missing fields"})
        return
    log.info(f"Confirm pairing for {address} pin={pin}")
    _pairing.confirm(address, pin)


# ---------------------------------------------------------------------------
# Internal callbacks → bus events
# ---------------------------------------------------------------------------

def _on_device_found(address: str, name: str, rssi: int) -> None:
    bus.publish("bluetooth.device.found", {"address": address, "name": name, "rssi": rssi})

def _on_discovery_done(devices: list) -> None:
    bus.publish("bluetooth.discovery.completed", {"devices": devices})

def _on_pin_requested(device_address: str, pin: str) -> None:
    bus.publish("bluetooth.pairing.pin", {"device_address": device_address, "pin": pin})

def _on_pairing_completed(device_address: str) -> None:
    bus.publish("bluetooth.pairing.completed", {"device_address": device_address})

def _on_pairing_failed(device_address: str, error: str) -> None:
    bus.publish("bluetooth.pairing.failed", {"device_address": device_address, "error": error})

def _on_rfcomm_connected(sock, address: str) -> None:
    log.info(f"RFCOMM connected from {address}")
    bus.publish("bluetooth.rfcomm.connected", {"device_address": address})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    # Config callbacks must be registered before bus.start()
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.start",              on_system_start)
    bus.subscribe("system.stop",               on_system_stop)
    bus.subscribe("bluetooth.discover",        on_discover)
    bus.subscribe("bluetooth.pair",            on_pair)
    bus.subscribe("bluetooth.confirm_pairing", on_confirm_pairing)

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
