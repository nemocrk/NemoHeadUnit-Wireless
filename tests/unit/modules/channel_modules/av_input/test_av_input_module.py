"""
Unit tests for channel_modules/av_input/main.py

Strategy:
  AVInputModule estende BaseChannelModule e gestisce cattura PCM via pacat.
  Tutti i proto, shared e subprocess sono patchati prima dell’import.
  I thread reali non vengono avviati: _start_stream e _stop_stream sono
  patchati nei test che non li testano direttamente.

Covers:
  Section 1  — costanti msg ID e _CHUNK_BYTES, _MAX_RETRIES
  Section 2  — __init__: stato iniziale, schema, _is_ready
  Section 3  — _init(): pacat disponibile → _pacat_ok=True, config SDR audio,
               pacat non disponibile → _pacat_ok=False, channel_config=None warning
  Section 4  — _cleanup(): chiama _stop_stream(publish=False), setta IDLE
  Section 5  — on_config_changed(): max_unacked aggiornato, chiave sconosciuta ignorata
  Section 6  — on_audio_source_selected(): stessa source = no-op;
               source cambia senza cattura = aggiorna _selected_source;
               source cambia durante cattura = restart stream
  Section 7  — on_aa_session_shutdown(): chiama _stop_stream, setta IDLE
  Section 8  — on_channel_open / on_channel_close
  Section 9  — on_frame(): drain queue + dispatch SETUP, CHANNEL_OPEN, INPUT_OPEN, ACK, unknown
  Section 10 — _handle_setup_request(): invia SETUP_RESPONSE, stato SETUP, max_unacked da config
  Section 11 — _handle_open_request(): invia CHANNEL_OPEN_RESPONSE, stato OPEN
  Section 12 — _handle_input_open_request(): req.open=True → _start_stream;
               req.open=False → _stop_stream; parse error → log.warning + return
  Section 13 — _start_stream(): pacat spawn OK → _capturing=True, stato PLAYING,
               pubblica av_input.mic_started;
               pacat spawn fallisce → _capturing=False, _proc=None
  Section 14 — _stop_stream(): da PLAYING → stato STOPPED, pubblica av_input.mic_stopped;
               no-op se già non in cattura
  Section 15 — _set_state(): aggiorna _state, pubblica av_input.state;
               no-op se stesso stato
  Section 16 — _drain_send_queue(): svuota queue, chiama send_frame per ogni item
"""

from __future__ import annotations

import queue
import sys
import threading
from unittest.mock import MagicMock, patch, call, PropertyMock
import pytest


# ---------------------------------------------------------------------------
# Stub proto + shared imports before module load
# ---------------------------------------------------------------------------

def _stub_all():
    stubs = [
        "oaa", "oaa.av", "oaa.control", "oaa.common",
        "oaa.av.AVChannelMessageIdsEnum_pb2",
        "oaa.av.AVChannelSetupResponseMessage_pb2",
        "oaa.av.AVChannelSetupStatusEnum_pb2",
        "oaa.av.AVInputOpenRequestMessage_pb2",
        "oaa.av.AVInputOpenResponseMessage_pb2",
        "oaa.control.ControlMessageIdsEnum_pb2",
        "oaa.control.ChannelOpenResponseMessage_pb2",
        "oaa.common.StatusEnum_pb2",
        "shared.proto_utils",
        "shared.config_schema",
    ]
    for name in stubs:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

_stub_all()

for _k in list(sys.modules.keys()):
    if "channel_modules.av_input" in _k and "test" not in _k:
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()), \
     patch("shared.bus_client.BusClient", MagicMock()):
    from channel_modules.av_input.main import (
        AVInputModule,
        _MSG_AV_CHANNEL_SETUP_REQUEST, _MSG_AV_CHANNEL_SETUP_RESPONSE,
        _MSG_CHANNEL_OPEN_REQUEST, _MSG_CHANNEL_OPEN_RESPONSE,
        _MSG_AV_INPUT_OPEN_REQUEST, _MSG_AV_INPUT_OPEN_RESPONSE,
        _MSG_AV_MEDIA_WITH_TIMESTAMP, _MSG_AV_MEDIA_ACK,
        _CHUNK_BYTES, _MAX_RETRIES,
    )

import channel_modules.av_input.main as _av_mod


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def avm():
    mod = AVInputModule()
    mod.bus = MagicMock()
    mod.log = MagicMock()
    mod.CHANNEL_ID = 7
    mod.channel_config = {}
    mod._config = {"max_unacked": 1}
    return mod


# ===========================================================================
# Section 1 — Constants
# ===========================================================================

class TestConstants:

    @pytest.mark.unit
    def test_chunk_bytes_positive(self):
        assert _CHUNK_BYTES > 0

    @pytest.mark.unit
    def test_max_retries_positive(self):
        assert _MAX_RETRIES > 0

    @pytest.mark.unit
    def test_msg_setup_request_defined(self):
        assert _MSG_AV_CHANNEL_SETUP_REQUEST is not None

    @pytest.mark.unit
    def test_msg_input_open_request_defined(self):
        assert _MSG_AV_INPUT_OPEN_REQUEST is not None


# ===========================================================================
# Section 2 — __init__ / schema / _is_ready
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_initial_state_idle(self, avm):
        assert avm._state == "IDLE"

    @pytest.mark.unit
    def test_capturing_false(self, avm):
        assert avm._capturing is False

    @pytest.mark.unit
    def test_pacat_ok_false(self, avm):
        assert avm._pacat_ok is False

    @pytest.mark.unit
    def test_proc_none(self, avm):
        assert avm._proc is None

    @pytest.mark.unit
    def test_schema_has_max_unacked(self, avm):
        assert "max_unacked" in avm.get_schema()

    @pytest.mark.unit
    def test_is_ready_false_when_pacat_not_ok(self, avm):
        avm._pacat_ok = False
        assert avm._is_ready() is False

    @pytest.mark.unit
    def test_is_ready_true_when_pacat_ok(self, avm):
        avm._pacat_ok = True
        assert avm._is_ready() is True


# ===========================================================================
# Section 3 — _init()
# ===========================================================================

class TestInitHook:

    @pytest.mark.unit
    def test_pacat_available_sets_ok(self, avm):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            avm._init()
        assert avm._pacat_ok is True

    @pytest.mark.unit
    def test_pacat_not_available_sets_not_ok(self, avm):
        with patch("subprocess.run", side_effect=FileNotFoundError("no pacat")):
            avm._init()
        assert avm._pacat_ok is False

    @pytest.mark.unit
    def test_pacat_not_available_logs_error(self, avm):
        with patch("subprocess.run", side_effect=FileNotFoundError("no pacat")):
            avm._init()
        avm.log.error.assert_called()

    @pytest.mark.unit
    def test_reads_audio_config_from_sdr(self, avm):
        avm.channel_config = {
            "av_input_channel": {
                "audio_config": {
                    "sample_rate": 44100,
                    "bit_depth": 16,
                    "channel_count": 2,
                }
            }
        }
        with patch("subprocess.run"):
            avm._init()
        assert avm._sample_rate == 44100
        assert avm._channel_count == 2

    @pytest.mark.unit
    def test_channel_config_none_logs_warning(self, avm):
        avm.channel_config = None
        with patch("subprocess.run"):
            avm._init()
        avm.log.warning.assert_called()


# ===========================================================================
# Section 4 — _cleanup()
# ===========================================================================

class TestCleanup:

    @pytest.mark.unit
    def test_calls_stop_stream_no_publish(self, avm):
        with patch.object(avm, "_stop_stream") as mock_ss, \
             patch.object(avm, "_set_state"):
            avm._cleanup()
        mock_ss.assert_called_once_with(publish=False)

    @pytest.mark.unit
    def test_sets_idle_state(self, avm):
        with patch.object(avm, "_stop_stream"), \
             patch.object(avm, "_set_state") as mock_ss:
            avm._cleanup()
        mock_ss.assert_called_once_with("IDLE")


# ===========================================================================
# Section 5 — on_config_changed()
# ===========================================================================

class TestOnConfigChanged:

    @pytest.mark.unit
    def test_updates_max_unacked(self, avm):
        with patch.object(AVInputModule.__bases__[0], "on_config_changed", lambda s, k, v: None):
            avm.on_config_changed("max_unacked", 4)
        assert avm._max_unacked == 4

    @pytest.mark.unit
    def test_logs_info_on_max_unacked_change(self, avm):
        with patch.object(AVInputModule.__bases__[0], "on_config_changed", lambda s, k, v: None):
            avm.on_config_changed("max_unacked", 4)
        avm.log.info.assert_called()


# ===========================================================================
# Section 6 — on_audio_source_selected()
# ===========================================================================

class TestOnAudioSourceSelected:

    @pytest.mark.unit
    def test_same_source_noop(self, avm):
        avm._selected_source = "hw:0"
        with patch.object(avm, "_stop_stream") as mock_ss:
            avm.on_audio_source_selected("", {"source": "hw:0"})
        mock_ss.assert_not_called()

    @pytest.mark.unit
    def test_default_source_becomes_none(self, avm):
        avm.on_audio_source_selected("", {"source": "default"})
        assert avm._selected_source is None

    @pytest.mark.unit
    def test_new_source_updates_selected_source(self, avm):
        with patch.object(avm, "_stop_stream"), patch.object(avm, "_start_stream"):
            avm.on_audio_source_selected("", {"source": "hw:1"})
        assert avm._selected_source == "hw:1"

    @pytest.mark.unit
    def test_source_change_during_capture_restarts(self, avm):
        avm._capturing = True
        with patch.object(avm, "_stop_stream") as mock_ss, \
             patch.object(avm, "_start_stream") as mock_start:
            avm.on_audio_source_selected("", {"source": "hw:2"})
        mock_ss.assert_called_once_with(publish=False)
        mock_start.assert_called_once()

    @pytest.mark.unit
    def test_source_change_without_capture_no_restart(self, avm):
        avm._capturing = False
        with patch.object(avm, "_stop_stream") as mock_ss, \
             patch.object(avm, "_start_stream") as mock_start:
            avm.on_audio_source_selected("", {"source": "hw:2"})
        mock_start.assert_not_called()


# ===========================================================================
# Section 7 — on_aa_session_shutdown()
# ===========================================================================

class TestOnAaSessionShutdown:

    @pytest.mark.unit
    def test_calls_stop_stream_with_publish(self, avm):
        with patch.object(avm, "_stop_stream") as mock_ss, \
             patch.object(avm, "_set_state"):
            avm.on_aa_session_shutdown("", {})
        mock_ss.assert_called_once_with(publish=True)

    @pytest.mark.unit
    def test_sets_idle(self, avm):
        with patch.object(avm, "_stop_stream"), \
             patch.object(avm, "_set_state") as mock_ss:
            avm.on_aa_session_shutdown("", {})
        mock_ss.assert_called_once_with("IDLE")


# ===========================================================================
# Section 8 — on_channel_open / on_channel_close
# ===========================================================================

class TestChannelOpenClose:

    @pytest.mark.unit
    def test_channel_open_sets_capturing_false(self, avm):
        avm._capturing = True
        with patch.object(avm, "_set_state"):
            avm.on_channel_open(7, {})
        assert avm._capturing is False

    @pytest.mark.unit
    def test_channel_open_sets_idle(self, avm):
        with patch.object(avm, "_set_state") as mock_ss:
            avm.on_channel_open(7, {})
        mock_ss.assert_called_once_with("IDLE")

    @pytest.mark.unit
    def test_channel_close_not_capturing_noop(self, avm):
        avm._capturing = False
        with patch.object(avm, "_stop_stream") as mock_ss, \
             patch.object(avm, "_set_state"):
            avm.on_channel_close(7)
        mock_ss.assert_not_called()

    @pytest.mark.unit
    def test_channel_close_while_capturing_stops(self, avm):
        avm._capturing = True
        with patch.object(avm, "_stop_stream") as mock_ss, \
             patch.object(avm, "_set_state"):
            avm.on_channel_close(7)
        mock_ss.assert_called_once_with(publish=True)


# ===========================================================================
# Section 9 — on_frame() dispatch
# ===========================================================================

class TestOnFrame:

    @pytest.mark.unit
    def test_dispatch_setup_request(self, avm):
        with patch.object(avm, "_drain_send_queue"), \
             patch.object(avm, "_handle_setup_request") as mock_h:
            avm.on_frame(7, _MSG_AV_CHANNEL_SETUP_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_dispatch_channel_open_request(self, avm):
        with patch.object(avm, "_drain_send_queue"), \
             patch.object(avm, "_handle_open_request") as mock_h:
            avm.on_frame(7, _MSG_CHANNEL_OPEN_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_dispatch_input_open_request(self, avm):
        with patch.object(avm, "_drain_send_queue"), \
             patch.object(avm, "_handle_input_open_request") as mock_h:
            avm.on_frame(7, _MSG_AV_INPUT_OPEN_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_dispatch_ack_logs_debug(self, avm):
        with patch.object(avm, "_drain_send_queue"):
            avm.on_frame(7, _MSG_AV_MEDIA_ACK, False, b"")
        avm.log.debug.assert_called()

    @pytest.mark.unit
    def test_unknown_msg_id_logs_debug(self, avm):
        with patch.object(avm, "_drain_send_queue"):
            avm.on_frame(7, 0xFFFF, False, b"")
        avm.log.debug.assert_called()

    @pytest.mark.unit
    def test_drains_queue_on_every_frame(self, avm):
        with patch.object(avm, "_drain_send_queue") as mock_drain, \
             patch.object(avm, "_handle_open_request"):
            avm.on_frame(7, _MSG_CHANNEL_OPEN_REQUEST, False, b"")
        mock_drain.assert_called_once()


# ===========================================================================
# Section 10 — _handle_setup_request()
# ===========================================================================

class TestHandleSetupRequest:

    @pytest.mark.unit
    def test_sends_setup_response(self, avm):
        with patch.object(avm, "send_frame") as mock_sf, \
             patch.object(avm, "_set_state"):
            avm._handle_setup_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_AV_CHANNEL_SETUP_RESPONSE

    @pytest.mark.unit
    def test_sets_state_setup(self, avm):
        with patch.object(avm, "send_frame"), \
             patch.object(avm, "_set_state") as mock_ss:
            avm._handle_setup_request(b"")
        mock_ss.assert_called_once_with("SETUP")

    @pytest.mark.unit
    def test_uses_max_unacked_from_config(self, avm):
        avm._config = {"max_unacked": 4}
        with patch.object(avm, "send_frame"), \
             patch.object(avm, "_set_state"):
            avm._handle_setup_request(b"")
        assert avm._max_unacked == 4


# ===========================================================================
# Section 11 — _handle_open_request()
# ===========================================================================

class TestHandleOpenRequest:

    @pytest.mark.unit
    def test_sends_channel_open_response(self, avm):
        with patch.object(avm, "send_frame") as mock_sf, \
             patch.object(avm, "_set_state"):
            avm._handle_open_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_CHANNEL_OPEN_RESPONSE

    @pytest.mark.unit
    def test_sets_state_open(self, avm):
        with patch.object(avm, "send_frame"), \
             patch.object(avm, "_set_state") as mock_ss:
            avm._handle_open_request(b"")
        mock_ss.assert_called_once_with("OPEN")


# ===========================================================================
# Section 12 — _handle_input_open_request()
# ===========================================================================

class TestHandleInputOpenRequest:

    @pytest.mark.unit
    def test_open_true_starts_stream(self, avm):
        req = MagicMock()
        req.open = True
        req.HasField = lambda f: False
        with patch.object(_av_mod, "AVInputOpenRequest", return_value=req), \
             patch.object(avm, "_start_stream") as mock_start:
            avm._handle_input_open_request(b"")
        mock_start.assert_called_once()

    @pytest.mark.unit
    def test_open_false_stops_stream(self, avm):
        req = MagicMock()
        req.open = False
        req.HasField = lambda f: False
        with patch.object(_av_mod, "AVInputOpenRequest", return_value=req), \
             patch.object(avm, "_stop_stream") as mock_stop:
            avm._handle_input_open_request(b"")
        mock_stop.assert_called_once_with(publish=True)

    @pytest.mark.unit
    def test_parse_error_logs_warning_and_returns(self, avm):
        req_cls = MagicMock()
        req_cls.return_value.ParseFromString.side_effect = Exception("bad")
        with patch.object(_av_mod, "AVInputOpenRequest", req_cls), \
             patch.object(avm, "_start_stream") as mock_start:
            avm._handle_input_open_request(b"garbage")
        avm.log.warning.assert_called()
        mock_start.assert_not_called()


# ===========================================================================
# Section 13 — _start_stream()
# ===========================================================================

class TestStartStream:

    @pytest.mark.unit
    def test_spawn_ok_sets_capturing_true(self, avm):
        proc_mock = MagicMock()
        proc_mock.pid = 1234
        proc_mock.stdout = MagicMock()
        with patch("subprocess.Popen", return_value=proc_mock), \
             patch.object(avm, "_set_state"), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            avm._start_stream()
        assert avm._capturing is True

    @pytest.mark.unit
    def test_spawn_ok_sets_state_playing(self, avm):
        proc_mock = MagicMock()
        proc_mock.pid = 1234
        with patch("subprocess.Popen", return_value=proc_mock), \
             patch.object(avm, "_set_state") as mock_ss, \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            avm._start_stream()
        mock_ss.assert_any_call("PLAYING")

    @pytest.mark.unit
    def test_spawn_ok_publishes_mic_started(self, avm):
        proc_mock = MagicMock()
        proc_mock.pid = 1234
        with patch("subprocess.Popen", return_value=proc_mock), \
             patch.object(avm, "_set_state"), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            avm._start_stream()
        avm.bus.publish.assert_any_call("av_input.mic_started", {"channel_id": 7})

    @pytest.mark.unit
    def test_spawn_fail_sets_capturing_false(self, avm):
        with patch("subprocess.Popen", side_effect=OSError("no binary")), \
             patch.object(avm, "_set_state"):
            avm._start_stream()
        assert avm._capturing is False

    @pytest.mark.unit
    def test_spawn_fail_proc_remains_none(self, avm):
        with patch("subprocess.Popen", side_effect=OSError("no binary")), \
             patch.object(avm, "_set_state"):
            avm._start_stream()
        assert avm._proc is None

    @pytest.mark.unit
    def test_spawn_fail_logs_error(self, avm):
        with patch("subprocess.Popen", side_effect=OSError("no binary")), \
             patch.object(avm, "_set_state"):
            avm._start_stream()
        avm.log.error.assert_called()


# ===========================================================================
# Section 14 — _stop_stream()
# ===========================================================================

class TestStopStream:

    @pytest.mark.unit
    def test_sets_state_stopped_when_capturing(self, avm):
        avm._capturing = True
        avm._proc = None
        avm._reader_thread = None
        with patch.object(avm, "_set_state") as mock_ss:
            avm._stop_stream(publish=False)
        mock_ss.assert_any_call("STOPPED")

    @pytest.mark.unit
    def test_publishes_mic_stopped_when_publish_true(self, avm):
        avm._capturing = True
        avm._proc = None
        avm._reader_thread = None
        with patch.object(avm, "_set_state"):
            avm._stop_stream(publish=True)
        avm.bus.publish.assert_called_with("av_input.mic_stopped", {"channel_id": 7})

    @pytest.mark.unit
    def test_no_publish_when_publish_false(self, avm):
        avm._capturing = True
        avm._proc = None
        avm._reader_thread = None
        with patch.object(avm, "_set_state"):
            avm._stop_stream(publish=False)
        # mic_stopped should NOT be published
        for c in avm.bus.publish.call_args_list:
            assert c[0][0] != "av_input.mic_stopped"

    @pytest.mark.unit
    def test_noop_when_not_capturing(self, avm):
        avm._capturing = False
        avm._proc = None
        avm._reader_thread = None
        with patch.object(avm, "_set_state") as mock_ss:
            avm._stop_stream(publish=True)
        # _set_state should not be called for STOPPED
        calls = [str(c) for c in mock_ss.call_args_list]
        assert not any("STOPPED" in c for c in calls)

    @pytest.mark.unit
    def test_terminates_proc_if_running(self, avm):
        avm._capturing = True
        proc_mock = MagicMock()
        avm._proc = proc_mock
        avm._reader_thread = None
        with patch.object(avm, "_set_state"):
            avm._stop_stream(publish=False)
        proc_mock.terminate.assert_called_once()


# ===========================================================================
# Section 15 — _set_state()
# ===========================================================================

class TestSetState:

    @pytest.mark.unit
    def test_updates_state(self, avm):
        avm._set_state("SETUP")
        assert avm._state == "SETUP"

    @pytest.mark.unit
    def test_publishes_av_input_state(self, avm):
        avm._set_state("PLAYING")
        avm.bus.publish.assert_called_with(
            "av_input.state", {"channel_id": 7, "state": "PLAYING"}
        )

    @pytest.mark.unit
    def test_noop_when_same_state(self, avm):
        avm._state = "IDLE"
        avm._set_state("IDLE")
        avm.bus.publish.assert_not_called()


# ===========================================================================
# Section 16 — _drain_send_queue()
# ===========================================================================

class TestDrainSendQueue:

    @pytest.mark.unit
    def test_empty_queue_noop(self, avm):
        with patch.object(avm, "send_frame") as mock_sf:
            avm._drain_send_queue()
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_drains_all_items(self, avm):
        avm._send_queue.put((1000, b"chunk1"))
        avm._send_queue.put((2000, b"chunk2"))
        with patch.object(avm, "send_frame") as mock_sf, \
             patch.object(_av_mod, "build_media_with_timestamp", side_effect=lambda ts, d: d):
            avm._drain_send_queue()
        assert mock_sf.call_count == 2

    @pytest.mark.unit
    def test_calls_send_frame_with_media_msg_id(self, avm):
        avm._send_queue.put((1000, b"chunk"))
        with patch.object(avm, "send_frame") as mock_sf, \
             patch.object(_av_mod, "build_media_with_timestamp", return_value=b"payload"):
            avm._drain_send_queue()
        assert mock_sf.call_args[0][0] == _MSG_AV_MEDIA_WITH_TIMESTAMP
