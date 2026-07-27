"""
nal_utils.py — Binary H.264 NAL parsing and binary streaming frame header pack/unpack.

Follows 'docs/new-pattern.md' specification:
  Schema: [StreamType: 1 Byte (0=Video, 1=Audio)] + [Timestamp: 8 Bytes (uint64 BE)] + [Payload: N Bytes]
"""

import struct
from typing import Tuple

STREAM_TYPE_VIDEO = 0
STREAM_TYPE_AUDIO = 1

NAL_TYPE_NON_IDR = 1
NAL_TYPE_IDR = 5
NAL_TYPE_SEI = 6
NAL_TYPE_SPS = 7
NAL_TYPE_PPS = 8


def get_nal_type(payload: bytes) -> int:
    """
    Extract the H.264 NAL unit type from raw payload bytes.
    Handles optional Annex B 3-byte (0x000001) or 4-byte (0x00000001) start codes.
    Formula: nal_type = payload[0] & 0x1F
    """
    if not payload:
        return 0

    idx = 0
    # Skip Annex B start code prefix if present
    if len(payload) >= 4 and payload[:4] == b"\x00\x00\x00\x01":
        idx = 4
    elif len(payload) >= 3 and payload[:3] == b"\x00\x00\x01":
        idx = 3

    if idx < len(payload):
        return payload[idx] & 0x1F
    return 0


def is_keyframe(payload: bytes) -> bool:
    """Returns True if the payload contains an IDR Keyframe (NAL unit type 5)."""
    return get_nal_type(payload) == NAL_TYPE_IDR


def is_header_nal(payload: bytes) -> bool:
    """Returns True if the payload contains SPS (7) or PPS (8) parameters."""
    nal_type = get_nal_type(payload)
    return nal_type in (NAL_TYPE_SPS, NAL_TYPE_PPS)


def pack_media_frame(channel_id: int, timestamp_us: int, payload: bytes) -> bytes:
    """
    Pack binary WebSocket media payload according to docs/new-pattern.md:
      [ChannelID: 1 Byte] + [Timestamp: 8 Bytes (unsigned long long BE)] + [Payload: N Bytes]
    """
    header = struct.pack(">B Q", channel_id, timestamp_us)
    return header + payload


def unpack_media_frame(data: bytes) -> Tuple[int, int, bytes]:
    """
    Unpack binary WebSocket media payload:
      Returns (channel_id, timestamp_us, payload)
    """
    if len(data) < 9:
        raise ValueError(f"Payload too short ({len(data)} bytes, minimum 9 required)")

    channel_id, timestamp_us = struct.unpack_from(">B Q", data, 0)
    payload = data[9:]
    return channel_id, timestamp_us, payload

