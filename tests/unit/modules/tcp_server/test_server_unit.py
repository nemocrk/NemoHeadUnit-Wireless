"""
Unit tests for tcp_server/server.py  (TCPServer)

RealAPI (from server.py):
  TCPServer(host, port)
  .start()  -> bool   — crea socket, bind, listen
  .stop()             — chiude socket, _running=False
  .accept(timeout)    -> Optional[Tuple[socket, str]]  — blocca fino a connessione o timeout
  .__enter__ / .__exit__

Strategy:
  patch('socket.socket') nel contesto di start() per evitare bind su porte reali.
  Nessun 'on_client_connected', nessun 'backlog', nessun 'is_running()': non esistono.

Covers:
  Section 1 — __init__: host/port stored, _running=False, _server_sock=None
  Section 2 — start(): socket creato con AF_INET/SOCK_STREAM,
               setsockopt SO_REUSEADDR, bind, listen(1), ritorna True;
               eccezione nel socket -> ritorna False
  Section 3 — stop(): _running=False, close() chiamato, _server_sock=None;
               double-stop safe; close() exception safe
  Section 4 — accept(): ritorna (sock, addr_str) nel happy path;
               ritorna None su socket.timeout;
               ritorna None su eccezione generica;
               ritorna None se not _running;
               ritorna None se _server_sock is None
  Section 5 — context manager: start() e stop() chiamati da __enter__/__exit__
"""

import socket
import pytest
from unittest.mock import MagicMock, patch, call

import sys
from pathlib import Path

_V2 = Path(__file__).parents[4]
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

with patch("shared.logger.get_logger", return_value=MagicMock()):
    from tcp_server.server import TCPServer, DEFAULT_HOST, DEFAULT_PORT, ACCEPT_TIMEOUT


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_mock_sock():
    s = MagicMock(spec=socket.socket)
    return s


# ===========================================================================
# Section 1 — __init__
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_default_host(self):
        srv = TCPServer()
        assert srv.host == DEFAULT_HOST

    @pytest.mark.unit
    def test_default_port(self):
        srv = TCPServer()
        assert srv.port == DEFAULT_PORT

    @pytest.mark.unit
    def test_custom_host_port(self):
        srv = TCPServer(host="192.168.7.2", port=9999)
        assert srv.host == "192.168.7.2"
        assert srv.port == 9999

    @pytest.mark.unit
    def test_running_false_after_init(self):
        srv = TCPServer()
        assert srv._running is False

    @pytest.mark.unit
    def test_server_sock_none_after_init(self):
        srv = TCPServer()
        assert srv._server_sock is None


# ===========================================================================
# Section 2 — start()
# ===========================================================================

class TestStart:

    @pytest.mark.unit
    def test_start_returns_true_on_success(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            result = srv.start()
        assert result is True

    @pytest.mark.unit
    def test_start_sets_running_true(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        assert srv._running is True

    @pytest.mark.unit
    def test_start_sets_server_sock(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        assert srv._server_sock is mock_sock

    @pytest.mark.unit
    def test_start_calls_setsockopt_reuse_addr(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        mock_sock.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )

    @pytest.mark.unit
    def test_start_calls_bind_with_host_port(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer(host="0.0.0.0", port=5288)
            srv.start()
        mock_sock.bind.assert_called_once_with(("0.0.0.0", 5288))

    @pytest.mark.unit
    def test_start_calls_listen_1(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        mock_sock.listen.assert_called_once_with(1)

    @pytest.mark.unit
    def test_start_returns_false_on_bind_exception(self):
        mock_sock = _make_mock_sock()
        mock_sock.bind.side_effect = OSError("address in use")
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            result = srv.start()
        assert result is False

    @pytest.mark.unit
    def test_start_running_stays_false_on_exception(self):
        mock_sock = _make_mock_sock()
        mock_sock.bind.side_effect = OSError("address in use")
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        assert srv._running is False


# ===========================================================================
# Section 3 — stop()
# ===========================================================================

class TestStop:

    def _started_server(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        return srv, mock_sock

    @pytest.mark.unit
    def test_stop_sets_running_false(self):
        srv, _ = self._started_server()
        srv.stop()
        assert srv._running is False

    @pytest.mark.unit
    def test_stop_closes_socket(self):
        srv, mock_sock = self._started_server()
        srv.stop()
        mock_sock.close.assert_called_once()

    @pytest.mark.unit
    def test_stop_clears_server_sock(self):
        srv, _ = self._started_server()
        srv.stop()
        assert srv._server_sock is None

    @pytest.mark.unit
    def test_stop_close_exception_no_crash(self):
        srv, mock_sock = self._started_server()
        mock_sock.close.side_effect = OSError("already closed")
        srv.stop()  # must not raise

    @pytest.mark.unit
    def test_double_stop_no_exception(self):
        srv, _ = self._started_server()
        srv.stop()
        srv.stop()  # _server_sock is None, must not raise

    @pytest.mark.unit
    def test_stop_on_unstarted_server_no_exception(self):
        srv = TCPServer()  # never started
        srv.stop()  # must not raise


# ===========================================================================
# Section 4 — accept()
# ===========================================================================

class TestAccept:

    def _started_server(self, sock_override=None):
        mock_sock = sock_override or _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            srv.start()
        return srv, mock_sock

    @pytest.mark.unit
    def test_accept_returns_conn_and_addr_string(self):
        mock_sock = _make_mock_sock()
        mock_conn = MagicMock()
        mock_sock.accept.return_value = (mock_conn, ("192.168.7.1", 12345))
        srv, _ = self._started_server(mock_sock)
        result = srv.accept()
        assert result is not None
        conn, addr_str = result
        assert conn is mock_conn
        assert addr_str == "192.168.7.1:12345"

    @pytest.mark.unit
    def test_accept_sets_timeout_on_socket(self):
        mock_sock = _make_mock_sock()
        mock_conn = MagicMock()
        mock_sock.accept.return_value = (mock_conn, ("1.2.3.4", 9000))
        srv, _ = self._started_server(mock_sock)
        srv.accept(timeout=30)
        mock_sock.settimeout.assert_called_with(30)

    @pytest.mark.unit
    def test_accept_uses_default_timeout(self):
        mock_sock = _make_mock_sock()
        mock_conn = MagicMock()
        mock_sock.accept.return_value = (mock_conn, ("1.2.3.4", 9000))
        srv, _ = self._started_server(mock_sock)
        srv.accept()
        mock_sock.settimeout.assert_called_with(ACCEPT_TIMEOUT)

    @pytest.mark.unit
    def test_accept_returns_none_on_timeout(self):
        mock_sock = _make_mock_sock()
        mock_sock.accept.side_effect = socket.timeout()
        srv, _ = self._started_server(mock_sock)
        assert srv.accept() is None

    @pytest.mark.unit
    def test_accept_returns_none_on_os_error(self):
        mock_sock = _make_mock_sock()
        mock_sock.accept.side_effect = OSError("reset")
        srv, _ = self._started_server(mock_sock)
        assert srv.accept() is None

    @pytest.mark.unit
    def test_accept_returns_none_if_not_running(self):
        srv = TCPServer()
        # _running=False, _server_sock=None
        assert srv.accept() is None

    @pytest.mark.unit
    def test_accept_returns_none_if_server_sock_none(self):
        srv = TCPServer()
        srv._running = True
        srv._server_sock = None
        assert srv.accept() is None


# ===========================================================================
# Section 5 — Context manager
# ===========================================================================

class TestContextManager:

    @pytest.mark.unit
    def test_enter_calls_start(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            srv = TCPServer()
            result = srv.__enter__()
        assert result is srv
        assert srv._running is True

    @pytest.mark.unit
    def test_exit_calls_stop(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            with TCPServer() as srv:
                sock_at_start = srv._server_sock
        assert srv._running is False
        assert srv._server_sock is None

    @pytest.mark.unit
    def test_with_block_start_stop_lifecycle(self):
        mock_sock = _make_mock_sock()
        with patch("socket.socket", return_value=mock_sock):
            with TCPServer() as srv:
                assert srv._running is True
        assert srv._running is False
