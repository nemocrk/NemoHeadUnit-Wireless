"""
NemoHeadUnit-Wireless v2 — tcp_server module

Module contract:
  Name        : tcp_server
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                rfcomm.handshake.completed  {device_address, phone_ip}
  Publishes   : system.module_ready          {name, priority}
                system.ready                 {name, priority}
                tcp.server.started          {host, port}
                tcp.session.connected       {address}
                aa.frame.received           {channel_id, flags, payload_hex}
                tcp.session.closed          {}
                tcp.server.error            {error}

Flow:
  1. Waits for rfcomm.handshake.completed — phone is now on the WiFi AP
  2. Starts TCPServer on port 5288
  3. Accepts the phone connection (with optional SSL)
  4. FrameRelay reads AA frames and publishes aa.frame.received for each one
  5. On socket close → publishes tcp.session.closed
  6. On system.stop → server + relay shutdown

Internal helpers (no ZMQ):
  server.py      — TCP bind/listen/accept, optional SSL wrap
  frame_relay.py — AA frame header parse, per-frame callback
"""

import sys
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

from shared.bus_client import BusClient           # noqa: E402
from shared.logger import get_logger              # noqa: E402
from tcp_server.server import TCPServer           # noqa: E402
from tcp_server.frame_relay import FrameRelay     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "tcp_server"
PRIORITY    = 1  # service level

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_server: Optional[TCPServer] = None
_relay:  Optional[FrameRelay] = None

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

    log.info(f"system.start priority={PRIORITY} — tcp_server ready")

    # No blocking init — this module is fully event-driven.
    # The TCP server starts only when rfcomm.handshake.completed arrives.
    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — tcp_server online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — shutting down TCP server")
    _teardown()
    bus.stop()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def on_handshake_completed(topic: str, payload: dict) -> None:
    """
    Phone has joined our WiFi AP — start the TCP server and wait for it.
    Runs in a background thread so the bus receive loop stays unblocked.
    """
    device_address = payload.get("device_address", "")
    phone_ip       = payload.get("phone_ip", "")
    log.info(f"Handshake completed from {device_address} (phone_ip={phone_ip}) — starting TCP server")
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _start_server() -> None:
    global _server, _relay

    _server = TCPServer()  # extend here to pass ssl_certfile/ssl_keyfile
    if not _server.start():
        bus.publish("tcp.server.error", {"error": "TCPServer.start() failed"})
        return

    bus.publish("tcp.server.started", {
        "host": _server.host,
        "port": _server.port,
    })

    result = _server.accept()
    if result is None:
        bus.publish("tcp.server.error", {"error": "No connection within timeout"})
        _teardown()
        return

    conn, address = result
    log.info(f"Phone connected: {address}")
    bus.publish("tcp.session.connected", {"address": address})

    _relay = FrameRelay(
        sock=conn,
        on_frame_cb=_on_frame,
        on_closed_cb=_on_session_closed,
    )
    _relay.start()  # blocking — returns when socket closes


def _teardown() -> None:
    global _server, _relay
    if _relay:
        _relay.stop()
        _relay = None
    if _server:
        _server.stop()
        _server = None


# ---------------------------------------------------------------------------
# FrameRelay callbacks → bus events
# ---------------------------------------------------------------------------

def _on_frame(channel_id: int, flags: int, payload: bytes) -> None:
    """
    Called for every AA frame received from the phone.
    Publishes payload as hex string to keep JSON-serialisable.
    """
    bus.publish("aa.frame.received", {
        "channel_id":  channel_id,
        "flags":       flags,
        "payload_hex": payload.hex(),
    })


def _on_session_closed() -> None:
    log.info("AA TCP session closed")
    bus.publish("tcp.session.closed", {})
    _teardown()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart",        on_system_readytostart)
    bus.subscribe("system.start",               on_system_start)
    bus.subscribe("system.stop",                on_system_stop)
    bus.subscribe("rfcomm.handshake.completed", on_handshake_completed)

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
