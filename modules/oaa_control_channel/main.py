"""
NemoHeadUnit-Wireless v2 — oaa_control_channel module

Module contract:
  Name        : oaa_control_channel
  Priority    : 2  (depends on tcp_server at priority 1)
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response          {module, config, requester}  ← handled by ConfigClient
                config.changed           {module, key, value}          ← handled by ConfigClient
                tcp.session.connected    {address}
                tcp.session.closed       {}
                aa.frame.ch0             {channel_id, message_id, encrypted, payload_hex}
                tcp.server.tls_handshake            {outgoing_hex}  ← forward TLS blob to phone
                tcp.server.tls_handshake_completed  {}              ← send AUTH_COMPLETE
                aa.session.restarting    {}  ← tcp_server reset cryptor, send VERSION_REQUEST
  Publishes   : system.module_ready      {name, priority}
                system.ready             {name, priority}
                aa.frame.send            {channel_id, message_id, payload_hex, encrypted}
                aa.handshake.start_tls   {}              ← trigger AACryptor init in tcp_server
                aa.handshake.feed_input  {payload_hex}   ← relay SSL round bytes to tcp_server
                aa.session.active        {}               ← session fully negotiated
                aa.session.shutdown      {}               ← phone disconnected
                aa.session.restart       {}               ← config changed, session must reopen
                aa.handshake.state       {state}          ← debug / UI

Handshake flow (end-to-end):
  1. On tcp.session.connected:    reset handshake SM, send VERSION_REQUEST (HU speaks first)
  2. On aa.frame.ch0:             feed {message_id, encrypted, body} to ControlChannelHandshake
  3. Handshake replies via aa.frame.send → tcp_server writes back to socket
  4. TLS delegated to tcp_server: handshake publishes aa.handshake.start_tls / feed_input,
     tcp_server replies tcp.server.tls_handshake / tls_handshake_completed
  5. On ACTIVE:  publish aa.session.active
  6. On tcp.session.closed: publish aa.session.shutdown + reset

Restart flow (config change):
  1. _on_config_changed() updates _cfg, publishes aa.session.restart
  2. tcp_server sends SHUTDOWN_REQUEST to phone, waits for ack, deinit() cryptor,
     publishes aa.session.restarting
  3. on_aa_session_restarting() creates a fresh ControlChannelHandshake (with updated _cfg)
     and immediately sends VERSION_REQUEST on the existing TCP connection

Config flow (flat-scalar schema):
  1. on_system_start() calls cfg.get(schema=_SCHEMA) via ConfigClient.
  2. ConfigClient delivers config.response → _on_config_loaded(config).
  3. system.ready is published only after _on_config_loaded populates _cfg.
  4. _cfg is passed directly to build_from_schema_cfg() inside
     ControlChannelHandshake at SERVICE_DISCOVERY_REQUEST time.
  5. On config.changed: _cfg[key] is updated and aa.session.restart triggers graceful restart.
"""

import sys
import time
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROTO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient              # noqa: E402
from shared.logger import get_logger                 # noqa: E402
from shared.config_client import ConfigClient        # noqa: E402
from oaa_control_channel.handshake import ControlChannelHandshake  # noqa: E402
from oaa_control_channel.service_discovery import SEMANTIC_DEFAULTS, _SCHEMA  # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "oaa_control_channel"
PRIORITY    = 2

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_handshake: ControlChannelHandshake | None = None
_cfg: dict = dict(SEMANTIC_DEFAULTS)


def _make_handshake() -> ControlChannelHandshake:
    """Instantiate a fresh handshake state machine wired to the bus.

    send_fn publishes on aa.frame.send using the new contract:
        {channel_id, message_id, payload_hex, encrypted}
    """
    def send_fn(message_id: int, proto_body: bytes, encrypted: bool = False) -> None:
        log.info("CH0 → msg_id=0x%04x len=%d enc=%s", message_id, len(proto_body), encrypted)
        bus.publish("aa.frame.send", {
            "channel_id":  0,
            "message_id":  message_id,
            "payload_hex": proto_body.hex(),
            "encrypted":   encrypted,
        })

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
    cfg.get(schema=_SCHEMA)


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — oaa_control_channel stopping")
    bus.stop()


# ---------------------------------------------------------------------------
# ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _cfg
    if config:
        merged = {k: v for k, v in config.items() if k in _cfg}
        _cfg.update(merged)
        log.info(
            "Config loaded: %d/%d key(s) merged from config_manager",
            len(merged), len(_cfg),
        )
    else:
        log.warning("config.response returned empty config — using SEMANTIC_DEFAULTS")

    bus.subscribe("tcp.session.connected",              on_tcp_session_connected)
    bus.subscribe("tcp.session.closed",                 on_tcp_session_closed)
    bus.subscribe("aa.frame.ch0",                       on_frame_ch0)
    bus.subscribe("tcp.server.tls_handshake",           on_tls_handshake)
    bus.subscribe("tcp.server.tls_handshake_completed", on_tls_handshake_completed)
    bus.subscribe("aa.session.restarting",              on_aa_session_restarting)
    bus.subscribe("channel_manager.channels_ready",     on_channel_ready)

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published — oaa_control_channel online")


def _on_config_changed(key: str, value) -> None:
    global _cfg, _handshake

    if key not in _cfg:
        log.warning("_on_config_changed: key %r is not a known schema key — ignoring", key)
        return

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
    _handshake.send_version_request()


def on_tcp_session_closed(topic: str, payload: dict) -> None:
    global _handshake
    log.info("TCP session closed — resetting handshake")
    _handshake = None
    bus.publish("aa.session.shutdown", {})
    bus.publish("aa.handshake.state", {"state": "DISCONNECTED"})


def on_aa_session_restarting(topic: str, payload: dict) -> None:
    global _handshake
    log.info("aa.session.restarting — rebuilding handshake with updated config")
    _handshake = _make_handshake()
    bus.publish("aa.handshake.state", {"state": "IDLE"})
    _handshake.send_version_request()
    log.info("VERSION_REQUEST sent — waiting for VERSION_RESPONSE")


# ---------------------------------------------------------------------------
# Frame handler — channel 0 only
# ---------------------------------------------------------------------------

def on_frame_ch0(topic: str, payload: dict) -> None:
    """Receive a fully assembled, decrypted ch0 frame from tcp_server.

    Payload contract: {channel_id, message_id, encrypted, payload_hex}
    message_id and body are already extracted by tcp_server.
    """
    if _handshake is None:
        log.warning("aa.frame.ch0 received but no active handshake — dropping")
        return

    try:
        message_id = int(payload["message_id"])
        encrypted  = bool(payload.get("encrypted", False))
        body       = bytes.fromhex(payload["payload_hex"])
    except (KeyError, ValueError) as exc:
        log.error("on_frame_ch0: malformed payload — %s", exc)
        return

    _handshake.on_message(message_id, body, encrypted)
    bus.publish("aa.handshake.state", {"state": _handshake.state.name})


# ---------------------------------------------------------------------------
# Channel manager integration
# ---------------------------------------------------------------------------

def on_channel_ready(topic: str, payload: dict) -> None:
    if _handshake is None:
        log.warning("channel_manager.channels_ready received but no active handshake — dropping")
        return

    try:
        sdr_bytes_hex = payload["sdr_bytes_hex"]
    except (KeyError, ValueError) as exc:
        log.error("on_channel_ready: malformed payload — %s", exc)
        return

    _handshake.on_channels_ready(sdr_bytes_hex)
    bus.publish("aa.handshake.state", {"state": _handshake.state.name})


# ---------------------------------------------------------------------------
# TLS delegation handlers (from tcp_server)
# ---------------------------------------------------------------------------

def on_tls_handshake(topic: str, payload: dict) -> None:
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
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart",                on_system_readytostart)
    bus.subscribe("system.start",                       on_system_start)
    bus.subscribe("system.stop",                        on_system_stop)

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
