"""
NemoHeadUnit-Wireless v2 — bluetooth module

Module contract:
  Name        : bluetooth
  Subscribes  : system.start
                system.stop
                bluetooth.discover          {duration_sec: int}
                bluetooth.pair              {device_address: str}
                bluetooth.confirm_pairing   {device_address: str, pin: str}
  Publishes   : bluetooth.device.found         {address, name, rssi}
                bluetooth.discovery.completed  {devices: [...]}
                bluetooth.pairing.pin          {device_address, pin}
                bluetooth.pairing.completed    {device_address}
                bluetooth.pairing.failed       {device_address, error}
                bluetooth.rfcomm.connected     {device_address}
                bluetooth.error                {error}

Internal helpers (no ZMQ dependency):
  bluez_adapter.py  — D-Bus / BlueZ init, profile registration
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

from shared.bus_client import BusClient  # noqa: E402
from shared.logger import get_logger     # noqa: E402

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

# ---------------------------------------------------------------------------
# Module-level singletons (created on system.start)
# ---------------------------------------------------------------------------

_adapter: BluezAdapter | None = None
_discovery: DiscoverySession | None = None
_pairing: PairingAgent | None = None
_rfcomm: RfcommListener | None = None

# ---------------------------------------------------------------------------
# system.start / system.stop
# ---------------------------------------------------------------------------

def on_system_start(topic: str, payload: dict) -> None:
    global _adapter, _discovery, _pairing, _rfcomm

    log.info("system.start received — initialising Bluetooth subsystem")

    _adapter = BluezAdapter()
    if not _adapter.init():
        bus.publish("bluetooth.error", {"error": "D-Bus init failed"})
        return

    if not _adapter.register_profiles():
        bus.publish("bluetooth.error", {"error": "Profile registration failed"})
        return

    _adapter.set_discoverable(True)

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
    duration = int(payload.get("duration_sec", 10))
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
    pin = payload.get("pin", "")
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
    bus.subscribe("system.start",              on_system_start)
    bus.subscribe("system.stop",               on_system_stop)
    bus.subscribe("bluetooth.discover",        on_discover)
    bus.subscribe("bluetooth.pair",            on_pair)
    bus.subscribe("bluetooth.confirm_pairing", on_confirm_pairing)

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
