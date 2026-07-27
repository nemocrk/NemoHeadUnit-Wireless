"""
Unit tests for oaa_control_channel/serializer.py

Covers:
  - FrameHeader: serialize, parse, round-trip, edge cases
  - FrameSerializer: single BULK frame, multi-frame (FIRST/MIDDLE/LAST), raw serialize, validation
  - Messenger: message type logic, encryption type logic, serialize_and_log end-to-end
"""

import struct
import sys
import importlib
import pytest

# ---------------------------------------------------------------------------
# Import under test (no module-level singletons — direct import is safe)
# ---------------------------------------------------------------------------
import importlib
import sys

# Ensure clean import from v2 source tree
_MODULE_PATH = "oaa_control_channel.serializer"


def _import_serializer():
    if _MODULE_PATH in sys.modules:
        del sys.modules[_MODULE_PATH]
    import oaa_control_channel.serializer as mod
    importlib.reload(mod)
    return mod


@pytest.fixture(scope="module")
def ser_mod():
    return _import_serializer()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_bulk_frame(frame: bytes):
    """Return (channel_id, flags_byte, payload_size, payload) for a BULK frame."""
    channel_id = frame[0]
    flags = frame[1]
    payload_size = struct.unpack(">H", frame[2:4])[0]
    payload = frame[4:]
    return channel_id, flags, payload_size, payload


def _parse_first_frame(frame: bytes):
    """Return (channel_id, flags_byte, frame_payload_size, total_size, payload)."""
    channel_id = frame[0]
    flags = frame[1]
    frame_payload_size = struct.unpack(">H", frame[2:4])[0]
    total_size = struct.unpack(">I", frame[4:8])[0]
    payload = frame[8:]
    return channel_id, flags, frame_payload_size, total_size, payload


# ===========================================================================
# Section 1 — FrameHeader
# ===========================================================================

class TestFrameHeaderSerialize:

    @pytest.mark.unit
    def test_serialize_returns_2_bytes(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert len(h.serialize()) == 2

    @pytest.mark.unit
    def test_serialize_channel_id_in_byte0(self, ser_mod):
        h = ser_mod.FrameHeader(7, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[0] == 7

    @pytest.mark.unit
    def test_serialize_bulk_plain_specific_flags(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        # BULK=0x03, PLAIN=0x00, SPECIFIC=0x00 → 0x03
        assert h.serialize()[1] == 0x03

    @pytest.mark.unit
    def test_serialize_encrypted_flag_set(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.ENCRYPTED, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[1] & 0x08 == 0x08

    @pytest.mark.unit
    def test_serialize_control_message_type_flag(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.CONTROL)
        assert h.serialize()[1] & 0x04 == 0x04

    @pytest.mark.unit
    def test_serialize_first_frame_type(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.FIRST, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[1] & 0x03 == ser_mod.FrameType.FIRST

    @pytest.mark.unit
    def test_serialize_last_frame_type(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.LAST, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[1] & 0x03 == ser_mod.FrameType.LAST

    @pytest.mark.unit
    def test_serialize_middle_frame_type(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.MIDDLE, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[1] & 0x03 == ser_mod.FrameType.MIDDLE

    @pytest.mark.unit
    def test_serialize_channel_id_255(self, ser_mod):
        h = ser_mod.FrameHeader(255, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[0] == 255

    @pytest.mark.unit
    def test_serialize_channel_id_0(self, ser_mod):
        h = ser_mod.FrameHeader(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC)
        assert h.serialize()[0] == 0


class TestFrameHeaderParse:

    @pytest.mark.unit
    def test_parse_channel_id(self, ser_mod):
        data = bytes([5, 0x03])
        h = ser_mod.FrameHeader.parse(data)
        assert h.channel_id == 5

    @pytest.mark.unit
    def test_parse_frame_type_bulk(self, ser_mod):
        data = bytes([0, 0x03])
        h = ser_mod.FrameHeader.parse(data)
        assert h.frame_type == ser_mod.FrameType.BULK

    @pytest.mark.unit
    def test_parse_frame_type_first(self, ser_mod):
        data = bytes([0, 0x01])
        h = ser_mod.FrameHeader.parse(data)
        assert h.frame_type == ser_mod.FrameType.FIRST

    @pytest.mark.unit
    def test_parse_encryption_plain(self, ser_mod):
        data = bytes([0, 0x03])
        h = ser_mod.FrameHeader.parse(data)
        assert h.encryption_type == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_parse_encryption_encrypted(self, ser_mod):
        data = bytes([0, 0x0B])  # BULK | ENCRYPTED
        h = ser_mod.FrameHeader.parse(data)
        assert h.encryption_type == ser_mod.EncryptionType.ENCRYPTED

    @pytest.mark.unit
    def test_parse_message_type_control(self, ser_mod):
        data = bytes([0, 0x07])  # BULK | CONTROL
        h = ser_mod.FrameHeader.parse(data)
        assert h.message_type == ser_mod.MessageType.CONTROL

    @pytest.mark.unit
    def test_parse_too_short_raises(self, ser_mod):
        with pytest.raises(ValueError):
            ser_mod.FrameHeader.parse(bytes([0]))

    @pytest.mark.unit
    def test_parse_empty_raises(self, ser_mod):
        with pytest.raises(ValueError):
            ser_mod.FrameHeader.parse(b"")


class TestFrameHeaderRoundtrip:

    @pytest.mark.unit
    @pytest.mark.parametrize("channel_id,frame_type,enc,msg_type", [
        (0, 0x03, 0x00, 0x00),
        (3, 0x01, 0x08, 0x04),
        (7, 0x02, 0x00, 0x00),
        (12, 0x03, 0x08, 0x04),
        (255, 0x03, 0x00, 0x04),
    ])
    def test_roundtrip(self, ser_mod, channel_id, frame_type, enc, msg_type):
        h = ser_mod.FrameHeader(channel_id, frame_type, enc, msg_type)
        data = h.serialize()
        h2 = ser_mod.FrameHeader.parse(data)
        assert h2.channel_id == channel_id
        assert h2.frame_type == frame_type
        assert h2.encryption_type == enc
        assert h2.message_type == msg_type


# ===========================================================================
# Section 2 — FrameSerializer
# ===========================================================================

class TestFrameSerializerBulk:

    @pytest.mark.unit
    def test_small_payload_produces_single_frame(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, b"\x01\x02\x03")
        assert len(frames) == 1

    @pytest.mark.unit
    def test_empty_payload_single_frame(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, b"")
        assert len(frames) == 1

    @pytest.mark.unit
    def test_bulk_frame_channel_id_correct(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        frames = fs.serialize(3, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, b"hello")
        ch, _, _, _ = _parse_bulk_frame(frames[0])
        assert ch == 3

    @pytest.mark.unit
    def test_bulk_frame_payload_size_field(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        payload = b"hello"
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        _, _, size, _ = _parse_bulk_frame(frames[0])
        assert size == len(payload)

    @pytest.mark.unit
    def test_bulk_frame_payload_content(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        payload = b"\xDE\xAD\xBE\xEF"
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        _, _, _, body = _parse_bulk_frame(frames[0])
        assert body == payload

    @pytest.mark.unit
    def test_bulk_frame_flags_encrypted(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.ENCRYPTED, b"x")
        _, flags, _, _ = _parse_bulk_frame(frames[0])
        assert flags & 0x08 == 0x08

    @pytest.mark.unit
    def test_bulk_frame_flags_plain(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, b"x")
        _, flags, _, _ = _parse_bulk_frame(frames[0])
        assert flags & 0x08 == 0x00

    @pytest.mark.unit
    def test_payload_exactly_at_threshold_is_bulk(self, ser_mod):
        threshold = 4096
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"A" * threshold
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        assert len(frames) == 1

    @pytest.mark.unit
    def test_invalid_channel_id_negative_raises(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        with pytest.raises(ValueError):
            fs.serialize(-1, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, b"x")

    @pytest.mark.unit
    def test_invalid_channel_id_over_255_raises(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        with pytest.raises(ValueError):
            fs.serialize(256, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, b"x")

    @pytest.mark.unit
    def test_non_bytes_payload_raises(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        with pytest.raises(TypeError):
            fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, "not bytes")

    @pytest.mark.unit
    def test_bytearray_payload_accepted(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, bytearray(b"ok"))
        assert len(frames) == 1

    @pytest.mark.unit
    def test_bulk_frame_total_length(self, ser_mod):
        # 2 bytes header + 2 bytes size field + N bytes payload
        fs = ser_mod.FrameSerializer()
        payload = b"hello"
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        assert len(frames[0]) == 2 + 2 + len(payload)


class TestFrameSerializerMultiFrame:

    @pytest.mark.unit
    def test_large_payload_produces_multiple_frames(self, ser_mod):
        threshold = 100
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"X" * 250
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        assert len(frames) >= 2

    @pytest.mark.unit
    def test_first_frame_flag_is_first(self, ser_mod):
        threshold = 100
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"X" * 250
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        _, flags, _, _, _ = _parse_first_frame(frames[0])
        assert flags & 0x03 == ser_mod.FrameType.FIRST

    @pytest.mark.unit
    def test_last_frame_flag_is_last(self, ser_mod):
        threshold = 100
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"X" * 250
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        last_frame = frames[-1]
        assert last_frame[1] & 0x03 == ser_mod.FrameType.LAST

    @pytest.mark.unit
    def test_first_frame_contains_total_size(self, ser_mod):
        threshold = 100
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"X" * 250
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        _, _, _, total_size, _ = _parse_first_frame(frames[0])
        assert total_size == len(payload)

    @pytest.mark.unit
    def test_middle_frames_flag_is_middle(self, ser_mod):
        threshold = 50
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"Y" * 200
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        # frames[1:-1] should all be MIDDLE
        for f in frames[1:-1]:
            assert f[1] & 0x03 == ser_mod.FrameType.MIDDLE

    @pytest.mark.unit
    def test_reassembled_payload_matches_original(self, ser_mod):
        threshold = 80
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = bytes(range(200))
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)

        # Extract payload from each frame
        reconstructed = b""
        for i, frame in enumerate(frames):
            if i == 0:
                _, _, _, _, body = _parse_first_frame(frame)
            else:
                _, _, size, body = _parse_bulk_frame(frame)
            reconstructed += body
        assert reconstructed == payload

    @pytest.mark.unit
    def test_all_frames_share_same_channel_id(self, ser_mod):
        threshold = 50
        fs = ser_mod.FrameSerializer(frame_size_threshold=threshold)
        payload = b"Z" * 200
        frames = fs.serialize(4, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        for f in frames:
            assert f[0] == 4

    @pytest.mark.unit
    def test_custom_threshold_1_byte_many_frames(self, ser_mod):
        fs = ser_mod.FrameSerializer(frame_size_threshold=1)
        payload = b"ABC"
        frames = fs.serialize(0, ser_mod.MessageType.SPECIFIC, ser_mod.EncryptionType.PLAIN, payload)
        # payload > threshold=1 so multi-frame
        assert len(frames) >= 2


class TestFrameSerializerRaw:

    @pytest.mark.unit
    def test_serialize_raw_bulk_returns_bytes(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        raw = fs.serialize_raw(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC, b"test")
        assert isinstance(raw, bytes)

    @pytest.mark.unit
    def test_serialize_raw_bulk_length(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        payload = b"hello"
        raw = fs.serialize_raw(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC, payload)
        assert len(raw) == 2 + 2 + len(payload)

    @pytest.mark.unit
    def test_serialize_raw_first_length(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        payload = b"hello"
        raw = fs.serialize_raw(0, ser_mod.FrameType.FIRST, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC, payload)
        # 2 header + 2 frame_size + 4 total_size + payload
        assert len(raw) == 2 + 2 + 4 + len(payload)

    @pytest.mark.unit
    def test_serialize_raw_channel_id(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        raw = fs.serialize_raw(9, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC, b"x")
        assert raw[0] == 9

    @pytest.mark.unit
    def test_serialize_raw_payload_content(self, ser_mod):
        fs = ser_mod.FrameSerializer()
        payload = b"\xCA\xFE"
        raw = fs.serialize_raw(0, ser_mod.FrameType.BULK, ser_mod.EncryptionType.PLAIN, ser_mod.MessageType.SPECIFIC, payload)
        assert raw[4:] == payload


# ===========================================================================
# Section 3 — Messenger
# ===========================================================================

class TestMessengerMessageType:

    @pytest.mark.unit
    def test_channel_0_is_specific(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_message_type(0, 0x0001) == ser_mod.MessageType.SPECIFIC

    @pytest.mark.unit
    def test_channel_open_response_is_control(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_message_type(3, 0x0008) == ser_mod.MessageType.CONTROL

    @pytest.mark.unit
    def test_non_zero_channel_non_open_response_is_specific(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_message_type(3, 0x0001) == ser_mod.MessageType.SPECIFIC

    @pytest.mark.unit
    def test_channel_0_any_msg_id_is_specific(self, ser_mod):
        m = ser_mod.Messenger()
        for msg_id in [0x0001, 0x0002, 0x000b, 0x000c, 0x9999]:
            assert m._get_message_type(0, msg_id) == ser_mod.MessageType.SPECIFIC


class TestMessengerEncryptionType:

    @pytest.mark.unit
    def test_ssl_inactive_always_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x0001, False) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_inactive_non_control_channel_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(3, 0x0010, False) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_msg_0001_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x0001, True) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_msg_0002_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x0002, True) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_msg_0003_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x0003, True) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_msg_0004_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x0004, True) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_msg_000b_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x000b, True) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_msg_000c_plain(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(0, 0x000c, True) == ser_mod.EncryptionType.PLAIN

    @pytest.mark.unit
    def test_ssl_active_ch0_other_msg_encrypted(self, ser_mod):
        m = ser_mod.Messenger()
        # msg_id not in exception list → ENCRYPTED
        assert m._get_encryption_type(0, 0x0010, True) == ser_mod.EncryptionType.ENCRYPTED

    @pytest.mark.unit
    def test_ssl_active_non_control_channel_encrypted(self, ser_mod):
        m = ser_mod.Messenger()
        assert m._get_encryption_type(3, 0x0001, True) == ser_mod.EncryptionType.ENCRYPTED


class TestMessengerSerializeAndLog:

    @pytest.mark.unit
    def test_returns_list_of_bytes(self, ser_mod):
        m = ser_mod.Messenger()
        result = m.serialize_and_log(0, 0x0001, b"\xAB\xCD", ssl_active=False)
        assert isinstance(result, list)
        assert all(isinstance(f, bytes) for f in result)

    @pytest.mark.unit
    def test_small_payload_single_frame(self, ser_mod):
        m = ser_mod.Messenger()
        result = m.serialize_and_log(0, 0x0001, b"\x01\x02", ssl_active=False)
        assert len(result) == 1

    @pytest.mark.unit
    def test_first_byte_is_channel_id(self, ser_mod):
        m = ser_mod.Messenger()
        result = m.serialize_and_log(5, 0x0001, b"\xAA", ssl_active=False)
        assert result[0][0] == 5

    @pytest.mark.unit
    def test_message_id_prepended_to_payload(self, ser_mod):
        m = ser_mod.Messenger()
        # serialize_and_log prepends message_id as 4-hex-chars to payload hex
        result = m.serialize_and_log(0, 0x0003, b"\xAB", ssl_active=False)
        # Frame: [ch_id, flags, size_hi, size_lo, 0x00, 0x03, 0xAB]
        frame_body = result[0][4:]  # skip header(2) + size(2)
        assert frame_body[:2] == bytes([0x00, 0x03])
        assert frame_body[2:] == b"\xAB"

    @pytest.mark.unit
    def test_no_ssl_produces_plain_frame(self, ser_mod):
        m = ser_mod.Messenger()
        result = m.serialize_and_log(3, 0x0001, b"\x00", ssl_active=False)
        flags = result[0][1]
        assert flags & 0x08 == 0x00  # PLAIN

    @pytest.mark.unit
    def test_ssl_active_non_exception_produces_encrypted(self, ser_mod):
        m = ser_mod.Messenger()
        result = m.serialize_and_log(3, 0x0010, b"\x00", ssl_active=True)
        flags = result[0][1]
        assert flags & 0x08 == 0x08  # ENCRYPTED

    @pytest.mark.unit
    def test_ssl_active_exception_msg_plain(self, ser_mod):
        m = ser_mod.Messenger()
        # ch=0, msg=0x0001 → always PLAIN even with ssl_active
        result = m.serialize_and_log(0, 0x0001, b"\x00", ssl_active=True)
        flags = result[0][1]
        assert flags & 0x08 == 0x00  # PLAIN
