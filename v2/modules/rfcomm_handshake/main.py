"""
NemoHeadUnit-Wireless v2 — rfcomm_handshake module

Module contract:
  Name        : rfcomm_handshake
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                hostapd.ready   {ssid, key, bssid, interface,
                                 gateway_ip, security_mode, ap_type}
  Publishes   : system.module_ready            {name, priority}
                system.ready                   {name, priority}
                rfcomm.handshake.started       {device_address}
                rfcomm.handshake.completed     {device_address, phone_ip}
                rfcomm.handshake.failed        {device_address, error}

Flow:
  1. Waits for hostapd.ready — stores WiFi credentials
  2. Opens an RFCOMM socket to the phone (the phone connects to us
     on RFCOMM ch.8; the bluetooth module accepted it — here we
     open a NEW connection or reuse the shared socket via a
     loopback bridge if needed)
  3. Runs the 5-stage handshake via RfcommHandshake
  4. On success → publishes rfcomm.handshake.completed {phone_ip}
     which triggers tcp_server to start listening

NOTE: Because modules are isolated processes the RFCOMM socket
accepted by the bluetooth module cannot be passed directly.
The bluetooth module keeps the socket open and relays bytes
through a Unix domain socket bridge to this module.
For now, this module opens its own RFCOMM client socket to the
device address published in bluetooth.rfcomm.connected.

Internal helpers (no ZMQ):
  packet.py    — packet encode/decode
  handshake.py — 5-stage handshake state machine
"""

import sys
import socket
import threading
from pathlib import Path
from typing import Optional

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient                    # noqa: E402
from shared.logger import get_logger                       # noqa: E402
from rfcomm_handshake.handshake import RfcommHandshake     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "rfcomm_handshake"
PRIORITY    = 1  # service level

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_credentials: Optional[dict] = None          # set on hostapd.ready
_device_address: Optional[str] = None        # set on bluetooth.rfcomm.connected
_pending_sock: Optional[socket.socket] = None

RFCOMM_CHANNEL = 8

# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart(topic: str, payload: dict) -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return

    log.info(f"system.start priority={PRIORITY} — rfcomm_handshake ready")

    # No blocking init — this module is fully event-driven.
    # Handshake starts when both bluetooth.rfcomm.connected
    # and hostapd.ready have been received.
    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — rfcomm_handshake online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received")
    _close_pending_sock()
    bus.stop()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def on_bluetooth_rfcomm_connected(topic: str, payload: dict) -> None:
    """Store the device address; handshake starts once hostapd.ready arrives."""
    global _device_address
    _device_address = payload.get("device_address", "")
    log.info(f"RFCOMM connected from {_device_address} — waiting for hostapd.ready")
    _try_start_handshake()


def on_hostapd_ready(topic: str, payload: dict) -> None:
    """Store WiFi credentials; start handshake if device address already known."""
    global _credentials
    _credentials = payload
    log.info(f"hostapd.ready received: ssid={payload.get('ssid')} — ready for handshake")
    _try_start_handshake()


# ---------------------------------------------------------------------------
# Handshake trigger
# ---------------------------------------------------------------------------

def _try_start_handshake() -> None:
    """
    Start the handshake only when BOTH the device address AND
    the AP credentials are available.
    """
    if not _device_address or not _credentials:
        return
    log.info(f"Both conditions met — starting handshake with {_device_address}")
    t = threading.Thread(target=_run_handshake, daemon=True)
    t.start()


def _run_handshake() -> None:
    global _pending_sock
    device_address = _device_address

    bus.publish("rfcomm.handshake.started", {"device_address": device_address})

    sock = _connect_rfcomm(device_address)
    if sock is None:
        bus.publish("rfcomm.handshake.failed", {
            "device_address": device_address,
            "error": "RFCOMM connect failed",
        })
        return

    _pending_sock = sock

    hs = RfcommHandshake(
        sock=sock,
        credentials=_credentials,
        on_stage_cb=lambda stage: log.info(f"Handshake stage: {stage}"),
    )
    result = hs.run()

    if result.success:
        log.info(f"Handshake OK — phone_ip={result.phone_ip}")
        bus.publish("rfcomm.handshake.completed", {
            "device_address": device_address,
            "phone_ip":       result.phone_ip,
        })
    else:
        log.error(f"Handshake failed: {result.error}")
        bus.publish("rfcomm.handshake.failed", {
            "device_address": device_address,
            "error":          result.error,
        })

    _close_pending_sock()


# ---------------------------------------------------------------------------
# Socket helpers
# ---------------------------------------------------------------------------

def _connect_rfcomm(address: str) -> Optional[socket.socket]:
    """Open an RFCOMM client socket to the given device address."""
    try:
        import bluetooth  # pybluez
        sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        sock.connect((address, RFCOMM_CHANNEL))
        log.info(f"RFCOMM socket connected to {address} ch.{RFCOMM_CHANNEL}")
        return sock
    except ImportError:
        log.error("pybluez not installed — RFCOMM connect unavailable")
        return None
    except Exception as e:
        log.error(f"RFCOMM connect failed: {e}")
        return None


def _close_pending_sock() -> None:
    global _pending_sock
    if _pending_sock:
        try:
            _pending_sock.close()
        except Exception:
            pass
        _pending_sock = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart",        on_system_readytostart)
    bus.subscribe("system.start",               on_system_start)
    bus.subscribe("system.stop",                on_system_stop)
    bus.subscribe("bluetooth.rfcomm.connected", on_bluetooth_rfcomm_connected)
    bus.subscribe("hostapd.ready",              on_hostapd_ready)

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
