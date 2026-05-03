"""
frame_codec.py — AA control-channel frame encode / decode.

AA frame wire format (control channel, channel 0):
  Byte 0   : channel_id  (always 0 for control)
  Byte 1   : flags       (0x0B = FIRST | LAST | ENCRYPTED during handshake)
  Byte 2-3 : payload length (u16 big-endian)
  Byte 4-5 : message_id   (u16 big-endian)  ← part of payload, first 2 bytes
  Byte 6+  : protobuf body

Note: before TLS is established the flags are 0x09 (FIRST | LAST, no ENCRYPT).
This module only encodes/decodes the framing layer; TLS wrapping is out-of-scope
for the Python head-unit daemon (TLS termination happens in liboaa / the prodigy lib).
"""

from __future__ import annotations

import struct
from typing import NamedTuple

# Frame flag constants
FLAG_FIRST     = 0x01
FLAG_LAST      = 0x02
FLAG_ENCRYPTED = 0x08
FLAG_CONTROL   = FLAG_FIRST | FLAG_LAST             # 0x03  pre-TLS
FLAG_FULL      = FLAG_FIRST | FLAG_LAST | FLAG_ENCRYPTED  # 0x0B post-TLS

CHANNEL_CONTROL = 0


class ControlFrame(NamedTuple):
    channel_id: int
    flags:      int
    message_id: int
    body:       bytes  # raw protobuf bytes (without the 2-byte message_id prefix)


def decode_control_frame(channel_id: int, flags: int, payload: bytes) -> ControlFrame | None:
    """Decode a raw AA payload (as received from FrameRelay) into a ControlFrame.

    The first 2 bytes of *payload* are the message_id; the remainder is the
    protobuf body.

    Returns None if the payload is too short.
    """
    if len(payload) < 2:
        return None
    message_id = struct.unpack_from(">H", payload, 0)[0]
    body = payload[2:]
    return ControlFrame(channel_id=channel_id, flags=flags, message_id=message_id, body=body)


def encode_control_frame(message_id: int, proto_body: bytes, encrypted: bool = False) -> dict:
    """Build an aa.frame.send payload dict for a control-channel message.

    Returns a dict ready to be passed to bus.publish("aa.frame.send", ...).

    Wire layout of the payload field:
        [message_id: 2B big-endian] [proto_body]
    """
    flags = FLAG_FULL if encrypted else FLAG_CONTROL
    payload = struct.pack(">H", message_id) + proto_body
    return {
        "channel_id":  CHANNEL_CONTROL,
        "flags":       flags,
        "payload_hex": payload.hex(),
    }
