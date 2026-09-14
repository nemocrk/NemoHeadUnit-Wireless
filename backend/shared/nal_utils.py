"""
nal_utils.py — Multi-codec bitstream parsing and binary streaming frame header pack/unpack.

Supports H.264, H.265 (HEVC), VP9, and AV1 keyframe and parameter header detection.
Follows 'docs/new-pattern.md' specification:
  Schema: [StreamType: 1 Byte (0=Video, 1=Audio)] + [Timestamp: 8 Bytes (uint64 BE)] + [Payload: N Bytes]
"""

import struct
from typing import Tuple

STREAM_TYPE_VIDEO = 0
STREAM_TYPE_AUDIO = 1

# H.264 NAL unit types
H264_NAL_NON_IDR = 1
H264_NAL_IDR = 5
H264_NAL_SEI = 6
H264_NAL_SPS = 7
H264_NAL_PPS = 8

# Backward compatibility aliases
NAL_TYPE_NON_IDR = H264_NAL_NON_IDR
NAL_TYPE_IDR = H264_NAL_IDR
NAL_TYPE_SEI = H264_NAL_SEI
NAL_TYPE_SPS = H264_NAL_SPS
NAL_TYPE_PPS = H264_NAL_PPS

# H.265 / HEVC NAL unit types
HEVC_NAL_TRAIL_N = 0
HEVC_NAL_TRAIL_R = 1
HEVC_NAL_IDR_W_RADL = 19
HEVC_NAL_IDR_N_LP = 20
HEVC_NAL_CRA_NUT = 21
HEVC_NAL_VPS = 32
HEVC_NAL_SPS = 33
HEVC_NAL_PPS = 34


def _find_annex_b_offset(payload: bytes) -> int:
    """Return index offset past optional Annex B 3-byte or 4-byte start code."""
    if not payload:
        return 0
    if len(payload) >= 4 and payload[:4] == b"\x00\x00\x00\x01":
        return 4
    if len(payload) >= 3 and payload[:3] == b"\x00\x00\x01":
        return 3
    return 0


def get_nal_type(payload: bytes) -> int:
    """Extract the H.264 NAL unit type from raw payload bytes."""
    if not payload:
        return 0
    idx = _find_annex_b_offset(payload)
    if idx < len(payload):
        return payload[idx] & 0x1F
    return 0


def get_nal_type_hevc(payload: bytes) -> int:
    """Extract the H.265 (HEVC) NAL unit type from raw payload bytes."""
    if not payload:
        return 0
    idx = _find_annex_b_offset(payload)
    if idx < len(payload):
        return (payload[idx] >> 1) & 0x3F
    return 0


def is_keyframe(payload: bytes, codec: str = "H264") -> bool:
    """
    Returns True if payload represents a Keyframe / IDR for the given codec
    ('H264', 'H265', 'HEVC', 'VP9', 'AV1').
    """
    if not payload:
        return False

    c = codec.upper()
    if "H265" in c or "HEVC" in c:
        nal_type = get_nal_type_hevc(payload)
        return nal_type in (HEVC_NAL_IDR_W_RADL, HEVC_NAL_IDR_N_LP, HEVC_NAL_CRA_NUT)

    elif "VP9" in c:
        # VP9 frame header: bit 0 of frame tag indicates frame_type (0 = keyframe, 1 = non-keyframe)
        idx = 0
        if len(payload) > idx:
            frame_marker = (payload[idx] >> 6) & 0x03
            if frame_marker == 0x02:  # VP9 standard frame marker binary 10
                frame_type = (payload[idx] >> 2) & 0x01
                return frame_type == 0
        return True  # Fallback to permissive keyframe

    elif "AV1" in c:
        # AV1 Open Bitstream Unit (OBU): OBU_SEQUENCE_HEADER (1) or OBU_FRAME_HEADER (3) / OBU_FRAME (6)
        idx = _find_annex_b_offset(payload)
        if idx < len(payload):
            obu_type = (payload[idx] >> 3) & 0x0F
            if obu_type in (1, 3, 6):
                return True
        return False

    else:  # Default H.264
        return get_nal_type(payload) == H264_NAL_IDR


def is_header_nal(payload: bytes, codec: str = "H264") -> bool:
    """Returns True if payload contains SPS/PPS/VPS parameter headers."""
    if not payload:
        return False

    c = codec.upper()
    if "H265" in c or "HEVC" in c:
        nal_type = get_nal_type_hevc(payload)
        return nal_type in (HEVC_NAL_VPS, HEVC_NAL_SPS, HEVC_NAL_PPS)
    elif "AV1" in c:
        idx = _find_annex_b_offset(payload)
        if idx < len(payload):
            obu_type = (payload[idx] >> 3) & 0x0F
            return obu_type == 1  # OBU_SEQUENCE_HEADER
        return False
    elif "VP9" in c:
        return False
    else:  # H.264
        nal_type = get_nal_type(payload)
        return nal_type in (H264_NAL_SPS, H264_NAL_PPS)


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
