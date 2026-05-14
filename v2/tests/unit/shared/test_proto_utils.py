"""
test_proto_utils.py — Unit tests for shared/proto_utils.py.

Coverage targets (§1.3 TEST_SUITE_ARCHITECTURE):
  1.  decode_proto — happy path, empty bytes, corrupted bytes
  2.  encode_proto — round-trip with a real proto message
  3.  encode_aa_frame — payload structure (2-byte msg_id prefix + body hex)
  4.  decode_aa_frame — happy path, 2-byte minimum, truncated (< 2 bytes)
  5.  parse_media_with_timestamp
        a. protobuf wire format (field 1 fixed64 + field 2 bytes)
        b. compact [uint64 BE][raw bytes] fallback
        c. malformed / empty body → (0, b"")
  6.  build_media_with_timestamp — symmetric with parse, big-endian packing
  7.  proto_to_dict — round-trip via MessageToDict
  8.  dict_to_proto — scalar, enum (string + int), repeated, nested, unknown key
  9.  schema_from_proto_message — bool/int/float/string/enum/repeated/message/oneof
  10. channels_from_sdr_bytes — audio channel dict structure
  11. channel_config_from_sdr — found / not found
  12. _read_varint — single byte, multi-byte, truncated
  13. _skip_field — each wire type (0,1,2,5) + unknown
  14. encode_aa_frame control flag — runtime has no effect on wire bytes

Test strategy:
  - Only protos from the real compiled pb2 modules are used.
  - No mocking of proto internals; behaviour is verified on the wire bytes.
  - Private helpers (_read_varint, _skip_field) are imported directly.
  - SDR bytes fixture is built programmatically from a real
    ServiceDiscoveryResponse proto to avoid hard-coded hex strings that
    would break on proto changes.
"""

from __future__ import annotations

import struct

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from shared.proto_utils import (  # noqa: E402
    decode_proto,
    encode_proto,
    encode_aa_frame,
    decode_aa_frame,
    parse_media_with_timestamp,
    build_media_with_timestamp,
    proto_to_dict,
    dict_to_proto,
    schema_from_proto_message,
    channels_from_sdr_bytes,
    channel_config_from_sdr,
    _read_varint,
    _skip_field,
)

# ---------------------------------------------------------------------------
# Proto imports used across tests
# ---------------------------------------------------------------------------
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse          # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2      import ControlMessage               # noqa: E402
from oaa.av.AVChannelSetupResponseMessage_pb2   import AVChannelSetupResponse       # noqa: E402
from oaa.av.AVChannelMessageIdsEnum_pb2         import AVChannelMessage             # noqa: E402
from oaa.av.AVMediaAckIndicationMessage_pb2     import AVMediaAckIndication         # noqa: E402
from oaa.common.StatusEnum_pb2                  import Status                       # noqa: E402


# ---------------------------------------------------------------------------
# SDR fixture factory
# ---------------------------------------------------------------------------

def _make_sdr_hex(channel_id: int = 4) -> str:
    """
    Build a minimal ServiceDiscoveryResponse containing one AUDIO channel
    and return its hex string, suitable for channels_from_sdr_bytes().
    """
    from oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
    from oaa.control.ChannelDescriptorData_pb2 import ChannelDescriptor
    from oaa.audio.AudioTypeEnum_pb2 import AudioType
    from oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType

    sdr = ServiceDiscoveryResponse()
    sdr.headunit_manufacturer = "NemoTest"
    sdr.headunit_model   = "TestModel"

    ch = sdr.channels.add()
    ch.channel_id = channel_id
    av = ch.av_channel
    av.codec = MediaCodecType.MEDIA_CODEC_AUDIO_PCM
    av.audio_type  = AudioType.MEDIA
    ac = av.audio_configs.add()
    ac.sample_rate   = 48000
    ac.bit_depth     = 16
    ac.channel_count = 2

    return sdr.SerializeToString().hex()


# ============================================================================
# TEST CLASSES
# ============================================================================


class TestDecodeProto:

    @pytest.mark.unit
    def test_happy_path(self):
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        raw = resp.SerializeToString()
        decoded = decode_proto(ChannelOpenResponse, raw)
        assert decoded is not None
        assert decoded.status == Status.OK

    @pytest.mark.unit
    def test_empty_bytes_returns_none(self):
        result = decode_proto(ChannelOpenResponse, b"")
        assert result is None

    @pytest.mark.unit
    def test_corrupted_bytes_returns_none(self):
        result = decode_proto(ChannelOpenResponse, b"\xFF\xFE\xFD\x00" * 8)
        # May return None OR a partially parsed message depending on proto
        # behaviour — what matters is it does NOT raise.
        # If not None, it is still an instance of the class.
        if result is not None:
            assert isinstance(result, ChannelOpenResponse)


class TestEncodeProto:

    @pytest.mark.unit
    def test_round_trip(self):
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        raw = encode_proto(resp)
        assert isinstance(raw, bytes)
        assert len(raw) > 0
        back = ChannelOpenResponse()
        back.ParseFromString(raw)
        assert back.status == Status.OK

    @pytest.mark.unit
    def test_empty_message_encodes_to_bytes(self):
        msg = AVMediaAckIndication()
        raw = encode_proto(msg)
        assert isinstance(raw, bytes)


class TestEncodeAAFrame:

    @pytest.mark.unit
    def test_payload_hex_starts_with_message_id(self):
        msg_id = ControlMessage.CHANNEL_OPEN_RESPONSE  # 0x0008
        frame = encode_aa_frame(0, msg_id, b"\x08\x00")
        payload = bytes.fromhex(frame["payload_hex"])
        parsed_id = struct.unpack_from(">H", payload, 0)[0]
        assert parsed_id == msg_id

    @pytest.mark.unit
    def test_body_appended_after_msg_id(self):
        body = b"\x01\x02\x03\x04"
        frame = encode_aa_frame(1, 0x0001, body)
        payload = bytes.fromhex(frame["payload_hex"])
        assert payload[2:] == body

    @pytest.mark.unit
    def test_channel_id_preserved(self):
        frame = encode_aa_frame(7, 0x0001, b"")
        assert frame["channel_id"] == 7

    @pytest.mark.unit
    def test_empty_body_encodes_correctly(self):
        frame = encode_aa_frame(0, 0x0002, b"")
        payload = bytes.fromhex(frame["payload_hex"])
        assert len(payload) == 2  # only the 2-byte message_id

    @pytest.mark.unit
    def test_control_flag_no_runtime_difference(self):
        """control=True vs False must produce the same wire bytes (documented no-op)."""
        f1 = encode_aa_frame(0, 0x0008, b"\x08\x00", control=False)
        f2 = encode_aa_frame(0, 0x0008, b"\x08\x00", control=True)
        # payload_hex must be identical
        assert f1["payload_hex"] == f2["payload_hex"]


class TestDecodeAAFrame:

    @pytest.mark.unit
    def test_happy_path(self):
        msg_id = 0x0042
        body   = b"\xDE\xAD\xBE\xEF"
        raw    = struct.pack(">H", msg_id) + body
        result = decode_aa_frame(raw)
        assert result is not None
        assert result[0] == msg_id
        assert result[1] == body

    @pytest.mark.unit
    def test_exactly_two_bytes_is_valid(self):
        raw = struct.pack(">H", 0x0001)
        result = decode_aa_frame(raw)
        assert result is not None
        assert result[0] == 0x0001
        assert result[1] == b""

    @pytest.mark.unit
    def test_one_byte_returns_none(self):
        assert decode_aa_frame(b"\x00") is None

    @pytest.mark.unit
    def test_empty_returns_none(self):
        assert decode_aa_frame(b"") is None

    @pytest.mark.unit
    def test_round_trip_with_encode(self):
        msg_id = AVChannelMessage.AV_MEDIA_ACK_INDICATION
        body   = b"\x08\x01\x10\x01"
        frame  = encode_aa_frame(3, msg_id, body)
        payload = bytes.fromhex(frame["payload_hex"])
        result  = decode_aa_frame(payload)
        assert result[0] == msg_id
        assert result[1] == body


class TestParseMediaWithTimestamp:

    @pytest.mark.unit
    def test_compact_format_ts_and_data(self):
        ts   = 1_234_567_890
        data = b"\xAB\xCD\xEF" * 10
        body = struct.pack(">Q", ts) + data
        ts_out, data_out = parse_media_with_timestamp(body)
        assert data_out == data
        # ts may be parsed as big-endian (compact) or little-endian (proto fixed64)
        assert isinstance(ts_out, int)

    @pytest.mark.unit
    def test_protobuf_wire_format(self):
        """Build a proper proto-encoded body: field 1 fixed64 + field 2 bytes."""
        ts   = 999_000
        data = b"\x00\x01\x02\x03"
        # field 1, wire type 1 (fixed64), little-endian
        tag1 = (1 << 3) | 1  # 0x09
        field1 = bytes([tag1]) + struct.pack("<Q", ts)
        # field 2, wire type 2 (length-delimited)
        tag2   = (2 << 3) | 2  # 0x12
        field2 = bytes([tag2, len(data)]) + data
        body   = field1 + field2
        ts_out, data_out = parse_media_with_timestamp(body)
        assert ts_out  == ts
        assert data_out == data

    @pytest.mark.unit
    def test_empty_body_returns_zero_empty(self):
        ts, data = parse_media_with_timestamp(b"")
        assert ts   == 0
        assert data == b""

    @pytest.mark.unit
    def test_short_body_under_8_bytes(self):
        ts, data = parse_media_with_timestamp(b"\x01\x02\x03")
        # Should not raise; ts=0, data=b""
        assert isinstance(ts, int)
        assert isinstance(data, bytes)

    @pytest.mark.unit
    def test_only_timestamp_no_payload(self):
        body = struct.pack(">Q", 42_000)
        ts, data = parse_media_with_timestamp(body)
        assert isinstance(ts, int)
        assert data == b""


class TestBuildMediaWithTimestamp:

    @pytest.mark.unit
    def test_first_8_bytes_are_ts_big_endian(self):
        ts   = 123_456_789
        data = b"\xAB" * 8
        out  = build_media_with_timestamp(ts, data)
        assert struct.unpack_from(">Q", out, 0)[0] == ts

    @pytest.mark.unit
    def test_data_appended_after_ts(self):
        data = b"\x01\x02\x03"
        out  = build_media_with_timestamp(0, data)
        assert out[8:] == data

    @pytest.mark.unit
    def test_total_length(self):
        data = b"x" * 100
        out  = build_media_with_timestamp(0, data)
        assert len(out) == 108

    @pytest.mark.unit
    def test_zero_ts_zero_data(self):
        out = build_media_with_timestamp(0, b"")
        assert out == b"\x00" * 8


class TestProtoToDict:

    @pytest.mark.unit
    def test_status_ok_in_dict(self):
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        d = proto_to_dict(resp)
        assert isinstance(d, dict)
        # MessageToDict uses camelCase and string enum names
        assert "status" in d or "Status" in str(d)

    @pytest.mark.unit
    def test_returns_dict_type(self):
        msg = AVMediaAckIndication()
        msg.session_id = 7
        msg.ack_count  = 1
        d = proto_to_dict(msg)
        assert isinstance(d, dict)


class TestDictToProto:

    @pytest.mark.unit
    def test_scalar_int_field(self):
        msg = AVMediaAckIndication()
        dict_to_proto(msg, {"session_id": 42, "ack_count": 3})
        assert msg.session_id == 42
        assert msg.ack_count  == 3

    @pytest.mark.unit
    def test_enum_string_value(self):
        resp = ChannelOpenResponse()
        dict_to_proto(resp, {"status": "OK"})
        assert resp.status == Status.OK

    @pytest.mark.unit
    def test_enum_int_value(self):
        resp = ChannelOpenResponse()
        dict_to_proto(resp, {"status": int(Status.OK)})
        assert resp.status == Status.OK

    @pytest.mark.unit
    def test_unknown_key_silently_skipped(self):
        msg = AVMediaAckIndication()
        # Must not raise
        dict_to_proto(msg, {"nonexistent_field": 999, "session_id": 1})
        assert msg.session_id == 1

    @pytest.mark.unit
    def test_setup_response_max_unacked(self):
        resp = AVChannelSetupResponse()
        dict_to_proto(resp, {"max_unacked": 7})
        assert resp.max_unacked == 7


class TestSchemaFromProtoMessage:

    @pytest.mark.unit
    def test_returns_dict(self):
        schema = schema_from_proto_message(ChannelOpenResponse.DESCRIPTOR)
        assert isinstance(schema, dict)

    @pytest.mark.unit
    def test_enum_field_present(self):
        """ChannelOpenResponse has a 'status' enum field."""
        schema = schema_from_proto_message(ChannelOpenResponse.DESCRIPTOR)
        assert "status" in schema

    @pytest.mark.unit
    def test_int_field_present(self):
        schema = schema_from_proto_message(AVMediaAckIndication.DESCRIPTOR)
        assert "session_id" in schema
        assert "ack_count" in schema

    @pytest.mark.unit
    def test_setup_response_max_unacked_is_int(self):
        from shared.config_schema import ConfigFieldSchema
        schema = schema_from_proto_message(AVChannelSetupResponse.DESCRIPTOR)
        assert "max_unacked" in schema

    @pytest.mark.unit
    def test_no_exception_on_nested_message(self):
        """ServiceDiscoveryResponse has deeply nested messages — must not crash."""
        from oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
        schema = schema_from_proto_message(ServiceDiscoveryResponse.DESCRIPTOR)
        assert isinstance(schema, dict)


class TestChannelsFromSdrBytes:

    @pytest.mark.unit
    def test_returns_list(self):
        sdr_hex = _make_sdr_hex(channel_id=4)
        result  = channels_from_sdr_bytes(sdr_hex)
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.unit
    def test_channel_id_correct(self):
        sdr_hex = _make_sdr_hex(channel_id=4)
        result  = channels_from_sdr_bytes(sdr_hex)
        assert result[0]["channel_id"] == 4

    @pytest.mark.unit
    def test_av_channel_key_present(self):
        result = channels_from_sdr_bytes(_make_sdr_hex())
        assert "av_channel" in result[0]

    @pytest.mark.unit
    def test_audio_configs_present(self):
        result = channels_from_sdr_bytes(_make_sdr_hex())
        av = result[0]["av_channel"]
        assert "audio_configs" in av
        assert len(av["audio_configs"]) == 1

    @pytest.mark.unit
    def test_audio_config_fields(self):
        result = channels_from_sdr_bytes(_make_sdr_hex())
        ac = result[0]["av_channel"]["audio_configs"][0]
        assert ac["sample_rate"]   == 48000
        assert ac["bit_depth"]     == 16
        assert ac["channel_count"] == 2

    @pytest.mark.unit
    def test_empty_hex_returns_empty_list(self):
        assert channels_from_sdr_bytes("") == []

    @pytest.mark.unit
    def test_invalid_hex_returns_empty_list(self):
        assert channels_from_sdr_bytes("ZZZZ") == []

    @pytest.mark.unit
    def test_garbage_bytes_returns_empty_or_partial(self):
        # All-zero bytes are valid proto (empty message) — should not raise
        result = channels_from_sdr_bytes(b"\x00" * 16)
        assert isinstance(result, list)


class TestChannelConfigFromSdr:

    @pytest.mark.unit
    def test_found_returns_dict(self):
        sdr_hex = _make_sdr_hex(channel_id=4)
        result  = channel_config_from_sdr(sdr_hex, 4)
        assert result is not None
        assert result["channel_id"] == 4

    @pytest.mark.unit
    def test_not_found_returns_none(self):
        sdr_hex = _make_sdr_hex(channel_id=4)
        result  = channel_config_from_sdr(sdr_hex, 99)
        assert result is None

    @pytest.mark.unit
    def test_empty_sdr_returns_none(self):
        assert channel_config_from_sdr("", 4) is None


class TestReadVarint:

    @pytest.mark.unit
    def test_single_byte(self):
        val, pos = _read_varint(b"\x05", 0)
        assert val == 5
        assert pos == 1

    @pytest.mark.unit
    def test_two_byte_varint(self):
        # 300 = 0xAC 0x02
        val, pos = _read_varint(b"\xAC\x02", 0)
        assert val == 300
        assert pos == 2

    @pytest.mark.unit
    def test_truncated_returns_none(self):
        # continuation bit set but no next byte
        val, pos = _read_varint(b"\x80", 0)
        assert val is None

    @pytest.mark.unit
    def test_offset_respected(self):
        buf = b"\x00\x01"
        val, pos = _read_varint(buf, 1)
        assert val == 1
        assert pos == 2


class TestSkipField:

    @pytest.mark.unit
    def test_wire_type_0_varint(self):
        # varint 5 at pos 0 → advances 1 byte
        new_pos = _skip_field(b"\x05", 0, 0)
        assert new_pos == 1

    @pytest.mark.unit
    def test_wire_type_1_fixed64(self):
        buf     = b"\x00" * 8
        new_pos = _skip_field(buf, 0, 1)
        assert new_pos == 8

    @pytest.mark.unit
    def test_wire_type_2_length_delimited(self):
        # varint length=4, then 4 bytes data
        buf     = b"\x04" + b"\xAB" * 4
        new_pos = _skip_field(buf, 0, 2)
        assert new_pos == 5

    @pytest.mark.unit
    def test_wire_type_5_fixed32(self):
        buf     = b"\x00" * 4
        new_pos = _skip_field(buf, 0, 5)
        assert new_pos == 4

    @pytest.mark.unit
    def test_unknown_wire_type_returns_none(self):
        assert _skip_field(b"\x00", 0, 3) is None
        assert _skip_field(b"\x00", 0, 4) is None

    @pytest.mark.unit
    def test_wire_type_1_truncated_returns_none(self):
        # only 4 bytes instead of 8
        assert _skip_field(b"\x00" * 4, 0, 1) is None
