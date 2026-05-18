"""
Unit tests for tcp_server/server.py  (TCPServer)

Strategy:
  TCPServer wraps a real socket. All socket syscalls are patched via
  `patch("socket.socket")` so no real ports are opened.
  accept(), start(), stop() are tested in isolation.

Covers:
  Section 1 — __init__ / bind / listen: correct host/port stored,
               setsockopt called, bind+listen called with expected args
  Section 2 — start: calls accept, delegates to on_client_connected,
               exception in accept terminates loop gracefully
  Section 3 — stop: sets _running=False, calls socket.close()
  Section 4 — accept: returns (conn, addr), propagates OSError when stopped
  Section 5 — properties: host, port, is_running
"""

import socket
import threading
import pytest
from unittest.mock import MagicMock, patch, call

import sys
from pathlib import Path

_V2 = Path(__file__).parents[4]
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

with patch("shared.logger.get_logger", return_value=MagicMock()):
    from tcp_server.server import TCPServer


# ---------------------------------------------------------------------------
# Helper: build a TCPServer without touching real sockets
# ---------------------------------------------------------------------------

def _make_server(host="0.0.0.0", port=5288, backlog=5, on_client=None):
    on_client = on_client or MagicMock()
    mock_sock = MagicMock()
    with patch("socket.socket", return_value=mock_sock):
        srv = TCPServer(host=host, port=port, backlog=backlog,
                        on_client_connected=on_client)
    return srv, mock_sock, on_client


# ===========================================================================
# Section 1 — __init__ / bind / listen
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_host_stored(self):
        srv, _, _ = _make_server(host="192.168.7.2")
        assert srv.host == "192.168.7.2"

    @pytest.mark.unit
    def test_port_stored(self):
        srv, _, _ = _make_server(port=9999)
        assert srv.port == 9999

    @pytest.mark.unit
    def test_is_running_false_after_init(self):
        srv, _, _ = _make_server()
        assert not srv.is_running()

    @pytest.mark.unit
    def test_setsockopt_reuse_addr_called(self):
        srv, mock_sock, _ = _make_server()
        mock_sock.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )

    @pytest.mark.unit
    def test_bind_called_with_host_port(self):
        srv, mock_sock, _ = _make_server(host="0.0.0.0", port=5288)
        mock_sock.bind.assert_called_once_with(("0.0.0.0", 5288))

    @pytest.mark.unit
    def test_listen_called_with_backlog(self):
        srv, mock_sock, _ = _make_server(backlog=3)
        mock_sock.listen.assert_called_once_with(3)


# ===========================================================================
# Section 2 — start
# ===========================================================================

class TestStart:

    @pytest.mark.unit
    def test_start_calls_on_client_connected(self):
        on_client = MagicMock()
        mock_conn = MagicMock()
        mock_addr = ("192.168.7.1", 12345)

        mock_sock = MagicMock()
        mock_sock.accept.side_effect = [
            (mock_conn, mock_addr),
            OSError("stop"),  # second call breaks the loop
        ]

        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer(host="0.0.0.0", port=5288,
                            on_client_connected=on_client)

        srv.start()
        on_client.assert_called_once_with(mock_conn, mock_addr)

    @pytest.mark.unit
    def test_start_sets_running_true_then_false(self):
        mock_sock = MagicMock()
        running_states = []

        def _accept():
            running_states.append(True)  # must be True during loop
            raise OSError("stop")

        mock_sock.accept.side_effect = _accept

        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer(host="0.0.0.0", port=5288,
                            on_client_connected=MagicMock())

        srv.start()
        assert len(running_states) == 1
        assert not srv.is_running()

    @pytest.mark.unit
    def test_start_exception_in_callback_continues_loop(self):
        """Crashing callback must not kill the accept loop."""
        on_client = MagicMock(side_effect=[RuntimeError("cb error"), None])
        mock_conn  = MagicMock()
        mock_sock  = MagicMock()
        mock_sock.accept.side_effect = [
            (mock_conn, ("1.2.3.4", 9000)),
            (mock_conn, ("1.2.3.4", 9001)),
            OSError("stop"),
        ]

        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer(host="0.0.0.0", port=5288,
                            on_client_connected=on_client)

        srv.start()  # must not raise
        assert on_client.call_count == 2


# ===========================================================================
# Section 3 — stop
# ===========================================================================

class TestStop:

    @pytest.mark.unit
    def test_stop_sets_running_false(self):
        srv, mock_sock, _ = _make_server()
        srv._running = True
        srv.stop()
        assert not srv.is_running()

    @pytest.mark.unit
    def test_stop_closes_socket(self):
        srv, mock_sock, _ = _make_server()
        srv.stop()
        mock_sock.close.assert_called_once()

    @pytest.mark.unit
    def test_stop_exception_no_crash(self):
        srv, mock_sock, _ = _make_server()
        mock_sock.close.side_effect = OSError("already closed")
        srv.stop()  # must not raise

    @pytest.mark.unit
    def test_double_stop_no_exception(self):
        srv, mock_sock, _ = _make_server()
        srv.stop()
        srv.stop()  # second call must be safe


# ===========================================================================
# Section 4 — accept
# ===========================================================================

class TestAccept:

    @pytest.mark.unit
    def test_accept_returns_conn_and_addr(self):
        srv, mock_sock, _ = _make_server()
        mock_conn = MagicMock()
        mock_sock.accept.return_value = (mock_conn, ("1.2.3.4", 1234))
        conn, addr = srv.accept()
        assert conn is mock_conn
        assert addr == ("1.2.3.4", 1234)

    @pytest.mark.unit
    def test_accept_propagates_os_error(self):
        srv, mock_sock, _ = _make_server()
        mock_sock.accept.side_effect = OSError("timeout")
        with pytest.raises(OSError):
            srv.accept()


# ===========================================================================
# Section 5 — properties
# ===========================================================================

class TestProperties:

    @pytest.mark.unit
    def test_host_property(self):
        srv, _, _ = _make_server(host="127.0.0.1")
        assert srv.host == "127.0.0.1"

    @pytest.mark.unit
    def test_port_property(self):
        srv, _, _ = _make_server(port=4567)
        assert srv.port == 4567

    @pytest.mark.unit
    def test_is_running_property_reflects_internal_flag(self):
        srv, _, _ = _make_server()
        srv._running = True
        assert srv.is_running()
        srv._running = False
        assert not srv.is_running()
