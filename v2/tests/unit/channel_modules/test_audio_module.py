"""
test_audio_module.py — Unit tests for AudioModule.

Coverage targets (§1.3 TEST_SUITE_ARCHITECTURE):
  1. AV handshake sequence: SetupRequest → SetupResponse, ChannelOpenRequest → OpenResponse
  2. State machine transitions: IDLE → SETUP → OPEN → PLAYING → STOPPED
  3. StartIndication / StopIndication handlers
  4. MediaAck is sent for every media frame
  5. AV_MEDIA_INDICATION codec_data detection (2-byte ASC) and store/update
  6. PCM codec pass-through in _write_audio (mocking pacat stdin)
  7. Prebuffer accumulation and flush at threshold
  8. _is_ready() gates on self._proc
  9. _cleanup() resets codec_data, prebuffer and closes stream
  10. on_audio_sink_selected — no reopen if sink unchanged, reopen if changed
  11. on_aa_session_shutdown — session_id/codec_data/prebuffer reset
  12. on_channel_open / on_channel_close — session_id/prebuffer reset
  13. _normalise_audio_codec — int passthrough, string mapping, fallback
  14. _pcm_s16le_stats — peak, rms, zero_ratio correctness
  15. Config: max_unacked loaded and used in SetupResponse

Test strategy:
  - subprocess.Popen is replaced by a MagicMock whose .stdin is a MagicMock;
    this simulates a live pacat process without spawning one.
  - av (PyAV) codec is mocked at module level via sys.modules patching.
  - BusClient, ConfigClient, get_logger are mocked via patch before __init__.
  - Proto classes are used for real (they are pure Python, no C extension needed).
  - _CODEC_PCM / _CODEC_AAC_LC numeric values are read from the actual protos
    to keep tests stable across enum renaming.
"""

from __future__ import annotations

import struct
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub 'av' before importing AudioModule so we never need PyAV installed.
# AudioModule imports av at module-level; the stub must exist in sys.modules
# BEFORE the first import of channel_modules.audio.main.
# ---------------------------------------------------------------------------
if "av" not in sys.modules:
    _av_stub = types.ModuleType("av")
    _av_stub.Packet    = MagicMock()
    _av_stub.codec     = types.SimpleNamespace(Codec=MagicMock())
    sys.modules["av"]  = _av_stub


# ---------------------------------------------------------------------------
# Read real codec enum values from the proto so tests are resilient to renames
# ---------------------------------------------------------------------------
from oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType  # noqa: E402
_CODEC_PCM         = MediaCodecType.MEDIA_CODEC_AUDIO_PCM
_CODEC_AAC_LC      = MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC
_CODEC_AAC_LC_ADTS = MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC_ADTS

from oaa.av.AVChannelMessageIdsEnum_pb2  import AVChannelMessage   # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage   # noqa: E402

_MSG_SETUP_REQUEST   = AVChannelMessage.SETUP_REQUEST
_MSG_SETUP_RESPONSE  = AVChannelMessage.SETUP_RESPONSE
_MSG_OPEN_REQUEST    = ControlMessage.CHANNEL_OPEN_REQUEST
_MSG_OPEN_RESPONSE   = ControlMessage.CHANNEL_OPEN_RESPONSE
_MSG_START           = AVChannelMessage.START_INDICATION
_MSG_STOP            = AVChannelMessage.STOP_INDICATION
_MSG_MEDIA           = AVChannelMessage.AV_MEDIA_INDICATION
_MSG_MEDIA_TS        = AVChannelMessage.AV_MEDIA_WITH_TIMESTAMP_INDICATION
_MSG_ACK             = AVChannelMessage.AV_MEDIA_ACK_INDICATION


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


def _make_mock_proc(stdin_raises: Exception | None = None) -> MagicMock:
    """Return a mock subprocess.Popen that owns a writable stdin."""
    proc = MagicMock()
    proc.pid = 9999
    if stdin_raises:
        proc.stdin.write.side_effect = stdin_raises
    else:
        proc.stdin.write  = MagicMock()
        proc.stdin.flush  = MagicMock()
        proc.stdin.close  = MagicMock()
    return proc


def _build_audio_module(
    channel_id: int = 4,
    codec: int | None = None,
    proc: MagicMock | None = None,
    channel_config: dict | None = None,
) -> tuple["AudioModule", MagicMock]:
    """
    Build a fully mocked AudioModule.

    - BusClient, ConfigClient, get_logger are patched.
    - subprocess.Popen is patched so _open_stream() does not spawn a real process.
    - av is already stubbed in sys.modules.
    - channel_config is set directly after construction.
    - If proc is provided it is injected as self._proc (simulate open stream).
    Returns (module, mock_bus).
    """
    import channel_modules.base_channel_module as bcm_mod
    import channel_modules.audio.main as audio_mod

    mock_bus = _make_mock_bus()
    fake_cli = types.SimpleNamespace(
        module_name="channel_audio_4",
        channel_id=channel_id,
        sdr_bytes_hex="",
    )
    default_proc = proc if proc is not None else _make_mock_proc()

    with (
        patch.object(bcm_mod, "_CLI_ARGS", fake_cli),
        patch.object(audio_mod, "_CLI_ARGS", fake_cli, create=True),
        patch("channel_modules.base_channel_module.BusClient", return_value=mock_bus),
        patch("channel_modules.base_channel_module.ConfigClient", return_value=MagicMock()),
        patch("channel_modules.base_channel_module.get_logger", return_value=MagicMock()),
        patch("channel_modules.base_channel_module.channel_config_from_sdr", return_value=None),
        patch("subprocess.Popen", return_value=default_proc),
    ):
        from channel_modules.audio.main import AudioModule
        module = AudioModule()

    # Set channel_config and codec
    module.channel_config = channel_config or {"channel_id": channel_id, "av_channel": {}}
    if codec is not None:
        module._codec = codec

    # Inject a mock process to satisfy _is_ready()
    module._proc = default_proc
    return module, mock_bus


# ---------------------------------------------------------------------------
# Frame payload helpers (build real serialised proto bodies)
# ---------------------------------------------------------------------------

def _setup_request_body() -> bytes:
    from oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
    req = AVChannelSetupRequest()
    req.config_index = 0
    return req.SerializeToString()


def _open_request_body() -> bytes:
    # ChannelOpenRequest has no required fields
    from oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
    return ChannelOpenRequest().SerializeToString()


def _start_indication_body(session: int = 42) -> bytes:
    from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
    msg = AVChannelStartIndication()
    msg.session = session
    return msg.SerializeToString()


def _stop_indication_body() -> bytes:
    from oaa.av.AVChannelStopIndicationMessage_pb2 import AVChannelStopIndication
    return AVChannelStopIndication().SerializeToString()


def _media_with_timestamp_body(ts_us: int, pcm: bytes) -> bytes:
    """Build a compact AV_MEDIA_WITH_TIMESTAMP frame body."""
    return struct.pack(">Q", ts_us) + pcm


# ============================================================================
# TEST CLASSES
# ============================================================================


class TestReadiness:
    """_is_ready() and channel_config gate."""

    @pytest.mark.unit
    def test_is_ready_when_proc_set(self):
        module, _ = _build_audio_module()
        assert module._is_ready() is True

    @pytest.mark.unit
    def test_is_not_ready_when_proc_is_none(self):
        module, _ = _build_audio_module()
        module._proc = None
        assert module._is_ready() is False


class TestAVHandshake:
    """SetupRequest/Response and ChannelOpenRequest/Response."""

    @pytest.mark.unit
    def test_setup_request_sends_response(self):
        module, bus = _build_audio_module()
        module._config["max_unacked"] = 1
        module.on_frame(4, _MSG_SETUP_REQUEST, False, _setup_request_body())
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "aa.frame.send" in topics

    @pytest.mark.unit
    def test_setup_request_sets_state_setup(self):
        module, _ = _build_audio_module()
        module.on_frame(4, _MSG_SETUP_REQUEST, False, _setup_request_body())
        assert module._state == "SETUP"

    @pytest.mark.unit
    def test_setup_response_contains_max_unacked(self):
        module, bus = _build_audio_module()
        module._config["max_unacked"] = 3
        module.on_frame(4, _MSG_SETUP_REQUEST, False, _setup_request_body())
        # Find the aa.frame.send call and verify message_id == SETUP_RESPONSE
        frame_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "aa.frame.send"
        ]
        assert frame_calls, "aa.frame.send not published"
        payload = frame_calls[-1].args[1]
        assert payload["message_id"] == _MSG_SETUP_RESPONSE

    @pytest.mark.unit
    def test_open_request_sends_response(self):
        module, bus = _build_audio_module()
        module.on_frame(4, _MSG_OPEN_REQUEST, False, _open_request_body())
        frame_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "aa.frame.send"
        ]
        assert frame_calls
        payload = frame_calls[-1].args[1]
        assert payload["message_id"] == _MSG_OPEN_RESPONSE

    @pytest.mark.unit
    def test_open_request_sets_state_open(self):
        module, _ = _build_audio_module()
        module.on_frame(4, _MSG_OPEN_REQUEST, False, _open_request_body())
        assert module._state == "OPEN"


class TestStateMachine:
    """State transitions across the full AV handshake cycle."""

    @pytest.mark.unit
    def test_full_handshake_state_sequence(self):
        module, _ = _build_audio_module()
        assert module._state == "IDLE"

        module.on_frame(4, _MSG_SETUP_REQUEST, False, _setup_request_body())
        assert module._state == "SETUP"

        module.on_frame(4, _MSG_OPEN_REQUEST, False, _open_request_body())
        assert module._state == "OPEN"

        module.on_frame(4, _MSG_START, False, _start_indication_body(session=7))
        assert module._state == "PLAYING"
        assert module._session_id == 7

        module.on_frame(4, _MSG_STOP, False, _stop_indication_body())
        assert module._state == "STOPPED"
        assert module._session_id == 0

    @pytest.mark.unit
    def test_stop_preserves_codec_data(self):
        """StopIndication must NOT clear _aac_codec_data."""
        module, _ = _build_audio_module()
        module._aac_codec_data = b"\x12\x10"
        module.on_frame(4, _MSG_STOP, False, _stop_indication_body())
        assert module._aac_codec_data == b"\x12\x10"

    @pytest.mark.unit
    def test_stop_clears_prebuffer(self):
        module, _ = _build_audio_module()
        module._prebuffer = [b"data"]
        module._prebuffer_bytes = 4
        module.on_frame(4, _MSG_STOP, False, _stop_indication_body())
        assert module._prebuffer == []
        assert module._prebuffer_bytes == 0

    @pytest.mark.unit
    def test_start_indication_parses_session_id(self):
        module, _ = _build_audio_module()
        module.on_frame(4, _MSG_START, False, _start_indication_body(session=99))
        assert module._session_id == 99


class TestMediaAck:
    """MediaAck is sent for every media frame."""

    @pytest.mark.unit
    def test_ack_sent_on_media_with_timestamp(self):
        module, bus = _build_audio_module(codec=_CODEC_PCM)
        module._state = "PLAYING"
        # Use big prebuffer so flush does not attempt real write
        module._prebuffer_threshold = 10_000_000
        body = _media_with_timestamp_body(1000, b"\x00" * 64)
        module.on_frame(4, _MSG_MEDIA_TS, False, body)
        frame_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "aa.frame.send" and c.args[1]["message_id"] == _MSG_ACK
        ]
        assert len(frame_calls) >= 1

    @pytest.mark.unit
    def test_ack_sent_on_media_indication_codec_data(self):
        """codec_data frame (2-byte ASC) via AV_MEDIA_INDICATION must be ACKed."""
        module, bus = _build_audio_module(codec=_CODEC_AAC_LC)
        # Build a body with 8-byte ts header + 2-byte ASC
        body = b"\x00" * 8 + b"\x12\x10"
        module.on_frame(4, _MSG_MEDIA, False, body)
        frame_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "aa.frame.send" and c.args[1]["message_id"] == _MSG_ACK
        ]
        assert len(frame_calls) >= 1

    @pytest.mark.unit
    def test_ack_session_id_matches_current(self):
        module, bus = _build_audio_module(codec=_CODEC_PCM)
        module._session_id = 42
        module._state = "PLAYING"
        module._prebuffer_threshold = 10_000_000
        body = _media_with_timestamp_body(0, b"\x00" * 32)
        module.on_frame(4, _MSG_MEDIA_TS, False, body)
        from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
        ack_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "aa.frame.send" and c.args[1]["message_id"] == _MSG_ACK
        ]
        assert ack_calls
        ack_payload_hex = ack_calls[-1].args[1]["payload_hex"]
        ack_msg = AVMediaAckIndication()
        ack_msg.ParseFromString(bytes.fromhex(ack_payload_hex))
        assert ack_msg.session_id == 42


class TestCodecData:
    """AV_MEDIA_INDICATION codec_data (AudioSpecificConfig) detection."""

    @pytest.mark.unit
    def test_2byte_asc_stored_as_codec_data(self):
        module, _ = _build_audio_module(codec=_CODEC_AAC_LC)
        body = b"\x00" * 8 + b"\x12\x10"
        module.on_frame(4, _MSG_MEDIA, False, body)
        assert module._aac_codec_data == b"\x12\x10"

    @pytest.mark.unit
    def test_codec_data_updated_on_second_asc(self):
        module, _ = _build_audio_module(codec=_CODEC_AAC_LC)
        module._aac_codec_data = b"\x11\x90"
        body = b"\x00" * 8 + b"\x12\x10"
        module.on_frame(4, _MSG_MEDIA, False, body)
        assert module._aac_codec_data == b"\x12\x10"

    @pytest.mark.unit
    def test_2byte_pcm_not_treated_as_codec_data(self):
        """PCM codec: 2-byte body should NOT be stored as codec_data."""
        module, _ = _build_audio_module(codec=_CODEC_PCM)
        module._aac_codec_data = None
        # Build a 2-byte non-AAC body
        body = b"\x00" * 8 + b"\xAB\xCD"
        module.on_frame(4, _MSG_MEDIA, False, body)
        # For PCM codec, codec_data stays None
        assert module._aac_codec_data is None

    @pytest.mark.unit
    def test_codec_data_not_cleared_by_stop_indication(self):
        module, _ = _build_audio_module(codec=_CODEC_AAC_LC)
        module._aac_codec_data = b"\x12\x10"
        module._handle_stop_indication(b"")
        assert module._aac_codec_data == b"\x12\x10"


class TestPrebuffer:
    """PCM prebuffer accumulation and flush."""

    @pytest.mark.unit
    def test_pcm_not_written_until_threshold(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(codec=_CODEC_PCM, proc=proc)
        module._prebuffer_threshold = 100  # 100 bytes threshold
        module._state = "PLAYING"
        # Write 50 bytes of PCM (2-byte aligned, s16le)
        pcm = b"\x01\x00" * 25  # 50 bytes
        module._write_audio(pcm)
        proc.stdin.write.assert_not_called()
        assert module._prebuffer_bytes == 50

    @pytest.mark.unit
    def test_pcm_flushed_when_threshold_reached(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(codec=_CODEC_PCM, proc=proc)
        module._prebuffer_threshold = 100
        module._state = "PLAYING"
        pcm = b"\x01\x00" * 60  # 120 bytes > threshold
        module._write_audio(pcm)
        proc.stdin.write.assert_called_once()
        assert module._prebuffer == []  # flushed
        assert module._prebuffer_bytes == 0

    @pytest.mark.unit
    def test_after_flush_writes_directly(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(codec=_CODEC_PCM, proc=proc)
        module._prebuffer_threshold = 10
        # Force threshold already met
        module._prebuffer_bytes = 100  # already past threshold
        module._state = "PLAYING"
        pcm = b"\x02\x00" * 4  # 8 bytes
        module._write_audio(pcm)
        proc.stdin.write.assert_called_once()

    @pytest.mark.unit
    def test_broken_pipe_closes_stream(self):
        proc = _make_mock_proc(stdin_raises=BrokenPipeError("pipe closed"))
        module, _ = _build_audio_module(codec=_CODEC_PCM, proc=proc)
        module._prebuffer_threshold = 0  # flush immediately
        module._prebuffer_bytes = 999   # past threshold
        module.on_frame(4, _MSG_MEDIA_TS, False, _media_with_timestamp_body(0, b"\x01\x00" * 8))
        # Stream should be closed after BrokenPipeError
        assert module._proc is None


class TestCleanup:
    """_cleanup() resets state correctly."""

    @pytest.mark.unit
    def test_cleanup_clears_codec_data(self):
        module, _ = _build_audio_module()
        module._aac_codec_data = b"\x12\x10"
        module._cleanup()
        assert module._aac_codec_data is None

    @pytest.mark.unit
    def test_cleanup_clears_prebuffer(self):
        module, _ = _build_audio_module()
        module._prebuffer = [b"data"]
        module._prebuffer_bytes = 4
        module._cleanup()
        assert module._prebuffer == []
        assert module._prebuffer_bytes == 0

    @pytest.mark.unit
    def test_cleanup_resets_state_to_idle(self):
        module, _ = _build_audio_module()
        module._state = "PLAYING"
        module._cleanup()
        assert module._state == "IDLE"

    @pytest.mark.unit
    def test_cleanup_closes_stream(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(proc=proc)
        module._cleanup()
        proc.stdin.close.assert_called()


class TestSinkSelection:
    """on_audio_sink_selected — sink routing logic."""

    @pytest.mark.unit
    def test_same_sink_does_not_reopen(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(proc=proc)
        module._selected_sink = "alsa_output.pci"
        with patch("subprocess.Popen", return_value=_make_mock_proc()) as mock_popen:
            module.on_audio_sink_selected(
                "audio.sink.selected", {"sink": "alsa_output.pci"}
            )
        mock_popen.assert_not_called()

    @pytest.mark.unit
    def test_new_sink_reopens_stream(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(proc=proc)
        module._selected_sink = "old_sink"
        new_proc = _make_mock_proc()
        with patch("subprocess.Popen", return_value=new_proc):
            module.on_audio_sink_selected(
                "audio.sink.selected", {"sink": "new_sink"}
            )
        assert module._selected_sink == "new_sink"

    @pytest.mark.unit
    def test_default_sink_sets_selected_to_none(self):
        proc = _make_mock_proc()
        module, _ = _build_audio_module(proc=proc)
        module._selected_sink = "some_sink"
        with patch("subprocess.Popen", return_value=_make_mock_proc()):
            module.on_audio_sink_selected(
                "audio.sink.selected", {"sink": "default"}
            )
        assert module._selected_sink is None


class TestSessionShutdown:
    """on_aa_session_shutdown and on_channel_close reset state."""

    @pytest.mark.unit
    def test_session_shutdown_resets_session_id(self):
        module, _ = _build_audio_module()
        module._session_id = 77
        module.on_aa_session_shutdown("aa.session.shutdown", {})
        assert module._session_id == 0

    @pytest.mark.unit
    def test_session_shutdown_clears_codec_data(self):
        module, _ = _build_audio_module()
        module._aac_codec_data = b"\x12\x10"
        module.on_aa_session_shutdown("aa.session.shutdown", {})
        assert module._aac_codec_data is None

    @pytest.mark.unit
    def test_session_shutdown_clears_prebuffer(self):
        module, _ = _build_audio_module()
        module._prebuffer = [b"x" * 32]
        module._prebuffer_bytes = 32
        module.on_aa_session_shutdown("aa.session.shutdown", {})
        assert module._prebuffer == []
        assert module._prebuffer_bytes == 0

    @pytest.mark.unit
    def test_channel_close_resets_session_and_prebuffer(self):
        module, _ = _build_audio_module()
        module._session_id = 10
        module._prebuffer = [b"data"]
        module._prebuffer_bytes = 4
        module.on_channel_close(4)
        assert module._session_id == 0
        assert module._prebuffer == []


class TestNormaliseCodec:
    """_normalise_audio_codec helper."""

    @pytest.mark.unit
    def test_int_passthrough(self):
        from channel_modules.audio.main import _normalise_audio_codec
        assert _normalise_audio_codec(_CODEC_PCM, None) == _CODEC_PCM
        assert _normalise_audio_codec(_CODEC_AAC_LC, None) == _CODEC_AAC_LC

    @pytest.mark.unit
    def test_string_mapping(self):
        from channel_modules.audio.main import _normalise_audio_codec
        assert _normalise_audio_codec("MEDIA_CODEC_AUDIO_PCM", None)         == _CODEC_PCM
        assert _normalise_audio_codec("MEDIA_CODEC_AUDIO_AAC_LC", None)      == _CODEC_AAC_LC
        assert _normalise_audio_codec("MEDIA_CODEC_AUDIO_AAC_LC_ADTS", None) == _CODEC_AAC_LC_ADTS

    @pytest.mark.unit
    def test_unknown_falls_back_to_pcm(self):
        from channel_modules.audio.main import _normalise_audio_codec
        result = _normalise_audio_codec("UNKNOWN_CODEC", None)
        assert result == _CODEC_PCM

    @pytest.mark.unit
    def test_none_codec_falls_back_to_pcm(self):
        from channel_modules.audio.main import _normalise_audio_codec
        result = _normalise_audio_codec(None, None)
        assert result == _CODEC_PCM


class TestPCMStats:
    """_pcm_s16le_stats helper."""

    @pytest.mark.unit
    def test_all_zeros_peak_zero(self):
        from channel_modules.audio.main import _pcm_s16le_stats
        pcm = b"\x00\x00" * 100
        stats = _pcm_s16le_stats(pcm)
        assert stats["peak"] == 0
        assert stats["rms"] == 0
        assert stats["zero_ratio"] == 1.0

    @pytest.mark.unit
    def test_single_max_sample(self):
        from channel_modules.audio.main import _pcm_s16le_stats
        # s16le max positive = 32767
        pcm = struct.pack("<h", 32767)
        stats = _pcm_s16le_stats(pcm)
        assert stats["peak"] == 32767
        assert stats["samples"] == 1

    @pytest.mark.unit
    def test_zero_ratio_nonzero_signal(self):
        from channel_modules.audio.main import _pcm_s16le_stats
        pcm = struct.pack("<hh", 1000, 0)
        stats = _pcm_s16le_stats(pcm)
        assert stats["zero_ratio"] == 0.5

    @pytest.mark.unit
    def test_empty_returns_none(self):
        from channel_modules.audio.main import _pcm_s16le_stats
        assert _pcm_s16le_stats(b"") is None

    @pytest.mark.unit
    def test_single_byte_returns_none(self):
        from channel_modules.audio.main import _pcm_s16le_stats
        assert _pcm_s16le_stats(b"\x01") is None


class TestConfigMaxUnacked:
    """max_unacked config integration with SetupResponse."""

    @pytest.mark.unit
    def test_max_unacked_3_appears_in_setup_response(self):
        from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
        module, bus = _build_audio_module()
        module._config["max_unacked"] = 3
        module.on_frame(4, _MSG_SETUP_REQUEST, False, _setup_request_body())
        frame_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "aa.frame.send" and c.args[1]["message_id"] == _MSG_SETUP_RESPONSE
        ]
        assert frame_calls
        payload_hex = frame_calls[-1].args[1]["payload_hex"]
        resp = AVChannelSetupResponse()
        resp.ParseFromString(bytes.fromhex(payload_hex))
        assert resp.max_unacked == 3
