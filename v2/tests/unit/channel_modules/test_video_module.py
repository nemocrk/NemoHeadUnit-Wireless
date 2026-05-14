"""
test_video_module.py — Unit tests for VideoModule.

Coverage targets (§1.3 TEST_SUITE_ARCHITECTURE):
  1. AV handshake: SetupRequest → SetupResponse + VideoFocusIndication(PROJECTED)
  2. State machine: IDLE → SETUP → OPEN → PLAYING → STOPPED
  3. ChannelOpenRequest → ChannelOpenResponse
  4. StartIndication parses session_id correctly
  5. StopIndication resets session_id and transitions STOPPED
  6. AV_MEDIA_INDICATION: ACK sent, video.frame published with is_config=True
  7. AV_MEDIA_WITH_TIMESTAMP: ACK sent, video.frame published with is_config=False
  8. VideoFocusRequest → VideoFocusIndication(PROJECTED)
  9. MediaAck session_id matches current session
  10. publish_frames=False suppresses video.frame bus publish
  11. _publish_video_frame encodes data as base64 correctly
  12. on_aa_session_shutdown resets session_id and state
  13. on_channel_open / on_channel_close reset session_id and state
  14. max_unacked config reflected in SetupResponse
  15. MediaWithTimestamp accepted even if state != PLAYING (promotes to PLAYING)
  16. Empty body in AV_MEDIA_INDICATION does not publish video.frame
  17. _init reads codec from channel_config
  18. _cleanup resets state to IDLE

Test strategy:
  - No subprocess, no av, no PyAV — VideoModule has no external process.
  - BusClient, ConfigClient, get_logger are mocked via patch before __init__.
  - Proto classes are used for real (pure Python).
  - send_frame() is spied via mock bus.publish to capture outgoing AA frames.
"""

from __future__ import annotations

import base64
import struct
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Proto enum aliases
# ---------------------------------------------------------------------------
from oaa.av.AVChannelMessageIdsEnum_pb2   import AVChannelMessage   # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage    # noqa: E402
from oaa.av.MediaCodecTypeEnum_pb2         import MediaCodecType    # noqa: E402
from oaa.video.VideoFocusModeEnum_pb2      import VideoFocusMode    # noqa: E402

_MSG_SETUP_REQUEST  = AVChannelMessage.SETUP_REQUEST
_MSG_SETUP_RESPONSE = AVChannelMessage.SETUP_RESPONSE
_MSG_OPEN_REQUEST   = ControlMessage.CHANNEL_OPEN_REQUEST
_MSG_OPEN_RESPONSE  = ControlMessage.CHANNEL_OPEN_RESPONSE
_MSG_START          = AVChannelMessage.START_INDICATION
_MSG_STOP           = AVChannelMessage.STOP_INDICATION
_MSG_MEDIA          = AVChannelMessage.AV_MEDIA_INDICATION
_MSG_MEDIA_TS       = AVChannelMessage.AV_MEDIA_WITH_TIMESTAMP_INDICATION
_MSG_ACK            = AVChannelMessage.AV_MEDIA_ACK_INDICATION
_MSG_FOCUS_REQ      = AVChannelMessage.VIDEO_FOCUS_REQUEST
_MSG_FOCUS_IND      = AVChannelMessage.VIDEO_FOCUS_INDICATION

_CODEC_H264_BP = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP
_FOCUS_PROJECTED = VideoFocusMode.Enum.PROJECTED


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def _make_mock_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish   = MagicMock()
    bus.subscribe = MagicMock()
    bus.stop      = MagicMock()
    bus.start     = MagicMock(return_value=MagicMock())
    return bus


def _build_video_module(
    channel_id: int = 1,
    channel_config: dict | None = None,
    max_unacked: int = 1,
    publish_frames: bool = True,
) -> tuple["VideoModule", MagicMock]:
    """
    Build a fully mocked VideoModule.
    Returns (module, mock_bus).
    """
    import channel_modules.base_channel_module as bcm_mod

    mock_bus = _make_mock_bus()
    fake_cli = types.SimpleNamespace(
        module_name="video",
        channel_id=channel_id,
        sdr_bytes_hex="",
    )

    with (
        patch.object(bcm_mod, "_CLI_ARGS", fake_cli),
        patch("channel_modules.base_channel_module.BusClient",   return_value=mock_bus),
        patch("channel_modules.base_channel_module.ConfigClient", return_value=MagicMock()),
        patch("channel_modules.base_channel_module.get_logger",   return_value=MagicMock()),
        patch("channel_modules.base_channel_module.channel_config_from_sdr", return_value=None),
    ):
        from channel_modules.video.main import VideoModule
        module = VideoModule()

    module.channel_config = channel_config or {"channel_id": channel_id, "av_channel": {}}
    module._config["max_unacked"]    = max_unacked
    module._config["publish_frames"] = publish_frames
    return module, mock_bus


# ---------------------------------------------------------------------------
# Proto frame builders
# ---------------------------------------------------------------------------

def _setup_request_body() -> bytes:
    from oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
    req = AVChannelSetupRequest()
    req.config_index = 0
    return req.SerializeToString()


def _open_request_body() -> bytes:
    from oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
    return ChannelOpenRequest().SerializeToString()


def _start_indication_body(session: int = 1) -> bytes:
    from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
    msg = AVChannelStartIndication()
    msg.session = session
    return msg.SerializeToString()


def _stop_indication_body() -> bytes:
    from oaa.av.AVChannelStopIndicationMessage_pb2 import AVChannelStopIndication
    return AVChannelStopIndication().SerializeToString()


def _focus_request_body() -> bytes:
    from oaa.video.VideoFocusRequestMessage_pb2 import VideoFocusRequest
    return VideoFocusRequest().SerializeToString()


def _media_with_timestamp_body(ts_us: int, data: bytes) -> bytes:
    return struct.pack(">Q", ts_us) + data


def _annexb_sps_pps() -> bytes:
    """Minimal AnnexB SPS+PPS NAL units (not valid H.264 — enough for testing the pipe)."""
    sps = b"\x00\x00\x00\x01\x67\x42\x00\x1e"
    pps = b"\x00\x00\x00\x01\x68\xce\x38\x80"
    return sps + pps


# Helper: collect all aa.frame.send calls with a given message_id
def _frame_calls(bus: MagicMock, message_id: int) -> list:
    return [
        c for c in bus.publish.call_args_list
        if c.args[0] == "aa.frame.send" and c.args[1].get("message_id") == message_id
    ]


# ============================================================================
# TEST CLASSES
# ============================================================================


class TestAVHandshake:
    """SetupRequest / OpenRequest response correctness."""

    @pytest.mark.unit
    def test_setup_request_sends_setup_response(self):
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        assert _frame_calls(bus, _MSG_SETUP_RESPONSE)

    @pytest.mark.unit
    def test_setup_request_sends_focus_indication(self):
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        assert _frame_calls(bus, _MSG_FOCUS_IND)

    @pytest.mark.unit
    def test_setup_response_contains_max_unacked(self):
        from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
        module, bus = _build_video_module(max_unacked=5)
        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        calls = _frame_calls(bus, _MSG_SETUP_RESPONSE)
        assert calls
        resp = AVChannelSetupResponse()
        resp.ParseFromString(bytes.fromhex(calls[-1].args[1]["payload_hex"]))
        assert resp.max_unacked == 5

    @pytest.mark.unit
    def test_setup_request_sets_state_setup(self):
        module, _ = _build_video_module()
        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        assert module._state == "SETUP"

    @pytest.mark.unit
    def test_open_request_sends_open_response(self):
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_OPEN_REQUEST, False, _open_request_body())
        assert _frame_calls(bus, _MSG_OPEN_RESPONSE)

    @pytest.mark.unit
    def test_open_request_sets_state_open(self):
        module, _ = _build_video_module()
        module.on_frame(1, _MSG_OPEN_REQUEST, False, _open_request_body())
        assert module._state == "OPEN"

    @pytest.mark.unit
    def test_focus_indication_focus_mode_is_projected(self):
        from oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        calls = _frame_calls(bus, _MSG_FOCUS_IND)
        assert calls
        ind = VideoFocusIndication()
        ind.ParseFromString(bytes.fromhex(calls[-1].args[1]["payload_hex"]))
        assert ind.focus_mode == _FOCUS_PROJECTED


class TestStateMachine:
    """Full state-machine cycle and edge transitions."""

    @pytest.mark.unit
    def test_full_handshake_state_sequence(self):
        module, _ = _build_video_module()
        assert module._state == "IDLE"

        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        assert module._state == "SETUP"

        module.on_frame(1, _MSG_OPEN_REQUEST, False, _open_request_body())
        assert module._state == "OPEN"

        module.on_frame(1, _MSG_START, False, _start_indication_body(session=3))
        assert module._state == "PLAYING"
        assert module._session_id == 3

        module.on_frame(1, _MSG_STOP, False, _stop_indication_body())
        assert module._state == "STOPPED"
        assert module._session_id == 0

    @pytest.mark.unit
    def test_start_indication_parses_session_zero(self):
        """session_id=0 is a valid AA session — must NOT be treated as unstarted."""
        module, _ = _build_video_module()
        module.on_frame(1, _MSG_START, False, _start_indication_body(session=0))
        assert module._session_id == 0
        assert module._state == "PLAYING"

    @pytest.mark.unit
    def test_state_change_publishes_video_state(self):
        module, bus = _build_video_module()
        bus.publish.reset_mock()
        module.on_frame(1, _MSG_SETUP_REQUEST, False, _setup_request_body())
        state_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "video.state"
        ]
        assert state_calls
        assert state_calls[0].args[1]["state"] == "SETUP"

    @pytest.mark.unit
    def test_repeated_same_state_no_duplicate_publish(self):
        module, bus = _build_video_module()
        module._state = "PLAYING"
        bus.publish.reset_mock()
        # calling _set_state with the same value must be a no-op
        module._set_state("PLAYING")
        state_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "video.state"
        ]
        assert len(state_calls) == 0

    @pytest.mark.unit
    def test_cleanup_resets_state_to_idle(self):
        module, _ = _build_video_module()
        module._state = "PLAYING"
        module._cleanup()
        assert module._state == "IDLE"


class TestMediaHandling:
    """AV_MEDIA_INDICATION and AV_MEDIA_WITH_TIMESTAMP."""

    @pytest.mark.unit
    def test_media_indication_sends_ack(self):
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_MEDIA, False, _annexb_sps_pps())
        assert _frame_calls(bus, _MSG_ACK)

    @pytest.mark.unit
    def test_media_indication_publishes_video_frame_is_config(self):
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_MEDIA, False, _annexb_sps_pps())
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert vf_calls
        assert vf_calls[-1].args[1]["is_config"] is True

    @pytest.mark.unit
    def test_media_indication_empty_body_no_video_frame(self):
        module, bus = _build_video_module()
        module.on_frame(1, _MSG_MEDIA, False, b"")
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert len(vf_calls) == 0

    @pytest.mark.unit
    def test_media_with_timestamp_sends_ack(self):
        module, bus = _build_video_module()
        module._state = "PLAYING"
        body = _media_with_timestamp_body(500_000, b"\x00\x01\x02\x03" * 16)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        assert _frame_calls(bus, _MSG_ACK)

    @pytest.mark.unit
    def test_media_with_timestamp_publishes_video_frame_not_config(self):
        module, bus = _build_video_module()
        module._state = "PLAYING"
        body = _media_with_timestamp_body(500_000, b"\xAB" * 32)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert vf_calls
        assert vf_calls[-1].args[1]["is_config"] is False

    @pytest.mark.unit
    def test_media_with_timestamp_payload_base64_correct(self):
        module, bus = _build_video_module()
        module._state = "PLAYING"
        raw = b"\xDE\xAD\xBE\xEF" * 8
        body = _media_with_timestamp_body(1_000_000, raw)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert vf_calls
        decoded = base64.b64decode(vf_calls[-1].args[1]["data_b64"])
        assert decoded == raw

    @pytest.mark.unit
    def test_media_with_timestamp_ts_us_passed_through(self):
        module, bus = _build_video_module()
        module._state = "PLAYING"
        ts = 123_456_789
        body = _media_with_timestamp_body(ts, b"\x01" * 16)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert vf_calls
        assert vf_calls[-1].args[1]["ts_us"] == ts

    @pytest.mark.unit
    def test_media_with_timestamp_from_idle_promotes_to_playing(self):
        """VideoModule must accept media even outside strict OPEN state."""
        module, _ = _build_video_module()
        module._state = "IDLE"
        body = _media_with_timestamp_body(0, b"\x00" * 32)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        assert module._state == "PLAYING"

    @pytest.mark.unit
    def test_publish_frames_false_suppresses_video_frame(self):
        module, bus = _build_video_module(publish_frames=False)
        module._state = "PLAYING"
        body = _media_with_timestamp_body(0, b"\xFF" * 16)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert len(vf_calls) == 0

    @pytest.mark.unit
    def test_video_frame_payload_contains_codec(self):
        module, bus = _build_video_module()
        module._state = "PLAYING"
        body = _media_with_timestamp_body(0, b"\x01" * 8)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        vf_calls = [c for c in bus.publish.call_args_list if c.args[0] == "video.frame"]
        assert vf_calls
        assert vf_calls[-1].args[1]["codec"] == _CODEC_H264_BP


class TestMediaAck:
    """MediaAck correctness."""

    @pytest.mark.unit
    def test_ack_session_id_matches_current(self):
        from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
        module, bus = _build_video_module()
        module._session_id = 55
        module._state = "PLAYING"
        body = _media_with_timestamp_body(0, b"\x00" * 16)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        ack_calls = _frame_calls(bus, _MSG_ACK)
        assert ack_calls
        ack = AVMediaAckIndication()
        ack.ParseFromString(bytes.fromhex(ack_calls[-1].args[1]["payload_hex"]))
        assert ack.session_id == 55

    @pytest.mark.unit
    def test_ack_count_is_one(self):
        from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
        module, bus = _build_video_module()
        module._state = "PLAYING"
        body = _media_with_timestamp_body(0, b"\x00" * 16)
        module.on_frame(1, _MSG_MEDIA_TS, False, body)
        ack_calls = _frame_calls(bus, _MSG_ACK)
        ack = AVMediaAckIndication()
        ack.ParseFromString(bytes.fromhex(ack_calls[-1].args[1]["payload_hex"]))
        assert ack.ack_count == 1


class TestVideoFocus:
    """VideoFocusRequest → VideoFocusIndication."""

    @pytest.mark.unit
    def test_focus_request_sends_indication(self):
        module, bus = _build_video_module()
        bus.publish.reset_mock()
        module.on_frame(1, _MSG_FOCUS_REQ, False, _focus_request_body())
        assert _frame_calls(bus, _MSG_FOCUS_IND)

    @pytest.mark.unit
    def test_focus_indication_is_projected_on_request(self):
        from oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication
        module, bus = _build_video_module()
        bus.publish.reset_mock()
        module.on_frame(1, _MSG_FOCUS_REQ, False, _focus_request_body())
        calls = _frame_calls(bus, _MSG_FOCUS_IND)
        ind = VideoFocusIndication()
        ind.ParseFromString(bytes.fromhex(calls[-1].args[1]["payload_hex"]))
        assert ind.focus_mode == _FOCUS_PROJECTED


class TestSessionLifecycle:
    """Session shutdown and channel open/close."""

    @pytest.mark.unit
    def test_session_shutdown_resets_session_id(self):
        module, _ = _build_video_module()
        module._session_id = 99
        module.on_aa_session_shutdown("aa.session.shutdown", {})
        assert module._session_id == 0

    @pytest.mark.unit
    def test_session_shutdown_resets_state_to_idle(self):
        module, _ = _build_video_module()
        module._state = "PLAYING"
        module.on_aa_session_shutdown("aa.session.shutdown", {})
        assert module._state == "IDLE"

    @pytest.mark.unit
    def test_channel_open_resets_session_and_state(self):
        module, _ = _build_video_module()
        module._session_id = 10
        module._state = "PLAYING"
        module.on_channel_open(1, {})
        assert module._session_id == 0
        assert module._state == "IDLE"

    @pytest.mark.unit
    def test_channel_close_resets_session_and_state(self):
        module, _ = _build_video_module()
        module._session_id = 7
        module._state = "PLAYING"
        module.on_channel_close(1)
        assert module._session_id == 0
        assert module._state == "IDLE"


class TestInitAndConfig:
    """_init() reads codec from channel_config; config keys work."""

    @pytest.mark.unit
    def test_init_reads_codec_from_channel_config(self):
        from oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
        codec_vp9 = MediaCodecType.MEDIA_CODEC_VIDEO_VP9
        channel_config = {
            "channel_id": 1,
            "av_channel": {
                "video_configs": [{"codec": codec_vp9, "resolution": 0}]
            },
        }
        module, _ = _build_video_module(channel_config=channel_config)
        module._init()
        assert module._codec_sdr == codec_vp9
        assert module._codec     == codec_vp9

    @pytest.mark.unit
    def test_init_no_video_configs_uses_default(self):
        module, _ = _build_video_module()
        module._init()
        assert module._codec_sdr == _CODEC_H264_BP
        assert module._codec     == _CODEC_H264_BP

    @pytest.mark.unit
    def test_init_none_channel_config_uses_default(self):
        module, _ = _build_video_module()
        module.channel_config = None
        module._init()
        assert module._codec == _CODEC_H264_BP
