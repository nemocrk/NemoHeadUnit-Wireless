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
                aa.frame.ch0                {channel_id, flags, payload_hex}  ← monitors SHUTDOWN_RESPONSE
                aa.handshake.start_tls      {}                 ← oaa_control_channel triggers TLS init
                aa.handshake.feed_input     {payload_hex}      ← SSL round bytes from phone
                aa.session.restart          {}                 ← config changed, restart AA session
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
                aa.session.restarting       {}                 ← cryptor reset done, oaa_control_channel
                                                                  should send VERSION_REQUEST

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
  8. On aa.session.restart:
       a. Send SHUTDOWN_REQUEST (ch0, msgId 0x000D) to phone
       b. Wait for SHUTDOWN_RESPONSE on aa.frame.ch0 (msgId 0x000E)
       c. deinit() cryptor (reset SSLObject)
       d. Publish aa.session.restarting → oaa_control_channel sends VERSION_REQUEST
  9. On socket close → publishes tcp.session.closed
  10. On system.stop  → server + relay + cryptor shutdown

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

# Set to True while a graceful restart is in progress; prevents _on_session_closed
# from treating the subsequent socket teardown as an unexpected disconnection.
_restart_pending = False
# Event signalled when SHUTDOWN_RESPONSE (msgId 0x000E) arrives on ch0.
_shutdown_ack_event = threading.Event()

# AA frame flags — encryption bit (bit 3)
_FLAG_ENCRYPTED = 0x08

# AA control-channel message IDs used by restart logic
_MSG_SHUTDOWN_REQUEST  = 0x000D
_MSG_SHUTDOWN_RESPONSE = 0x000E
# How long (seconds) to wait for SHUTDOWN_RESPONSE before giving up
_SHUTDOWN_ACK_TIMEOUT  = 3.0

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
        encrypted   = bool(flags & _FLAG_ENCRYPTED)
        raw_payload = bytes.fromhex(payload["payload_hex"])
        frame_data   = payload.get("frame_data", {})
    except (KeyError, ValueError) as exc:
        log.error("on_frame_send: malformed payload — %s", exc)
        return

    # AA wire format: [channel:1B][flags:1B][len:2B_BE][payload]
    log.debug(f"on_frame_send: calculated plain_hex_bytes={(struct.pack('>BBH', channel_id, flags, len(raw_payload)) + (raw_payload if len(raw_payload) < 300 else raw_payload[:300] + b'...')).hex()}")
    frame_data_for_log = {**frame_data, 'payload_hex': payload.get('payload_hex', '')[:600] + ('...' if len(payload.get('payload_hex', '')) > 600 else '')}
    log.debug(f"on_frame_send: frame_data={frame_data_for_log}")
    if encrypted:
        if _cryptor is None or not _cryptor.is_active():
            log.warning("on_frame_send: encryption requested but cryptor not active — sending unencrypted")
            encrypted = False
        else:
            try:
                raw_payload = _cryptor.encrypt(raw_payload)
            except Exception as exc:
                log.error("on_frame_send: encrypt failed ch=%d — %s", channel_id, exc)
                return
    
    frame = struct.pack(">BBH", channel_id, flags, len(raw_payload)) + raw_payload

    try:
        with _write_lock:
            relay.send_raw(frame)
            log.debug(f"on_frame_send: sent frame on ch={payload.get('channel_id')}, flags=0x{payload.get('flags', 0):02X}, encrypted={bool(payload.get('flags', 0) & _FLAG_ENCRYPTED)}, payload_len={len(payload.get('payload_hex', '')) // 2}")
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


def on_aa_session_restart(topic: str, payload: dict) -> None:
    """Graceful AA session restart triggered by a config change.

    Sequence:
      1. Send SHUTDOWN_REQUEST (ch0, msgId 0x000D) to phone.
      2. Wait up to _SHUTDOWN_ACK_TIMEOUT seconds for SHUTDOWN_RESPONSE (msgId 0x000E).
         The ack is signalled by on_ch0_frame via _shutdown_ack_event.
      3. deinit() the AACryptor (discards the SSL objects).
      4. Publish aa.session.restarting — oaa_control_channel will call
         send_version_request() to kick off a fresh handshake on the
         existing TCP connection.
    """
    global _restart_pending

    if _relay is None:
        log.warning("on_aa_session_restart: no active session — ignoring")
        return

    log.info("aa.session.restart — sending SHUTDOWN_REQUEST to phone")
    _restart_pending = True
    _shutdown_ack_event.clear()

    # Build SHUTDOWN_REQUEST: AA control frame on ch0, unencrypted
    # Wire layout: [channel:1B][flags:1B][len:2B_BE][msg_id:2B_BE]
    msg_id_bytes = struct.pack(">H", _MSG_SHUTDOWN_REQUEST)
    frame = struct.pack(">BBH", 0, 0x00, len(msg_id_bytes)) + msg_id_bytes
    try:
        with _write_lock:
            _relay.send_raw(frame)
    except Exception as exc:
        log.error("on_aa_session_restart: failed to send SHUTDOWN_REQUEST — %s", exc)
        _restart_pending = False
        return

    # Wait for SHUTDOWN_RESPONSE from phone
    acked = _shutdown_ack_event.wait(timeout=_SHUTDOWN_ACK_TIMEOUT)
    if not acked:
        log.warning(
            "on_aa_session_restart: SHUTDOWN_RESPONSE not received within %.1fs — proceeding anyway",
            _SHUTDOWN_ACK_TIMEOUT,
        )
    else:
        log.info("on_aa_session_restart: SHUTDOWN_RESPONSE received")

    # Reset the cryptor (discards SSL state without tearing down the TCP socket)
    if _cryptor is not None:
        _cryptor.deinit()
        log.info("on_aa_session_restart: AACryptor reset")

    _restart_pending = False

    # Signal oaa_control_channel to re-run the handshake on the same TCP connection
    log.info("on_aa_session_restart: publishing aa.session.restarting")
    bus.publish("aa.session.restarting", {})


def on_ch0_frame(topic: str, payload: dict) -> None:
    """Monitor ch0 frames for SHUTDOWN_RESPONSE during a restart sequence.

    Only acts when _restart_pending is True; all other ch0 frames are ignored
    here (oaa_control_channel handles them via its own aa.frame.ch0 subscription).
    """
    if not _restart_pending:
        return

    try:
        raw = bytes.fromhex(payload["payload_hex"])
    except (KeyError, ValueError):
        return

    # Control frame body starts with a 2-byte big-endian message ID
    if len(raw) < 2:
        return

    msg_id = struct.unpack_from(">H", raw, 0)[0]
    if msg_id == _MSG_SHUTDOWN_RESPONSE:
        _shutdown_ack_event.set()


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
    log.info(f"_on_frame: published aa.frame.received and aa.frame.ch{channel_id} for ch={channel_id}, flags=0x{flags:02X}, payload_len={len(payload)}")


def _on_session_closed() -> None:
    if _restart_pending:
        # Chiusura voluta dalla sequenza di restart — non è un errore
        log.debug("_on_session_closed: called during restart sequence — ignoring")
        return
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
    bus.subscribe("aa.session.restart",         on_aa_session_restart)
    bus.subscribe("aa.frame.ch0",               on_ch0_frame)

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
