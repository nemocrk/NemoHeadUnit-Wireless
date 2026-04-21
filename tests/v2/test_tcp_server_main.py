"""
Unit tests for v2/modules/tcp_server/main.py

Tested behaviours:
  - on_handshake_completed: starts _start_server in background thread
  - _start_server: publishes tcp.server.error when TCPServer.start() fails
  - _start_server: publishes tcp.server.started with host/port on success
  - _start_server: publishes tcp.server.error when accept() returns None
  - _start_server: publishes tcp.session.connected on accepted connection
  - _start_server: starts FrameRelay after accept
  - _on_frame: publishes aa.frame.received with hex payload
  - _on_session_closed: publishes tcp.session.closed and tears down
  - on_system_stop: stops relay and server, stops bus
  - _teardown: safe when relay and server are None
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_TESTS   = Path(__file__).parent
_ROOT    = _TESTS.parent.parent
_V2      = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in (str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Stub shared dependencies
# ---------------------------------------------------------------------------

_bus_instance = MagicMock()
_bus_class    = MagicMock(return_value=_bus_instance)

_shared_pkg     = types.ModuleType("shared")
_bus_client_mod = types.ModuleType("shared.bus_client")
_logger_mod     = types.ModuleType("shared.logger")

_bus_client_mod.BusClient = _bus_class
_logger_mod.get_logger    = MagicMock(return_value=MagicMock())

sys.modules.setdefault("shared",             _shared_pkg)
sys.modules["shared.bus_client"] = _bus_client_mod
sys.modules["shared.logger"]     = _logger_mod

# Stub tcp_server sub-helpers
_tcp_pkg       = types.ModuleType("tcp_server")
_server_mod    = types.ModuleType("tcp_server.server")
_relay_mod     = types.ModuleType("tcp_server.frame_relay")

_MockTCPServer  = MagicMock()
_MockFrameRelay = MagicMock()

_server_mod.TCPServer   = _MockTCPServer
_relay_mod.FrameRelay   = _MockFrameRelay

sys.modules.setdefault("tcp_server",              _tcp_pkg)
sys.modules["tcp_server.server"]      = _server_mod
sys.modules["tcp_server.frame_relay"] = _relay_mod

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------

import v2.modules.tcp_server.main as tcp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_module_state():
    tcp._server = None
    tcp._relay  = None
    tcp.bus.publish.reset_mock()
    tcp.bus.stop.reset_mock()
    _MockTCPServer.reset_mock()
    _MockFrameRelay.reset_mock()


def _published(topic):
    return [
        args[1]
        for args, _ in tcp.bus.publish.call_args_list
        if args[0] == topic
    ]


def _make_server(start_ok=True, accept_result=(MagicMock(), "192.168.50.100")):
    s = MagicMock()
    s.start.return_value  = start_ok
    s.accept.return_value = accept_result
    s.host = "0.0.0.0"
    s.port = 5288
    return s


# ---------------------------------------------------------------------------
# Tests — on_handshake_completed
# ---------------------------------------------------------------------------

class TestHandshakeCompleted:
    def test_starts_background_thread(self):
        mock_thread = MagicMock()
        with patch("threading.Thread", return_value=mock_thread):
            tcp.on_handshake_completed(
                "rfcomm.handshake.completed",
                {"device_address": "AA:BB", "phone_ip": "192.168.50.100"},
            )
            mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — _start_server
# ---------------------------------------------------------------------------

class TestStartServer:
    def test_server_start_failure_publishes_error(self):
        _MockTCPServer.return_value = _make_server(start_ok=False)
        tcp._start_server()
        assert len(_published("tcp.server.error")) == 1

    def test_server_started_publishes_host_and_port(self):
        server = _make_server()
        relay  = MagicMock()
        _MockTCPServer.return_value  = server
        _MockFrameRelay.return_value = relay

        tcp._start_server()

        started = _published("tcp.server.started")
        assert len(started) == 1
        assert started[0]["host"] == "0.0.0.0"
        assert started[0]["port"] == 5288

    def test_accept_timeout_publishes_error(self):
        server = _make_server(accept_result=None)
        _MockTCPServer.return_value = server
        tcp._start_server()
        assert len(_published("tcp.server.error")) == 1

    def test_session_connected_published_after_accept(self):
        conn   = MagicMock()
        server = _make_server(accept_result=(conn, "192.168.50.100"))
        relay  = MagicMock()
        _MockTCPServer.return_value  = server
        _MockFrameRelay.return_value = relay

        tcp._start_server()

        connected = _published("tcp.session.connected")
        assert len(connected) == 1
        assert connected[0]["address"] == "192.168.50.100"

    def test_frame_relay_started_after_accept(self):
        conn   = MagicMock()
        server = _make_server(accept_result=(conn, "192.168.50.100"))
        relay  = MagicMock()
        _MockTCPServer.return_value  = server
        _MockFrameRelay.return_value = relay

        tcp._start_server()

        relay.start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — _on_frame
# ---------------------------------------------------------------------------

class TestOnFrame:
    def test_publishes_aa_frame_with_hex_payload(self):
        tcp._on_frame(channel_id=1, flags=0, payload=b"\xde\xad\xbe\xef")
        frames = _published("aa.frame.received")
        assert len(frames) == 1
        assert frames[0]["channel_id"]  == 1
        assert frames[0]["flags"]       == 0
        assert frames[0]["payload_hex"] == "deadbeef"

    def test_empty_payload_publishes_empty_hex(self):
        tcp._on_frame(channel_id=0, flags=0, payload=b"")
        frames = _published("aa.frame.received")
        assert frames[0]["payload_hex"] == ""


# ---------------------------------------------------------------------------
# Tests — _on_session_closed
# ---------------------------------------------------------------------------

class TestOnSessionClosed:
    def test_publishes_tcp_session_closed(self):
        tcp._on_session_closed()
        assert len(_published("tcp.session.closed")) == 1

    def test_tears_down_relay_and_server(self):
        tcp._relay  = MagicMock()
        tcp._server = MagicMock()
        tcp._on_session_closed()
        tcp._relay.stop.assert_called_once()
        tcp._server.stop.assert_called_once()
        assert tcp._relay  is None
        assert tcp._server is None


# ---------------------------------------------------------------------------
# Tests — on_system_stop
# ---------------------------------------------------------------------------

class TestSystemStop:
    def test_stops_relay_and_server(self):
        tcp._relay  = MagicMock()
        tcp._server = MagicMock()
        tcp.on_system_stop("system.stop", {})
        tcp._relay.stop.assert_called_once()
        tcp._server.stop.assert_called_once()
        tcp.bus.stop.assert_called_once()

    def test_safe_when_nothing_running(self):
        tcp.on_system_stop("system.stop", {})
        tcp.bus.stop.assert_called_once()

    def test_publishes_tcp_session_closed_on_stop(self):
        tcp._relay  = MagicMock()
        tcp._server = MagicMock()
        tcp.on_system_stop("system.stop", {})
        assert len(_published("tcp.session.closed")) == 1
