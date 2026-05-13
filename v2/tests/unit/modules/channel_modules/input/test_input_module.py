"""
Unit tests for channel_modules/input/main.py

Strategy:
  InputModule extends BaseChannelModule; proto imports sono ignorati
  patchando i moduli oaa.* in sys.modules con MagicMock PRIMA dell’import.
  La fixture `im` costruisce un InputModule con bus e log mockati.

  I metodi proto come SerializeToString() ritornano b"" di default.
  send_frame è patchato tramite patch.object per tracciare le chiamate
  senza dipendere dalla pipeline TCP.

Covers:
  Section 1  — keycodes e costanti
  Section 2  — __init__: stato iniziale
  Section 3  — _init() / _cleanup(): side-effect
  Section 4  — on_aa_session_active() / on_aa_session_shutdown(): reset stato
  Section 5  — on_channel_open() / on_channel_close(): flag + stato
  Section 6  — on_frame(): dispatch per message_id
  Section 7  — _handle_open_request(): invia ChannelOpenResponse, stato OPEN
  Section 8  — _handle_key_binding_request(): requested ⊆ supported, subset,
               empty body → tutti i default keycodes, stato BOUND
  Section 9  — on_input_touch(): drop se not OPEN/BOUND, drop se pointers vuoti,
               send_frame chiamato, action/pointers passati
  Section 10 — on_input_key(): drop se not OPEN/BOUND, drop se keycode non bound,
               send_frame chiamato, keycode bound inviato
  Section 11 — send_touch_down/up/move(), send_key_down/up(): delegano on_input_touch/key
  Section 12 — _set_state(): aggiorna self._state, pubblica input.state
  Section 13 — protobuf helpers: _encode_varint, _read_varint, _field,
               _varint_field, _bytes_field, _bool_field
  Section 14 — _build_touch_event() / _build_key_event()
  Section 15 — _build_input_report_touch() / _build_input_report_key(): sono bytes
  Section 16 — _parse_key_binding_request(): body vuoto → set(), varint validi,
               ignora campi sconosciuti
"""

from __future__ import annotations

import sys
import types
import importlib
import struct
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Stub proto imports before module import
# ---------------------------------------------------------------------------

def _stub_proto_modules():
    stubs = [
        "oaa", "oaa.control", "oaa.input", "oaa.common",
        "oaa.control.ChannelOpenResponseMessage_pb2",
        "oaa.control.ControlMessageIdsEnum_pb2",
        "oaa.common.StatusEnum_pb2",
        "oaa.input.InputChannelMessageIdsEnum_pb2",
        "oaa.input.InputBindingResponseMessage_pb2",
    ]
    for name in stubs:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

_stub_proto_modules()

for _k in list(sys.modules.keys()):
    if "channel_modules.input" in _k or \
       ("channel_modules" in _k and "input" in _k and "test" not in _k):
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()), \
     patch("shared.bus_client.BusClient", MagicMock()):
    from channel_modules.input.main import (
        InputModule,
        _encode_varint, _read_varint,
        _field, _varint_field, _bytes_field, _bool_field,
        _build_touch_event, _build_key_event,
        _build_input_report_touch, _build_input_report_key,
        _parse_key_binding_request,
        ACTION_DOWN, ACTION_UP, ACTION_MOVED,
        KEYCODE_HOME, KEYCODE_BACK, KEYCODE_VOLUME_UP,
        _DEFAULT_KEYCODES,
        _MSG_CHANNEL_OPEN_RESPONSE, _MSG_KEY_BINDING_RESPONSE, _MSG_INPUT_REPORT,
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def im():
    mod = InputModule()
    mod.bus = MagicMock()
    mod.log = MagicMock()
    mod.CHANNEL_ID = 3
    mod.channel_config = {}
    return mod


# ===========================================================================
# Section 1 — Keycodes & constants
# ===========================================================================

class TestConstants:

    @pytest.mark.unit
    def test_action_down_is_zero(self):
        assert ACTION_DOWN == 0

    @pytest.mark.unit
    def test_action_up_is_one(self):
        assert ACTION_UP == 1

    @pytest.mark.unit
    def test_action_moved_is_two(self):
        assert ACTION_MOVED == 2

    @pytest.mark.unit
    def test_default_keycodes_not_empty(self):
        assert len(_DEFAULT_KEYCODES) > 0

    @pytest.mark.unit
    def test_keycode_home_in_defaults(self):
        assert KEYCODE_HOME in _DEFAULT_KEYCODES


# ===========================================================================
# Section 2 — __init__
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_initial_state_is_idle(self, im):
        assert im._state == "IDLE"

    @pytest.mark.unit
    def test_bound_keycodes_empty(self, im):
        assert len(im._bound_keycodes) == 0

    @pytest.mark.unit
    def test_channel_open_flag_false(self, im):
        assert im._channel_open_flag is False


# ===========================================================================
# Section 3 — _init() / _cleanup()
# ===========================================================================

class TestInitCleanup:

    @pytest.mark.unit
    def test_init_logs_info(self, im):
        im._init()
        im.log.info.assert_called()

    @pytest.mark.unit
    def test_cleanup_resets_state(self, im):
        im._state = "BOUND"
        with patch.object(im, "_set_state") as mock_ss:
            im._cleanup()
        mock_ss.assert_called_once_with("IDLE")

    @pytest.mark.unit
    def test_cleanup_clears_bound_keycodes(self, im):
        im._bound_keycodes = {KEYCODE_HOME}
        with patch.object(im, "_set_state"):
            im._cleanup()
        assert len(im._bound_keycodes) == 0

    @pytest.mark.unit
    def test_cleanup_resets_channel_open_flag(self, im):
        im._channel_open_flag = True
        with patch.object(im, "_set_state"):
            im._cleanup()
        assert im._channel_open_flag is False


# ===========================================================================
# Section 4 — on_aa_session_active/shutdown
# ===========================================================================

class TestSessionEvents:

    @pytest.mark.unit
    def test_session_active_resets_state(self, im):
        im._state = "BOUND"
        with patch.object(im, "_set_state") as mock_ss:
            im.on_aa_session_active("", {})
        mock_ss.assert_called_once_with("IDLE")

    @pytest.mark.unit
    def test_session_active_clears_keycodes(self, im):
        im._bound_keycodes = {KEYCODE_HOME}
        with patch.object(im, "_set_state"):
            im.on_aa_session_active("", {})
        assert len(im._bound_keycodes) == 0

    @pytest.mark.unit
    def test_session_shutdown_resets_state(self, im):
        with patch.object(im, "_set_state") as mock_ss:
            im.on_aa_session_shutdown("", {})
        mock_ss.assert_called_once_with("IDLE")

    @pytest.mark.unit
    def test_session_shutdown_clears_channel_flag(self, im):
        im._channel_open_flag = True
        with patch.object(im, "_set_state"):
            im.on_aa_session_shutdown("", {})
        assert im._channel_open_flag is False


# ===========================================================================
# Section 5 — on_channel_open / on_channel_close
# ===========================================================================

class TestChannelOpenClose:

    @pytest.mark.unit
    def test_channel_open_sets_flag(self, im):
        im.on_channel_open(3, {})
        assert im._channel_open_flag is True

    @pytest.mark.unit
    def test_channel_close_clears_flag(self, im):
        im._channel_open_flag = True
        with patch.object(im, "_set_state"):
            im.on_channel_close(3)
        assert im._channel_open_flag is False

    @pytest.mark.unit
    def test_channel_close_sets_idle(self, im):
        with patch.object(im, "_set_state") as mock_ss:
            im.on_channel_close(3)
        mock_ss.assert_called_once_with("IDLE")


# ===========================================================================
# Section 6 — on_frame() dispatch
# ===========================================================================

class TestOnFrame:

    @pytest.mark.unit
    def test_dispatch_open_request(self, im):
        with patch.object(im, "_handle_open_request") as mock_h:
            im.on_frame(3, int(_MSG_CHANNEL_OPEN_REQUEST := im._MSG_CHANNEL_OPEN_REQUEST
                               if hasattr(im, "_MSG_CHANNEL_OPEN_REQUEST") else
                               __import__(
                                   "channel_modules.input.main",
                                   fromlist=["_MSG_CHANNEL_OPEN_REQUEST"]
                               )._MSG_CHANNEL_OPEN_REQUEST), False, b"")
        # rebuild using module-level constant
        pass  # covered by test below

    @pytest.mark.unit
    def test_dispatch_open_request_direct(self, im):
        import channel_modules.input.main as _m
        with patch.object(im, "_handle_open_request") as mock_h:
            im.on_frame(3, _m._MSG_CHANNEL_OPEN_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_dispatch_key_binding_request(self, im):
        import channel_modules.input.main as _m
        with patch.object(im, "_handle_key_binding_request") as mock_h:
            im.on_frame(3, _m._MSG_KEY_BINDING_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_unknown_msg_id_logs_debug(self, im):
        im.on_frame(3, 0xFFFF, False, b"")
        im.log.debug.assert_called()


# ===========================================================================
# Section 7 — _handle_open_request()
# ===========================================================================

class TestHandleOpenRequest:

    @pytest.mark.unit
    def test_sends_channel_open_response(self, im):
        with patch.object(im, "send_frame") as mock_sf, \
             patch.object(im, "_set_state"):
            im._handle_open_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_CHANNEL_OPEN_RESPONSE

    @pytest.mark.unit
    def test_sets_state_open(self, im):
        with patch.object(im, "send_frame"), \
             patch.object(im, "_set_state") as mock_ss:
            im._handle_open_request(b"")
        mock_ss.assert_called_once_with("OPEN")


# ===========================================================================
# Section 8 — _handle_key_binding_request()
# ===========================================================================

class TestHandleKeyBindingRequest:

    @pytest.mark.unit
    def test_sends_binding_response(self, im):
        with patch.object(im, "send_frame") as mock_sf, \
             patch.object(im, "_set_state"):
            im._handle_key_binding_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_KEY_BINDING_RESPONSE

    @pytest.mark.unit
    def test_empty_body_binds_all_defaults(self, im):
        with patch.object(im, "send_frame"), \
             patch.object(im, "_set_state"):
            im._handle_key_binding_request(b"")
        assert im._bound_keycodes == set(_DEFAULT_KEYCODES)

    @pytest.mark.unit
    def test_sets_state_bound(self, im):
        with patch.object(im, "send_frame"), \
             patch.object(im, "_set_state") as mock_ss:
            im._handle_key_binding_request(b"")
        mock_ss.assert_called_once_with("BOUND")

    @pytest.mark.unit
    def test_requested_subset_accepted(self, im):
        # build a body requesting KEYCODE_HOME (3)
        body = _varint_field(1, KEYCODE_HOME)
        with patch.object(im, "send_frame"), \
             patch.object(im, "_set_state"):
            im._handle_key_binding_request(body)
        assert KEYCODE_HOME in im._bound_keycodes

    @pytest.mark.unit
    def test_unsupported_keycodes_rejected(self, im):
        unsupported = 9999
        body = _varint_field(1, unsupported)
        with patch.object(im, "send_frame"), \
             patch.object(im, "_set_state"):
            im._handle_key_binding_request(body)
        assert unsupported not in im._bound_keycodes


# ===========================================================================
# Section 9 — on_input_touch()
# ===========================================================================

class TestOnInputTouch:

    @pytest.mark.unit
    def test_drops_when_idle(self, im):
        im._state = "IDLE"
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_touch("", {"action": 0, "pointers": [[100, 200, 0]]})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_drops_empty_pointers(self, im):
        im._state = "OPEN"
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_touch("", {"action": 0, "pointers": []})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_sends_frame_when_open(self, im):
        im._state = "OPEN"
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_touch("", {"action": ACTION_DOWN, "pointers": [[100, 200, 0]]})
        mock_sf.assert_called_once()
        assert mock_sf.call_args[0][0] == _MSG_INPUT_REPORT

    @pytest.mark.unit
    def test_sends_frame_when_bound(self, im):
        im._state = "BOUND"
        im._bound_keycodes = {KEYCODE_HOME}
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_touch("", {"action": ACTION_DOWN, "pointers": [[50, 60, 0]]})
        mock_sf.assert_called_once()


# ===========================================================================
# Section 10 — on_input_key()
# ===========================================================================

class TestOnInputKey:

    @pytest.mark.unit
    def test_drops_when_idle(self, im):
        im._state = "IDLE"
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_key("", {"keycode": KEYCODE_HOME, "down": True})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_drops_unbound_keycode(self, im):
        im._state = "BOUND"
        im._bound_keycodes = {KEYCODE_HOME}  # HOME only
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_key("", {"keycode": KEYCODE_BACK, "down": True})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_sends_frame_for_bound_keycode(self, im):
        im._state = "BOUND"
        im._bound_keycodes = {KEYCODE_HOME}
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_key("", {"keycode": KEYCODE_HOME, "down": True})
        mock_sf.assert_called_once()
        assert mock_sf.call_args[0][0] == _MSG_INPUT_REPORT

    @pytest.mark.unit
    def test_sends_frame_when_no_bound_keycodes(self, im):
        """Empty bound set → no filtering."""
        im._state = "OPEN"
        im._bound_keycodes = set()
        with patch.object(im, "send_frame") as mock_sf:
            im.on_input_key("", {"keycode": KEYCODE_HOME, "down": True})
        mock_sf.assert_called_once()


# ===========================================================================
# Section 11 — send_touch_* / send_key_* helpers
# ===========================================================================

class TestSendHelpers:

    @pytest.mark.unit
    def test_send_touch_down_delegates(self, im):
        with patch.object(im, "on_input_touch") as mock_t:
            im.send_touch_down(10, 20)
        mock_t.assert_called_once()
        assert mock_t.call_args[0][1]["action"] == ACTION_DOWN

    @pytest.mark.unit
    def test_send_touch_up_delegates(self, im):
        with patch.object(im, "on_input_touch") as mock_t:
            im.send_touch_up(10, 20)
        assert mock_t.call_args[0][1]["action"] == ACTION_UP

    @pytest.mark.unit
    def test_send_touch_move_delegates(self, im):
        with patch.object(im, "on_input_touch") as mock_t:
            im.send_touch_move(10, 20)
        assert mock_t.call_args[0][1]["action"] == ACTION_MOVED

    @pytest.mark.unit
    def test_send_key_down_delegates(self, im):
        with patch.object(im, "on_input_key") as mock_k:
            im.send_key_down(KEYCODE_HOME)
        assert mock_k.call_args[0][1]["down"] is True

    @pytest.mark.unit
    def test_send_key_up_delegates(self, im):
        with patch.object(im, "on_input_key") as mock_k:
            im.send_key_up(KEYCODE_HOME)
        assert mock_k.call_args[0][1]["down"] is False


# ===========================================================================
# Section 12 — _set_state()
# ===========================================================================

class TestSetState:

    @pytest.mark.unit
    def test_updates_state(self, im):
        im._set_state("OPEN")
        assert im._state == "OPEN"

    @pytest.mark.unit
    def test_publishes_input_state(self, im):
        im._set_state("BOUND")
        im.bus.publish.assert_called_with("input.state", {"state": "BOUND"})


# ===========================================================================
# Section 13 — protobuf helpers
# ===========================================================================

class TestProtobufHelpers:

    @pytest.mark.unit
    def test_encode_varint_single_byte(self):
        assert _encode_varint(1) == b"\x01"

    @pytest.mark.unit
    def test_encode_varint_multibyte(self):
        assert _encode_varint(300) == b"\xac\x02"

    @pytest.mark.unit
    def test_read_varint_single_byte(self):
        val, pos = _read_varint(b"\x01", 0)
        assert val == 1 and pos == 1

    @pytest.mark.unit
    def test_read_varint_multibyte(self):
        val, pos = _read_varint(b"\xac\x02", 0)
        assert val == 300

    @pytest.mark.unit
    def test_read_varint_empty_returns_none(self):
        val, _ = _read_varint(b"", 0)
        assert val is None

    @pytest.mark.unit
    def test_varint_field_is_bytes(self):
        result = _varint_field(1, 42)
        assert isinstance(result, bytes)

    @pytest.mark.unit
    def test_bytes_field_is_bytes(self):
        result = _bytes_field(2, b"payload")
        assert isinstance(result, bytes)

    @pytest.mark.unit
    def test_bool_field_true(self):
        result = _bool_field(1, True)
        assert isinstance(result, bytes)
        assert len(result) > 0


# ===========================================================================
# Section 14 — _build_touch_event / _build_key_event
# ===========================================================================

class TestBuildEvents:

    @pytest.mark.unit
    def test_build_touch_event_returns_bytes(self):
        result = _build_touch_event(ACTION_DOWN, [(100, 200, 0)])
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_build_key_event_returns_bytes(self):
        result = _build_key_event(KEYCODE_HOME, True)
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_build_touch_event_multiple_pointers(self):
        result = _build_touch_event(ACTION_DOWN, [(10, 20, 0), (30, 40, 1)])
        assert isinstance(result, bytes) and len(result) > 0


# ===========================================================================
# Section 15 — _build_input_report_touch / _build_input_report_key
# ===========================================================================

class TestBuildInputReports:

    @pytest.mark.unit
    def test_touch_report_is_bytes(self):
        result = _build_input_report_touch(ACTION_DOWN, [(100, 200, 0)])
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_key_report_is_bytes(self):
        result = _build_input_report_key(KEYCODE_HOME, True)
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_touch_report_with_disp_channel(self):
        result = _build_input_report_touch(ACTION_DOWN, [(1, 2, 0)], disp_channel_id=1)
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_key_report_with_disp_channel(self):
        result = _build_input_report_key(KEYCODE_HOME, False, disp_channel_id=2)
        assert isinstance(result, bytes) and len(result) > 0


# ===========================================================================
# Section 16 — _parse_key_binding_request()
# ===========================================================================

class TestParseKeyBindingRequest:

    @pytest.mark.unit
    def test_empty_body_returns_empty_set(self):
        assert _parse_key_binding_request(b"") == set()

    @pytest.mark.unit
    def test_single_keycode_parsed(self):
        body = _varint_field(1, KEYCODE_HOME)
        result = _parse_key_binding_request(body)
        assert KEYCODE_HOME in result

    @pytest.mark.unit
    def test_multiple_keycodes_parsed(self):
        body = _varint_field(1, KEYCODE_HOME) + _varint_field(1, KEYCODE_BACK)
        result = _parse_key_binding_request(body)
        assert KEYCODE_HOME in result
        assert KEYCODE_BACK in result

    @pytest.mark.unit
    def test_unknown_field_skipped(self):
        # field 2, wire type 2 (length-delimited)
        from channel_modules.input.main import _encode_varint as ev
        unknown = bytes([(2 << 3) | 2]) + ev(3) + b"abc"
        result = _parse_key_binding_request(unknown)
        assert isinstance(result, set)
