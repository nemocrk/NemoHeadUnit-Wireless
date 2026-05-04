"""
NemoHeadUnit-Wireless v2 — video module

Module contract:
  Name        : video
  Priority    : 2
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response          {module, config, requester}  ← cross-module read
                aa.session.active        {}  ← subscribe dynamically to aa.frame.chN
                aa.session.shutdown      {}  ← unsubscribe / reset session
                aa.frame.chN             {channel_id, flags, payload_hex}  ← N resolved at boot
  Publishes   : system.module_ready      {name, priority}
                system.ready             {name, priority}
                aa.frame.send            {channel_id, flags, payload_hex}  ← MediaAck
                video.frame              {channel_id, session_id, ts_us, data_b64}
                video.state              {state}  ← IDLE | SETUP | OPEN | PLAYING | STOPPED

Flow:
  1. On system.start: request oaa_control_channel config to discover the VIDEO channel id.
  2. On config.response (module=oaa_control_channel): scan channels list for
     av_channel.stream_type == "VIDEO", extract channel_id, subscribe aa.frame.chN.
  3. Publish system.ready once channel id is resolved.
  4. On aa.session.active: reset session state.
  5. On aa.frame.chN: decode AA media frame, dispatch by message_id:
       - AVChannelSetupRequest  → reply AVChannelSetupResponse, publish video.state=SETUP
       - AVChannelOpenRequest   → reply AVChannelOpenResponse, publish video.state=OPEN
       - MediaWithTimestamp     → send MediaAck, publish video.frame (for video_ui)
       - AVChannelStopIndication → publish video.state=STOPPED
  6. On aa.session.shutdown: reset state, publish video.state=IDLE.

ACK strategy:
  MediaAck is sent immediately after each MediaWithTimestamp frame is received.
  video_ui is fire-and-forget: the ACK does NOT depend on video_ui processing.
  This prevents backpressure from a slow/absent UI from stalling the phone stream.

Notes on channel discovery:
  The video channel id is NOT hardcoded. It is resolved at boot by reading
  the oaa_control_channel config and scanning for the descriptor whose
  av_channel.stream_type == "VIDEO". The fallback is channel 3 (the default
  in SEMANTIC_DEFAULTS) used only if the config is unavailable.
"""

from __future__ import annotations

import base64
import struct
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

from shared.bus_client import BusClient   # noqa: E402
from shared.logger import get_logger      # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "video"
PRIORITY    = 2

# Fallback channel id if oaa_control_channel config is not available at boot.
_VIDEO_CHANNEL_FALLBACK = 3

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# ---------------------------------------------------------------------------
# AA media frame constants
# ---------------------------------------------------------------------------

# Frame flags (same convention as frame_codec.py)
_FLAG_FIRST     = 0x01
_FLAG_LAST      = 0x02
_FLAG_ENCRYPTED = 0x08
_FLAG_FULL      = _FLAG_FIRST | _FLAG_LAST | _FLAG_ENCRYPTED  # 0x0B

# AA message ids for the video / AV channel
_MSG_AV_CHANNEL_SETUP_REQUEST    = 0x8000
_MSG_AV_CHANNEL_SETUP_RESPONSE   = 0x8001
_MSG_AV_CHANNEL_OPEN_REQUEST     = 0x8003
_MSG_AV_CHANNEL_OPEN_RESPONSE    = 0x8005
_MSG_AV_CHANNEL_STOP_INDICATION  = 0x8004
_MSG_MEDIA_WITH_TIMESTAMP        = 0x0001
_MSG_MEDIA_ACK                   = 0x0002

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_video_channel_id: int       = _VIDEO_CHANNEL_FALLBACK
_channel_resolved: bool      = False
_session_id: int             = 0
_state: str                  = "IDLE"   # IDLE | SETUP | OPEN | PLAYING | STOPPED

# ---------------------------------------------------------------------------
# Helpers — frame encode / decode
# ---------------------------------------------------------------------------

def _encode_media_frame(channel_id: int, message_id: int, proto_body: bytes) -> dict:
    """Build an aa.frame.send payload for a media channel message."""
    payload = struct.pack(">H", message_id) + proto_body
    return {
        "channel_id":  channel_id,
        "flags":       _FLAG_FULL,
        "payload_hex": payload.hex(),
    }


def _decode_media_frame(payload_hex: str) -> tuple[int, bytes] | None:
    """Decode payload_hex → (message_id, body). Returns None on error."""
    try:
        raw = bytes.fromhex(payload_hex)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    message_id = struct.unpack_from(">H", raw, 0)[0]
    body = raw[2:]
    return message_id, body


def _set_state(new_state: str) -> None:
    global _state
    _state = new_state
    bus.publish("video.state", {"state": new_state})
    log.info("video.state → %s", new_state)


# ---------------------------------------------------------------------------
# Channel discovery — read oaa_control_channel config
# ---------------------------------------------------------------------------

def _request_oaa_config() -> None:
    """Publish config.get for oaa_control_channel (cross-module read)."""
    bus.publish("config.get", {
        "module":    "oaa_control_channel",
        "requester": MODULE_NAME,
    })
    log.info("Requested oaa_control_channel config for VIDEO channel discovery")


def _resolve_video_channel(channels: list) -> int | None:
    """Scan a channels list dict for the entry with av_channel.stream_type == VIDEO.

    Returns the channel_id or None if not found.
    """
    for ch in channels:
        av = ch.get("av_channel", {})
        if av.get("stream_type") == "VIDEO":
            cid = ch.get("channel_id")
            if cid is not None:
                return int(cid)
    return None


def on_config_response(topic: str, payload: dict) -> None:
    """Handle config.response.

    Accepts only responses directed at this module (requester == MODULE_NAME)
    for the oaa_control_channel module.  Resolves the VIDEO channel id,
    subscribes aa.frame.chN, then publishes system.ready.
    """
    global _video_channel_id, _channel_resolved

    if payload.get("module") != "oaa_control_channel":
        return
    if payload.get("requester") != MODULE_NAME:
        return

    config   = payload.get("config", {})
    channels = config.get("channels", [])

    resolved = _resolve_video_channel(channels)
    if resolved is not None:
        _video_channel_id = resolved
        log.info("VIDEO channel resolved from config: channel_id=%d", _video_channel_id)
    else:
        _video_channel_id = _VIDEO_CHANNEL_FALLBACK
        log.warning(
            "VIDEO channel not found in oaa_control_channel config — "
            "falling back to channel_id=%d",
            _video_channel_id,
        )

    if not _channel_resolved:
        _channel_resolved = True
        _subscribe_video_channel()
        bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
        log.info("system.ready published (priority=%d)", PRIORITY)


def _subscribe_video_channel() -> None:
    topic = f"aa.frame.ch{_video_channel_id}"
    bus.subscribe(topic, on_aa_frame_video)
    log.info("Subscribed to %s", topic)


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info("system.readytostart — announcing priority %d", PRIORITY)
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info("system.start priority=%d — requesting oaa_control_channel config", PRIORITY)
    _request_oaa_config()
    # system.ready is published in on_config_response once channel is resolved


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — video module stopping")
    bus.stop()


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def on_aa_session_active(topic: str, payload: dict) -> None:
    global _session_id
    _session_id = 0
    _set_state("IDLE")
    log.info("AA session active — video ready")


def on_aa_session_shutdown(topic: str, payload: dict) -> None:
    global _session_id
    _session_id = 0
    _set_state("IDLE")
    log.info("AA session shutdown — video reset")


# ---------------------------------------------------------------------------
# AA frame handler — video channel
# ---------------------------------------------------------------------------

def on_aa_frame_video(topic: str, payload: dict) -> None:
    global _session_id

    result = _decode_media_frame(payload.get("payload_hex", ""))
    if result is None:
        log.error("on_aa_frame_video: malformed payload — dropping")
        return

    message_id, body = result

    if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
        _handle_setup_request(body)

    elif message_id == _MSG_AV_CHANNEL_OPEN_REQUEST:
        _handle_open_request(body)

    elif message_id == _MSG_MEDIA_WITH_TIMESTAMP:
        _handle_media_with_timestamp(body)

    elif message_id == _MSG_AV_CHANNEL_STOP_INDICATION:
        log.info("AVChannelStopIndication received")
        _set_state("STOPPED")

    else:
        log.debug("Unhandled video msg_id=0x%04x len=%d", message_id, len(body))


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

def _handle_setup_request(body: bytes) -> None:
    """Reply with AVChannelSetupResponse (status=OK, max_unacked=1)."""
    # AVChannelSetupResponse proto layout (minimal, field 1 = status OK = 0):
    # field 1 (status): varint 0 → 0x08 0x00
    # field 2 (max_unacked): varint 1 → 0x10 0x01
    proto_body = b"\x08\x00\x10\x01"
    frame = _encode_media_frame(_video_channel_id, _MSG_AV_CHANNEL_SETUP_RESPONSE, proto_body)
    bus.publish("aa.frame.send", frame)
    _set_state("SETUP")
    log.info("AVChannelSetupRequest → AVChannelSetupResponse sent (max_unacked=1)")


def _handle_open_request(body: bytes) -> None:
    """Reply with AVChannelOpenResponse (status=OK)."""
    # AVChannelOpenResponse proto: field 1 (status OK = 0) → 0x08 0x00
    proto_body = b"\x08\x00"
    frame = _encode_media_frame(_video_channel_id, _MSG_AV_CHANNEL_OPEN_RESPONSE, proto_body)
    bus.publish("aa.frame.send", frame)
    _set_state("OPEN")
    log.info("AVChannelOpenRequest → AVChannelOpenResponse sent")


def _handle_media_with_timestamp(body: bytes) -> None:
    """Parse MediaWithTimestamp, send MediaAck, publish video.frame for video_ui."""
    global _session_id

    # MediaWithTimestamp proto layout:
    #   field 1 (timestamp_us): fixed64  → tag 0x09, 8 bytes LE
    #   field 2 (data):         bytes    → tag 0x12, varint len, bytes
    ts_us = 0
    data  = b""

    pos = 0
    while pos < len(body):
        if pos >= len(body):
            break
        tag_byte = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07

        if field_number == 1 and wire_type == 1:
            # fixed64 little-endian
            if pos + 8 > len(body):
                break
            ts_us = struct.unpack_from("<Q", body, pos)[0]
            pos += 8

        elif field_number == 2 and wire_type == 2:
            # length-delimited bytes (H.264 NAL data)
            length, pos = _read_varint(body, pos)
            if length is None:
                break
            data = body[pos: pos + length]
            pos += length

        else:
            # Skip unknown fields
            if wire_type == 0:
                _, pos = _read_varint(body, pos)
            elif wire_type == 1:
                pos += 8
            elif wire_type == 2:
                length, pos = _read_varint(body, pos)
                if length:
                    pos += length
            elif wire_type == 5:
                pos += 4
            else:
                break  # unknown wire type, stop parsing

    if _state not in ("OPEN", "PLAYING"):
        _set_state("PLAYING")

    # 1. Send MediaAck immediately — do NOT wait for video_ui
    _send_media_ack()

    # 2. Publish video.frame for video_ui (fire-and-forget)
    if data:
        bus.publish("video.frame", {
            "channel_id": _video_channel_id,
            "session_id": _session_id,
            "ts_us":      ts_us,
            "data_b64":   base64.b64encode(data).decode("ascii"),
        })


def _send_media_ack() -> None:
    """Encode and send MediaAck {session_id, ack_count=1}."""
    # MediaAck proto layout:
    #   field 1 (session_id): varint → tag 0x08
    #   field 2 (ack_count):  varint → tag 0x10
    proto_body = _encode_varint_field(1, _session_id) + _encode_varint_field(2, 1)
    frame = _encode_media_frame(_video_channel_id, _MSG_MEDIA_ACK, proto_body)
    bus.publish("aa.frame.send", frame)


# ---------------------------------------------------------------------------
# Minimal protobuf varint helpers (no proto dependency)
# ---------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int | None, int]:
    """Read a varint from buf at pos. Returns (value, new_pos) or (None, pos) on error."""
    result = 0
    shift  = 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            return None, pos
    return None, pos


def _encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a protobuf varint."""
    out = []
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _encode_varint_field(field_number: int, value: int) -> bytes:
    """Encode a varint proto field: tag (wire type 0) + varint value."""
    tag = (field_number << 3) | 0x00
    return _encode_varint(tag) + _encode_varint(value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart",  on_system_readytostart)
    bus.subscribe("system.start",         on_system_start)
    bus.subscribe("system.stop",          on_system_stop)
    bus.subscribe("config.response",      on_config_response)
    bus.subscribe("aa.session.active",    on_aa_session_active)
    bus.subscribe("aa.session.shutdown",  on_aa_session_shutdown)
    # aa.frame.chN is subscribed dynamically in on_config_response

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
