import struct
import pytest
from unittest.mock import MagicMock
from modules.tcp_server.frame_codec import (
    encode,
    FrameAssembler,
    FRAME_SIZE_THRESHOLD,
    _FT_BULK,
    _FT_FIRST,
    _FT_MIDDLE,
    _FT_LAST,
    _MT_SPECIFIC,
    _MT_CONTROL,
    _ET_PLAIN,
    _ET_ENCRYPTED,
    _MSG_CHANNEL_OPEN_RESPONSE,
)

pytestmark = pytest.mark.unit


def test_frame_codec_encode_bulk_plain():
    channel_id = 0
    message_id = 0x0001
    body = b"hello_aa"

    frames = encode(channel_id, message_id, body, ssl_active=False)
    assert len(frames) == 1
    raw = frames[0]

    # Header: [ch: 1B][flags: 1B][len: 2B BE] + payload
    ch, flags, payload_len = struct.unpack_from(">BBH", raw, 0)
    assert ch == channel_id
    assert flags & 0x03 == _FT_BULK
    assert flags & 0x04 == _MT_SPECIFIC
    assert flags & 0x08 == _ET_PLAIN
    assert payload_len == 2 + len(body)
    msg_id = struct.unpack_from(">H", raw, 4)[0]
    assert msg_id == message_id
    assert raw[6:] == body


def test_frame_codec_encode_control_message_type():
    # Only CHANNEL_OPEN_RESPONSE on channel != 0 uses CONTROL message type
    frames_ctl = encode(1, _MSG_CHANNEL_OPEN_RESPONSE, b"ok", ssl_active=False)
    flags_ctl = frames_ctl[0][1]
    assert flags_ctl & 0x04 == _MT_CONTROL

    # CHANNEL_OPEN_RESPONSE on channel 0 stays SPECIFIC
    frames_ch0 = encode(0, _MSG_CHANNEL_OPEN_RESPONSE, b"ok", ssl_active=False)
    flags_ch0 = frames_ch0[0][1]
    assert flags_ch0 & 0x04 == _MT_SPECIFIC

    # Other messages on channel 1 stay SPECIFIC
    frames_other = encode(1, 0x8001, b"other", ssl_active=False)
    flags_other = frames_other[0][1]
    assert flags_other & 0x04 == _MT_SPECIFIC


def test_frame_codec_encode_encryption_active_and_fallback():
    mock_cryptor = MagicMock()
    mock_cryptor.is_active.return_value = True
    mock_cryptor.encrypt_records.return_value = [b"encrypted_cipher"]

    # When ssl_active is True and cryptor is active, uses _ET_ENCRYPTED
    frames = encode(1, 0x8001, b"plain_body", ssl_active=True, cryptor=mock_cryptor)
    assert len(frames) == 1
    flags = frames[0][1]
    assert flags & 0x08 == _ET_ENCRYPTED
    mock_cryptor.encrypt_records.assert_called_once()

    # Fallback to plain if cryptor is None or not active
    frames_fallback = encode(1, 0x8001, b"plain_body", ssl_active=True, cryptor=None)
    flags_fb = frames_fallback[0][1]
    assert flags_fb & 0x08 == _ET_PLAIN


def test_frame_codec_encode_fragmentation():
    channel_id = 3
    message_id = 0x8001
    large_body = b"X" * (FRAME_SIZE_THRESHOLD * 2 + 500)

    frames = encode(channel_id, message_id, large_body, ssl_active=False)
    assert len(frames) == 3  # FIRST + MIDDLE + LAST

    # 1. FIRST frame has 8-byte header: [ch: 1B][flags: 1B][len: 2B BE][total_size: 4B BE]
    f1 = frames[0]
    ch1, flags1, len1 = struct.unpack_from(">BBH", f1, 0)
    assert ch1 == channel_id
    assert flags1 & 0x03 == _FT_FIRST
    assert len1 == FRAME_SIZE_THRESHOLD
    total_size = struct.unpack_from(">I", f1, 4)[0]
    assert total_size == 2 + len(large_body)

    # 2. MIDDLE frame has 4-byte header
    f2 = frames[1]
    ch2, flags2, len2 = struct.unpack_from(">BBH", f2, 0)
    assert ch2 == channel_id
    assert flags2 & 0x03 == _FT_MIDDLE
    assert len2 == FRAME_SIZE_THRESHOLD

    # 3. LAST frame has 4-byte header
    f3 = frames[2]
    ch3, flags3, len3 = struct.unpack_from(">BBH", f3, 0)
    assert ch3 == channel_id
    assert flags3 & 0x03 == _FT_LAST
    assert len3 == (2 + len(large_body)) - (FRAME_SIZE_THRESHOLD * 2)


def test_frame_assembler_bulk():
    assembler = FrameAssembler()
    payload = b"\x00\x01test"
    res = assembler.feed(channel_id=0, flags=_FT_BULK, payload=payload, total_size=len(payload))
    assert res == (0, _FT_BULK, payload, len(payload))


def test_frame_assembler_multi_frame_flow():
    assembler = FrameAssembler()
    chunk1 = b"chunk_1"
    chunk2 = b"chunk_2"
    chunk3 = b"chunk_3"
    total_len = len(chunk1) + len(chunk2) + len(chunk3)

    # Feed FIRST
    r1 = assembler.feed(1, _FT_FIRST | _MT_SPECIFIC, chunk1, total_size=total_len)
    assert r1 is None

    # Feed MIDDLE
    r2 = assembler.feed(1, _FT_MIDDLE | _MT_SPECIFIC, chunk2)
    assert r2 is None

    # Feed LAST
    r3 = assembler.feed(1, _FT_LAST | _MT_SPECIFIC, chunk3)
    assert r3 is not None
    ch, out_flags, full_data, declared_total = r3
    assert ch == 1
    assert out_flags & 0x03 == _FT_BULK
    assert full_data == chunk1 + chunk2 + chunk3
    assert declared_total == total_len


def test_frame_assembler_orphan_middle_and_reset():
    assembler = FrameAssembler()
    # Feeding MIDDLE without FIRST drops and returns None
    assert assembler.feed(2, _FT_MIDDLE, b"orphan") is None

    # Feeding FIRST then resetting clears state
    assembler.feed(2, _FT_FIRST, b"first_chunk", total_size=100)
    assert 2 in assembler._buffers
    assembler.reset(channel_id=2)
    assert 2 not in assembler._buffers
