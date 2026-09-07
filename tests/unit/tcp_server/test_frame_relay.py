import socket
import struct
import pytest
from unittest.mock import MagicMock
from modules.tcp_server.frame_relay import FrameRelay, FRAMETYPE_FIRST

pytestmark = pytest.mark.unit


def test_frame_relay_read_bulk_frame():
    channel_id = 0
    flags = 0x03  # BULK
    payload = b"\x00\x01ping"
    header = struct.pack(">BBH", channel_id, flags, len(payload))
    wire_bytes = header + payload

    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [wire_bytes[:2], wire_bytes[2:4], wire_bytes[4:], b""]

    frames_received = []
    closed_called = []

    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda ch, fl, pay, tot: frames_received.append((ch, fl, pay, tot)),
        on_closed_cb=lambda: closed_called.append(True),
    )

    relay.start()

    assert len(frames_received) == 1
    assert frames_received[0] == (channel_id, flags, payload, 0)
    assert len(closed_called) == 1


def test_frame_relay_read_first_frame():
    channel_id = 1
    flags = FRAMETYPE_FIRST  # 0x01
    payload = b"first_chunk"
    total_size = 5000
    # FIRST header: [ch: 1B][flags: 1B][this_len: 2B BE][total_len: 4B BE]
    wire_bytes = struct.pack(">BBHI", channel_id, flags, len(payload), total_size) + payload

    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [
        wire_bytes[:2],
        wire_bytes[2:4],
        wire_bytes[4:8],
        wire_bytes[8:],
        b"",
    ]

    frames_received = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda ch, fl, pay, tot: frames_received.append((ch, fl, pay, tot)),
    )

    relay.start()

    assert len(frames_received) == 1
    ch, fl, pay, tot = frames_received[0]
    assert ch == channel_id
    assert fl == flags
    assert pay == payload
    assert tot == total_size


def test_frame_relay_socket_exception_and_stop():
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = OSError("Socket read fault")

    closed_called = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=MagicMock(),
        on_closed_cb=lambda: closed_called.append(True),
    )

    relay.start()
    assert len(closed_called) == 1

    relay.stop()
    mock_sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)


def test_frame_relay_send_raw():
    mock_sock = MagicMock()
    # Simulate partial write: 5 bytes then remaining 5 bytes
    mock_sock.send.side_effect = [5, 5]

    relay = FrameRelay(sock=mock_sock, on_frame_cb=MagicMock())
    data = b"0123456789"
    relay.send_raw(data)

    assert mock_sock.send.call_count == 2

    # Verify BrokenPipeError on zero-byte return
    mock_sock.send.side_effect = [0]
    with pytest.raises(BrokenPipeError):
        relay.send_raw(b"fail")


def _streaming_sock(*chunks: bytes):
    data = b"".join(chunks)
    pos = 0

    def recv(n):
        nonlocal pos
        if pos >= len(data):
            return b""
        chunk = data[pos : pos + n]
        pos += len(chunk)
        return chunk

    sock = MagicMock()
    sock.recv.side_effect = recv
    return sock


def test_frame_relay_read_middle_and_last_frames():
    # Middle frame (0x00) and Last frame (0x02) should have total_size == 0
    f_middle = struct.pack(">BBH", 2, 0x00, 4) + b"mid1"
    f_last = struct.pack(">BBH", 2, 0x02, 4) + b"last"

    mock_sock = _streaming_sock(f_middle, f_last)

    dispatched = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda ch, fl, pay, tot: dispatched.append((ch, fl, pay, tot)),
    )
    relay.start()

    assert len(dispatched) == 2
    assert dispatched[0] == (2, 0x00, b"mid1", 0)
    assert dispatched[1] == (2, 0x02, b"last", 0)


def test_frame_relay_recv_exact_chunked_accumulation():
    mock_sock = MagicMock()
    # 4 bytes delivered as 1 byte chunks
    mock_sock.recv.side_effect = [b"A", b"B", b"C", b"D"]

    relay = FrameRelay(sock=mock_sock, on_frame_cb=MagicMock())
    relay._running = True

    result = relay._recv_exact(4)
    assert result == b"ABCD"
    assert mock_sock.recv.call_count == 4


def test_frame_relay_short_header_eof():
    # Only 1 byte returned before EOF
    mock_sock = _streaming_sock(b"\x00")

    frames = []
    closed = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda *args: frames.append(args),
        on_closed_cb=lambda: closed.append(True),
    )
    relay.start()

    assert len(frames) == 0
    assert len(closed) == 1


def test_frame_relay_short_size_field_eof():
    # 2 bytes header, then 1 byte of size field before EOF
    mock_sock = _streaming_sock(b"\x01\x03\x00")

    frames = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda *args: frames.append(args),
    )
    relay.start()

    assert len(frames) == 0


def test_frame_relay_short_total_size_field_eof():
    # FIRST frame header + size field (2B payload len), then only 2 of 4 bytes total_size before EOF
    partial_first = struct.pack(">BBH", 1, FRAMETYPE_FIRST, 10) + b"\x00\x00"
    mock_sock = _streaming_sock(partial_first)

    frames = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda *args: frames.append(args),
    )
    relay.start()

    assert len(frames) == 0


def test_frame_relay_short_payload_eof():
    # Header claims 10 bytes payload, but socket returns only 4 bytes then EOF
    wire = struct.pack(">BBH", 1, 0x03, 10) + b"1234"
    mock_sock = _streaming_sock(wire)

    frames = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda *args: frames.append(args),
    )
    relay.start()

    assert len(frames) == 0


def test_frame_relay_empty_payload():
    wire = struct.pack(">BBH", 3, 0x03, 0)
    mock_sock = _streaming_sock(wire)

    dispatched = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda ch, fl, pay, tot: dispatched.append((ch, fl, pay, tot)),
    )
    relay.start()

    assert len(dispatched) == 1
    assert dispatched[0] == (3, 0x03, b"", 0)


def test_frame_relay_stop_swallows_shutdown_exception():
    mock_sock = MagicMock()
    mock_sock.shutdown.side_effect = OSError("Socket already closed")

    relay = FrameRelay(sock=mock_sock, on_frame_cb=MagicMock())
    relay._running = True
    relay.stop()

    assert not relay._running
    mock_sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)


def test_frame_relay_send_raw_empty():
    mock_sock = MagicMock()
    relay = FrameRelay(sock=mock_sock, on_frame_cb=MagicMock())
    relay.send_raw(b"")

    mock_sock.send.assert_not_called()


def test_frame_relay_on_closed_cb_none():
    mock_sock = MagicMock()
    mock_sock.recv.return_value = b""

    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=MagicMock(),
        on_closed_cb=None,
    )
    # Should not raise TypeError when on_closed_cb is None
    relay.start()
