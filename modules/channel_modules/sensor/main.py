"""
NemoHeadUnit-Wireless v2 — channel_modules/sensor

Module contract:
  Name        : sensor  (overridden by --module-name)
  Priority    : 1
  Channel ID  : supplied via --channel-id CLI arg (parsed by BaseChannelModule)
  SDR bytes   : supplied via --sdr-bytes-hex CLI arg, parsed by base into
                self.channel_config.
  Subscribes  : channel_manager.module_start
                channel_manager.module_stop
                aa.channel.open          {channel_id, ...}
                aa.channel.close         {channel_id}
                aa.frame.ch<channel_id>  {channel_id, message_id, encrypted, payload_hex}
                aa.session.active         {}
                aa.session.shutdown       {}
                sensor.driving_status     {status: int}
                sensor.night_mode         {night_mode: bool}
                sensor.gps                {latitude, longitude, bearing, speed, ...}
  Publishes   : channel_manager.module_ready              {name, priority}
                aa.frame.send             bytes  (via BaseChannelModule.send_frame)
                sensor.state              {state}  IDLE | OPEN
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent
_CHANNEL_MODS = _HERE.parent
_MODULES      = _CHANNEL_MODS.parent
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

for _p in (_REPO_ROOT, _MODULES, _CHANNEL_MODS, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# Proto — control
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse   # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage             # noqa: E402
from oaa.common.StatusEnum_pb2 import Status                                 # noqa: E402

# Proto — sensor
from oaa.sensor.SensorChannelMessageIdsEnum_pb2 import SensorChannelMessage         # noqa: E402
from oaa.sensor.SensorStartResponseMessage_pb2 import SensorStartResponseMessage

# ---------------------------------------------------------------------------
# AA message IDs
# ---------------------------------------------------------------------------

_MSG_CHANNEL_OPEN_REQUEST      = ControlMessage.CHANNEL_OPEN_REQUEST
_MSG_CHANNEL_OPEN_RESPONSE     = ControlMessage.CHANNEL_OPEN_RESPONSE
_MSG_SENSOR_START_REQUEST      = SensorChannelMessage.SENSOR_REQUEST
_MSG_SENSOR_START_RESPONSE     = SensorChannelMessage.SENSOR_START_RESPONSE
_MSG_SENSOR_EVENT_INDICATION   = SensorChannelMessage.SENSOR_EVENT_INDICATION

# ---------------------------------------------------------------------------
# SensorType wire values
# ---------------------------------------------------------------------------

SENSOR_DRIVING_STATUS = 1
SENSOR_NIGHT_MODE     = 4
SENSOR_LOCATION       = 6

DRIVE_STATUS_UNRESTRICTED     = 0
DRIVE_STATUS_NO_VIDEO         = 1
DRIVE_STATUS_NO_KEYB_INPUT    = 2
DRIVE_STATUS_NO_VOICE         = 4
DRIVE_STATUS_NO_CONFIG        = 8
DRIVE_STATUS_LIMIT_MESS_LEN   = 16
DRIVE_STATUS_FULLY_RESTRICTED = 31


# ---------------------------------------------------------------------------
# SensorModule
# ---------------------------------------------------------------------------

class SensorModule(BaseChannelModule):
    MODULE_NAME = "sensor"
    CHANNEL_ID  = -1
    PRIORITY    = 1

    def __init__(self) -> None:
        super().__init__()
        self._state             = "IDLE"
        self._started_sensors: set = set()

    def _init(self) -> None:
        self.log.info(
            "SensorModule _init: channel_id=%d channel_config=%s",
            self.CHANNEL_ID, self.channel_config,
        )

    def _cleanup(self) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")

    def on_aa_session_active(self, topic: str, payload: dict) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")
        self.log.info("AA session active — sensor ready")

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")
        self.log.info("AA session shutdown — sensor reset")

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self.log.info("Sensor channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")
        self.log.info("Sensor channel %d closed", channel_id)

    def on_frame(self, channel_id: int, message_id: int, encrypted: bool, data: bytes) -> None:
        """Dispatch incoming frame by AA message_id (already extracted by tcp_server)."""
        if message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(data)
        elif message_id == _MSG_SENSOR_START_REQUEST:
            self._handle_sensor_start_request(data)
        else:
            self.log.debug(
                "Unhandled sensor msg_id=0x%04x len=%d", message_id, len(data)
            )

    def _handle_open_request(self, body: bytes) -> None:
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString())
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest -> ChannelOpenResponse sent (STATUS_SUCCESS)")

    def _handle_sensor_start_request(self, body: bytes) -> None:
        sensor_type = _parse_sensor_start_request(body)
        self.log.info("SensorStartRequest sensor_type=%d", sensor_type)
        self._started_sensors.add(sensor_type)

        resp = SensorStartResponseMessage()
        resp.status = Status.OK
        self.send_frame(_MSG_SENSOR_START_RESPONSE, resp.SerializeToString())
        self.log.debug("SensorStartResponseMessage sent for sensor_type=%d", sensor_type)

        batch_bytes = _build_default_sensor_batch(sensor_type)
        if batch_bytes:
            self.send_frame(_MSG_SENSOR_EVENT_INDICATION, batch_bytes)
            self.log.info(
                "Initial SensorEventIndication sent for sensor_type=%d", sensor_type
            )

    def on_sensor_driving_status(self, topic: str, payload: dict) -> None:
        if self._state != "OPEN" or SENSOR_DRIVING_STATUS not in self._started_sensors:
            self.log.debug("on_sensor_driving_status: channel not ready — dropping")
            return
        status = int(payload.get("status", DRIVE_STATUS_UNRESTRICTED))
        self._send_sensor_event(_bytes_field(1, _varint_field(1, status)))
        self.log.debug("SensorEventIndication driving_status=%d sent", status)

    def on_sensor_night_mode(self, topic: str, payload: dict) -> None:
        if self._state != "OPEN" or SENSOR_NIGHT_MODE not in self._started_sensors:
            self.log.debug("on_sensor_night_mode: channel not ready — dropping")
            return
        night = bool(payload.get("night_mode", False))
        self._send_sensor_event(_bytes_field(4, _bool_field(1, night)))
        self.log.debug("SensorEventIndication night_mode=%s sent", night)

    def on_sensor_gps(self, topic: str, payload: dict) -> None:
        if self._state != "OPEN" or SENSOR_LOCATION not in self._started_sensors:
            self.log.debug("on_sensor_gps: channel not ready — dropping")
            return
        lat  = int(payload.get("latitude",  0))
        lon  = int(payload.get("longitude", 0))
        bear = int(payload.get("bearing",   0))
        spd  = int(payload.get("speed",     0))
        gps_data = (
            _sfixed32_field(1, lat)
            + _sfixed32_field(2, lon)
            + _sfixed32_field(3, bear)
            + _sfixed32_field(4, spd)
        )
        if "altitude" in payload:
            gps_data += _sfixed32_field(5, int(payload["altitude"]))
        if "accuracy" in payload:
            gps_data += _sfixed32_field(6, int(payload["accuracy"]))
        if "timestamp" in payload:
            gps_data += _int64_field(7, int(payload["timestamp"]))
        self._send_sensor_event(_bytes_field(6, gps_data))
        self.log.debug(
            "SensorEventIndication GPS lat=%d lon=%d speed=%d sent", lat, lon, spd
        )

    def _send_sensor_event(self, batch_bytes: bytes) -> None:
        self.send_frame(_MSG_SENSOR_EVENT_INDICATION, batch_bytes)

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish("sensor.state", {"state": new_state})
        self.log.info("sensor.state -> %s", new_state)

    def run(self) -> None:
        self.bus.subscribe("aa.session.active",        self.on_aa_session_active)
        self.bus.subscribe("aa.session.shutdown",      self.on_aa_session_shutdown)
        self.bus.subscribe("sensor.driving_status",    self.on_sensor_driving_status)
        self.bus.subscribe("sensor.night_mode",        self.on_sensor_night_mode)
        self.bus.subscribe("sensor.gps",               self.on_sensor_gps)
        super().run()


# ---------------------------------------------------------------------------
# Module-level default sensor batch helper
# ---------------------------------------------------------------------------

def _build_default_sensor_batch(sensor_type: int) -> bytes:
    if sensor_type == SENSOR_DRIVING_STATUS:
        return _bytes_field(1, _varint_field(1, DRIVE_STATUS_UNRESTRICTED))
    if sensor_type == SENSOR_NIGHT_MODE:
        return _bytes_field(4, _bool_field(1, False))
    return b""


# ---------------------------------------------------------------------------
# Minimal hand-rolled protobuf helpers
# ---------------------------------------------------------------------------

def _encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = []
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_varint(buf: bytes, pos: int) -> tuple[int | None, int]:
    result = 0
    shift  = 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            return None, pos
    return None, pos


def _field_tag(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _varint_field(field_number: int, value: int) -> bytes:
    return _field_tag(field_number, 0) + _encode_varint(value)


def _bool_field(field_number: int, value: bool) -> bytes:
    return _varint_field(field_number, 1 if value else 0)


def _bytes_field(field_number: int, data: bytes) -> bytes:
    return _field_tag(field_number, 2) + _encode_varint(len(data)) + data


def _sfixed32_field(field_number: int, value: int) -> bytes:
    return _field_tag(field_number, 5) + struct.pack("<i", value)


def _int64_field(field_number: int, value: int) -> bytes:
    return _varint_field(field_number, value)


def _parse_sensor_start_request(body: bytes) -> int:
    pos = 0
    while pos < len(body):
        tag_byte     = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07
        if field_number == 1 and wire_type == 0:
            val, _ = _read_varint(body, pos)
            return val if val is not None else 0
        if wire_type == 0:
            _, pos = _read_varint(body, pos)
        elif wire_type == 1:
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(body, pos)
            if length:
                pos += length
        elif wire_type == 5:
            pos += 4
        else:
            break
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    SensorModule().run()
