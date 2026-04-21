"""
test_tcp_server.py — Unit tests for tcp_server/server.py and tcp_server/frame_relay.py

No hardware, no ZMQ required.
"""

import socket
import struct
import threading
import time
import pytest

from tcp_server.server import TCPServer
from tcp_server.frame_relay import FrameRelay, FRAME_HEADER_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_aa_frame(channel_id: int, flags: int, payload: bytes) -> bytes:
    """Build a raw AA frame as the phone would send it."""
    header = struct.pack(">I", len(payload)) + bytes([channel_id, flags])
    return header + payload


# ---------------------------------------------------------------------------
# TCPServer tests
# ---------------------------------------------------------------------------

class TestTCPServer:
    def test_start_binds_port(self):
        srv = TCPServer(port=0)  # OS picks a free port
        assert srv.start() is True
        assert srv._server_sock is not None
        srv.stop()

    def test_stop_cleans_up(self):
        srv = TCPServer(port=0)
        srv.start()
        srv.stop()
        assert srv._server_sock is None
        assert srv._running is False

    def test_accept_receives_connection(self):
        srv = TCPServer(host="127.0.0.1", port=0)
        srv.start()
        actual_port = srv._server_sock.getsockname()[1]

        # Connect from another thread
        def connect():
            c = socket.create_connection(("127.0.0.1", actual_port))
            time.sleep(0.1)
            c.close()

        threading.Thread(target=connect, daemon=True).start()
        result = srv.accept(timeout=3)
        srv.stop()

        assert result is not None
        conn, addr = result
        assert "127.0.0.1" in addr
        conn.close()

    def test_accept_timeout_returns_none(self):
        srv = TCPServer(host="127.0.0.1", port=0)
        srv.start()
        result = srv.accept(timeout=1)  # nobody connects
        srv.stop()
        assert result is None

    def test_context_manager(self):
        with TCPServer(port=0) as srv:
            assert srv._running is True
        assert srv._running is False


# ---------------------------------------------------------------------------
# FrameRelay tests
# ---------------------------------------------------------------------------

class TestFrameRelay:
    def test_single_frame_received(self):
        a, b = socket.socketpair()
        frames = []

        relay = FrameRelay(
            sock=a,
            on_frame_cb=lambda ch, fl, pl: frames.append((ch, fl, pl)),
        )

        payload = b"hello AA"
        b.sendall(_make_aa_frame(channel_id=1, flags=0x0B, payload=payload))
        b.close()  # trigger socket close after frame

        relay.start()  # blocking; returns when b is closed
        a.close()

        assert len(frames) == 1
        assert frames[0] == (1, 0x0B, payload)

    def test_multiple_frames_received(self):
        a, b = socket.socketpair()
        frames = []

        relay = FrameRelay(
            sock=a,
            on_frame_cb=lambda ch, fl, pl: frames.append((ch, fl, pl)),
        )

        for i in range(5):
            b.sendall(_make_aa_frame(channel_id=i, flags=0x00, payload=bytes([i] * 4)))
        b.close()

        relay.start()
        a.close()

        assert len(frames) == 5
        for i, (ch, fl, pl) in enumerate(frames):
            assert ch == i
            assert pl == bytes([i] * 4)

    def test_on_closed_callback_called(self):
        a, b = socket.socketpair()
        closed = []

        relay = FrameRelay(
            sock=a,
            on_frame_cb=lambda *_: None,
            on_closed_cb=lambda: closed.append(True),
        )
        b.close()  # immediately close
        relay.start()
        a.close()

        assert closed == [True]

    def test_empty_payload_frame(self):
        a, b = socket.socketpair()
        frames = []

        relay = FrameRelay(
            sock=a,
            on_frame_cb=lambda ch, fl, pl: frames.append((ch, fl, pl)),
        )
        b.sendall(_make_aa_frame(channel_id=0, flags=0x00, payload=b""))
        b.close()
        relay.start()
        a.close()

        assert len(frames) == 1
        assert frames[0][2] == b""

    def test_stop_aborts_relay(self):
        a, b = socket.socketpair()
        relay = FrameRelay(sock=a, on_frame_cb=lambda *_: None)

        t = threading.Thread(target=relay.start, daemon=True)
        t.start()
        time.sleep(0.05)
        relay.stop()          # signal abort
        t.join(timeout=2)

        assert not relay._running
        a.close()
        b.close()
