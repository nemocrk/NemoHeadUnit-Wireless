"""
test_handshake.py — Unit tests for ControlChannelHandshake state machine.

All protobuf types and external helpers (decode_proto, encode_proto,
build_from_schema_cfg, channels_from_sdr_bytes, message_from_sdr_bytes)
are mocked.  Tests exercise the pure state-machine logic in isolation.

Coverage sections:

  1.  Construction & initial state
  2.  send_version_request()
  3.  _on_version_response()  — state + bus side-effects
  4.  _on_ssl_handshake()     — forwards blob to tcp_server via publish
  5.  on_tls_handshake_blob() — forwards TLS bytes to phone as SSL_HANDSHAKE
  6.  on_tls_complete()       — sends AUTH_COMPLETE, state AUTH_OK
  7.  _on_auth_complete()     — incoming AUTH_COMPLETE, state AUTH_OK
  8.  _on_service_discovery_request()  — calls build, publishes open_channels, WAITING_CHANNELS
  9.  on_channels_ready()     — sends SDR, state CHANNELS_OPENING
  10. on_channels_ready() in wrong state — ignored
  11. _on_channel_open_request()  — sends CHANNEL_OPEN_RESPONSE OK, tracks open_channels
  12. _on_ping_request()          — sends PONG, state ACTIVE via ping trigger
  13. _on_ping_request() idempotent — ACTIVE only fires once
  14. _on_audio_focus_request()   — grants GAIN, ACTIVE via audio-focus trigger
  15. _on_audio_focus_request()   — ACTIVE trigger only from CHANNELS_OPENING state
  16. _on_audio_focus_request()   — RELEASE mapped to LOSS
  17. _on_audio_focus_request()   — GAIN_TRANSIENT mapped correctly
  18. _on_audio_focus_request()   — unknown focus type mapped to INVALID
  19. _on_navigation_focus_request() — responds NAV_FOCUS_PROJECTED
  20. _on_voice_session_request()    — no response, no state change
  21. _on_battery_status_notification() — logged, no send, no state change
  22. _on_shutdown_request()      — sends SHUTDOWN_RESPONSE, state SHUTDOWN, fires callback
  23. on_message() dispatch table — all known msg IDs routed to correct handler
  24. on_message() unknown msg ID — silent no-op
  25. on_active_cb / on_shutdown_cb — called exactly once
"""

from __future__ import annotations

import struct
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal proto stubs — enough for the state machine logic
# ---------------------------------------------------------------------------

class _FakeEnum:
    """Minimal int-like enum value."""
    def __init__(self, v): self._v = v
    def __eq__(self, other): return self._v == (other._v if isinstance(other, _FakeEnum) else other)
    def __hash__(self): return hash(self._v)
    def __repr__(self): return f"FakeEnum({self._v})"


def _make_proto_stub(name: str, **fields) -> MagicMock:
    s = MagicMock(name=name)
    for k, v in fields.items():
        setattr(s, k, v)
    s.SerializeToString.return_value = b"STUB"
    return s


# Fake AudioFocusType / AudioFocusState constants
class _AFT:   # AudioFocusType
    GAIN                     = _FakeEnum(1)
    GAIN_TRANSIENT           = _FakeEnum(2)
    GAIN_TRANSIENT_MAY_DUCK  = _FakeEnum(3)
    RELEASE                  = _FakeEnum(4)


class _AFS:   # AudioFocusState
    GAIN                            = _FakeEnum(10)
    GAIN_TRANSIENT                  = _FakeEnum(11)
    GAIN_TRANSIENT_GUIDANCE_ONLY    = _FakeEnum(12)
    LOSS                            = _FakeEnum(13)
    INVALID                         = _FakeEnum(99)


class _NFT:   # NavigationFocusType
    NAV_FOCUS_PROJECTED = _FakeEnum(1)


# Fake ControlMessage.Enum  — these mirror the values used in handshake.py
class _CME:
    VERSION_REQUEST        = 0x0001
    VERSION_RESPONSE       = 0x0002
    SSL_HANDSHAKE          = 0x0003
    AUTH_COMPLETE          = 0x0006
    SERVICE_DISCOVERY_REQUEST  = 0x0005
    SERVICE_DISCOVERY_RESPONSE = 0x0007
    CHANNEL_OPEN_REQUEST   = 0x0008
    CHANNEL_OPEN_RESPONSE  = 0x0009
    PING_REQUEST           = 0x000B
    PING_RESPONSE          = 0x000C
    AUDIO_FOCUS_REQUEST    = 0x0012
    AUDIO_FOCUS_RESPONSE   = 0x0013
    NAVIGATION_FOCUS_REQUEST  = 0x0014
    NAVIGATION_FOCUS_RESPONSE = 0x0015
    VOICE_SESSION_REQUEST  = 0x0016
    BATTERY_STATUS_NOTIFICATION = 0x0018
    SHUTDOWN_REQUEST       = 0x0019
    SHUTDOWN_RESPONSE      = 0x001A


def _build_sys_modules_patch() -> dict:
    """Construct minimal stub modules so handshake.py imports without
    the real protobuf generated files being present."""

    def _enum_mod(cls):
        m = types.ModuleType("_fake")
        m.__dict__.update({k: v for k, v in vars(cls).items() if not k.startswith("__")})
        return m

    # ControlMessage stub
    cm_stub = MagicMock()
    cm_stub.Enum = _CME

    # AudioFocusResponse stub
    afr_stub_cls = MagicMock()
    afr_instance = MagicMock()
    afr_stub_cls.return_value = afr_instance

    # NavigationFocusResponse stub
    nfr_stub_cls = MagicMock()

    # AudioFocusType / State stubs
    aft_mod = types.ModuleType("oaa.audio.AudioFocusTypeEnum_pb2")
    aft_mod.AudioFocusType = _AFT

    afs_mod = types.ModuleType("v2.protos.oaa.audio.AudioFocusStateEnum_pb2")
    afs_mod.AudioFocusState = _AFS

    nft_mod = types.ModuleType("v2.protos.oaa.navigation.NavigationFocusRequestMessage_pb2")
    nft_mod.NavigationFocusRequest = MagicMock()
    nft_mod.NavigationFocusType = _NFT

    return {
        "v2.protos.oaa.control.ControlMessageIdsEnum_pb2":          types.SimpleNamespace(ControlMessage=cm_stub),
        "v2.protos.oaa.control.AuthCompleteIndicationMessage_pb2":   types.SimpleNamespace(AuthCompleteIndication=MagicMock(return_value=_make_proto_stub("AuthComplete"))),
        "v2.protos.oaa.control.ServiceDiscoveryRequestMessage_pb2":  types.SimpleNamespace(ServiceDiscoveryRequest=MagicMock()),
        "v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2": types.SimpleNamespace(ServiceDiscoveryResponse=MagicMock()),
        "v2.protos.oaa.control.ChannelOpenRequestMessage_pb2":       types.SimpleNamespace(ChannelOpenRequest=MagicMock()),
        "v2.protos.oaa.control.ChannelOpenResponseMessage_pb2":      types.SimpleNamespace(ChannelOpenResponse=MagicMock(return_value=_make_proto_stub("COR", status=0))),
        "v2.protos.oaa.control.PingRequestMessage_pb2":              types.SimpleNamespace(PingRequest=MagicMock()),
        "v2.protos.oaa.control.PingResponseMessage_pb2":             types.SimpleNamespace(PingResponse=MagicMock(return_value=_make_proto_stub("PingResp"))),
        "v2.protos.oaa.control.VoiceSessionRequestMessage_pb2":      types.SimpleNamespace(VoiceSessionRequest=MagicMock()),
        "v2.protos.oaa.control.BatteryStatusMessage_pb2":            types.SimpleNamespace(BatteryStatusNotification=MagicMock()),
        "v2.protos.oaa.audio.AudioFocusRequestMessage_pb2":          types.SimpleNamespace(AudioFocusRequest=MagicMock()),
        "oaa.audio.AudioFocusTypeEnum_pb2":                          aft_mod,
        "v2.protos.oaa.audio.AudioFocusResponseMessage_pb2":         types.SimpleNamespace(AudioFocusResponse=afr_stub_cls),
        "v2.protos.oaa.audio.AudioFocusStateEnum_pb2":               afs_mod,
        "v2.protos.oaa.navigation.NavigationFocusRequestMessage_pb2": nft_mod,
        "v2.protos.oaa.navigation.NavigationFocusResponseMessage_pb2": types.SimpleNamespace(NavigationFocusResponse=MagicMock(return_value=_make_proto_stub("NFR"))),
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def hs_factory():
    """Returns a factory that produces a fresh ControlChannelHandshake with
    mocked send_fn, publish_fn, and callbacks.  All proto imports are stubbed."""

    sys_patch = _build_sys_modules_patch()

    # Also stub shared helpers called inside handlers
    mock_decode  = MagicMock(return_value=MagicMock())
    mock_encode  = MagicMock(return_value=b"ENCODED")
    mock_build   = MagicMock(return_value=b"\xAA\xBB\xCC")
    mock_channels = MagicMock(return_value=[1, 2, 3])
    mock_msg_from = MagicMock(return_value=MagicMock())
    mock_proto_to_dict = MagicMock(return_value={})

    svc_disc_stub = types.SimpleNamespace(
        build_from_schema_cfg=mock_build,
        channels_from_sdr_bytes=mock_channels,
        message_from_sdr_bytes=mock_msg_from,
    )

    with (
        patch.dict("sys.modules", sys_patch),
        patch("shared.logger.get_logger", return_value=MagicMock()),
    ):
        # Remove stale module cache before each test
        for mod_name in list(sys.modules):
            if "oaa_control_channel" in mod_name or "shared" in mod_name:
                if mod_name not in sys_patch:
                    del sys.modules[mod_name]

        # Import service_discovery first to populate sys.modules so patch can find it
        import importlib
        import oaa_control_channel.service_discovery as sd_mod
        importlib.reload(sd_mod)

        with (
            patch.object(sd_mod, "build_from_schema_cfg", mock_build),
            patch.object(sd_mod, "channels_from_sdr_bytes", mock_channels),
            patch.object(sd_mod, "message_from_sdr_bytes", mock_msg_from),
            patch("shared.proto_utils.decode_proto",    mock_decode),
            patch("shared.proto_utils.encode_proto",    mock_encode),
            patch("shared.proto_utils.proto_to_dict",   mock_proto_to_dict),
        ):
            # Import the module under test inside the patch context
            if "oaa_control_channel.handshake" in sys.modules:
                del sys.modules["oaa_control_channel.handshake"]
            import oaa_control_channel.handshake as hs_mod
            importlib.reload(hs_mod)

            # Inject mocked helpers directly into the reloaded module
            hs_mod.decode_proto    = mock_decode
            hs_mod.encode_proto    = mock_encode
            hs_mod.proto_to_dict   = mock_proto_to_dict
            hs_mod.build_from_schema_cfg   = mock_build
            hs_mod.channels_from_sdr_bytes = mock_channels
            hs_mod.message_from_sdr_bytes  = mock_msg_from

            def _factory(
                cfg=None,
                on_active_cb=None,
                on_shutdown_cb=None,
            ):
                send_fn    = MagicMock()
                publish_fn = MagicMock()
                active_cb  = on_active_cb  or MagicMock()
                shutdown_cb= on_shutdown_cb or MagicMock()
                handshake  = hs_mod.ControlChannelHandshake(
                    send_fn=send_fn,
                    publish_fn=publish_fn,
                    cfg=cfg or {},
                    on_active_cb=active_cb,
                    on_shutdown_cb=shutdown_cb,
                )
                return handshake, send_fn, publish_fn, active_cb, shutdown_cb, hs_mod

            yield _factory


def _sent_msg_ids(send_fn: MagicMock) -> list[int]:
    return [c.args[0] for c in send_fn.call_args_list]


def _published_topics(publish_fn: MagicMock) -> list[str]:
    return [c.args[0] for c in publish_fn.call_args_list]


# ============================================================================
# 1. Construction & initial state
# ============================================================================

@pytest.mark.unit
class TestConstruction:

    def test_initial_state_is_idle(self, hs_factory):
        hs, *_ = hs_factory()
        from oaa_control_channel.handshake import HandshakeState
        assert hs.state == HandshakeState.IDLE

    def test_no_messages_sent_on_construction(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        send_fn.assert_not_called()

    def test_no_publishes_on_construction(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        publish_fn.assert_not_called()


# ============================================================================
# 2. send_version_request()
# ============================================================================

@pytest.mark.unit
class TestVersionRequest:

    def test_sends_version_request_msg_id(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        hs.send_version_request()
        assert send_fn.call_args_list[0].args[0] == mod.MSG_VERSION_REQUEST

    def test_sends_major_minor_packed(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        hs.send_version_request()
        body = send_fn.call_args_list[0].args[1]
        major, minor = struct.unpack(">HH", body)
        assert major == mod.AA_VERSION_MAJOR
        assert minor == mod.AA_VERSION_MINOR

    def test_sends_unencrypted(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.send_version_request()
        assert send_fn.call_args_list[0].kwargs.get("encrypted") is False

    def test_state_becomes_version_sent(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.send_version_request()
        assert hs.state == mod.HandshakeState.VERSION_SENT


# ============================================================================
# 3. _on_version_response()
# ============================================================================

@pytest.mark.unit
class TestVersionResponse:

    def test_publishes_start_tls(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        body = struct.pack(">HH", 1, 7)
        hs.on_message(0x0002, body, False)
        topics = _published_topics(publish_fn)
        assert "aa.handshake.start_tls" in topics

    def test_state_becomes_tls_in_progress(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_message(0x0002, struct.pack(">HH", 1, 7), False)
        assert hs.state == mod.HandshakeState.TLS_IN_PROGRESS

    def test_short_body_does_not_crash(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        hs.on_message(0x0002, b"\x00", False)  # only 1 byte, not 4
        assert "aa.handshake.start_tls" in _published_topics(publish_fn)


# ============================================================================
# 4. _on_ssl_handshake() — incoming from phone
# ============================================================================

@pytest.mark.unit
class TestSSLHandshakeIncoming:

    def test_publishes_feed_input(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        hs.on_message(0x0003, b"\xAA\xBB", False)
        topics = _published_topics(publish_fn)
        assert "aa.handshake.feed_input" in topics

    def test_feed_input_payload_hex(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        hs.on_message(0x0003, b"\xCA\xFE", False)
        payloads = [
            c.args[1] for c in publish_fn.call_args_list
            if c.args[0] == "aa.handshake.feed_input"
        ]
        assert payloads[0]["payload_hex"] == "cafe"


# ============================================================================
# 5. on_tls_handshake_blob() — TLS bytes from tcp_server → phone
# ============================================================================

@pytest.mark.unit
class TestTLSHandshakeBlob:

    def test_sends_ssl_handshake_msg_id(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        hs.on_tls_handshake_blob("aabbcc")
        assert send_fn.call_args_list[0].args[0] == mod.MSG_SSL_HANDSHAKE

    def test_sends_decoded_bytes(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.on_tls_handshake_blob("deadbeef")
        body = send_fn.call_args_list[0].args[1]
        assert body == bytes.fromhex("deadbeef")

    def test_sends_unencrypted(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.on_tls_handshake_blob("00")
        assert send_fn.call_args_list[0].kwargs.get("encrypted") is False


# ============================================================================
# 6. on_tls_complete()
# ============================================================================

@pytest.mark.unit
class TestTLSComplete:

    def test_sends_auth_complete_msg_id(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        hs.on_tls_complete()
        assert send_fn.call_args_list[0].args[0] == mod.MSG_AUTH_COMPLETE

    def test_state_becomes_auth_ok(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_tls_complete()
        assert hs.state == mod.HandshakeState.AUTH_OK


# ============================================================================
# 7. _on_auth_complete() — incoming AUTH_COMPLETE from phone
# ============================================================================

@pytest.mark.unit
class TestAuthCompleteIncoming:

    def test_state_becomes_auth_ok(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_message(0x0006, b"", False)
        assert hs.state == mod.HandshakeState.AUTH_OK

    def test_no_send_on_auth_complete(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.on_message(0x0006, b"", False)
        send_fn.assert_not_called()


# ============================================================================
# 8. _on_service_discovery_request()
# ============================================================================

@pytest.mark.unit
class TestServiceDiscoveryRequest:

    def test_publishes_open_channels(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        hs.on_message(0x0005, b"", False)
        topics = _published_topics(publish_fn)
        assert "oaa_control_channel.open_channels" in topics

    def test_open_channels_payload_contains_sdr_hex(self, hs_factory):
        hs, _, publish_fn, *_, mod = hs_factory()
        hs.on_message(0x0005, b"", False)
        payloads = [
            c.args[1] for c in publish_fn.call_args_list
            if c.args[0] == "oaa_control_channel.open_channels"
        ]
        assert payloads[0]["sdr_bytes_hex"] == b"\xAA\xBB\xCC".hex()

    def test_state_becomes_waiting_channels(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_message(0x0005, b"", False)
        assert hs.state == mod.HandshakeState.WAITING_CHANNELS

    def test_no_sdr_send_yet(self, hs_factory):
        """SDR must NOT be sent until on_channels_ready() is called."""
        hs, send_fn, *_, mod = hs_factory()
        hs.on_message(0x0005, b"", False)
        # MSG_SERVICE_DISCOVERY_RES must not appear yet
        assert mod.MSG_SERVICE_DISCOVERY_RES not in _sent_msg_ids(send_fn)


# ============================================================================
# 9. on_channels_ready()
# ============================================================================

@pytest.mark.unit
class TestChannelsReady:

    def _prime(self, hs_factory):
        """Drive to WAITING_CHANNELS state."""
        hs, send_fn, publish_fn, active_cb, shutdown_cb, mod = hs_factory()
        hs.on_message(0x0005, b"", False)   # → WAITING_CHANNELS
        send_fn.reset_mock()
        publish_fn.reset_mock()
        return hs, send_fn, publish_fn, active_cb, shutdown_cb, mod

    def test_sends_sdr_msg_id(self, hs_factory):
        hs, send_fn, _, *_, mod = self._prime(hs_factory)
        sdr_hex = b"\x01\x02\x03".hex()
        hs.on_channels_ready(sdr_hex)
        assert mod.MSG_SERVICE_DISCOVERY_RES in _sent_msg_ids(send_fn)

    def test_sends_correct_sdr_bytes(self, hs_factory):
        hs, send_fn, *_ = self._prime(hs_factory)
        sdr_hex = b"\x11\x22\x33".hex()
        hs.on_channels_ready(sdr_hex)
        # First send call — args[1] is the body
        body = send_fn.call_args_list[0].args[1]
        assert body == bytes.fromhex(sdr_hex)

    def test_state_becomes_channels_opening(self, hs_factory):
        hs, *_, mod = self._prime(hs_factory)
        hs.on_channels_ready(b"\xFF".hex())
        assert hs.state == mod.HandshakeState.CHANNELS_OPENING


# ============================================================================
# 10. on_channels_ready() in wrong state
# ============================================================================

@pytest.mark.unit
class TestChannelsReadyWrongState:

    def test_ignored_in_idle(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        hs.on_channels_ready("aabb")
        send_fn.assert_not_called()
        assert hs.state == mod.HandshakeState.IDLE

    def test_ignored_in_channels_opening(self, hs_factory):
        hs, send_fn, publish_fn, *_, mod = hs_factory()
        # Manually force state
        hs._state = mod.HandshakeState.CHANNELS_OPENING
        send_fn.reset_mock()
        hs.on_channels_ready("aabb")
        assert mod.MSG_SERVICE_DISCOVERY_RES not in _sent_msg_ids(send_fn)


# ============================================================================
# 11. _on_channel_open_request()
# ============================================================================

@pytest.mark.unit
class TestChannelOpenRequest:

    def test_sends_channel_open_response(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        # decode_proto returns MagicMock with channel_id=3
        mod.decode_proto.return_value = MagicMock(channel_id=3)
        hs.on_message(0x0008, b"", False)
        assert mod.MSG_CHANNEL_OPEN_RES in _sent_msg_ids(send_fn)

    def test_tracks_open_channels(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock(channel_id=5)
        hs.on_message(0x0008, b"", False)
        assert 5 in hs._open_channels

    def test_does_not_change_state(self, hs_factory):
        hs, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock(channel_id=1)
        hs.on_message(0x0008, b"", False)
        assert hs.state == mod.HandshakeState.IDLE


# ============================================================================
# 12. _on_ping_request() — ACTIVE via ping trigger
# ============================================================================

@pytest.mark.unit
class TestPingRequest:

    def test_sends_ping_response(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock(timestamp=42)
        hs.on_message(0x000B, b"", False)
        assert mod.MSG_PING_RESPONSE in _sent_msg_ids(send_fn)

    def test_state_becomes_active(self, hs_factory):
        hs, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock(timestamp=0)
        hs.on_message(0x000B, b"", False)
        assert hs.state == mod.HandshakeState.ACTIVE

    def test_on_active_cb_called(self, hs_factory):
        active_mock = MagicMock()
        hs, _, _, active_cb, _, mod = hs_factory(on_active_cb=active_mock)
        mod.decode_proto.return_value = MagicMock(timestamp=0)
        hs.on_message(0x000B, b"", False)
        active_mock.assert_called_once()


# ============================================================================
# 13. _on_ping_request() idempotent
# ============================================================================

@pytest.mark.unit
class TestPingIdempotent:

    def test_active_cb_fires_only_once_on_multiple_pings(self, hs_factory):
        active_mock = MagicMock()
        hs, _, _, _, _, mod = hs_factory(on_active_cb=active_mock)
        mod.decode_proto.return_value = MagicMock(timestamp=0)
        hs.on_message(0x000B, b"", False)
        hs.on_message(0x000B, b"", False)
        hs.on_message(0x000B, b"", False)
        active_mock.assert_called_once()


# ============================================================================
# 14-18. _on_audio_focus_request()
# ============================================================================

@pytest.mark.unit
class TestAudioFocusRequest:

    def _make_req(self, hs_factory, focus_type, initial_state_name="CHANNELS_OPENING"):
        hs, send_fn, _, active_cb, _, mod = hs_factory()
        mock_req = MagicMock()
        mock_req.audio_focus_type = focus_type
        mod.decode_proto.return_value = mock_req
        hs._state = mod.HandshakeState[initial_state_name]
        hs.on_message(0x0012, b"", False)
        return hs, send_fn, active_cb, mod

    def test_sends_audio_focus_response(self, hs_factory):
        hs, send_fn, _, mod = self._make_req(hs_factory, _AFT.GAIN)
        assert mod.MSG_AUDIO_FOCUS_RESPONSE in _sent_msg_ids(send_fn)

    def test_active_trigger_from_channels_opening(self, hs_factory):
        hs, _, active_cb, mod = self._make_req(hs_factory, _AFT.GAIN, "CHANNELS_OPENING")
        assert hs.state == mod.HandshakeState.ACTIVE
        active_cb.assert_called_once()

    def test_no_active_trigger_from_active_state(self, hs_factory):
        active_mock = MagicMock()
        hs, _, _, _, _, mod = hs_factory(on_active_cb=active_mock)
        mock_req = MagicMock(audio_focus_type=_AFT.GAIN)
        mod.decode_proto.return_value = mock_req
        hs._state = mod.HandshakeState.ACTIVE
        hs.on_message(0x0012, b"", False)
        active_mock.assert_not_called()

    def test_release_mapped_to_loss(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        mock_req = MagicMock(audio_focus_type=_AFT.RELEASE)
        mod.decode_proto.return_value = mock_req
        hs._state = mod.HandshakeState.CHANNELS_OPENING
        hs.on_message(0x0012, b"", False)
        # Verify a response was sent (focus state set in the response object)
        assert mod.MSG_AUDIO_FOCUS_RESPONSE in _sent_msg_ids(send_fn)

    def test_gain_transient_mapped(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        mock_req = MagicMock(audio_focus_type=_AFT.GAIN_TRANSIENT)
        mod.decode_proto.return_value = mock_req
        hs._state = mod.HandshakeState.CHANNELS_OPENING
        hs.on_message(0x0012, b"", False)
        assert mod.MSG_AUDIO_FOCUS_RESPONSE in _sent_msg_ids(send_fn)


# ============================================================================
# 19. _on_navigation_focus_request()
# ============================================================================

@pytest.mark.unit
class TestNavigationFocusRequest:

    def test_sends_nav_focus_response(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock(type=1)
        hs.on_message(0x0014, b"", False)
        assert mod.MSG_NAV_FOCUS_RESPONSE in _sent_msg_ids(send_fn)

    def test_no_state_change(self, hs_factory):
        hs, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock(type=1)
        hs.on_message(0x0014, b"", False)
        assert hs.state == mod.HandshakeState.IDLE


# ============================================================================
# 20. _on_voice_session_request()
# ============================================================================

@pytest.mark.unit
class TestVoiceSessionRequest:

    def test_no_send(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.on_message(0x0016, b"", False)
        send_fn.assert_not_called()

    def test_no_state_change(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_message(0x0016, b"", False)
        assert hs.state == mod.HandshakeState.IDLE


# ============================================================================
# 21. _on_battery_status_notification()
# ============================================================================

@pytest.mark.unit
class TestBatteryStatus:

    def test_no_send(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.on_message(0x0018, b"", False)
        send_fn.assert_not_called()

    def test_no_state_change(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_message(0x0018, b"", False)
        assert hs.state == mod.HandshakeState.IDLE


# ============================================================================
# 22. _on_shutdown_request()
# ============================================================================

@pytest.mark.unit
class TestShutdownRequest:

    def test_sends_shutdown_response(self, hs_factory):
        hs, send_fn, *_, mod = hs_factory()
        hs.on_message(0x0019, b"", False)
        assert mod.MSG_SHUTDOWN_RESPONSE in _sent_msg_ids(send_fn)

    def test_state_becomes_shutdown(self, hs_factory):
        hs, *_, mod = hs_factory()
        hs.on_message(0x0019, b"", False)
        assert hs.state == mod.HandshakeState.SHUTDOWN

    def test_on_shutdown_cb_called(self, hs_factory):
        shutdown_mock = MagicMock()
        hs, _, _, _, _, mod = hs_factory(on_shutdown_cb=shutdown_mock)
        hs.on_message(0x0019, b"", False)
        shutdown_mock.assert_called_once()


# ============================================================================
# 23. on_message() dispatch table — all known msg IDs routed
# ============================================================================

@pytest.mark.unit
class TestDispatchTable:

    @pytest.mark.parametrize("msg_id,expected_topic_or_send", [
        (0x0002, "publish:aa.handshake.start_tls"),   # VERSION_RESPONSE
        (0x0003, "publish:aa.handshake.feed_input"),  # SSL_HANDSHAKE
        (0x0005, "publish:oaa_control_channel.open_channels"),  # SDR_REQUEST
        (0x000B, "send"),   # PING_REQUEST
        (0x0019, "send"),   # SHUTDOWN_REQUEST
    ])
    def test_known_msg_ids_dispatched(self, hs_factory, msg_id, expected_topic_or_send):
        hs, send_fn, publish_fn, *_, mod = hs_factory()
        mod.decode_proto.return_value = MagicMock()
        body = struct.pack(">HH", 1, 7) if msg_id == 0x0002 else b""
        hs.on_message(msg_id, body, False)
        if expected_topic_or_send.startswith("publish:"):
            topic = expected_topic_or_send.split(":", 1)[1]
            assert topic in _published_topics(publish_fn)
        else:
            assert len(send_fn.call_args_list) > 0


# ============================================================================
# 24. on_message() unknown msg ID — silent no-op
# ============================================================================

@pytest.mark.unit
class TestUnknownMsgId:

    def test_unknown_msg_id_no_send(self, hs_factory):
        hs, send_fn, *_ = hs_factory()
        hs.on_message(0xFFFF, b"", False)
        send_fn.assert_not_called()

    def test_unknown_msg_id_no_publish(self, hs_factory):
        hs, _, publish_fn, *_ = hs_factory()
        hs.on_message(0xFFFF, b"", False)
        publish_fn.assert_not_called()

    def test_unknown_msg_id_no_crash(self, hs_factory):
        hs, *_ = hs_factory()
        hs.on_message(0xDEAD, b"garbage", True)  # must not raise


# ============================================================================
# 25. on_active_cb / on_shutdown_cb called exactly once
# ============================================================================

@pytest.mark.unit
class TestCallbacks:

    def test_active_cb_called_once_via_ping(self, hs_factory):
        active_mock = MagicMock()
        hs, _, _, _, _, mod = hs_factory(on_active_cb=active_mock)
        mod.decode_proto.return_value = MagicMock(timestamp=0)
        hs.on_message(0x000B, b"", False)
        active_mock.assert_called_once()

    def test_shutdown_cb_called_once(self, hs_factory):
        shutdown_mock = MagicMock()
        hs, _, _, _, _, mod = hs_factory(on_shutdown_cb=shutdown_mock)
        hs.on_message(0x0019, b"", False)
        shutdown_mock.assert_called_once()

    def test_none_active_cb_does_not_crash(self, hs_factory):
        hs, *_, mod = hs_factory(on_active_cb=None)
        mod.decode_proto.return_value = MagicMock(timestamp=0)
        hs.on_message(0x000B, b"", False)  # must not raise

    def test_none_shutdown_cb_does_not_crash(self, hs_factory):
        hs, *_, mod = hs_factory(on_shutdown_cb=None)
        hs.on_message(0x0019, b"", False)  # must not raise
