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


def test_get_codec_descriptor_all_codecs():
    from shared.proto_utils import get_codec_descriptor

    # Test by integer enum
    desc_pcm = get_codec_descriptor(1)
    assert desc_pcm["media_type"] == "AUDIO"
    assert desc_pcm["codec"] == "PCM"

    desc_aac_raw = get_codec_descriptor(2)
    assert desc_aac_raw["media_type"] == "AUDIO"
    assert desc_aac_raw["codec"] == "mp4a.40.2"

    desc_h264 = get_codec_descriptor(3)
    assert desc_h264["media_type"] == "VIDEO"
    assert desc_h264["codec"] == "avc1.42E01E"

    desc_adts = get_codec_descriptor(4)
    assert desc_adts["audio_format"] == "aac_adts"

    desc_vp9 = get_codec_descriptor(5)
    assert desc_vp9["codec"] == "vp09.00.10.08"

    desc_av1 = get_codec_descriptor(6)
    assert desc_av1["codec"] == "av01.0.04M.08"

    desc_h265 = get_codec_descriptor(7)
    assert desc_h265["codec"] == "hev1.1.6.L93.B0"

    # Test by string name
    desc_str = get_codec_descriptor("MEDIA_CODEC_VIDEO_VP9")
    assert desc_str["codec_enum"] == 5


def test_proto_to_dict_and_dict_to_proto():
    from shared.proto_utils import proto_to_dict, dict_to_proto, decode_proto, encode_proto
    from protos.oaa.control.PingRequestMessage_pb2 import PingRequest

    req = PingRequest()
    req.timestamp = 123456789
    encoded = encode_proto(req)
    assert len(encoded) > 0

    decoded = decode_proto(PingRequest, encoded)
    assert decoded.timestamp == 123456789

    # decode_proto on empty bytes returns None
    assert decode_proto(PingRequest, b"") is None

    # decode_proto on malformed bytes returns None
    assert decode_proto(PingRequest, b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01") is None

    # proto_to_dict (64-bit ints serialize as string per proto JSON spec)
    d = proto_to_dict(req)
    assert int(d["timestamp"]) == 123456789

    # dict_to_proto
    rebuilt = PingRequest()
    dict_to_proto(rebuilt, {"timestamp": 987654321})
    assert rebuilt.timestamp == 987654321


def test_media_with_timestamp_proto_wire_roundtrip():
    from shared.proto_utils import build_media_with_timestamp, parse_media_with_timestamp
    import struct

    ts_us = 1234567890
    data = b"h264_nal_unit_slice_payload"

    # 1. Big-endian wire format from build_media_with_timestamp
    raw_wire = build_media_with_timestamp(ts_us, data)
    parsed_ts, parsed_data = parse_media_with_timestamp(raw_wire)
    assert parsed_ts == ts_us
    assert parsed_data == data

    # 2. Proto wire format with tag 0x09 (field 1 fixed64) and 0x12 (field 2 bytes)
    proto_wire = b"\x09" + struct.pack("<Q", ts_us) + b"\x12" + bytes([len(data)]) + data
    proto_ts, proto_data = parse_media_with_timestamp(proto_wire)
    assert proto_ts == ts_us
    assert proto_data == data


def test_read_varint_and_skip_field():
    from shared.proto_utils import _read_varint, _skip_field

    # Single-byte varint
    val, pos = _read_varint(b"\x01\x02", 0)
    assert val == 1
    assert pos == 1

    # Multi-byte varint (300 = 0xac 0x02)
    val, pos = _read_varint(b"\xac\x02\xff", 0)
    assert val == 300
    assert pos == 2

    # Truncated varint
    val, pos = _read_varint(b"\x80", 0)
    assert val is None

    # Overflow varint (> 64 bits)
    val, pos = _read_varint(b"\x80" * 11, 0)
    assert val is None

    # _skip_field wire_type 0 (varint)
    assert _skip_field(b"\x05\xaa", 0, 0) == 1
    # _skip_field wire_type 1 (64-bit)
    assert _skip_field(b"12345678extra", 0, 1) == 8
    # _skip_field wire_type 2 (length-delimited: len 4 + 4 bytes)
    assert _skip_field(b"\x04abcdextra", 0, 2) == 5
    # _skip_field wire_type 5 (32-bit)
    assert _skip_field(b"1234extra", 0, 5) == 4
    # _skip_field invalid wire type
    assert _skip_field(b"extra", 0, 99) is None


def test_coerce_scalar_types():
    from shared.proto_utils import _coerce_scalar
    from google.protobuf import descriptor as _descriptor
    from unittest.mock import MagicMock

    # TYPE_BOOL
    field_bool = MagicMock()
    field_bool.type = _descriptor.FieldDescriptor.TYPE_BOOL
    assert _coerce_scalar(field_bool, "true") is True
    assert _coerce_scalar(field_bool, "yes") is True
    assert _coerce_scalar(field_bool, "0") is False
    assert _coerce_scalar(field_bool, True) is True

    # TYPE_INT
    field_int = MagicMock()
    field_int.type = _descriptor.FieldDescriptor.TYPE_INT32
    assert _coerce_scalar(field_int, "42") == 42
    assert _coerce_scalar(field_int, "-10") == -10
    assert _coerce_scalar(field_int, 100) == 100

    # TYPE_ENUM
    field_enum = MagicMock()
    field_enum.type = _descriptor.FieldDescriptor.TYPE_ENUM
    val_mock = MagicMock()
    val_mock.name = "MEDIA_CODEC_VIDEO_H264_BP"
    val_mock.number = 1
    field_enum.enum_type.values = [val_mock]
    field_enum.enum_type.values_by_name = {"MEDIA_CODEC_VIDEO_H264_BP": val_mock}
    field_enum.default_value = 0

    # Exact name match
    assert _coerce_scalar(field_enum, "MEDIA_CODEC_VIDEO_H264_BP") == 1
    # Case insensitive match
    assert _coerce_scalar(field_enum, "media_codec_video_h264_bp") == 1
    # Integer as string
    assert _coerce_scalar(field_enum, "5") == 5
    # Integer
    assert _coerce_scalar(field_enum, 2) == 2
    # Invalid fallback
    assert _coerce_scalar(field_enum, "NON_EXISTENT") == 0


def test_channels_from_sdr_bytes_and_lookup():
    from shared.proto_utils import channels_from_sdr_bytes, channel_config_from_sdr
    from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
    from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType

    sdr = ServiceDiscoveryResponse()
    ch1 = sdr.channels.add()
    ch1.channel_id = 1
    ch1.sensor_channel.sensors.add().type = 1

    ch3 = sdr.channels.add()
    ch3.channel_id = 3
    ch3.av_channel.codec = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP

    ch4 = sdr.channels.add()
    ch4.channel_id = 4
    ch4.av_channel.codec = MediaCodecType.MEDIA_CODEC_AUDIO_PCM
    ch4.av_channel.audio_type = 1
    ac = ch4.av_channel.audio_configs.add()
    ac.sample_rate = 48000
    ac.bit_depth = 16
    ac.channel_count = 2

    sdr_hex = sdr.SerializeToString().hex()

    channels = channels_from_sdr_bytes(sdr_hex)
    assert len(channels) == 3
    assert channels[0]["channel_id"] == 1
    assert "sensor_channel" in channels[0]
    assert channels[1]["channel_id"] == 3
    assert channels[1]["av_channel"]["av_type"] == MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP
    assert channels[2]["channel_id"] == 4
    assert len(channels[2]["av_channel"]["audio_configs"]) == 1

    # channel_config_from_sdr
    assert channel_config_from_sdr(sdr_hex, 3)["channel_id"] == 3
    assert channel_config_from_sdr(sdr_hex, 999) is None

    # Error handling
    assert channels_from_sdr_bytes("bad_hex") == []


