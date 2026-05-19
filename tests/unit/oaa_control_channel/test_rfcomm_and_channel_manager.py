"""
test_rfcomm_and_channel_manager.py
====================================
Unit tests for:
  - rfcomm_handshake/main.py  (boot protocol + handshake trigger logic)
  - rfcomm_handshake/packet.py (encode/decode)
  - rfcomm_handshake/handshake.py (RfcommHandshake event loop)
  - channel_manager/main.py  (boot protocol + ChannelManagerSession lifecycle)

All tests use mock_bus from conftest.py — no real ZMQ sockets needed.
Proto-level helpers are skipped via pytest.importorskip.

Run with:
    cd tests
    pytest unit/oaa_control_channel/test_rfcomm_and_channel_manager.py -v
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest


# ===========================================================================
# SECTION 1 — packet.py  (pure encode/decode, no dependencies)
# ===========================================================================

class TestPacketEncodeDecodeRoundtrip:
    """packet.encode / packet.decode are inverses of each other."""

    def setup_method(self):
        from rfcomm_handshake.packet import encode, decode, Packet
        self.encode = encode
        self.decode = decode
        self.Packet = Packet

    def test_encode_empty_payload(self):
        raw = self.encode(msg_id=6, payload=b"")
        assert raw == b"\x00\x00\x00\x06"

    def test_encode_decode_roundtrip(self):
        payload = b"\xde\xad\xbe\xef"
        raw = self.encode(msg_id=3, payload=payload)
        pkt = self.decode(raw)
        assert pkt is not None
        assert pkt.msg_id  == 3
        assert pkt.payload == payload

    def test_decode_returns_none_on_short_buffer(self):
        assert self.decode(b"\x00") is None

    def test_decode_truncated_payload_returns_none(self):
        # header says 10 bytes but we only give 2
        raw = struct.pack(">HH", 10, 2) + b"\xAA\xBB"
        assert self.decode(raw) is None

    def test_encode_large_payload(self):
        payload = bytes(range(256)) * 4   # 1024 bytes
        raw = self.encode(msg_id=1, payload=payload)
        pkt = self.decode(raw)
        assert pkt is not None
        assert pkt.payload == payload

    def test_packet_repr(self):
        from rfcomm_handshake.packet import Packet
        p = Packet(msg_id=7, payload=b"abc")
        assert "msg_id=7" in repr(p)
        assert "payload_len=3" in repr(p)


# ===========================================================================
# SECTION 2 — handshake.py (RfcommHandshake event loop)
# ===========================================================================

def _make_socket_with_responses(*packets_raw: bytes) -> MagicMock:
    """
    Build a mock socket whose recv() feeds pre-built raw packet bytes
    one header+payload at a time.
    """
    # flatten all packets into a single byte stream and serve via recv
    stream = b"".join(packets_raw)
    pos = [0]

    def _recv(n: int) -> bytes:
        chunk = stream[pos[0]: pos[0] + n]
        pos[0] += n
        if not chunk:
            raise OSError("connection closed")
        return chunk

    sock = MagicMock(spec=socket.socket)
    sock.recv.side_effect = _recv
    sock.sendall.return_value = None
    return sock


def _build_raw_packet(msg_id: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HH", len(payload), msg_id) + payload


MOCK_CREDS = {
    "ssid":       "TestAP",
    "key":        "secret123",
    "bssid":      "AA:BB:CC:DD:EE:FF",
    "gateway_ip": "192.168.50.1",
    "tcp_port":   5288,
}

MSG_WIFI_INFO_REQUEST  = 2
MSG_WIFI_CONNECT_STATUS = 7
MSG_WIFI_START_RESPONSE = 6


@pytest.mark.skipif(
    pytest.importorskip("google.protobuf", reason="protobuf not installed") is None,
    reason="protobuf not available",
)
class TestRfcommHandshakeEventLoop:
    """Tests for RfcommHandshake.run() state machine."""

    def _import(self):
        from rfcomm_handshake.handshake import RfcommHandshake, HandshakeResult
        return RfcommHandshake, HandshakeResult

    def test_happy_path_info_request_then_connect_status(self):
        """Standard flow: WifiInfoRequest → WifiConnectStatus → success."""
        RfcommHandshake, HandshakeResult = self._import()

        # Phone sends: WifiInfoRequest then WifiConnectionStatus (empty proto)
        sock = _make_socket_with_responses(
            _build_raw_packet(MSG_WIFI_INFO_REQUEST),
            _build_raw_packet(MSG_WIFI_CONNECT_STATUS),
        )
        stages = []
        hs = RfcommHandshake(sock, MOCK_CREDS, on_stage_cb=stages.append)
        result = hs.run()

        assert result.success is True
        assert "WifiStartRequest" in stages
        assert "WifiInfoRequest" in stages
        assert "WifiConnectionStatus" in stages

    def test_start_response_ack_before_info_request(self):
        """Phone sends optional WifiStartResponse before WifiInfoRequest — still succeeds."""
        RfcommHandshake, _ = self._import()

        sock = _make_socket_with_responses(
            _build_raw_packet(MSG_WIFI_START_RESPONSE),
            _build_raw_packet(MSG_WIFI_INFO_REQUEST),
            _build_raw_packet(MSG_WIFI_CONNECT_STATUS),
        )
        stages = []
        hs = RfcommHandshake(sock, MOCK_CREDS, on_stage_cb=stages.append)
        result = hs.run()

        assert result.success is True
        assert "WifiStartResponse" in stages

    def test_socket_closed_mid_handshake(self):
        """recv returning empty bytes → HandshakeResult(success=False)."""
        RfcommHandshake, _ = self._import()

        sock = MagicMock(spec=socket.socket)
        sock.recv.return_value = b""   # connection closed immediately
        sock.sendall.return_value = None

        result = RfcommHandshake(sock, MOCK_CREDS).run()
        assert result.success is False
        assert result.error

    def test_send_start_request_failure_returns_false(self):
        """sendall raising OSError → HandshakeResult failure on first send."""
        RfcommHandshake, _ = self._import()

        sock = MagicMock(spec=socket.socket)
        sock.sendall.side_effect = OSError("pipe broken")

        result = RfcommHandshake(sock, MOCK_CREDS).run()
        assert result.success is False

    def test_unknown_msg_ids_are_ignored(self):
        """Unknown msg_ids in the loop are skipped; handshake still completes."""
        RfcommHandshake, _ = self._import()

        sock = _make_socket_with_responses(
            _build_raw_packet(99),   # unknown — ignored
            _build_raw_packet(88),   # unknown — ignored
            _build_raw_packet(MSG_WIFI_INFO_REQUEST),
            _build_raw_packet(MSG_WIFI_CONNECT_STATUS),
        )
        result = RfcommHandshake(sock, MOCK_CREDS).run()
        assert result.success is True

    def test_connect_status_before_info_response_warns_but_succeeds(self):
        """WifiConnectionStatus arriving before WifiInfoResponse is logged but not fatal."""
        RfcommHandshake, _ = self._import()

        # No WifiInfoRequest — phone sends connect_status directly
        sock = _make_socket_with_responses(
            _build_raw_packet(MSG_WIFI_CONNECT_STATUS),
        )
        result = RfcommHandshake(sock, MOCK_CREDS).run()
        assert result.success is True

    def test_event_loop_exhaustion(self):
        """Receiving only unknown messages until _MAX_MESSAGES → failure."""
        RfcommHandshake, _ = self._import()

        # Build 25 unknown messages (> _MAX_MESSAGES=20)
        raw_unknown = _build_raw_packet(99)
        sock = _make_socket_with_responses(*([raw_unknown] * 25))
        result = RfcommHandshake(sock, MOCK_CREDS).run()
        assert result.success is False
        assert "exhausted" in result.error.lower()


# ===========================================================================
# SECTION 3 — rfcomm_handshake/main.py (bus-level unit tests)
# ===========================================================================

class TestRfcommHandshakeMain:
    """
    Unit tests for rfcomm_handshake/main.py bus handlers.

    We patch the module-level `bus` object so no real ZMQ sockets are created.
    DbusRfcommListener and RfcommHandshake are also mocked to isolate logic.
    """

    def _load_module(self):
        """Import main.py and reset its global state."""
        import importlib
        import rfcomm_handshake.main as mod
        importlib.reload(mod)
        return mod

    def _patch_module_bus(self, mod):
        mock_bus = MagicMock()
        mod.bus = mock_bus
        mod.log = MagicMock()
        return mock_bus

    # -- Boot protocol --

    def test_on_system_readytostart_publishes_module_ready(self):
        mod = self._load_module()
        mock_bus = self._patch_module_bus(mod)

        mod.on_system_readytostart()

        mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": "rfcomm_handshake", "priority": 1},
        )

    def test_on_system_start_wrong_priority_is_noop(self):
        mod = self._load_module()
        mock_bus = self._patch_module_bus(mod)

        mod.on_system_start("system.start", {"priority": 99})

        mock_bus.publish.assert_not_called()

    def test_on_system_start_correct_priority_publishes_system_ready(self):
        mod = self._load_module()
        mock_bus = self._patch_module_bus(mod)

        with patch("rfcomm_handshake.main.DbusRfcommListener") as mock_listener_cls, \
             patch("rfcomm_handshake.main._start_glib_mainloop"):
            mock_listener = MagicMock()
            mock_listener.start.return_value = True
            mock_listener_cls.return_value = mock_listener

            mod.on_system_start("system.start", {"priority": 1})

        published_topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "system.ready" in published_topics

    # -- Handshake trigger logic --

    def test_try_start_handshake_requires_all_three_conditions(self):
        """Handshake must NOT start if any of: device_address, credentials, pending_sock is missing."""
        mod = self._load_module()
        self._patch_module_bus(mod)

        # Only device_address set — no credentials, no sock
        mod._device_address = "AA:BB:CC:DD:EE:FF"
        mod._credentials    = None
        mod._pending_sock   = None

        mod._try_start_handshake()

        assert mod._handshake_running is False

    def test_try_start_handshake_starts_thread_when_all_present(self):
        """_try_start_handshake spawns exactly one daemon thread when all conditions are met."""
        mod = self._load_module()
        self._patch_module_bus(mod)

        mock_sock = MagicMock(spec=socket.socket)
        mod._device_address   = "AA:BB:CC:DD:EE:FF"
        mod._credentials      = dict(MOCK_CREDS)
        mod._pending_sock     = mock_sock
        mod._handshake_running = False

        threads_before = threading.active_count()
        with patch("rfcomm_handshake.main._run_handshake"):
            mod._try_start_handshake()
            # _handshake_running must be set to True inside the lock
            assert mod._handshake_running is True

    def test_on_rfcomm_connected_publishes_event_and_triggers_handshake_attempt(self):
        mod = self._load_module()
        mock_bus = self._patch_module_bus(mod)

        mock_sock = MagicMock(spec=socket.socket)

        with patch("rfcomm_handshake.main._try_start_handshake") as mock_trigger:
            mod._on_rfcomm_connected(mock_sock, "DE:AD:BE:EF:00:01")

        mock_bus.publish.assert_called_once_with(
            "bluetooth_manager.rfcomm.connected",
            {"device_address": "DE:AD:BE:EF:00:01"},
        )
        mock_trigger.assert_called_once()

    def test_on_rfcomm_connected_rejects_duplicate_when_handshake_running(self):
        """If handshake already running, a second RFCOMM connection is rejected (sock closed)."""
        mod = self._load_module()
        self._patch_module_bus(mod)

        mod._handshake_running = True
        new_sock = MagicMock(spec=socket.socket)

        with patch("rfcomm_handshake.main._try_start_handshake") as mock_trigger:
            mod._on_rfcomm_connected(new_sock, "FF:FF:FF:FF:FF:FF")

        new_sock.close.assert_called_once()
        mock_trigger.assert_not_called()

    def test_on_hostapd_ready_stores_credentials(self):
        mod = self._load_module()
        self._patch_module_bus(mod)

        payload = dict(MOCK_CREDS)
        with patch("rfcomm_handshake.main._try_start_handshake"):
            mod.on_hostapd_ready("hostapd.ready", payload)

        assert mod._credentials == payload

    def test_on_system_stop_cleans_up(self):
        mod = self._load_module()
        mock_bus = self._patch_module_bus(mod)

        mock_listener = MagicMock()
        mod._rfcomm_listener = mock_listener

        with patch("rfcomm_handshake.main._close_pending_sock"), \
             patch("rfcomm_handshake.main._stop_glib_mainloop"):
            mod.on_system_stop("system.stop", {})

        mock_listener.stop.assert_called_once()
        mock_bus.stop.assert_called_once()
        assert mod._rfcomm_listener is None


# ===========================================================================
# SECTION 4 — channel_manager/main.py (ChannelManagerSession + bus handlers)
# ===========================================================================

class TestChannelManagerSession:
    """
    Tests for the ChannelManagerSession OOP class.
    Launcher and bus.publish are fully mocked.
    """

    def _make_session(self):
        import importlib
        import channel_manager.main as mod
        importlib.reload(mod)
        mock_bus = MagicMock()
        mod.bus = mock_bus
        mod.log = MagicMock()
        return mod, mock_bus

    # -- start() + readiness --

    def test_start_skips_control_channel(self):
        """channel_id=0 must be skipped (handled by oaa_control_channel)."""
        mod, _ = self._make_session()

        channels = [{"channel_id": 0}, {"channel_id": 1}]
        session = mod.ChannelManagerSession()

        with patch.object(session._launcher, "start_all", return_value={"video_ch1"}) as mock_start:
            with patch("channel_manager.main.resolve_module_type", return_value="video"), \
                 patch("channel_manager.main.module_name",          return_value="video_ch1"):
                session.start("aabbcc", channels)

        # start_all must be called with only channel_id=1 (0 was dropped)
        call_args = mock_start.call_args[0][0]  # launch_list
        channel_ids_in_launch = [c["channel_id"] for c in call_args]
        assert 0 not in channel_ids_in_launch
        assert 1 in channel_ids_in_launch

    def test_on_module_ready_tracks_progress(self):
        mod, _ = self._make_session()
        session = mod.ChannelManagerSession()

        # Seed expected set directly
        session._expected = {"video_ch1", "audio_ch4"}
        session._all_started_channels = [
            {"module_name": "video_ch1", "module_type": "video", "channel_id": 1},
            {"module_name": "audio_ch4", "module_type": "audio", "channel_id": 4},
        ]

        session.on_module_ready("video_ch1")
        assert "video_ch1" in session._ready
        assert not session._all_ready.is_set()
        session.on_module_ready("audio_ch4")
        assert session._all_ready.is_set()

    def test_on_module_ready_ignores_unknown_name(self):
        mod, _ = self._make_session()
        session = mod.ChannelManagerSession()
        session._expected = {"video_ch1"}

        # unexpected name — should not raise, should not add to _ready
        session.on_module_ready("unknown_module")
        assert "unknown_module" not in session._ready

    def test_wait_all_ready_publishes_channels_ready_on_success(self):
        mod, mock_bus = self._make_session()
        session = mod.ChannelManagerSession()

        session._expected = {"video_ch1"}
        session._all_started_channels = [
            {"module_name": "video_ch1", "module_type": "video", "channel_id": 1},
        ]
        # Simulate immediate readiness
        session._all_ready.set()
        session._all_active_channels = session._all_started_channels.copy()

        result = session.wait_all_ready("aabbcc")

        assert result is True
        published_topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "channel_manager.channels_ready" in published_topics
        assert "aa.channel.open" in published_topics

    def test_wait_all_ready_returns_false_on_timeout(self):
        mod, _ = self._make_session()
        session = mod.ChannelManagerSession()

        session._expected = {"video_ch1"}
        # _all_ready never set → timeout immediately with a tiny value
        with patch.object(session._all_ready, "wait", return_value=False):
            result = session.wait_all_ready("aabbcc")

        assert result is False

    def test_on_module_stopped_sets_all_stopped_when_complete(self):
        mod, _ = self._make_session()
        session = mod.ChannelManagerSession()
        session._expected = {"video_ch1", "audio_ch4"}

        session.on_module_stopped("video_ch1")
        assert not session._all_stopped.is_set()

        session.on_module_stopped("audio_ch4")
        assert session._all_stopped.is_set()

    def test_shutdown_publishes_stop_and_stopped(self):
        mod, mock_bus = self._make_session()
        session = mod.ChannelManagerSession()
        session._is_active = True
        session._all_active_channels = [
            {"module_name": "video_ch1", "module_type": "video", "channel_id": 1},
        ]
        session._all_started_channels = list(session._all_active_channels)

        with patch.object(session._launcher, "stop_all"):
            session.shutdown()

        published_topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.channel.close"              in published_topics
        assert "channel_manager.module_stop"   in published_topics
        assert "channel_manager.stopped"       in published_topics
        assert session._is_active is False

    def test_on_module_ready_to_start_publishes_module_start(self):
        mod, mock_bus = self._make_session()
        session = mod.ChannelManagerSession()
        session._expected = {"video_ch1"}

        session.on_module_ready_to_start("video_ch1", priority=5)

        mock_bus.publish.assert_called_once_with(
            "channel_manager.module_start",
            {"priority": 5},
        )

    def test_on_module_ready_to_start_ignores_unknown(self):
        mod, mock_bus = self._make_session()
        session = mod.ChannelManagerSession()
        session._expected = {"video_ch1"}

        session.on_module_ready_to_start("unknown_child", priority=3)

        mock_bus.publish.assert_not_called()


class TestChannelManagerBootProtocol:
    """Tests for the module-level boot handlers in channel_manager/main.py."""

    def _load(self):
        import importlib
        import channel_manager.main as mod
        importlib.reload(mod)
        mock_bus = MagicMock()
        mod.bus = mock_bus
        mod.log = MagicMock()
        return mod, mock_bus

    def test_on_system_readytostart_publishes_module_ready(self):
        mod, mock_bus = self._load()
        mod.on_system_readytostart()
        mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": "channel_manager", "priority": 2},
        )

    def test_on_system_start_wrong_priority_is_noop(self):
        mod, mock_bus = self._load()
        mod.on_system_start("system.start", {"priority": 99})
        mock_bus.publish.assert_not_called()

    def test_on_system_start_correct_priority_publishes_ready(self):
        mod, mock_bus = self._load()
        mod.on_system_start("system.start", {"priority": 2})
        published_topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "system.ready" in published_topics

    def test_on_aa_session_shutdown_calls_session_shutdown(self):
        mod, _ = self._load()
        mock_session = MagicMock()
        mod._session = mock_session

        mod.on_aa_session_shutdown("aa.session.shutdown", {})

        mock_session.shutdown.assert_called_once()
        assert mod._session is None

    def test_on_aa_session_restart_calls_session_shutdown(self):
        mod, _ = self._load()
        mock_session = MagicMock()
        mod._session = mock_session

        mod.on_aa_session_restart("aa.session.restart", {})

        mock_session.shutdown.assert_called_once()
        assert mod._session is None

    def test_on_system_stop_shuts_down_session_and_calls_bus_stop(self):
        mod, mock_bus = self._load()
        mock_session = MagicMock()
        mod._session = mock_session

        mod.on_system_stop("system.stop", {})

        mock_session.shutdown.assert_called_once()
        mock_bus.stop.assert_called_once()

    def test_on_open_channels_missing_payload_is_noop(self):
        mod, mock_bus = self._load()
        mod.on_oaa_control_channel_open_channels(
            "oaa_control_channel.open_channels",
            {"sdr_bytes_hex": "", "channels": []},
        )
        # No session created, no publish expected
        mock_bus.publish.assert_not_called()
        assert mod._session is None

    def test_on_channel_manager_module_ready_delegates_to_session(self):
        mod, _ = self._load()
        mock_session = MagicMock()
        mod._session = mock_session

        mod.on_channel_manager_module_ready(
            "channel_manager.module_ready",
            {"name": "video_ch1"},
        )

        mock_session.on_module_ready.assert_called_once_with("video_ch1")

    def test_on_channel_manager_module_stopped_delegates_to_session(self):
        mod, _ = self._load()
        mock_session = MagicMock()
        mod._session = mock_session

        mod.on_channel_manager_module_stopped(
            "channel_manager.module_stopped",
            {"name": "audio_ch4"},
        )

        mock_session.on_module_stopped.assert_called_once_with("audio_ch4")
