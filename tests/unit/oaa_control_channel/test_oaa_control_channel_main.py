"""
test_oaa_control_channel_main.py — Unit tests for oaa_control_channel/main.py.

All module-level singletons (bus, log, cfg, _handshake, _cfg) are reset
before each test via the _patch_module fixture so tests are independent.

Coverage targets:

  1. Boot protocol
        a. on_system_readytostart() publishes system.module_ready {name, priority}
        b. on_system_start() matching priority calls cfg.get(schema=_SCHEMA)
        c. on_system_start() wrong priority is a no-op
        d. on_system_stop() calls bus.stop()

  2. Config flow
        a. _on_config_loaded(config) merges known keys into _cfg
        b. _on_config_loaded(config) only merges keys present in SEMANTIC_DEFAULTS
        c. _on_config_loaded({}) uses SEMANTIC_DEFAULTS unchanged
        d. _on_config_loaded() publishes system.ready {name, priority}
        e. _on_config_changed(known_key, value) updates _cfg[key]
        f. _on_config_changed(known_key) publishes aa.session.restart
        g. _on_config_changed(known_key) with active handshake nullifies _handshake
           and publishes aa.session.shutdown + aa.handshake.state DISCONNECTED
        h. _on_config_changed(unknown_key) is a no-op (no publish, no crash)

  3. Session lifecycle
        a. on_tcp_session_connected() creates _handshake, publishes aa.handshake.state IDLE,
           calls send_version_request()
        b. on_tcp_session_closed() sets _handshake=None, publishes aa.session.shutdown
           and aa.handshake.state DISCONNECTED
        c. on_aa_session_restarting() recreates _handshake, publishes IDLE,
           calls send_version_request()

  4. Frame dispatch (on_frame_ch0)
        a. Frame forwarded to _handshake.on_message(message_id, body, encrypted)
        b. aa.handshake.state published with handshake.state.name after dispatch
        c. Frame dropped (warning) when _handshake is None
        d. Malformed payload (missing keys) does not raise
        e. payload_hex decoded to bytes before on_message
        f. encrypted flag forwarded correctly

  5. TLS delegation
        a. on_tls_handshake() calls _handshake.on_tls_handshake_blob(outgoing_hex)
        b. on_tls_handshake() drops if _handshake is None
        c. on_tls_handshake() drops on missing outgoing_hex key (no raise)
        d. on_tls_handshake_completed() calls _handshake.on_tls_complete()
        e. on_tls_handshake_completed() drops if _handshake is None

  6. Channel manager integration
        a. on_channel_ready() calls _handshake.on_channels_ready(sdr_bytes_hex)
        b. on_channel_ready() publishes aa.handshake.state with handshake.state.name
        c. on_channel_ready() drops if _handshake is None
        d. on_channel_ready() drops on malformed payload (no raise)

  7. Handshake callbacks (_on_session_active, _on_session_shutdown)
        a. _on_session_active() publishes aa.session.active and aa.handshake.state ACTIVE
        b. _on_session_shutdown() publishes aa.session.shutdown and aa.handshake.state SHUTDOWN
"""

from __future__ import annotations

import importlib
import sys
import types
from enum import Enum
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeState(Enum):
    IDLE        = "IDLE"
    ACTIVE      = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"


def _make_mock_handshake(state_name: str = "IDLE") -> MagicMock:
    hs = MagicMock()
    hs.state      = _FakeState[state_name]
    hs.on_message = MagicMock()
    hs.on_tls_handshake_blob = MagicMock()
    hs.on_tls_complete       = MagicMock()
    hs.on_channels_ready     = MagicMock()
    hs.send_version_request  = MagicMock()
    return hs


@pytest.fixture(autouse=True)
def _patch_module(monkeypatch):
    """
    Reload oaa_control_channel.main with mocked dependencies so each test
    starts with a clean module state.
    """
    mock_bus = MagicMock()
    mock_bus.publish   = MagicMock(return_value=True)
    mock_bus.subscribe = MagicMock()
    mock_bus.stop      = MagicMock()
    mock_bus.start     = MagicMock(return_value=MagicMock())

    mock_cfg = MagicMock()
    mock_log = MagicMock()

    # Minimal SEMANTIC_DEFAULTS and _SCHEMA stubs
    fake_semantic_defaults = {
        "video_codec":    "h264",
        "audio_sampling": 16000,
        "max_unacked":    1,
    }
    fake_schema = {}

    with (
        patch("shared.bus_client.BusClient",   return_value=mock_bus),
        patch("shared.logger.get_logger",       return_value=mock_log),
        patch("shared.config_client.ConfigClient", return_value=mock_cfg),
        patch.dict("sys.modules", {
            "oaa_control_channel.handshake": types.SimpleNamespace(
                ControlChannelHandshake=MagicMock
            ),
            "oaa_control_channel.service_discovery": types.SimpleNamespace(
                SEMANTIC_DEFAULTS=fake_semantic_defaults,
                _SCHEMA=fake_schema,
            ),
        }),
    ):
        # Force fresh import each test
        mod_name = "oaa_control_channel.main"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import oaa_control_channel.main as mod
        importlib.reload(mod)

        # Inject our mocks into the reloaded module
        mod.bus = mock_bus
        mod.log = mock_log
        mod.cfg = mock_cfg
        mod._handshake = None
        mod._cfg       = dict(fake_semantic_defaults)

        yield mod, mock_bus, mock_cfg


def _published(bus: MagicMock, topic: str) -> list[dict]:
    return [
        c.args[1] for c in bus.publish.call_args_list
        if c.args[0] == topic
    ]


def _reset_bus(bus: MagicMock):
    bus.publish.reset_mock()


# ============================================================================
# 1. Boot protocol
# ============================================================================

@pytest.mark.unit
class TestBootProtocol:

    def test_readytostart_publishes_module_ready(self, _patch_module):
        mod, bus, _ = _patch_module
        mod.on_system_readytostart()
        payloads = _published(bus, "system.module_ready")
        assert payloads
        assert payloads[0]["name"]     == "oaa_control_channel"
        assert payloads[0]["priority"] == 2

    def test_system_start_matching_priority_calls_cfg_get(self, _patch_module):
        mod, _, cfg = _patch_module
        mod.on_system_start("", {"priority": 2})
        cfg.get.assert_called_once()

    def test_system_start_wrong_priority_noop(self, _patch_module):
        mod, bus, cfg = _patch_module
        mod.on_system_start("", {"priority": 99})
        cfg.get.assert_not_called()
        assert not _published(bus, "system.ready")

    def test_system_stop_calls_bus_stop(self, _patch_module):
        mod, bus, _ = _patch_module
        mod.on_system_stop("", {})
        bus.stop.assert_called_once()


# ============================================================================
# 2. Config flow
# ============================================================================

@pytest.mark.unit
class TestConfigFlow:

    def test_config_loaded_merges_known_keys(self, _patch_module):
        mod, _, _ = _patch_module
        mod._on_config_loaded({"video_codec": "h265", "max_unacked": 4})
        assert mod._cfg["video_codec"]  == "h265"
        assert mod._cfg["max_unacked"]  == 4

    def test_config_loaded_ignores_unknown_keys(self, _patch_module):
        mod, _, _ = _patch_module
        mod._on_config_loaded({"unknown_key": "foo", "video_codec": "h265"})
        assert "unknown_key" not in mod._cfg

    def test_config_loaded_empty_dict_preserves_defaults(self, _patch_module):
        mod, _, _ = _patch_module
        original = dict(mod._cfg)
        mod._on_config_loaded({})
        assert mod._cfg == original

    def test_config_loaded_publishes_system_ready(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._on_config_loaded({})
        payloads = _published(bus, "system.ready")
        assert payloads
        assert payloads[0]["name"]     == "oaa_control_channel"
        assert payloads[0]["priority"] == 2

    def test_config_changed_known_key_updates_cfg(self, _patch_module):
        mod, _, _ = _patch_module
        mod._on_config_changed("audio_sampling", 44100)
        assert mod._cfg["audio_sampling"] == 44100

    def test_config_changed_known_key_publishes_restart(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._on_config_changed("max_unacked", 8)
        assert _published(bus, "aa.session.restart")

    def test_config_changed_with_active_handshake_nullifies_it(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod._on_config_changed("video_codec", "h265")
        assert mod._handshake is None

    def test_config_changed_with_active_handshake_publishes_shutdown(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod._on_config_changed("video_codec", "h265")
        assert _published(bus, "aa.session.shutdown")

    def test_config_changed_with_active_handshake_publishes_disconnected(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod._on_config_changed("video_codec", "h265")
        states = _published(bus, "aa.handshake.state")
        assert any(s["state"] == "DISCONNECTED" for s in states)

    def test_config_changed_unknown_key_noop(self, _patch_module):
        mod, bus, _ = _patch_module
        original = dict(mod._cfg)
        mod._on_config_changed("nonexistent", 999)
        assert mod._cfg == original
        assert not _published(bus, "aa.session.restart")


# ============================================================================
# 3. Session lifecycle
# ============================================================================

@pytest.mark.unit
class TestSessionLifecycle:

    def test_tcp_connected_creates_handshake(self, _patch_module):
        mod, _, _ = _patch_module
        mod.on_tcp_session_connected("", {"address": "192.168.1.1"})
        assert mod._handshake is not None

    def test_tcp_connected_publishes_idle_state(self, _patch_module):
        mod, bus, _ = _patch_module
        mod.on_tcp_session_connected("", {"address": "192.168.1.1"})
        states = _published(bus, "aa.handshake.state")
        assert any(s["state"] == "IDLE" for s in states)

    def test_tcp_connected_calls_send_version_request(self, _patch_module):
        mod, _, _ = _patch_module
        mod.on_tcp_session_connected("", {"address": "192.168.1.1"})
        mod._handshake.send_version_request.assert_called_once()

    def test_tcp_closed_nullifies_handshake(self, _patch_module):
        mod, _, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod.on_tcp_session_closed("", {})
        assert mod._handshake is None

    def test_tcp_closed_publishes_shutdown(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod.on_tcp_session_closed("", {})
        assert _published(bus, "aa.session.shutdown")

    def test_tcp_closed_publishes_disconnected(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod.on_tcp_session_closed("", {})
        states = _published(bus, "aa.handshake.state")
        assert any(s["state"] == "DISCONNECTED" for s in states)

    def test_session_restarting_recreates_handshake(self, _patch_module):
        mod, _, _ = _patch_module
        old = _make_mock_handshake()
        mod._handshake = old
        mod.on_aa_session_restarting("", {})
        assert mod._handshake is not old
        assert mod._handshake is not None

    def test_session_restarting_publishes_idle(self, _patch_module):
        mod, bus, _ = _patch_module
        mod.on_aa_session_restarting("", {})
        states = _published(bus, "aa.handshake.state")
        assert any(s["state"] == "IDLE" for s in states)

    def test_session_restarting_calls_send_version_request(self, _patch_module):
        mod, _, _ = _patch_module
        mod.on_aa_session_restarting("", {})
        mod._handshake.send_version_request.assert_called_once()


# ============================================================================
# 4. Frame dispatch
# ============================================================================

@pytest.mark.unit
class TestFrameDispatch:

    def _valid_payload(self, msg_id=0x0001, hex_body="deadbeef", enc=False):
        return {
            "channel_id":  0,
            "message_id":  msg_id,
            "encrypted":   enc,
            "payload_hex": hex_body,
        }

    def test_frame_forwarded_to_on_message(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_frame_ch0("", self._valid_payload())
        hs.on_message.assert_called_once_with(0x0001, bytes.fromhex("deadbeef"), False)

    def test_handshake_state_published_after_dispatch(self, _patch_module):
        mod, bus, _ = _patch_module
        hs = _make_mock_handshake("IDLE")
        mod._handshake = hs
        mod.on_frame_ch0("", self._valid_payload())
        states = _published(bus, "aa.handshake.state")
        assert states  # at least one published

    def test_frame_dropped_when_no_handshake(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = None
        mod.on_frame_ch0("", self._valid_payload())
        # on_message never called because handshake is None — just verify no crash

    def test_malformed_payload_does_not_raise(self, _patch_module):
        mod, _, _ = _patch_module
        mod._handshake = _make_mock_handshake()
        mod.on_frame_ch0("", {})  # missing all keys

    def test_payload_hex_decoded_to_bytes(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_frame_ch0("", self._valid_payload(hex_body="0102"))
        _, args, _ = hs.on_message.mock_calls[0]
        assert args[1] == b"\x01\x02"

    def test_encrypted_flag_forwarded(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_frame_ch0("", self._valid_payload(enc=True))
        _, args, _ = hs.on_message.mock_calls[0]
        assert args[2] is True


# ============================================================================
# 5. TLS delegation
# ============================================================================

@pytest.mark.unit
class TestTLSDelegation:

    def test_tls_handshake_calls_on_tls_handshake_blob(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_tls_handshake("", {"outgoing_hex": "aabbcc"})
        hs.on_tls_handshake_blob.assert_called_once_with("aabbcc")

    def test_tls_handshake_drops_when_no_handshake(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._handshake = None
        mod.on_tls_handshake("", {"outgoing_hex": "aabb"})
        # No crash; nothing forwarded

    def test_tls_handshake_drops_on_missing_key(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_tls_handshake("", {})  # missing outgoing_hex
        hs.on_tls_handshake_blob.assert_not_called()

    def test_tls_completed_calls_on_tls_complete(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_tls_handshake_completed("", {})
        hs.on_tls_complete.assert_called_once()

    def test_tls_completed_drops_when_no_handshake(self, _patch_module):
        mod, _, _ = _patch_module
        mod._handshake = None
        mod.on_tls_handshake_completed("", {})  # no crash


# ============================================================================
# 6. Channel manager integration
# ============================================================================

@pytest.mark.unit
class TestChannelManagerIntegration:

    def test_channel_ready_calls_on_channels_ready(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_channel_ready("", {"sdr_bytes_hex": "deadbeef"})
        hs.on_channels_ready.assert_called_once_with("deadbeef")

    def test_channel_ready_publishes_handshake_state(self, _patch_module):
        mod, bus, _ = _patch_module
        hs = _make_mock_handshake("IDLE")
        mod._handshake = hs
        mod.on_channel_ready("", {"sdr_bytes_hex": "aa"})
        assert _published(bus, "aa.handshake.state")

    def test_channel_ready_drops_when_no_handshake(self, _patch_module):
        mod, _, _ = _patch_module
        mod._handshake = None
        mod.on_channel_ready("", {"sdr_bytes_hex": "aa"})  # no crash

    def test_channel_ready_drops_on_malformed_payload(self, _patch_module):
        mod, _, _ = _patch_module
        hs = _make_mock_handshake()
        mod._handshake = hs
        mod.on_channel_ready("", {})  # missing sdr_bytes_hex
        hs.on_channels_ready.assert_not_called()


# ============================================================================
# 7. Handshake callbacks
# ============================================================================

@pytest.mark.unit
class TestHandshakeCallbacks:

    def test_on_session_active_publishes_aa_session_active(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._on_session_active()
        assert _published(bus, "aa.session.active")

    def test_on_session_active_publishes_handshake_state_active(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._on_session_active()
        states = _published(bus, "aa.handshake.state")
        assert any(s["state"] == "ACTIVE" for s in states)

    def test_on_session_shutdown_publishes_aa_session_shutdown(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._on_session_shutdown()
        assert _published(bus, "aa.session.shutdown")

    def test_on_session_shutdown_publishes_handshake_state_shutdown(self, _patch_module):
        mod, bus, _ = _patch_module
        mod._on_session_shutdown()
        states = _published(bus, "aa.handshake.state")
        assert any(s["state"] == "SHUTDOWN" for s in states)
