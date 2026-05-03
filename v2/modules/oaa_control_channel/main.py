"""
NemoHeadUnit-Wireless v2 — oaa_control_channel module

Module contract:
  Name        : oaa_control_channel
  Priority    : 2  (depends on tcp_server at priority 1)
  Subscribes  : system.readytostart
                system.start
                system.stop
                tcp.session.connected    {address}
                tcp.session.closed       {}
                aa.frame.ch0             {channel_id, flags, payload_hex}
  Publishes   : system.module_ready      {name, priority}
                system.ready             {name, priority}
                aa.frame.send            {channel_id, flags, payload_hex}
                aa.session.active        {}         ← session fully negotiated
                aa.session.shutdown      {}         ← phone disconnected
                aa.handshake.state       {state}    ← debug / UI

Handshake flow (end-to-end):
  1. On tcp.session.connected: reset handshake state machine
  2. On aa.frame.ch0:          feed frame to ControlChannelHandshake
  3. Handshake replies via aa.frame.send → tcp_server writes back to socket
  4. On ACTIVE:  publish aa.session.active
  5. On tcp.session.closed: publish aa.session.shutdown + reset
"""

import sys
import time
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient  # noqa: E402
from shared.logger import get_logger     # noqa: E402
from oaa_control_channel.frame_codec import encode_control_frame  # noqa: E402
from oaa_control_channel.handshake import ControlChannelHandshake  # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "oaa_control_channel"
PRIORITY    = 2

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_handshake: ControlChannelHandshake | None = None


def _make_handshake() -> ControlChannelHandshake:
    """Instantiate a fresh handshake state machine wired to the bus."""
    def send_fn(message_id: int, proto_body: bytes, encrypted: bool = False) -> None:
        frame = encode_control_frame(message_id, proto_body, encrypted=encrypted)
        log.debug("CH0 → msg_id=0x%04x len=%d enc=%s",
                  message_id, len(proto_body), encrypted)
        bus.publish("aa.frame.send", frame)

    return ControlChannelHandshake(
        send_fn=send_fn,
        on_active_cb=_on_session_active,
        on_shutdown_cb=_on_session_shutdown,
    )


# ---------------------------------------------------------------------------
# Boot protocol
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info("system.readytostart — announcing priority %d", PRIORITY)
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info("system.start priority=%d — oaa_control_channel ready", PRIORITY)
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — oaa_control_channel stopping")
    bus.stop()


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def on_tcp_session_connected(topic: str, payload: dict) -> None:
    global _handshake
    address = payload.get("address", "?")
    log.info("TCP session connected from %s — initialising handshake", address)
    _handshake = _make_handshake()
    bus.publish("aa.handshake.state", {"state": "IDLE"})


def on_tcp_session_closed(topic: str, payload: dict) -> None:
    global _handshake
    log.info("TCP session closed — resetting handshake")
    _handshake = None
    bus.publish("aa.session.shutdown", {})
    bus.publish("aa.handshake.state", {"state": "DISCONNECTED"})


# ---------------------------------------------------------------------------
# Frame handler — channel 0 only
# ---------------------------------------------------------------------------

def on_frame_ch0(topic: str, payload: dict) -> None:
    global _handshake
    if _handshake is None:
        log.warning("aa.frame.ch0 received but no active handshake — dropping")
        return

    try:
        channel_id  = int(payload["channel_id"])
        flags       = int(payload["flags"])
        raw_payload = bytes.fromhex(payload["payload_hex"])
    except (KeyError, ValueError) as exc:
        log.error("on_frame_ch0: malformed payload — %s", exc)
        return

    _handshake.on_message(channel_id, flags, raw_payload)
    # Publish current state for observability
    bus.publish("aa.handshake.state", {"state": _handshake.state.name})


# ---------------------------------------------------------------------------
# Handshake callbacks
# ---------------------------------------------------------------------------

def _on_session_active() -> None:
    log.info("✓ Android Auto session ACTIVE")
    bus.publish("aa.session.active", {})
    bus.publish("aa.handshake.state", {"state": "ACTIVE"})


def _on_session_shutdown() -> None:
    log.info("Android Auto SHUTDOWN requested by phone")
    bus.publish("aa.session.shutdown", {})
    bus.publish("aa.handshake.state", {"state": "SHUTDOWN"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart",     on_system_readytostart)
    bus.subscribe("system.start",            on_system_start)
    bus.subscribe("system.stop",             on_system_stop)
    bus.subscribe("tcp.session.connected",   on_tcp_session_connected)
    bus.subscribe("tcp.session.closed",      on_tcp_session_closed)
    bus.subscribe("aa.frame.ch0",            on_frame_ch0)

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
