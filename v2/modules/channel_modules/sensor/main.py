"""
NemoHeadUnit-Wireless v2 — channel_modules/sensor

Module contract:
  Name        : sensor
  Priority    : 1
  Channel ID  : resolved at boot from oaa_control_channel config
                (stream_type == "SENSOR")  fallback: 10
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response           {module, config, requester}
                oaa.channel.open          {channel_id, ...}
                oaa.channel.close         {channel_id}
                oaa.frame.<channel_id>    raw bytes (ChannelOpenRequest, SensorStartRequest)
                aa.session.active         {}
                aa.session.shutdown       {}
                sensor.driving_status     {status: int}   ← from vehicle integration
                sensor.night_mode         {night_mode: bool}
                sensor.gps                {latitude, longitude, bearing, speed, ...}
  Publishes   : system.module_ready       {name, priority}
                system.ready             {name, priority}
                aa.frame.send            {channel_id, flags, payload_hex}
                                           ← ChannelOpenResponse
                                           ← SensorStartResponse
                                           ← SensorEventIndication (SensorBatch)
                sensor.state             {state}  IDLE | OPEN

Flow:
  1. On system.start: request oaa_control_channel config to discover SENSOR channel id.
  2. On config.response: find channel with av_channel.stream_type == "SENSOR";
     fallback = 10.
  3. Publish system.ready once resolved.
  4. On oaa.frame.<channel_id>:
       - ChannelOpenRequest   → reply ChannelOpenResponse (STATUS_SUCCESS)
       - SensorStartRequest   → reply SensorStartResponse (STATUS_SUCCESS)
                                + immediate SensorEventIndication for:
                                    SENSOR_DRIVING_STATUS_DATA → DRIVE_STATUS_UNRESTRICTED
                                    SENSOR_NIGHT_MODE          → night_mode = False
  5. On sensor.driving_status / sensor.night_mode / sensor.gps: build and send
     SensorEventIndication (SensorBatch) if channel is OPEN.
  6. On aa.session.shutdown: reset to IDLE.

SensorType wire values (from aasdk_proto SensorType.proto):
  SENSOR_DRIVING_STATUS_DATA = 1
  SENSOR_NIGHT_MODE          = 4
  SENSOR_LOCATION            = 6

DrivingStatus wire values:
  DRIVE_STATUS_UNRESTRICTED  = 0
  DRIVE_STATUS_NO_VIDEO      = 1
  DRIVE_STATUS_NO_KEYB_INPUT = 2
  DRIVE_STATUS_NO_VOICE      = 4
  DRIVE_STATUS_NO_CONFIG     = 8
  DRIVE_STATUS_LIMIT_MESS_LEN = 16
  DRIVE_STATUS_FULLY_RESTRICTED = 31

No proto dependency — all encoding is hand-rolled.
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

_HERE         = Path(__file__).parent          # v2/modules/channel_modules/sensor/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/

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

_SENSOR_CHANNEL_FALLBACK = 10

# ---------------------------------------------------------------------------
# SensorType wire values
# ---------------------------------------------------------------------------

SENSOR_DRIVING_STATUS = 1
SENSOR_NIGHT_MODE     = 4
SENSOR_LOCATION       = 6

# DrivingStatus wire values
DRIVE_STATUS_UNRESTRICTED   = 0
DRIVE_STATUS_NO_VIDEO       = 1
DRIVE_STATUS_NO_KEYB_INPUT  = 2
DRIVE_STATUS_NO_VOICE       = 4
DRIVE_STATUS_NO_CONFIG      = 8
DRIVE_STATUS_LIMIT_MESS_LEN = 16
DRIVE_STATUS_FULLY_RESTRICTED = 31


# ---------------------------------------------------------------------------
# SensorModule
# ---------------------------------------------------------------------------

class SensorModule(BaseChannelModule):
    """
    OAA Sensor channel module.

    Handles ChannelOpenRequest and SensorStartRequest from the phone,
    then sends SensorEventIndication (SensorBatch) whenever sensor data
    is published on the bus by the vehicle integration layer.
    """

    MODULE_NAME = "sensor"
    CHANNEL_ID  = _SENSOR_CHANNEL_FALLBACK  # overwritten after config.response
    PRIORITY    = 1

    def __init__(self) -> None:
        super().__init__()
        self._channel_resolved = False
        self._state            = "IDLE"   # IDLE | OPEN
        # Track which sensor types the phone has started
        self._started_sensors: set = set()

    # ------------------------------------------------------------------
    # Channel discovery
    # ------------------------------------------------------------------

    def _request_oaa_config(self) -> None:
        self.bus.publish("config.get", {
            "module":    "oaa_control_channel",
            "requester": self.MODULE_NAME,
        })
        self.log.info("Requested oaa_control_channel config for SENSOR channel discovery")

    def on_config_response(self, topic: str, payload: dict) -> None:
        if payload.get("module") != "oaa_control_channel":
            return
        if payload.get("requester") != self.MODULE_NAME:
            return

        channels = payload.get("config", {}).get("channels", [])
        resolved = self._resolve_sensor_channel(channels)

        if resolved is not None:
            self.CHANNEL_ID = resolved
            self.log.info("SENSOR channel resolved: channel_id=%d", self.CHANNEL_ID)
        else:
            self.CHANNEL_ID = _SENSOR_CHANNEL_FALLBACK
            self.log.warning(
                "SENSOR channel not found in config — fallback channel_id=%d",
                self.CHANNEL_ID,
            )

        if not self._channel_resolved:
            self._channel_resolved = True
            frame_topic = f"oaa.frame.{self.CHANNEL_ID}"
            self.bus.subscribe(frame_topic, self._on_oaa_frame)
            self.log.info("Subscribed to %s", frame_topic)
            self.bus.publish("system.ready", {
                "name":     self.MODULE_NAME,
                "priority": self.PRIORITY,
            })
            self.log.info("system.ready published (priority=%d)", self.PRIORITY)

    @staticmethod
    def _resolve_sensor_channel(channels: list) -> int | None:
        for ch in channels:
            av = ch.get("av_channel", {})
            if av.get("stream_type") == "SENSOR":
                cid = ch.get("channel_id")
                if cid is not None:
                    return int(cid)
        return None

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        self._request_oaa_config()

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
        proto_body = b"\x08\x00"   # status = STATUS_SUCCESS (field 1, varint 0)
        frame = self._encode_frame(self.CHANNEL_ID, _MSG_CHANNEL_OPEN_RESPONSE, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest → ChannelOpenResponse sent (STATUS_SUCCESS)")

    def _handle_sensor_start_request(self, body: bytes) -> None:
        """
        Parse SensorStartRequest, reply SensorStartResponse,
        then send an immediate SensorEventIndication with default data.
        """
        sensor_type = _parse_sensor_start_request(body)
        self.log.info("SensorStartRequest sensor_type=%d", sensor_type)
        self._started_sensors.add(sensor_type)

        # SensorStartResponse: status = STATUS_SUCCESS
        resp_body = b"\x08\x00"
        frame = self._encode_frame(self.CHANNEL_ID, _MSG_SENSOR_START_RESPONSE, resp_body)
        self.bus.publish("aa.frame.send", frame)
        self.log.debug("SensorStartResponse sent for sensor_type=%d", sensor_type)

        # Immediate SensorEventIndication with default/safe values
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
        """
        Build a SensorBatch with safe default values for a given sensor type.

        SensorBatch proto:
          field 1 (bytes, repeated) = driving_status_data  (DrivingStatusData)
          field 4 (bytes, repeated) = night_mode_data      (NightModeData)
          field 6 (bytes, repeated) = gps_data             (GPSData)

        DrivingStatusData: field 1 (varint) = status
        NightModeData:     field 1 (bool)   = night_mode
        """
        if sensor_type == SENSOR_DRIVING_STATUS:
            # DrivingStatusData: status = DRIVE_STATUS_UNRESTRICTED (0)
            driving_status_data = _varint_field(1, DRIVE_STATUS_UNRESTRICTED)
            return _bytes_field(1, driving_status_data)

        if sensor_type == SENSOR_NIGHT_MODE:
            # NightModeData: night_mode = False (0)
            night_mode_data = _bool_field(1, False)
            return _bytes_field(4, night_mode_data)

        return b""

    # ------------------------------------------------------------------
    # Outgoing sensor updates  (bus events from vehicle integration)
    # ------------------------------------------------------------------

    def on_sensor_driving_status(self, topic: str, payload: dict) -> None:
        """
        Bus handler for sensor.driving_status.
        payload: {status: int}  — use DRIVE_STATUS_* constants.
        """
        if self._state != "OPEN" or SENSOR_DRIVING_STATUS not in self._started_sensors:
            self.log.debug("on_sensor_driving_status: channel not ready — dropping")
            return

        status = int(payload.get("status", DRIVE_STATUS_UNRESTRICTED))
        driving_data = _varint_field(1, status)
        batch_bytes  = _bytes_field(1, driving_data)
        self._send_sensor_event(batch_bytes)
        self.log.debug("SensorEventIndication driving_status=%d sent", status)

    def on_sensor_night_mode(self, topic: str, payload: dict) -> None:
        """
        Bus handler for sensor.night_mode.
        payload: {night_mode: bool}
        """
        if self._state != "OPEN" or SENSOR_NIGHT_MODE not in self._started_sensors:
            self.log.debug("on_sensor_night_mode: channel not ready — dropping")
            return

        night = bool(payload.get("night_mode", False))
        night_data  = _bool_field(1, night)
        batch_bytes = _bytes_field(4, night_data)
        self._send_sensor_event(batch_bytes)
        self.log.debug("SensorEventIndication night_mode=%s sent", night)

    def on_sensor_gps(self, topic: str, payload: dict) -> None:
        """
        Bus handler for sensor.gps.
        payload: {
            latitude:   float  (degrees * 1e7 as int, or float degrees),
            longitude:  float,
            bearing:    float  (degrees),
            speed:      float  (m/s),
            altitude:   float  (metres, optional),
            accuracy:   float  (metres, optional),
            timestamp:  int    (unix ms, optional),
        }

        GPSData proto (field 6 in SensorBatch):
          field 1 (sfixed32) = latitude   (* 1e7)
          field 2 (sfixed32) = longitude  (* 1e7)
          field 3 (sfixed32) = bearing    (* 1e6)
          field 4 (sfixed32) = speed      (* 1e3)
          field 5 (sfixed32) = altitude   (* 1e3, optional)
          field 6 (sfixed32) = accuracy   (* 1e3, optional)
          field 7 (int64)    = timestamp_ms (optional)
        """
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

        batch_bytes = _bytes_field(6, gps_data)
        self._send_sensor_event(batch_bytes)
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
        self.bus.subscribe("config.response",          self.on_config_response)
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
    """32-bit little-endian signed integer (wire type 5)."""
    return _field_tag(field_number, 5) + struct.pack("<i", value)


def _int64_field(field_number: int, value: int) -> bytes:
    """64-bit varint (wire type 0, zigzag not needed for positive timestamps)."""
    return _varint_field(field_number, value)


def _parse_sensor_start_request(body: bytes) -> int:
    """
    SensorRequest proto:
      field 1 (varint) = type (SensorType)
    Returns sensor_type int (default 0 if not found).
    """
    pos = 0
    while pos < len(body):
        tag_byte     = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07
        if field_number == 1 and wire_type == 0:
            val, _ = _read_varint(body, pos)
            return val if val is not None else 0
        # skip unknown
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
