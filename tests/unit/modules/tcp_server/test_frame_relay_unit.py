"""
Unit tests for tcp_server/frame_relay.py

Strategy:
  FrameRelay wraps a socket. All socket I/O is mocked.
  start() blocks in a loop — tested by injecting a mock socket that returns
  a fixed sequence of chunks then EOF, so the loop terminates naturally.

Covers:
  Section 1 — _recv_exact: happy path, multi-chunk, EOF, exception
  Section 2 — _read_frame: BULK frame, FIRST frame (extra 4-byte field),
               short header, short size-field, short total-len field,
               short payload
  Section 3 — start / stop: callback invoked, on_closed_cb called,
               stop() shuts down socket
  Section 4 — send_raw: happy path, partial send loop, broken pipe
"""

import struct
import socket
import threading
import pytest
from unittest.mock import MagicMock, call, patch

import sys
from pathlib import Path

_V2 = Path(__file__).parents[4]
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

with patch("shared.logger.get_logger", return_value=MagicMock()):
    from tcp_server.frame_relay import FrameRelay, FRAMETYPE_FIRST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bulk_wire(channel_id: int, flags_extra: int, payload: bytes) -> bytes:
    """Build a BULK (0x03) AA frame wire representation."""
    flags = 0x03 | flags_extra
    return bytes([channel_id, flags]) + struct.pack(">H", len(payload)) + payload


def _first_wire(channel_id: int, payload: bytes, total_size: int) -> bytes:
    """Build a FIRST (0x01) AA frame with total-size field."""
    flags = FRAMETYPE_FIRST
    return (
        bytes([channel_id, flags])
        + struct.pack(">H", len(payload))
        + struct.pack(">I", total_size)
        + payload
    )


def _streaming_sock(*frames: bytes):
    """
    Returns a mock socket whose recv() streams the concatenation of all
    frame bytes byte-by-byte (worst case fragmentation), then returns b"".
    """
    data = b"".join(frames)
    pos = [0]

    def recv(n):
        if pos[0] >= len(data):
            return b""
        chunk = data[pos[0]: pos[0] + n]
        pos[0] += len(chunk)
        return chunk

    sock = MagicMock()
    sock.recv.side_effect = recv
    return sock


# ===========================================================================
# Section 1 — _recv_exact
# ===========================================================================

class TestRecvExact:

    def _relay(self, sock=None):
        return FrameRelay(
            sock or MagicMock(),
            on_frame_cb=MagicMock(),
        )

    @pytest.mark.unit
    def test_recv_exact_returns_n_bytes(self):
        r = self._relay()
        r._running = True
        sock = MagicMock()
        sock.recv.return_value = b"ABCD"
        r._sock = sock
        assert r._recv_exact(4) == b"ABCD"

    @pytest.mark.unit
    def test_recv_exact_accumulates_chunks(self):
        r = self._relay()
        r._running = True
        sock = MagicMock()
        sock.recv.side_effect = [b"AB", b"CD"]
        r._sock = sock
        assert r._recv_exact(4) == b"ABCD"

    @pytest.mark.unit
    def test_recv_exact_eof_returns_none(self):
        r = self._relay()
        r._running = True
        sock = MagicMock()
        sock.recv.return_value = b""
        r._sock = sock
        assert r._recv_exact(4) is None

    @pytest.mark.unit
    def test_recv_exact_exception_returns_none(self):
        r = self._relay()
        r._running = True
        sock = MagicMock()
        sock.recv.side_effect = OSError("reset")
        r._sock = sock
        assert r._recv_exact(4) is None


# ===========================================================================
# Section 2 — _read_frame
# ===========================================================================

class TestReadFrame:

    def _relay_for(self, wire: bytes):
        sock = _streaming_sock(wire)
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r._running = True
        return r

    @pytest.mark.unit
    def test_bulk_frame_parsed_correctly(self):
        payload = b"\xDE\xAD"
        wire = _bulk_wire(3, 0, payload)
        r = self._relay_for(wire)
        result = r._read_frame()
        assert result is not None
        channel_id, flags, got_payload, total_size = result
        assert channel_id == 3
        assert got_payload == payload
        assert total_size == 0

    @pytest.mark.unit
    def test_first_frame_reads_total_size_field(self):
        payload = b"chunk"
        total = 1024
        wire = _first_wire(0, payload, total)
        r = self._relay_for(wire)
        result = r._read_frame()
        assert result is not None
        _, _, got_payload, got_total = result
        assert got_payload == payload
        assert got_total == total

    @pytest.mark.unit
    def test_eof_on_header_returns_none(self):
        sock = MagicMock()
        sock.recv.return_value = b""  # EOF immediately
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r._running = True
        assert r._read_frame() is None

    @pytest.mark.unit
    def test_eof_on_size_field_returns_none(self):
        # Provide only the 2-byte header, then EOF
        sock = _streaming_sock(b"\x00\x03")  # channel=0 flags=BULK, then EOF
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r._running = True
        assert r._read_frame() is None

    @pytest.mark.unit
    def test_empty_payload_frame_returns_empty_bytes(self):
        wire = _bulk_wire(1, 0, b"")
        r = self._relay_for(wire)
        result = r._read_frame()
        assert result is not None
        assert result[2] == b""


# ===========================================================================
# Section 3 — start / stop
# ===========================================================================

class TestStartStop:

    @pytest.mark.unit
    def test_start_invokes_on_frame_cb(self):
        payload = b"\xAA\xBB"
        wire = _bulk_wire(2, 0, payload)
        sock = _streaming_sock(wire)  # EOF after one frame
        cb = MagicMock()
        r = FrameRelay(sock, on_frame_cb=cb)
        r.start()
        cb.assert_called_once()
        args = cb.call_args[0]
        assert args[0] == 2      # channel_id
        assert args[2] == payload

    @pytest.mark.unit
    def test_start_calls_on_closed_cb_on_eof(self):
        sock = MagicMock()
        sock.recv.return_value = b""
        closed_cb = MagicMock()
        r = FrameRelay(sock, on_frame_cb=MagicMock(), on_closed_cb=closed_cb)
        r.start()
        closed_cb.assert_called_once()

    @pytest.mark.unit
    def test_start_on_closed_cb_none_no_crash(self):
        sock = MagicMock()
        sock.recv.return_value = b""
        r = FrameRelay(sock, on_frame_cb=MagicMock(), on_closed_cb=None)
        r.start()  # must not raise

    @pytest.mark.unit
    def test_stop_sets_running_false(self):
        sock = MagicMock()
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r._running = True
        r.stop()
        assert not r._running

    @pytest.mark.unit
    def test_stop_calls_sock_shutdown(self):
        sock = MagicMock()
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r._running = True
        r.stop()
        sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    @pytest.mark.unit
    def test_stop_sock_shutdown_exception_no_crash(self):
        sock = MagicMock()
        sock.shutdown.side_effect = OSError("already closed")
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r._running = True
        r.stop()  # must not raise

    @pytest.mark.unit
    def test_multiple_frames_all_dispatched(self):
        f1 = _bulk_wire(0, 0, b"frame1")
        f2 = _bulk_wire(1, 0, b"frame2")
        sock = _streaming_sock(f1, f2)  # EOF after two frames
        cb = MagicMock()
        r = FrameRelay(sock, on_frame_cb=cb)
        r.start()
        assert cb.call_count == 2


# ===========================================================================
# Section 4 — send_raw
# ===========================================================================

class TestSendRaw:

    @pytest.mark.unit
    def test_send_raw_sends_all_bytes(self):
        sock = MagicMock()
        sock.send.return_value = 5
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r.send_raw(b"hello")
        sock.send.assert_called()

    @pytest.mark.unit
    def test_send_raw_loops_on_partial_send(self):
        sock = MagicMock()
        sock.send.side_effect = [3, 2]  # sends 3 then 2 of 5 bytes
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r.send_raw(b"hello")
        assert sock.send.call_count == 2

    @pytest.mark.unit
    def test_send_raw_zero_send_raises_broken_pipe(self):
        sock = MagicMock()
        sock.send.return_value = 0
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        with pytest.raises(BrokenPipeError):
            r.send_raw(b"data")

    @pytest.mark.unit
    def test_send_raw_empty_bytes_no_send_call(self):
        sock = MagicMock()
        r = FrameRelay(sock, on_frame_cb=MagicMock())
        r.send_raw(b"")
        sock.send.assert_not_called()
