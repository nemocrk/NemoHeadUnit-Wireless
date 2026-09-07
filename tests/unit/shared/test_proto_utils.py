import struct
import pytest
from shared.proto_utils import (
    encode_aa_frame,
    decode_aa_frame,
    build_media_with_timestamp,
    parse_media_with_timestamp,
)

pytestmark = pytest.mark.unit


def test_aa_frame_encode_decode_roundtrip():
    channel_id = 3
    message_id = 0x8001
    payload = b"test_payload_bytes_12345"

    frame = encode_aa_frame(channel_id=channel_id, message_id=message_id, proto_body=payload)
    assert frame["channel_id"] == channel_id
    assert frame["flags"] == 0x0B

    raw_wire_bytes = bytes.fromhex(frame["payload_hex"])
    decoded = decode_aa_frame(raw_wire_bytes)
    assert decoded is not None
    dec_msg_id, dec_body = decoded
    assert dec_msg_id == message_id
    assert dec_body == payload


def test_aa_frame_decode_malformed_short_bytes():
    assert decode_aa_frame(b"") is None
    assert decode_aa_frame(b"\x01") is None


def test_media_with_timestamp_roundtrip():
    timestamp_us = 1718000000123456
    media_data = b"\x00\x00\x01\x65\x88\x84\x00\x10\xff\xee"

    packed = build_media_with_timestamp(timestamp_us, media_data)
    # Check 8-byte BE timestamp prefix
    ts_prefix = struct.unpack(">Q", packed[:8])[0]
    assert ts_prefix == timestamp_us
    assert packed[8:] == media_data

    dec_ts, dec_data = parse_media_with_timestamp(packed)
    assert dec_ts == timestamp_us
    assert dec_data == media_data


def test_media_with_timestamp_truncated():
    dec_ts, dec_data = parse_media_with_timestamp(b"\x01\x02\x03")
    assert dec_ts == 0
    assert dec_data == b""
