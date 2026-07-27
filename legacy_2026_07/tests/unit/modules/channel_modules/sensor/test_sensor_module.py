"""
Unit tests for channel_modules/sensor/main.py

Strategy:
  SensorModule extends BaseChannelModule; proto imports sono ignorati
  patchando oaa.* in sys.modules con MagicMock PRIMA dell’import.
  La fixture `sm` costruisce un SensorModule con bus e log mockati.

Covers:
  Section 1  — costanti: SensorType, DriveStatus
  Section 2  — __init__: stato iniziale
  Section 3  — _init() / _cleanup()
  Section 4  — on_aa_session_active/shutdown
  Section 5  — on_channel_open / on_channel_close
  Section 6  — on_frame() dispatch
  Section 7  — _handle_open_request(): stato OPEN, send_frame CHANNEL_OPEN_RESPONSE
  Section 8  — _handle_sensor_start_request(): aggiunge sensor_type, invia response,
               invia batch iniziale per DRIVING_STATUS e NIGHT_MODE, no batch per tipo ignoto
  Section 9  — on_sensor_driving_status(): drop se IDLE, drop se non started,
               send_frame quando ready
  Section 10 — on_sensor_night_mode(): drop se IDLE, drop se non started,
               send_frame quando ready
  Section 11 — on_sensor_gps(): drop se IDLE, drop se non started,
               send_frame con tutti i campi, con campi opzionali
  Section 12 — _set_state(): aggiorna _state, pubblica sensor.state,
               no-op se stesso stato
  Section 13 — protobuf helpers: _encode_varint, _varint_field, _bytes_field,
               _bool_field, _sfixed32_field, _int64_field
  Section 14 — _build_default_sensor_batch(): DRIVING_STATUS, NIGHT_MODE, ignoto→b""
  Section 15 — _parse_sensor_start_request(): body vuoto→0, varint valido,
               campo sconosciuto ignorato
"""

from __future__ import annotations

import sys
import types
import importlib
import struct
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Stub proto imports
# ---------------------------------------------------------------------------

def _stub_protos():
    stubs = [
        "oaa", "oaa.control", "oaa.sensor", "oaa.common",
        "oaa.control.ChannelOpenResponseMessage_pb2",
        "oaa.control.ControlMessageIdsEnum_pb2",
        "oaa.common.StatusEnum_pb2",
        "oaa.sensor.SensorChannelMessageIdsEnum_pb2",
        "oaa.sensor.SensorStartResponseMessage_pb2",
    ]
    for name in stubs:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

_stub_protos()

for _k in list(sys.modules.keys()):
    if "channel_modules.sensor" in _k or \
       ("channel_modules" in _k and "sensor" in _k and "test" not in _k):
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()), \
     patch("shared.bus_client.BusClient", MagicMock()):
    from channel_modules.sensor.main import (
        SensorModule,
        _encode_varint, _varint_field, _bytes_field, _bool_field,
        _sfixed32_field, _int64_field,
        _build_default_sensor_batch, _parse_sensor_start_request,
        SENSOR_DRIVING_STATUS, SENSOR_NIGHT_MODE, SENSOR_LOCATION,
        DRIVE_STATUS_UNRESTRICTED, DRIVE_STATUS_FULLY_RESTRICTED,
        _MSG_CHANNEL_OPEN_RESPONSE, _MSG_SENSOR_START_RESPONSE, _MSG_SENSOR_EVENT_INDICATION,
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def sm():
    mod = SensorModule()
    mod.bus = MagicMock()
    mod.log = MagicMock()
    mod.CHANNEL_ID = 4
    mod.channel_config = {}
    return mod


# ===========================================================================
# Section 1 — Constants
# ===========================================================================

class TestConstants:

    @pytest.mark.unit
    def test_sensor_driving_status_is_1(self):
        assert SENSOR_DRIVING_STATUS == 1

    @pytest.mark.unit
    def test_sensor_night_mode_is_4(self):
        assert SENSOR_NIGHT_MODE == 4

    @pytest.mark.unit
    def test_sensor_location_is_6(self):
        assert SENSOR_LOCATION == 6

    @pytest.mark.unit
    def test_drive_status_unrestricted_is_0(self):
        assert DRIVE_STATUS_UNRESTRICTED == 0

    @pytest.mark.unit
    def test_drive_status_fully_restricted_is_31(self):
        assert DRIVE_STATUS_FULLY_RESTRICTED == 31


# ===========================================================================
# Section 2 — __init__
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_initial_state_idle(self, sm):
        assert sm._state == "IDLE"

    @pytest.mark.unit
    def test_started_sensors_empty(self, sm):
        assert len(sm._started_sensors) == 0


# ===========================================================================
# Section 3 — _init() / _cleanup()
# ===========================================================================

class TestInitCleanup:

    @pytest.mark.unit
    def test_init_logs_info(self, sm):
        sm._init()
        sm.log.info.assert_called()

    @pytest.mark.unit
    def test_cleanup_resets_state(self, sm):
        sm._state = "OPEN"
        with patch.object(sm, "_set_state") as mock_ss:
            sm._cleanup()
        mock_ss.assert_called_once_with("IDLE")

    @pytest.mark.unit
    def test_cleanup_clears_started_sensors(self, sm):
        sm._started_sensors.add(SENSOR_DRIVING_STATUS)
        with patch.object(sm, "_set_state"):
            sm._cleanup()
        assert len(sm._started_sensors) == 0


# ===========================================================================
# Section 4 — on_aa_session_active/shutdown
# ===========================================================================

class TestSessionEvents:

    @pytest.mark.unit
    def test_session_active_resets_state(self, sm):
        with patch.object(sm, "_set_state") as mock_ss:
            sm.on_aa_session_active("", {})
        mock_ss.assert_called_once_with("IDLE")

    @pytest.mark.unit
    def test_session_active_clears_started_sensors(self, sm):
        sm._started_sensors.add(SENSOR_DRIVING_STATUS)
        with patch.object(sm, "_set_state"):
            sm.on_aa_session_active("", {})
        assert len(sm._started_sensors) == 0

    @pytest.mark.unit
    def test_session_shutdown_resets_state(self, sm):
        with patch.object(sm, "_set_state") as mock_ss:
            sm.on_aa_session_shutdown("", {})
        mock_ss.assert_called_once_with("IDLE")


# ===========================================================================
# Section 5 — on_channel_open / on_channel_close
# ===========================================================================

class TestChannelOpenClose:

    @pytest.mark.unit
    def test_channel_open_logs(self, sm):
        sm.on_channel_open(4, {})
        sm.log.info.assert_called()

    @pytest.mark.unit
    def test_channel_close_clears_sensors(self, sm):
        sm._started_sensors.add(SENSOR_DRIVING_STATUS)
        with patch.object(sm, "_set_state"):
            sm.on_channel_close(4)
        assert len(sm._started_sensors) == 0

    @pytest.mark.unit
    def test_channel_close_sets_idle(self, sm):
        with patch.object(sm, "_set_state") as mock_ss:
            sm.on_channel_close(4)
        mock_ss.assert_called_once_with("IDLE")


# ===========================================================================
# Section 6 — on_frame() dispatch
# ===========================================================================

class TestOnFrame:

    @pytest.mark.unit
    def test_dispatch_open_request(self, sm):
        import channel_modules.sensor.main as _m
        with patch.object(sm, "_handle_open_request") as mock_h:
            sm.on_frame(4, _m._MSG_CHANNEL_OPEN_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_dispatch_sensor_start_request(self, sm):
        import channel_modules.sensor.main as _m
        with patch.object(sm, "_handle_sensor_start_request") as mock_h:
            sm.on_frame(4, _m._MSG_SENSOR_START_REQUEST, False, b"data")
        mock_h.assert_called_once_with(b"data")

    @pytest.mark.unit
    def test_unknown_msg_id_logs_debug(self, sm):
        sm.on_frame(4, 0xFFFF, False, b"")
        sm.log.debug.assert_called()


# ===========================================================================
# Section 7 — _handle_open_request()
# ===========================================================================

class TestHandleOpenRequest:

    @pytest.mark.unit
    def test_sends_channel_open_response(self, sm):
        with patch.object(sm, "send_frame") as mock_sf, \
             patch.object(sm, "_set_state"):
            sm._handle_open_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_CHANNEL_OPEN_RESPONSE

    @pytest.mark.unit
    def test_sets_state_open(self, sm):
        with patch.object(sm, "send_frame"), \
             patch.object(sm, "_set_state") as mock_ss:
            sm._handle_open_request(b"")
        mock_ss.assert_called_once_with("OPEN")


# ===========================================================================
# Section 8 — _handle_sensor_start_request()
# ===========================================================================

class TestHandleSensorStartRequest:

    def _body_for(self, sensor_type: int) -> bytes:
        return _varint_field(1, sensor_type)

    @pytest.mark.unit
    def test_adds_sensor_type_to_started(self, sm):
        with patch.object(sm, "send_frame"):
            sm._handle_sensor_start_request(self._body_for(SENSOR_DRIVING_STATUS))
        assert SENSOR_DRIVING_STATUS in sm._started_sensors

    @pytest.mark.unit
    def test_sends_sensor_start_response(self, sm):
        frames = []
        with patch.object(sm, "send_frame", side_effect=lambda mid, d: frames.append(mid)):
            sm._handle_sensor_start_request(self._body_for(SENSOR_DRIVING_STATUS))
        assert _MSG_SENSOR_START_RESPONSE in frames

    @pytest.mark.unit
    def test_sends_initial_batch_for_driving_status(self, sm):
        frames = []
        with patch.object(sm, "send_frame", side_effect=lambda mid, d: frames.append(mid)):
            sm._handle_sensor_start_request(self._body_for(SENSOR_DRIVING_STATUS))
        assert _MSG_SENSOR_EVENT_INDICATION in frames

    @pytest.mark.unit
    def test_sends_initial_batch_for_night_mode(self, sm):
        frames = []
        with patch.object(sm, "send_frame", side_effect=lambda mid, d: frames.append(mid)):
            sm._handle_sensor_start_request(self._body_for(SENSOR_NIGHT_MODE))
        assert _MSG_SENSOR_EVENT_INDICATION in frames

    @pytest.mark.unit
    def test_no_initial_batch_for_location(self, sm):
        frames = []
        with patch.object(sm, "send_frame", side_effect=lambda mid, d: frames.append(mid)):
            sm._handle_sensor_start_request(self._body_for(SENSOR_LOCATION))
        # only SENSOR_START_RESPONSE, no SENSOR_EVENT_INDICATION
        assert frames.count(_MSG_SENSOR_EVENT_INDICATION) == 0


# ===========================================================================
# Section 9 — on_sensor_driving_status()
# ===========================================================================

class TestOnSensorDrivingStatus:

    @pytest.mark.unit
    def test_drops_when_idle(self, sm):
        sm._state = "IDLE"
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_driving_status("", {"status": 0})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_drops_when_sensor_not_started(self, sm):
        sm._state = "OPEN"
        sm._started_sensors = set()  # DRIVING_STATUS not started
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_driving_status("", {"status": 0})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_sends_event_when_ready(self, sm):
        sm._state = "OPEN"
        sm._started_sensors.add(SENSOR_DRIVING_STATUS)
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_driving_status("", {"status": DRIVE_STATUS_UNRESTRICTED})
        mock_sf.assert_called_once()


# ===========================================================================
# Section 10 — on_sensor_night_mode()
# ===========================================================================

class TestOnSensorNightMode:

    @pytest.mark.unit
    def test_drops_when_idle(self, sm):
        sm._state = "IDLE"
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_night_mode("", {"night_mode": False})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_drops_when_sensor_not_started(self, sm):
        sm._state = "OPEN"
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_night_mode("", {"night_mode": False})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_sends_event_when_ready(self, sm):
        sm._state = "OPEN"
        sm._started_sensors.add(SENSOR_NIGHT_MODE)
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_night_mode("", {"night_mode": True})
        mock_sf.assert_called_once()


# ===========================================================================
# Section 11 — on_sensor_gps()
# ===========================================================================

class TestOnSensorGps:

    @pytest.mark.unit
    def test_drops_when_idle(self, sm):
        sm._state = "IDLE"
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_gps("", {"latitude": 0, "longitude": 0, "bearing": 0, "speed": 0})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_drops_when_sensor_not_started(self, sm):
        sm._state = "OPEN"
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_gps("", {"latitude": 0, "longitude": 0, "bearing": 0, "speed": 0})
        mock_sf.assert_not_called()

    @pytest.mark.unit
    def test_sends_event_when_ready(self, sm):
        sm._state = "OPEN"
        sm._started_sensors.add(SENSOR_LOCATION)
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_gps("", {"latitude": 10, "longitude": 20, "bearing": 0, "speed": 50})
        mock_sf.assert_called_once()

    @pytest.mark.unit
    def test_sends_with_optional_fields(self, sm):
        sm._state = "OPEN"
        sm._started_sensors.add(SENSOR_LOCATION)
        with patch.object(sm, "send_frame") as mock_sf:
            sm.on_sensor_gps("", {
                "latitude": 10, "longitude": 20, "bearing": 0, "speed": 50,
                "altitude": 100, "accuracy": 5, "timestamp": 123456789,
            })
        mock_sf.assert_called_once()


# ===========================================================================
# Section 12 — _set_state()
# ===========================================================================

class TestSetState:

    @pytest.mark.unit
    def test_updates_state(self, sm):
        sm._set_state("OPEN")
        assert sm._state == "OPEN"

    @pytest.mark.unit
    def test_publishes_sensor_state(self, sm):
        sm._set_state("OPEN")
        sm.bus.publish.assert_called_with("sensor.state", {"state": "OPEN"})

    @pytest.mark.unit
    def test_noop_when_same_state(self, sm):
        sm._state = "IDLE"
        sm._set_state("IDLE")
        sm.bus.publish.assert_not_called()


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
    def test_varint_field_is_bytes(self):
        assert isinstance(_varint_field(1, 42), bytes)

    @pytest.mark.unit
    def test_bytes_field_is_bytes(self):
        assert isinstance(_bytes_field(1, b"x"), bytes)

    @pytest.mark.unit
    def test_bool_field_true(self):
        result = _bool_field(1, True)
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_sfixed32_field_is_4_bytes_payload(self):
        result = _sfixed32_field(1, 1000)
        # tag + 4 bytes value
        assert isinstance(result, bytes) and len(result) >= 4

    @pytest.mark.unit
    def test_int64_field_is_bytes(self):
        assert isinstance(_int64_field(1, 9999), bytes)


# ===========================================================================
# Section 14 — _build_default_sensor_batch()
# ===========================================================================

class TestBuildDefaultSensorBatch:

    @pytest.mark.unit
    def test_driving_status_returns_bytes(self):
        result = _build_default_sensor_batch(SENSOR_DRIVING_STATUS)
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_night_mode_returns_bytes(self):
        result = _build_default_sensor_batch(SENSOR_NIGHT_MODE)
        assert isinstance(result, bytes) and len(result) > 0

    @pytest.mark.unit
    def test_unknown_type_returns_empty(self):
        result = _build_default_sensor_batch(999)
        assert result == b""


# ===========================================================================
# Section 15 — _parse_sensor_start_request()
# ===========================================================================

class TestParseSensorStartRequest:

    @pytest.mark.unit
    def test_empty_body_returns_zero(self):
        assert _parse_sensor_start_request(b"") == 0

    @pytest.mark.unit
    def test_driving_status_parsed(self):
        body = _varint_field(1, SENSOR_DRIVING_STATUS)
        assert _parse_sensor_start_request(body) == SENSOR_DRIVING_STATUS

    @pytest.mark.unit
    def test_night_mode_parsed(self):
        body = _varint_field(1, SENSOR_NIGHT_MODE)
        assert _parse_sensor_start_request(body) == SENSOR_NIGHT_MODE

    @pytest.mark.unit
    def test_location_parsed(self):
        body = _varint_field(1, SENSOR_LOCATION)
        assert _parse_sensor_start_request(body) == SENSOR_LOCATION
