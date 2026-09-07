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


def test_channels_from_sdr_invalid_hex():
    from shared.proto_utils import channels_from_sdr_bytes, channel_config_from_sdr
    assert channels_from_sdr_bytes("not_valid_hex") == []
    assert channels_from_sdr_bytes("") == []
    assert channel_config_from_sdr("not_valid_hex", 1) is None


def test_channels_from_sdr_synthetic():
    from shared.proto_utils import channels_from_sdr_bytes, channel_config_from_sdr
    from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
    from protos.oaa.control.ChannelDescriptorData_pb2 import ChannelDescriptor

    resp = ServiceDiscoveryResponse()
    ch = resp.channels.add()
    ch.channel_id = 1
    ch.sensor_channel.SetInParent()

    sdr_hex = resp.SerializeToString().hex()
    channels = channels_from_sdr_bytes(sdr_hex)
    assert len(channels) == 1
    assert channels[0]["channel_id"] == 1
    assert "sensor_channel" in channels[0]

    cfg = channel_config_from_sdr(sdr_hex, channel_id=1)
    assert cfg is not None
    assert cfg["channel_id"] == 1
    assert channel_config_from_sdr(sdr_hex, channel_id=99) is None
