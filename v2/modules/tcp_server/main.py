"""
NemoHeadUnit-Wireless v2 — tcp_server module

Module contract:
  Name        : tcp_server
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                rfcomm.handshake.completed  {device_address, phone_ip}
                aa.frame.send               {channel_id, message_id, payload_hex, encrypted}
                aa.frame.ch0                {channel_id, message_id, encrypted, payload_hex}  ← monitors SHUTDOWN_RESPONSE
                aa.handshake.start_tls      {}                 ← oaa_control_channel triggers TLS init
                aa.handshake.feed_input     {payload_hex}      ← SSL round bytes from phone
                aa.session.restart          {}                 ← config changed, restart AA session
  Publishes   : system.module_ready          {name, priority}
                system.ready                 {name, priority}
                tcp.server.started          {host, port}
                tcp.session.connected       {address}
                aa.frame.received           lightweight metadata mirror for diagnostics
                aa.frame.ch<N>              {channel_id, message_id, encrypted, payload_hex}  (per-channel)
                tcp.session.closed          {}
                tcp.server.error            {error}
                tcp.server.tls_handshake    {outgoing_hex}     ← TLS bytes to forward to phone
                tcp.server.tls_handshake_completed  {}         ← TLS is_active(), AUTH_COMPLETE can be sent
                aa.session.restarting       {}                 ← cryptor reset done, oaa_control_channel
                                                                  should send VERSION_REQUEST

aa.frame.send payload contract:
    channel_id  : int   — AA channel (0 = control)
    message_id  : int   — 2-byte AA message identifier
    payload_hex : str   — serialised proto body ONLY (no message_id prepended)
    encrypted   : bool  — semantic flag; tcp_server enforces actual encryption policy via frame_codec

aa.frame.ch<N> publish contract:
    channel_id  : int   — AA channel
    message_id  : int   — 2-byte AA message identifier (stripped from wire payload by tcp_server)
    encrypted   : bool  — True if the frame was encrypted on the wire (echo of original flag)
    payload_hex : str   — decrypted proto body ONLY (message_id already removed)

aa.frame.received is diagnostic-only and intentionally lightweight by default.
Set AA_FRAME_RECEIVED_FULL=1 to include payload_hex there too.

Flow:
  1. Waits for rfcomm.handshake.completed — phone is now on the WiFi AP
  2. Starts plain TCPServer on port 5288
  3. Accepts the phone connection (plain TCP — no TLS wrap)
  4. FrameRelay reads raw AA frames → FrameAssembler reassembles multi-frame messages:
     - If assembled and encrypted flag set and cryptor is active → decrypt
     - Extract message_id (2B BE) from assembled payload
     - Publish {channel_id, message_id, encrypted, payload_hex} on bus
  5. On aa.frame.send → frame_codec.encode() builds wire frames → FrameRelay.send_raw()
  6. On aa.handshake.start_tls → AACryptor.init() + drive_handshake() → publish tcp.server.tls_handshake
  7. On aa.handshake.feed_input → write_handshake_input() + drive_handshake():
       - if outgoing bytes → publish tcp.server.tls_handshake
       - if is_active()   → publish tcp.server.tls_handshake_completed
  8. On aa.session.restart:
       a. Send SHUTDOWN_REQUEST (ch0, msgId 0x000D) to phone
       b. Wait for SHUTDOWN_RESPONSE on aa.frame.ch0 (message_id == 0x000E)
       c. deinit() cryptor (reset SSLObject)
       d. Publish aa.session.restarting → oaa_control_channel sends VERSION_REQUEST
  9. On socket close → publishes tcp.session.closed
  10. On system.stop  → join _server_thread → server + relay + cryptor + assembler shutdown

  TLS note: Android Auto negotiates encryption in-band on channel 0 (msgId 0x0003).
  The TCP socket is always plain. AACryptor is now owned by tcp_server.

Internal helpers (no ZMQ):
  server.py      — TCP bind/listen/accept (plain)
  frame_relay.py — AA frame header parse, per-frame callback
  frame_codec.py — AA frame encode / FrameAssembler (multi-frame reassembly)
  aa_cryptor.py  — memory-BIO TLS (mirrors aasdk Cryptor.cpp)
"""

import os
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

from shared.bus_client import BusClient           # noqa: E402
from shared.logger import get_logger              # noqa: E402
from tcp_server.server import TCPServer           # noqa: E402
from tcp_server.frame_relay import FrameRelay     # noqa: E402
from tcp_server.frame_codec import encode, FrameAssembler  # noqa: E402
from tcp_server.aa_cryptor import AACryptor       # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "tcp_server"
PRIORITY    = 1

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
_PUBLISH_FULL_FRAME_RECEIVED = os.getenv("AA_FRAME_RECEIVED_FULL") == "1"

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_server:        Optional[TCPServer]      = None
_relay:         Optional[FrameRelay]     = None
_cryptor:       Optional[AACryptor]      = None
_assembler:     Optional[FrameAssembler] = None
_server_thread: Optional[threading.Thread] = None   # track _start_server thread for join on stop
_server_starting = False
_server_lock = threading.Lock()
_write_lock  = threading.Lock()
_crypto_lock = threading.RLock()

_restart_pending      = False
_shutdown_ack_event   = threading.Event()

# AA frame flags — encryption bit
_FLAG_ENCRYPTED = 0x08

# AA control-channel message IDs used by restart logic
_MSG_SHUTDOWN_REQUEST  = 0x000D
_MSG_SHUTDOWN_RESPONSE = 0x000E
_SHUTDOWN_ACK_TIMEOUT  = 3.0

# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — tcp_server ready")
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published — tcp_server online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — shutting down TCP server")
    _teardown()
    # Join _server_thread before bus.stop() so that any in-flight
    # _on_session_closed() call (which does bus.publish) completes while
    # the ZMQ socket is still open.  Without this join, bus.stop() can
    # close the socket while the daemon thread is still inside publish(),
    # causing ZMQError: Socket operation on non-socket.
    t = _server_thread
    if t is not None and t.is_alive():
        log.debug("on_system_stop: joining _server_thread (timeout=3s)")
        t.join(timeout=3.0)
    bus.stop()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def on_handshake_completed(topic: str, payload: dict) -> None:
    global _server_starting, _server_thread
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
    _server_thread = t
    t.start()


def on_frame_send(topic: str, payload: dict) -> None:
    """Write an AA frame to the active socket.

    Expected payload keys:
        channel_id  : int   — AA channel id
        message_id  : int   — 2-byte AA message identifier
        payload_hex : str   — proto body ONLY (no message_id prepended)
        encrypted   : bool  — semantic hint; actual policy enforced by frame_codec
    """
    relay = _relay
    if relay is None:
        log.warning("on_frame_send: no active relay, dropping frame (ch=%s)",
                    payload.get("channel_id"))
        return
    try:
        channel_id  = int(payload["channel_id"])
        message_id  = int(payload["message_id"])
        body        = bytes.fromhex(payload["payload_hex"])
        ssl_active  = bool(payload.get("encrypted", False))
    except (KeyError, ValueError) as exc:
        log.error("on_frame_send: malformed payload — %s", exc)
        return

    try:
        with _crypto_lock:
            frames = encode(
                channel_id=channel_id,
                message_id=message_id,
                body=body,
                ssl_active=ssl_active,
                cryptor=_cryptor,
            )
    except Exception as exc:
        log.error("on_frame_send: encode failed ch=%d msg=0x%04x — %s", channel_id, message_id, exc)
        return

    try:
        with _write_lock:
            for frame in frames:
                relay.send_raw(frame)
        log.debug(
            "on_frame_send: sent %d frame(s) ch=%d msg=0x%04x body_len=%d enc=%s",
            len(frames), channel_id, message_id, len(body), ssl_active,
        )
    except Exception as exc:
        log.error("on_frame_send: socket write failed — %s", exc)


def on_handshake_start_tls(topic: str, payload: dict) -> None:
    """oaa_control_channel signals VERSION_RESPONSE received — init cryptor and send ClientHello."""
    global _cryptor
    log.info("aa.handshake.start_tls — initialising AACryptor")
    with _crypto_lock:
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

    with _crypto_lock:
        _cryptor.write_handshake_input(data)
        outgoing = _cryptor.drive_handshake()
        active = _cryptor.is_active()

    if active:
        log.info("TLS handshake complete — publishing tcp.server.tls_handshake_completed")
        bus.publish("tcp.server.tls_handshake_completed", {})
    elif outgoing:
        log.debug("TLS round (%d bytes) — publishing tcp.server.tls_handshake", len(outgoing))
        bus.publish("tcp.server.tls_handshake", {"outgoing_hex": outgoing.hex()})


def on_aa_session_restart(topic: str, payload: dict) -> None:
    """Graceful AA session restart triggered by a config change."""
    global _restart_pending

    if _relay is None:
        log.warning("on_aa_session_restart: no active session — ignoring")
        return

    log.info("aa.session.restart — sending SHUTDOWN_REQUEST to phone")
    _restart_pending = True
    _shutdown_ack_event.clear()

    # Build and send SHUTDOWN_REQUEST directly via frame_codec (bypasses bus round-trip)
    with _crypto_lock:
        shutdown_frames = encode(
            channel_id=0,
            message_id=_MSG_SHUTDOWN_REQUEST,
            body=b"",
            ssl_active=(_cryptor is not None and _cryptor.is_active()),
            cryptor=_cryptor,
        )
    try:
        with _write_lock:
            for frame in shutdown_frames:
                _relay.send_raw(frame)
    except Exception as exc:
        log.error("on_aa_session_restart: failed to send SHUTDOWN_REQUEST — %s", exc)
        _restart_pending = False
        return

    acked = _shutdown_ack_event.wait(timeout=_SHUTDOWN_ACK_TIMEOUT)
    if not acked:
        log.warning(
            "on_aa_session_restart: SHUTDOWN_RESPONSE not received within %.1fs — proceeding anyway",
            _SHUTDOWN_ACK_TIMEOUT,
        )
    else:
        log.info("on_aa_session_restart: SHUTDOWN_RESPONSE received")

    if _cryptor is not None:
        with _crypto_lock:
            _cryptor.deinit()
        log.info("on_aa_session_restart: AACryptor reset")

    if _assembler is not None:
        _assembler.reset()
        log.info("on_aa_session_restart: FrameAssembler reset")

    _restart_pending = False
    log.info("on_aa_session_restart: publishing aa.session.restarting")
    bus.publish("aa.session.restarting", {})


def on_ch0_frame(topic: str, payload: dict) -> None:
    """Monitor ch0 frames for SHUTDOWN_RESPONSE during a restart sequence."""
    if not _restart_pending:
        return
    if int(payload.get("message_id", -1)) == _MSG_SHUTDOWN_RESPONSE:
        _shutdown_ack_event.set()


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _start_server() -> None:
    global _server, _relay, _assembler, _server_starting

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

    bus.publish("tcp.server.started", {"host": server.host, "port": server.port})

    result = server.accept()
    if result is None:
        bus.publish("tcp.server.error", {"error": "No connection within timeout"})
        _teardown()
        return

    conn, address = result
    log.info(f"Phone connected: {address}")
    bus.publish("tcp.session.connected", {"address": address})

    _assembler = FrameAssembler()

    _relay = FrameRelay(
        sock=conn,
        on_frame_cb=_on_raw_frame,
        on_closed_cb=_on_session_closed,
    )
    _relay.start()


def _teardown() -> None:
    global _server, _relay, _assembler, _server_starting, _cryptor
    if _relay:
        _relay.stop()
        _relay = None
    if _server:
        _server.stop()
        _server = None
    if _cryptor:
        with _crypto_lock:
            _cryptor.deinit()
            _cryptor = None
    if _assembler:
        _assembler.reset()
        _assembler = None
    with _server_lock:
        _server_starting = False


# ---------------------------------------------------------------------------
# FrameRelay callback → assemble → decrypt → publish
# ---------------------------------------------------------------------------

def _on_raw_frame(channel_id: int, flags: int, payload: bytes, total_size: int) -> None:
    """Called by FrameRelay for every raw frame off the socket.

    Pipeline:
      1. Feed into FrameAssembler — returns None until message is complete
      2. Save encrypted flag (echo to subscribers) before decrypting
      3. Decrypt if needed
      4. Strip message_id (2B BE) from assembled payload
      5. Publish {channel_id, message_id, encrypted, payload_hex} on bus
    """
    result = _assembler.feed(channel_id, flags, payload, total_size)
    if result is None:
        return

    channel_id, flags, assembled = result

    # Save original encrypted flag — echoed to subscribers
    encrypted = bool(flags & _FLAG_ENCRYPTED)

    # Decrypt if needed
    if encrypted:
        try:
            cipher_len = len(assembled)
            decrypted = False
            with _crypto_lock:
                cryptor = _cryptor
                if cryptor is not None and cryptor.is_active():
                    assembled = cryptor.decrypt(assembled)
                    decrypted = True
            if decrypted:
                log.debug(
                    "_on_raw_frame: decrypt ok ch=%d cipher_len=%d plain_len=%d",
                    channel_id, cipher_len, len(assembled),
                )
        except Exception as exc:
            log.error("_on_raw_frame: decrypt failed ch=%d — %s", channel_id, exc)
            return

    # Extract message_id from the assembled payload (always first 2 bytes)
    if len(assembled) < 2:
        log.error("_on_raw_frame: ch=%d assembled payload too short (%d bytes) — dropping",
                  channel_id, len(assembled))
        return
    message_id = struct.unpack_from(">H", assembled, 0)[0]
    body       = assembled[2:]

    frame_data = {
        "channel_id":  channel_id,
        "message_id":  message_id,
        "encrypted":   encrypted,
        "payload_hex": body.hex(),
    }
    received_data = {
        "channel_id": channel_id,
        "message_id": message_id,
        "encrypted": encrypted,
        "payload_len": len(body),
        "payload_head": body[:16].hex(),
    }
    if _PUBLISH_FULL_FRAME_RECEIVED:
        received_data["payload_hex"] = frame_data["payload_hex"]
    bus.publish("aa.frame.received", received_data)
    bus.publish(f"aa.frame.ch{channel_id}", frame_data)
    log.debug(
        "_on_raw_frame: ch=%d msg=0x%04x enc=%s body_len=%d",
        channel_id, message_id, encrypted, len(body),
    )


def _on_session_closed() -> None:
    if _restart_pending:
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
