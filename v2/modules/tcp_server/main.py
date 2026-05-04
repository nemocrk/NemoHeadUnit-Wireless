"""
NemoHeadUnit-Wireless v2 — tcp_server module

Module contract:
  Name        : tcp_server
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                rfcomm.handshake.completed  {device_address, phone_ip}
                aa.frame.send               {channel_id, flags, payload_hex}
                aa.handshake.start_tls      {}                 ← oaa_control_channel triggers TLS init
                aa.handshake.feed_input     {payload_hex}      ← SSL round bytes from phone
  Publishes   : system.module_ready          {name, priority}
                system.ready                 {name, priority}
                tcp.server.started          {host, port}
                tcp.session.connected       {address}
                aa.frame.received           {channel_id, flags, payload_hex}  (all channels, plain)
                aa.frame.ch<N>              {channel_id, flags, payload_hex}  (per-channel, plain)
                tcp.session.closed          {}
                tcp.server.error            {error}
                tcp.server.tls_handshake    {outgoing_hex}     ← TLS bytes to forward to phone
                tcp.server.tls_handshake_completed  {}         ← TLS is_active(), AUTH_COMPLETE can be sent

Flow:
  1. Waits for rfcomm.handshake.completed — phone is now on the WiFi AP
  2. Starts plain TCPServer on port 5288
  3. Accepts the phone connection (plain TCP — no TLS wrap)
  4. FrameRelay reads AA frames:
     - If encrypted flag set and cryptor is active → decrypt before publishing
     - Otherwise publish as-is
  5. On aa.frame.send → serialises the frame and writes it back to the phone
  6. On aa.handshake.start_tls → AACryptor.init() + drive_handshake() → publish tcp.server.tls_handshake
  7. On aa.handshake.feed_input → write_handshake_input() + drive_handshake():
       - if outgoing bytes → publish tcp.server.tls_handshake
       - if is_active()   → publish tcp.server.tls_handshake_completed
  8. On socket close → publishes tcp.session.closed
  9. On system.stop  → server + relay + cryptor shutdown

  TLS note: Android Auto negotiates encryption in-band on channel 0 (msgId 0x0003).
  The TCP socket is always plain. AACryptor is now owned by tcp_server.

Internal helpers (no ZMQ):
  server.py      — TCP bind/listen/accept (plain)
  frame_relay.py — AA frame header parse, per-frame callback
  aa_cryptor.py  — memory-BIO TLS (mirrors aasdk Cryptor.cpp)
"""

import sys
import struct
import threading
from pathlib import Path
import time
from typing import Optional

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient       # noqa: E402
from shared.logger import get_logger          # noqa: E402
from tcp_server.server import TCPServer       # noqa: E402
from tcp_server.frame_relay import FrameRelay # noqa: E402
from tcp_server.aa_cryptor import AACryptor   # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "tcp_server"
PRIORITY    = 1  # service level

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_server: Optional[TCPServer] = None
_relay:  Optional[FrameRelay] = None
_cryptor: Optional[AACryptor] = None
_server_starting = False
_server_lock = threading.Lock()
_write_lock  = threading.Lock()   # protects socket writes from concurrent senders

# AA frame flags — encryption bit (bit 3)
_FLAG_ENCRYPTED = 0x08

# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — tcp_server ready")
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
    global _server_starting
    device_address = payload.get("device_address", "")
    phone_ip       = payload.get("phone_ip", "")

    with _server_lock:
        if _server_starting or _server is not None:
            log.info(
                f"Handshake completed from {device_address} (phone_ip={phone_ip}) "
                "but TCP server is already active — ignoring duplicate"
            )
            return
        _server_starting = True

    log.info(f"Handshake completed from {device_address} (phone_ip={phone_ip}) — starting TCP server")
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()


def on_frame_send(topic: str, payload: dict) -> None:
    """Write an AA frame to the active socket (called by other modules via aa.frame.send).

    Expected payload keys:
        channel_id  : int   — AA channel id (0 = control, 1 = input, ...)
        flags       : int   — frame flags byte
        payload_hex : str   — serialised payload as hex string
    """
    relay = _relay
    if relay is None:
        log.warning("on_frame_send: no active relay, dropping frame (ch=%s)",
                    payload.get("channel_id"))
        return

    try:
        channel_id  = int(payload["channel_id"])
        flags       = int(payload["flags"])
        raw_payload = bytes.fromhex(payload["payload_hex"])
    except (KeyError, ValueError) as exc:
        log.error("on_frame_send: malformed payload — %s", exc)
        return

    # AA wire format: [channel:1B][flags:1B][len:2B_BE][payload]
    frame = struct.pack(">BBH", channel_id, flags, len(raw_payload)) + raw_payload

    try:
        with _write_lock:
            relay.send_raw(frame)
    except Exception as exc:
        log.error("on_frame_send: socket write failed — %s", exc)


def on_handshake_start_tls(topic: str, payload: dict) -> None:
    """oaa_control_channel signals VERSION_RESPONSE received — init cryptor and send ClientHello."""
    global _cryptor
    log.info("aa.handshake.start_tls — initialising AACryptor")
    _cryptor = AACryptor()
    _cryptor.init()
    outgoing = _cryptor.drive_handshake()
    if outgoing:
        log.debug("TLS ClientHello generated (%d bytes) — publishing tcp.server.tls_handshake", len(outgoing))
        bus.publish("tcp.server.tls_handshake", {"outgoing_hex": outgoing.hex()})


def on_handshake_feed_input(topic: str, payload: dict) -> None:
    """oaa_control_channel relays SSL_HANDSHAKE frame payload from phone."""
    if _cryptor is None:
        log.warning("aa.handshake.feed_input received but cryptor not initialised — dropping")
        return

    try:
        data = bytes.fromhex(payload["payload_hex"])
    except (KeyError, ValueError) as exc:
        log.error("on_handshake_feed_input: malformed payload — %s", exc)
        return

    _cryptor.write_handshake_input(data)
    outgoing = _cryptor.drive_handshake()

    if _cryptor.is_active():
        log.info("TLS handshake complete — publishing tcp.server.tls_handshake_completed")
        bus.publish("tcp.server.tls_handshake_completed", {})
    elif outgoing:
        log.debug("TLS round (%d bytes) — publishing tcp.server.tls_handshake", len(outgoing))
        bus.publish("tcp.server.tls_handshake", {"outgoing_hex": outgoing.hex()})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _start_server() -> None:
    global _server, _relay, _server_starting

    server = TCPServer()
    with _server_lock:
        _server = server

    if not server.start():
        with _server_lock:
            if _server is server:
                _server = None
            _server_starting = False
        bus.publish("tcp.server.error", {"error": "TCPServer.start() failed"})
        return

    with _server_lock:
        _server_starting = False

    bus.publish("tcp.server.started", {
        "host": server.host,
        "port": server.port,
    })

    result = server.accept()
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
    _relay.start()


def _teardown() -> None:
    global _server, _relay, _server_starting, _cryptor
    if _relay:
        _relay.stop()
        _relay = None
    if _server:
        _server.stop()
        _server = None
    if _cryptor:
        _cryptor.deinit()
        _cryptor = None
    with _server_lock:
        _server_starting = False


# ---------------------------------------------------------------------------
# FrameRelay callbacks → bus events
# ---------------------------------------------------------------------------

def _on_frame(channel_id: int, flags: int, payload: bytes) -> None:
    # Decrypt if encrypted flag is set and cryptor is active
    if (flags & _FLAG_ENCRYPTED) and _cryptor is not None and _cryptor.is_active():
        try:
            payload = _cryptor.decrypt(payload)
        except Exception as exc:
            log.error("_on_frame: decrypt failed ch=%d — %s", channel_id, exc)
            return
        # Clear encryption flag: downstream consumers receive plain frames
        flags = flags & ~_FLAG_ENCRYPTED

    frame_data = {
        "channel_id":  channel_id,
        "flags":       flags,
        "payload_hex": payload.hex(),
    }
    bus.publish("aa.frame.received", frame_data)
    bus.publish(f"aa.frame.ch{channel_id}", frame_data)


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
    bus.subscribe("aa.frame.send",              on_frame_send)
    bus.subscribe("aa.handshake.start_tls",     on_handshake_start_tls)
    bus.subscribe("aa.handshake.feed_input",    on_handshake_feed_input)

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
