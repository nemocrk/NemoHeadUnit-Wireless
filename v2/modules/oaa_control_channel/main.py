"""
NemoHeadUnit-Wireless v2 — oaa_control_channel module

Module contract:
  Name        : oaa_control_channel
  Priority    : 2  (depends on tcp_server at priority 1)
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response          {module, config, requester}  ← config pre-load
                config.changed           {module, key, value}          ← session restart trigger
                tcp.session.connected    {address}
                tcp.session.closed       {}
                aa.frame.ch0             {channel_id, flags, payload_hex}
                tcp.server.tls_handshake            {outgoing_hex}  ← forward TLS blob to phone
                tcp.server.tls_handshake_completed  {}              ← send AUTH_COMPLETE
  Publishes   : system.module_ready      {name, priority}
                system.ready             {name, priority}
                config.get               {module, requester, defaults}  ← pre-load on boot
                aa.frame.send            {channel_id, flags, payload_hex}
                aa.handshake.start_tls   {}              ← trigger AACryptor init in tcp_server
                aa.handshake.feed_input  {payload_hex}   ← relay SSL round bytes to tcp_server
                aa.session.active        {}               ← session fully negotiated
                aa.session.shutdown      {}               ← phone disconnected
                aa.session.restart       {}               ← config changed, session must reopen
                aa.handshake.state       {state}          ← debug / UI

Handshake flow (end-to-end):
  1. On tcp.session.connected: reset handshake SM, send VERSION_REQUEST (HU speaks first)
  2. On aa.frame.ch0:          feed frame to ControlChannelHandshake
  3. Handshake replies via aa.frame.send → tcp_server writes back to socket
  4. TLS delegated to tcp_server: handshake publishes aa.handshake.start_tls / feed_input,
     tcp_server replies tcp.server.tls_handshake / tls_handshake_completed
  5. On ACTIVE:  publish aa.session.active
  6. On tcp.session.closed: publish aa.session.shutdown + reset

Config flow:
  1. on_system_start() publishes config.get with DEFAULTS (first-boot seeding)
  2. on_config_response() stores the returned dict in _cfg (in-memory)
  3. system.ready is published only after _cfg is populated
  4. build_service_discovery_response() reads from _cfg at handshake time
  5. On config.changed for this module: update _cfg, close active session,
     publish aa.session.restart so tcp_server drops the TCP connection.
     The phone will reconnect and the next handshake uses the new values.
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
from oaa_control_channel.service_discovery import DEFAULTS as _CFG_DEFAULTS  # noqa: E402

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
_cfg: dict = dict(_CFG_DEFAULTS)  # in-memory config; populated from config_manager at boot
_cfg_loaded: bool = False          # True once config.response has been received


def _make_handshake() -> ControlChannelHandshake:
    """Instantiate a fresh handshake state machine wired to the bus."""
    def send_fn(message_id: int, proto_body: bytes, encrypted: bool = False) -> None:
        frame = encode_control_frame(message_id, proto_body, encrypted=encrypted)
        log.info("CH0 → msg_id=0x%04x len=%d enc=%s",
                  message_id, len(proto_body), encrypted)
        bus.publish("aa.frame.send", frame)

    return ControlChannelHandshake(
        send_fn=send_fn,
        publish_fn=bus.publish,
        cfg=_cfg,
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
    log.info("system.start priority=%d — requesting config from config_manager", PRIORITY)
    bus.publish("config.get", {
        "module":    MODULE_NAME,
        "requester": MODULE_NAME,
        "defaults":  _CFG_DEFAULTS,
    })
    # system.ready is published in on_config_response once _cfg is populated


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — oaa_control_channel stopping")
    bus.stop()


# ---------------------------------------------------------------------------
# Config handlers
# ---------------------------------------------------------------------------

def on_config_response(topic: str, payload: dict) -> None:
    global _cfg, _cfg_loaded

    if payload.get("requester") != MODULE_NAME:
        return  # not for us
    if payload.get("module") != MODULE_NAME:
        return

    received: dict = payload.get("config", {})
    if received:
        _cfg.update(received)
        log.info("Config loaded: %d keys", len(_cfg))
    else:
        log.warning("config.response returned empty config — using built-in defaults")

    _cfg_loaded = True
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published — oaa_control_channel online")


def on_config_changed(topic: str, payload: dict) -> None:
    """A config value for this module changed at runtime.

    Update the in-memory dict and trigger a session restart so the
    next handshake uses the new values.  The phone will reconnect
    automatically.
    """
    global _cfg, _handshake

    if payload.get("module") != MODULE_NAME:
        return

    key   = payload.get("key")
    value = payload.get("value")
    _cfg[key] = value
    log.info("Config updated: %s = %r — triggering session restart", key, value)

    if _handshake is not None:
        _handshake = None
        bus.publish("aa.session.shutdown", {})
        bus.publish("aa.handshake.state",  {"state": "DISCONNECTED"})

    bus.publish("aa.session.restart", {})


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def on_tcp_session_connected(topic: str, payload: dict) -> None:
    global _handshake
    address = payload.get("address", "?")
    log.info("TCP session connected from %s — initialising handshake", address)
    _handshake = _make_handshake()
    bus.publish("aa.handshake.state", {"state": "IDLE"})
    # HU always speaks first: phone waits in silence for VERSION_REQUEST
    _handshake.send_version_request()


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
    bus.publish("aa.handshake.state", {"state": _handshake.state.name})


# ---------------------------------------------------------------------------
# TLS delegation handlers (from tcp_server)
# ---------------------------------------------------------------------------

def on_tls_handshake(topic: str, payload: dict) -> None:
    """tcp_server has a TLS blob to send to the phone."""
    if _handshake is None:
        log.warning("tcp.server.tls_handshake received but no active handshake — dropping")
        return
    try:
        outgoing_hex = payload["outgoing_hex"]
    except KeyError as exc:
        log.error("on_tls_handshake: missing key — %s", exc)
        return
    _handshake.on_tls_handshake_blob(outgoing_hex)
    bus.publish("aa.handshake.state", {"state": _handshake.state.name})


def on_tls_handshake_completed(topic: str, payload: dict) -> None:
    """tcp_server signals TLS is_active() — send AUTH_COMPLETE to phone."""
    if _handshake is None:
        log.warning("tcp.server.tls_handshake_completed received but no active handshake — dropping")
        return
    _handshake.on_tls_complete()
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
    bus.subscribe("system.readytostart",                on_system_readytostart)
    bus.subscribe("system.start",                       on_system_start)
    bus.subscribe("system.stop",                        on_system_stop)
    bus.subscribe("config.response",                    on_config_response)
    bus.subscribe("config.changed",                     on_config_changed)
    bus.subscribe("tcp.session.connected",              on_tcp_session_connected)
    bus.subscribe("tcp.session.closed",                 on_tcp_session_closed)
    bus.subscribe("aa.frame.ch0",                       on_frame_ch0)
    bus.subscribe("tcp.server.tls_handshake",           on_tls_handshake)
    bus.subscribe("tcp.server.tls_handshake_completed", on_tls_handshake_completed)

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
