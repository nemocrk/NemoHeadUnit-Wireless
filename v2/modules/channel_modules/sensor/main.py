"""
NemoHeadUnit-Wireless v2 — channel_modules/sensor

Module contract:
  Name        : sensor  (overridden by --module-name)
  Priority    : 1
  Channel ID  : supplied via --channel-id CLI arg (parsed by BaseChannelModule)
  SDR bytes   : supplied via --sdr-bytes-hex CLI arg, parsed by base into
                self.channel_config.
  Subscribes  : channel_manager.module_readytostart
                channel_manager.module_start
                channel_manager.module_stop
                aa.channel.open          {channel_id, ...}
                aa.channel.close         {channel_id}
                aa.frame.ch<channel_id>    raw bytes (ChannelOpenRequest, SensorStartRequest)
                aa.session.active         {}
                aa.session.shutdown       {}
                sensor.driving_status     {status: int}   ← from vehicle integration
                sensor.night_mode         {night_mode: bool}
                sensor.gps                {latitude, longitude, bearing, speed, ...}
  Publishes   : channel_manager.module_module_ready       {name, priority}
                channel_manager.module_ready              {name, priority}
                aa.frame.send             {channel_id, flags, payload_hex}
                                            ← ChannelOpenResponse
                                            ← SensorStartResponse
                                            ← SensorEventIndication (SensorBatch)
                sensor.state              {state}  IDLE | OPEN

Flow:
  1. BaseChannelModule parses CLI and populates self.CHANNEL_ID and
     self.channel_config from --channel-id / --sdr-bytes-hex.
  2. channel_manager.module_ready is published lazily by base once _init_done, config_loaded
     and channel_config is not None.
  3. On aa.frame.ch<channel_id>:
       - ChannelOpenRequest   → reply ChannelOpenResponse (STATUS_SUCCESS)
       - SensorStartRequest   → reply SensorStartResponse (STATUS_SUCCESS)
                                + immediate SensorEventIndication for:
                                    SENSOR_DRIVING_STATUS_DATA → DRIVE_STATUS_UNRESTRICTED
                                    SENSOR_NIGHT_MODE          → night_mode = False
  4. On sensor.driving_status / sensor.night_mode / sensor.gps: build and send
     SensorEventIndication (SensorBatch) if channel is OPEN.
  5. On aa.session.shutdown: reset to IDLE.

SensorType wire values (from aasdk_proto SensorType.proto):
  SENSOR_DRIVING_STATUS_DATA = 1
  SENSOR_NIGHT_MODE          = 4
  SENSOR_LOCATION            = 6

DrivingStatus wire values:
  DRIVE_STATUS_UNRESTRICTED   = 0
  DRIVE_STATUS_NO_VIDEO       = 1
  DRIVE_STATUS_NO_KEYB_INPUT  = 2
  DRIVE_STATUS_NO_VOICE       = 4
  DRIVE_STATUS_NO_CONFIG      = 8
  DRIVE_STATUS_LIMIT_MESS_LEN = 16
  DRIVE_STATUS_FULLY_RESTRICTED = 31

No proto dependency — all encoding is hand-rolled.
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

_HERE         = Path(__file__).parent
_CHANNEL_MODS = _HERE.parent
_MODULES      = _CHANNEL_MODS.parent
_V2           = _MODULES.parent

for _p in (_V2, _MODULES, _CHANNEL_MODS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# AA frame constants
# ---------------------------------------------------------------------------

_FLAG_FIRST     = 0x01
_FLAG_LAST      = 0x02
_FLAG_ENCRYPTED = 0x08
_FLAG_FULL      = _FLAG_FIRST | _FLAG_LAST | _FLAG_ENCRYPTED  # 0x0B

_MSG_CHANNEL_OPEN_REQUEST      = 0x8003
_MSG_CHANNEL_OPEN_RESPONSE     = 0x8005
_MSG_SENSOR_START_REQUEST      = 0x8009
_MSG_SENSOR_START_RESPONSE     = 0x800A
_MSG_SENSOR_EVENT_INDICATION   = 0x0001

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
    """
    AA Sensor channel module.

    Handles ChannelOpenRequest and SensorStartRequest from the phone,
    then sends SensorEventIndication (SensorBatch) whenever sensor data
    is published on the bus by the vehicle integration layer.

    channel_id and SDR bytes are provided at spawn time via CLI by
    channel_manager and parsed by BaseChannelModule into self.CHANNEL_ID
    and self.channel_config.  channel_manager.module_ready is hard-blocked by base if
    channel_config is None.
    """

    MODULE_NAME = "sensor"  # overridden by --module-name CLI
    CHANNEL_ID  = -1         # overridden by --channel-id CLI
    PRIORITY    = 1

    def __init__(self) -> None:
        super().__init__()
        self._state             = "IDLE"   # IDLE | OPEN
        self._started_sensors: set = set()

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        self.log.info(
            "SensorModule _init: channel_id=%d channel_config=%s",
            self.CHANNEL_ID,
            self.channel_config,
        )

    def _cleanup(self) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_active(self, topic: str, payload: dict) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")
        self.log.info("AA session active — sensor ready")

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")
        self.log.info("AA session shutdown — sensor reset")

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self.log.info("Sensor channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        self._started_sensors.clear()
        self._set_state("IDLE")
        self.log.info("Sensor channel %d closed", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        result = self._decode_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload — dropping")
            return

        message_id, body = result

        if message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_channel_open_request(body)
        elif message_id == _MSG_SENSOR_START_REQUEST:
            self._handle_sensor_start_request(body)
        else:
            self.log.debug(
                "Unhandled sensor msg_id=0x%04x len=%d", message_id, len(body)
            )

    # ------------------------------------------------------------------
    # Incoming message handlers
    # ------------------------------------------------------------------

    def _handle_channel_open_request(self, body: bytes) -> None:
        proto_body = b"\x08\x00"
        frame = self._encode_frame(self.CHANNEL_ID, _MSG_CHANNEL_OPEN_RESPONSE, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest → ChannelOpenResponse sent (STATUS_SUCCESS)")

    def _handle_sensor_start_request(self, body: bytes) -> None:
        sensor_type = _parse_sensor_start_request(body)
        self.log.info("SensorStartRequest sensor_type=%d", sensor_type)
        self._started_sensors.add(sensor_type)

        resp_body = b"\x08\x00"
        frame = self._encode_frame(self.CHANNEL_ID, _MSG_SENSOR_START_RESPONSE, resp_body)
        self.bus.publish("aa.frame.send", frame)
        self.log.debug("SensorStartResponse sent for sensor_type=%d", sensor_type)

        batch_bytes = self._build_default_sensor_batch(sensor_type)
        if batch_bytes:
            frame = self._encode_frame(
                self.CHANNEL_ID, _MSG_SENSOR_EVENT_INDICATION, batch_bytes
            )
            self.bus.publish("aa.frame.send", frame)
            self.log.info(
                "Initial SensorEventIndication sent for sensor_type=%d", sensor_type
            )

    @staticmethod
    def _build_default_sensor_batch(sensor_type: int) -> bytes:
        if sensor_type == SENSOR_DRIVING_STATUS:
            return _bytes_field(1, _varint_field(1, DRIVE_STATUS_UNRESTRICTED))
        if sensor_type == SENSOR_NIGHT_MODE:
            return _bytes_field(4, _bool_field(1, False))
        return b""

    # ------------------------------------------------------------------
    # Outgoing sensor updates  (bus events from vehicle integration)
    # ------------------------------------------------------------------

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

        lat  = int(float(payload.get("latitude",  0)) * 1e7)
        lon  = int(float(payload.get("longitude", 0)) * 1e7)
        bear = int(float(payload.get("bearing",   0)) * 1e6)
        spd  = int(float(payload.get("speed",     0)) * 1e3)

        gps_data = (
            _sfixed32_field(1, lat)
            + _sfixed32_field(2, lon)
            + _sfixed32_field(3, bear)
            + _sfixed32_field(4, spd)
        )
        if "altitude" in payload:
            gps_data += _sfixed32_field(5, int(float(payload["altitude"]) * 1e3))
        if "accuracy" in payload:
            gps_data += _sfixed32_field(6, int(float(payload["accuracy"]) * 1e3))
        if "timestamp" in payload:
            gps_data += _int64_field(7, int(payload["timestamp"]))

        self._send_sensor_event(_bytes_field(6, gps_data))
        self.log.debug(
            "SensorEventIndication GPS lat=%d lon=%d speed=%d sent", lat, lon, spd
        )

    def _send_sensor_event(self, batch_bytes: bytes) -> None:
        frame = self._encode_frame(
            self.CHANNEL_ID, _MSG_SENSOR_EVENT_INDICATION, batch_bytes
        )
        self.bus.publish("aa.frame.send", frame)

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        self._state = new_state
        self.bus.publish("sensor.state", {"state": new_state})
        self.log.info("sensor.state → %s", new_state)

    # ------------------------------------------------------------------
    # Frame encode / decode helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_frame(channel_id: int, message_id: int, proto_body: bytes) -> dict:
        payload = struct.pack(">H", message_id) + proto_body
        return {
            "channel_id":  channel_id,
            "flags":       _FLAG_FULL,
            "payload_hex": payload.hex(),
        }

    @staticmethod
    def _decode_frame(data: bytes) -> tuple[int, bytes] | None:
        if len(data) < 2:
            return None
        message_id = struct.unpack_from(">H", data, 0)[0]
        return message_id, data[2:]

    # ------------------------------------------------------------------
    # run() override
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("aa.session.active",        self.on_aa_session_active)
        self.bus.subscribe("aa.session.shutdown",      self.on_aa_session_shutdown)
        self.bus.subscribe("sensor.driving_status",    self.on_sensor_driving_status)
        self.bus.subscribe("sensor.night_mode",        self.on_sensor_night_mode)
        self.bus.subscribe("sensor.gps",               self.on_sensor_gps)
        super().run()


# ---------------------------------------------------------------------------
# Minimal hand-rolled protobuf helpers  (no proto dependency)
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
