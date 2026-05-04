"""
service_discovery.py — Build the ServiceDiscoveryResponse for channel 0 handshake.

Port of openauto-prodigy ServiceDiscoveryBuilder.cpp to Python.
Proto classes are pre-compiled _pb2.py files in v2/protos/oaa/.

Only the subset of channels required for a minimal wireless AA session
is advertised here.  Additional channels can be added incrementally.

Channels advertised:
    0  — Control        (implicit, not in descriptor list)
    1  — Input          (touch + keycodes)
    2  — Sensor
    3  — Video          (H.264, 720p, 30fps)
    4  — MediaAudio     (PCM 48kHz stereo)
    5  — SpeechAudio    (PCM 48kHz mono)
    6  — SystemAudio    (PCM 16kHz mono)
    7  — AVInput
    8  — Bluetooth
    9  — Navigation
   10  — MediaStatus
   14  — WiFi

Schema
------
_SCHEMA is generated at import time from ServiceDiscoveryResponse.DESCRIPTOR
via proto_utils.schema_from_proto_message(). It reflects the full proto tree
as a config_schema-compatible dict (ConfigFieldMessage / ConfigFieldList /
ConfigFieldOneof / ConfigFieldSchema leaves).

Semantic defaults (SEMANTIC_DEFAULTS) are applied on top of the raw proto
defaults so that the config UI starts with sane values instead of proto
zero-values.

Entry points
------------
build_service_discovery_response(cfg, bt_mac, wifi_bssid)
    Legacy flat-dict API. Still fully functional. Used until config_manager
    supports nested/structured cfg dicts.

build_from_schema_cfg(schema_cfg, bt_mac, wifi_bssid)
    New proto-driven API. Accepts a nested dict matching _SCHEMA structure
    (i.e. field names are proto field names, not the old flat keys).
    Use this once config_manager is updated to handle ConfigFieldMessage /
    ConfigFieldList / ConfigFieldOneof.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT  = Path(__file__).parent.parent.parent.parent
_PROTO_ROOT = _REPO_ROOT / "v2" / "protos"

for _p in (_REPO_ROOT, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.proto_utils import encode_proto, schema_from_proto_message  # noqa: E402
from shared.config_schema import (
    AnyFieldSchema,
    ConfigFieldList,
    ConfigFieldMessage,
    ConfigFieldOneof,
    ConfigFieldSchema,
)

# Control / discovery
from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import (
    ServiceDiscoveryResponse,
)
from v2.protos.oaa.control.ChannelDescriptorData_pb2 import ChannelDescriptor
from v2.protos.oaa.common.DriverPositionEnum_pb2 import DriverPosition

# AV / Video / Audio enums
from v2.protos.oaa.av.AVChannelData_pb2 import AVChannel  # noqa
from v2.protos.oaa.av.AVStreamTypeEnum_pb2 import AVStreamType
from v2.protos.oaa.audio.AudioTypeEnum_pb2 import AudioType
from v2.protos.oaa.audio.AudioConfigData_pb2 import AudioConfig
from v2.protos.oaa.video.VideoConfigData_pb2 import VideoConfig
from v2.protos.oaa.video.VideoResolutionEnum_pb2 import VideoResolution
from v2.protos.oaa.video.VideoFPSEnum_pb2 import VideoFPS
from v2.protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType

# Sensor
from v2.protos.oaa.sensor.SensorChannelData_pb2 import SensorChannel
from v2.protos.oaa.sensor.SensorTypeEnum_pb2 import SensorType

# Bluetooth
from v2.protos.oaa.bluetooth.BluetoothChannelData_pb2 import BluetoothChannel
from v2.protos.oaa.bluetooth.BluetoothPairingMethodEnum_pb2 import BluetoothPairingMethod

# WiFi
from v2.protos.oaa.wifi.WifiChannelData_pb2 import WifiChannel

# Navigation
from v2.protos.oaa.navigation.NavigationChannelData_pb2 import NavigationChannel
from v2.protos.oaa.navigation.NavigationTypeEnum_pb2 import NavigationType
from v2.protos.oaa.navigation.NavigationImageOptionsData_pb2 import NavigationImageOptions

# Media
from v2.protos.oaa.media.MediaChannelData_pb2 import MediaInfoChannel

# AV Input
from v2.protos.oaa.av.AVInputChannelData_pb2 import AVInputChannel

# Phone status
try:
    from v2.protos.oaa.phone.PhoneStatusChannelData_pb2 import PhoneStatusChannel
    _HAS_PHONE_STATUS = True
except ImportError:
    _HAS_PHONE_STATUS = False

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Proto-derived schema
# ---------------------------------------------------------------------------

# Full config schema generated from the ServiceDiscoveryResponse proto tree.
# Generated once at import time; reflects ALL fields in the proto, including
# nested messages, repeated fields, and oneof groups.
_SCHEMA: dict[str, AnyFieldSchema] = schema_from_proto_message(
    ServiceDiscoveryResponse.DESCRIPTOR
)

# Semantic defaults: override proto zero-values with sane operational values.
# These are applied via _apply_defaults_to_schema() at the end of this section.
# Structure mirrors proto field names (NOT the old flat keys).
SEMANTIC_DEFAULTS: dict[str, Any] = {
    "head_unit_name":        "NemoHeadUnit",
    "headunit_manufacturer": "Nemo",
    "headunit_model":        "NemoHeadUnit-Wireless",
    "sw_version":            "2.0",
    "sw_build":              "1",
    "car_model":             "Universal",
    "car_year":              "2025",
    "car_serial":            "20250101",
    "can_play_native_media_during_vr": True,
}


def _apply_defaults_to_schema(
    schema: dict[str, AnyFieldSchema],
    overrides: dict[str, Any],
) -> None:
    """Apply semantic default overrides to scalar leaves in *schema* in-place.

    Only top-level scalar keys are overridden; nested message defaults are
    left to proto zero-values unless explicitly included in *overrides*.
    """
    for key, value in overrides.items():
        node = schema.get(key)
        if isinstance(node, ConfigFieldSchema):
            schema[key] = ConfigFieldSchema(
                type=node.type,
                default=value,
                min=node.min,
                max=node.max,
                choices=node.choices,
            )
        else:
            log.debug(
                "_apply_defaults_to_schema: key %r not found as scalar in schema — skipped",
                key,
            )


_apply_defaults_to_schema(_SCHEMA, SEMANTIC_DEFAULTS)


# ---------------------------------------------------------------------------
# Legacy flat-dict defaults (backward compat with build_service_discovery_response)
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    "hu.name":                    "NemoHeadUnit",
    "hu.make":                    "Nemo",
    "hu.model":                   "NemoHeadUnit-Wireless",
    "hu.sw_version":              "2.0",
    "video.resolution":           "VIDEO_1280x720",
    "video.fps":                  "_30",
    "video.dpi":                  140,
    "touch.width":                1280,
    "touch.height":               720,
    "audio.media.sample_rate":    48000,
    "audio.media.channel_count":  2,
    "audio.speech.sample_rate":   48000,
    "audio.system.sample_rate":   16000,
    "nav.min_interval_ms":        500,
    "nav.image.width":            64,
    "nav.image.height":           64,
}


# ---------------------------------------------------------------------------
# Proto-driven builder (new API)
# ---------------------------------------------------------------------------

def build_from_schema_cfg(
    schema_cfg: dict,
    bt_mac:     str = "00:00:00:00:00:00",
    wifi_bssid: str = "",
) -> bytes:
    """Build and serialise a ServiceDiscoveryResponse from a nested schema cfg dict.

    *schema_cfg* must mirror the proto field-name tree produced by _SCHEMA.
    Use this entry point once config_manager supports ConfigFieldMessage /
    ConfigFieldList / ConfigFieldOneof.

    Args:
        schema_cfg:  nested dict with proto field names as keys.
        bt_mac:      local BT adapter MAC (runtime, not persisted).
        wifi_bssid:  local WiFi BSSID (runtime, not persisted).

    Returns:
        Serialised proto bytes ready to send on the wire.
    """
    resp = ServiceDiscoveryResponse()
    _dict_to_proto(resp, schema_cfg)

    # Runtime-only fields injected after generic population
    for ch in resp.channels:
        bt = ch.HasField("bluetooth_channel") and ch.bluetooth_channel
        if bt:
            bt.adapter_address = bt_mac
        wf = ch.HasField("wifi_channel") and ch.wifi_channel
        if wf:
            wf.bssid = wifi_bssid

    return encode_proto(resp)


def _dict_to_proto(msg: Any, data: dict) -> None:
    """Recursively populate a proto message from a nested plain dict.

    Keys in *data* must match proto field names exactly.
    Enum values are accepted as string names (e.g. "VIDEO_1280x720") and
    resolved via the field's enum_type descriptor.
    Repeated fields expect a list of values or dicts.
    """
    descriptor = msg.DESCRIPTOR
    fields_by_name = descriptor.fields_by_name

    for key, value in data.items():
        if key not in fields_by_name:
            log.debug("_dict_to_proto(%s): unknown field %r — skipped", descriptor.name, key)
            continue

        field_desc = fields_by_name[key]
        is_repeated = field_desc.label == field_desc.LABEL_REPEATED

        if is_repeated:
            proto_list = getattr(msg, key)
            for item in (value if isinstance(value, list) else []):
                if field_desc.message_type is not None:
                    entry = proto_list.add()
                    if isinstance(item, dict):
                        _dict_to_proto(entry, item)
                else:
                    proto_list.append(_coerce_scalar(field_desc, item))
            continue

        if field_desc.message_type is not None:
            if isinstance(value, dict) and value:
                _dict_to_proto(getattr(msg, key), value)
            continue

        try:
            setattr(msg, key, _coerce_scalar(field_desc, value))
        except (AttributeError, ValueError, TypeError) as exc:
            log.warning("_dict_to_proto(%s): cannot set %r=%r — %s", descriptor.name, key, value, exc)


def _coerce_scalar(field_desc: Any, value: Any) -> Any:
    """Coerce *value* to the correct Python type for *field_desc*.

    Enum fields accept either an integer or a string name.
    """
    from google.protobuf import descriptor as _descriptor

    if field_desc.type == _descriptor.FieldDescriptor.TYPE_ENUM:
        if isinstance(value, str):
            return field_desc.enum_type.values_by_name[value].number
        return int(value)

    if field_desc.type == _descriptor.FieldDescriptor.TYPE_BOOL:
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "on"}
        return bool(value)

    if field_desc.type in {
        _descriptor.FieldDescriptor.TYPE_INT32,
        _descriptor.FieldDescriptor.TYPE_INT64,
        _descriptor.FieldDescriptor.TYPE_UINT32,
        _descriptor.FieldDescriptor.TYPE_UINT64,
        _descriptor.FieldDescriptor.TYPE_FIXED32,
        _descriptor.FieldDescriptor.TYPE_FIXED64,
        _descriptor.FieldDescriptor.TYPE_SFIXED32,
        _descriptor.FieldDescriptor.TYPE_SFIXED64,
        _descriptor.FieldDescriptor.TYPE_SINT32,
        _descriptor.FieldDescriptor.TYPE_SINT64,
    }:
        return int(value)

    if field_desc.type in {
        _descriptor.FieldDescriptor.TYPE_FLOAT,
        _descriptor.FieldDescriptor.TYPE_DOUBLE,
    }:
        return float(value)

    return str(value)  # STRING / BYTES fallback


# ---------------------------------------------------------------------------
# Legacy flat-dict API (backward compat — unchanged)
# ---------------------------------------------------------------------------

def build_service_discovery_response(
    cfg:        dict,
    bt_mac:     str = "00:00:00:00:00:00",
    wifi_bssid: str = "",
) -> bytes:
    """Return the serialised ServiceDiscoveryResponse protobuf bytes.

    Args:
        cfg:        config dict pre-loaded from config_manager (keys as in DEFAULTS).
        bt_mac:     local BT adapter MAC address (runtime value, not persisted).
        wifi_bssid: local WiFi BSSID (runtime value, not persisted).
    """
    resp = ServiceDiscoveryResponse()
    resp.head_unit_name   = cfg.get("hu.name",       DEFAULTS["hu.name"])
    resp.car_model        = "Universal"
    resp.car_year         = "2025"
    resp.car_serial       = "20250101"
    resp.driver_position  = DriverPosition.LEFT
    resp.headunit_manufacturer     = cfg.get("hu.make",       DEFAULTS["hu.make"])
    resp.headunit_model            = cfg.get("hu.model",      DEFAULTS["hu.model"])
    resp.sw_build         = "1"
    resp.sw_version       = cfg.get("hu.sw_version", DEFAULTS["hu.sw_version"])
    resp.can_play_native_media_during_vr = True

    descriptors = [
        _build_video_descriptor(cfg),
        _build_media_audio_descriptor(cfg),
        _build_speech_audio_descriptor(cfg),
        _build_system_audio_descriptor(cfg),
        _build_input_descriptor(cfg),
        _build_sensor_descriptor(),
        _build_bluetooth_descriptor(bt_mac),
        _build_wifi_descriptor(wifi_bssid),
        _build_av_input_descriptor(),
        _build_navigation_descriptor(cfg),
        _build_media_status_descriptor(),
    ]
    if _HAS_PHONE_STATUS:
        descriptors.append(_build_phone_status_descriptor())

    for desc in descriptors:
        resp.channels.add().MergeFrom(desc)

    return encode_proto(resp)


# ---------------------------------------------------------------------------
# Channel descriptor builders (legacy — mirror ServiceDiscoveryBuilder.cpp)
# ---------------------------------------------------------------------------

def _build_video_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 3
    av = desc.av_channel
    av.stream_type = AVStreamType.VIDEO

    resolution_name = cfg.get("video.resolution", DEFAULTS["video.resolution"])
    fps_name        = cfg.get("video.fps",         DEFAULTS["video.fps"])

    cfg_pb = av.video_configs.add()
    cfg_pb.video_resolution = VideoResolution.Enum.Value(resolution_name)
    cfg_pb.video_fps        = VideoFPS.Enum.Value(fps_name)
    cfg_pb.margin_width     = 0
    cfg_pb.margin_height    = 0
    cfg_pb.dpi              = int(cfg.get("video.dpi", DEFAULTS["video.dpi"]))
    cfg_pb.codec            = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP
    return desc


def _build_media_audio_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 4
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.MEDIA
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio.media.sample_rate",   DEFAULTS["audio.media.sample_rate"]))
    ac.bit_depth     = 16
    ac.channel_count = int(cfg.get("audio.media.channel_count", DEFAULTS["audio.media.channel_count"]))
    return desc


def _build_speech_audio_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 5
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.SPEECH
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio.speech.sample_rate", DEFAULTS["audio.speech.sample_rate"]))
    ac.bit_depth     = 16
    ac.channel_count = 1
    return desc


def _build_system_audio_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 6
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.SYSTEM
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio.system.sample_rate", DEFAULTS["audio.system.sample_rate"]))
    ac.bit_depth     = 16
    ac.channel_count = 1
    return desc


def _build_input_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 1
    inp = desc.input_channel
    ts = inp.touch_screen_configs.add()
    ts.width  = int(cfg.get("touch.width",  DEFAULTS["touch.width"]))
    ts.height = int(cfg.get("touch.height", DEFAULTS["touch.height"]))
    for kc in [3, 4, 84, 85, 86, 87, 88, 126, 127, 219, 231]:
        inp.supported_keycodes.append(kc)
    return desc


def _build_sensor_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 2
    sc = desc.sensor_channel
    for st in [SensorType.NIGHT_DATA, SensorType.DRIVING_STATUS, SensorType.PARKING_BRAKE]:
        sc.sensors.add().type = st
    return desc


def _build_bluetooth_descriptor(bt_mac: str) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 8
    bt = desc.bluetooth_channel
    bt.adapter_address = bt_mac
    bt.supported_pairing_methods.append(BluetoothPairingMethod.PIN)
    return desc


def _build_wifi_descriptor(bssid: str) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 14
    wf = desc.wifi_channel
    wf.bssid = bssid
    return desc


def _build_av_input_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 7
    av = desc.av_input_channel
    av.stream_type = AVStreamType.AUDIO
    ac = av.audio_config
    ac.sample_rate   = 16000
    ac.bit_depth     = 16
    ac.channel_count = 1
    return desc


def _build_navigation_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 9
    nav = desc.navigation_channel
    nav.minimum_interval_ms             = int(cfg.get("nav.min_interval_ms", DEFAULTS["nav.min_interval_ms"]))
    nav.type                            = NavigationType.TURN_BY_TURN
    nav.image_options.width             = int(cfg.get("nav.image.width",  DEFAULTS["nav.image.width"]))
    nav.image_options.height            = int(cfg.get("nav.image.height", DEFAULTS["nav.image.height"]))
    nav.image_options.colour_depth_bits = 32
    return desc


def _build_media_status_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 10
    desc.media_info_channel.SetInParent()
    return desc


def _build_phone_status_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 11
    desc.phone_status_channel.SetInParent()
    return desc
