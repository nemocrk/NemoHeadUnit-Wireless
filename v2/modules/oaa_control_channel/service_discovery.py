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
zero-values.  SEMANTIC_DEFAULTS includes a full 'channels' list with
proto-ready dicts for all 11 advertised channels so that config_manager
can seed and persist the entire structure in the YAML.

Entry points
------------
build_from_schema_cfg(schema_cfg, bt_mac, wifi_bssid)
    Primary API.  Accepts a nested dict matching the _SCHEMA / proto field-
    name tree.  Calls dict_to_proto() from proto_utils for generic population,
    then injects bt_mac / wifi_bssid into the relevant channels at runtime.
    bt_mac and wifi_bssid are never persisted in the YAML.

build_service_discovery_response(cfg, bt_mac, wifi_bssid)
    Legacy flat-dict API.  Still fully functional for backward compat.

channels_from_sdr_bytes(sdr_bytes)
    Parse serialised SDR bytes and return the channel list as a list of
    plain dicts suitable for publishing on the bus.  Used by handshake.py
    to populate the oaa_control_channel.open_channels payload without
    re-running build_from_schema_cfg().
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

from shared.proto_utils import encode_proto, schema_from_proto_message, dict_to_proto  # noqa: E402
from shared.config_schema import (           # noqa: E402
    AnyFieldSchema,
    ConfigFieldList,
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

_SCHEMA: dict[str, AnyFieldSchema] = schema_from_proto_message(
    ServiceDiscoveryResponse.DESCRIPTOR
)

# Semantic defaults: sane operational values overlaid on top of proto
# zero-values.  Includes the full 'channels' list as proto-ready dicts
# so config_manager can seed and persist the entire structure in the YAML.
#
# bt_mac / wifi_bssid are NOT included here — they are runtime-only values
# injected by build_from_schema_cfg() after loading from the bus.
SEMANTIC_DEFAULTS: dict[str, Any] = {
    # --- Head-unit identity ---
    "head_unit_name":        "NemoHeadUnit",
    "headunit_manufacturer": "Nemo",
    "headunit_model":        "NemoHeadUnit-Wireless",
    "sw_version":            "2.0",
    "sw_build":              "1",
    # --- Vehicle ---
    "car_model":  "Universal",
    "car_year":   "2025",
    "car_serial": "20250101",
    "driver_position": "LEFT",
    "can_play_native_media_during_vr": True,
    # --- Channel descriptors (proto field names, values as strings for enums) ---
    "channels": [
        # ch 1 — Input (touch)
        {
            "channel_id": 1,
            "input_channel": {
                "touch_screen_configs": [
                    {"width": 1280, "height": 720},
                ],
                "supported_keycodes": [3, 4, 84, 85, 86, 87, 88, 126, 127, 219, 231],
            },
        },
        # ch 2 — Sensor
        {
            "channel_id": 2,
            "sensor_channel": {
                "sensors": [
                    {"type": "NIGHT_DATA"},
                    {"type": "DRIVING_STATUS"},
                    {"type": "PARKING_BRAKE"},
                ],
            },
        },
        # ch 3 — Video (H.264, 720p, 30fps)
        {
            "channel_id": 3,
            "av_channel": {
                "stream_type": "VIDEO",
                "video_configs": [
                    {
                        "video_resolution": "VIDEO_1280x720",
                        "video_fps":        "_30",
                        "margin_width":     0,
                        "margin_height":    0,
                        "dpi":              140,
                        "codec":            "MEDIA_CODEC_VIDEO_H264_BP",
                    },
                ],
            },
        },
        # ch 4 — MediaAudio (PCM 48kHz stereo)
        {
            "channel_id": 4,
            "av_channel": {
                "stream_type": "AUDIO",
                "audio_type":  "MEDIA",
                "audio_configs": [
                    {"sample_rate": 48000, "bit_depth": 16, "channel_count": 2},
                ],
            },
        },
        # ch 5 — SpeechAudio (PCM 48kHz mono)
        {
            "channel_id": 5,
            "av_channel": {
                "stream_type": "AUDIO",
                "audio_type":  "SPEECH",
                "audio_configs": [
                    {"sample_rate": 48000, "bit_depth": 16, "channel_count": 1},
                ],
            },
        },
        # ch 6 — SystemAudio (PCM 16kHz mono)
        {
            "channel_id": 6,
            "av_channel": {
                "stream_type": "AUDIO",
                "audio_type":  "SYSTEM",
                "audio_configs": [
                    {"sample_rate": 16000, "bit_depth": 16, "channel_count": 1},
                ],
            },
        },
        # ch 7 — AVInput (PCM 16kHz mono)
        {
            "channel_id": 7,
            "av_input_channel": {
                "stream_type": "AUDIO",
                "audio_config": {"sample_rate": 16000, "bit_depth": 16, "channel_count": 1},
            },
        },
        # ch 8 — Bluetooth (bt_mac injected at runtime)
        {
            "channel_id": 8,
            "bluetooth_channel": {
                "adapter_address": "",
                "supported_pairing_methods": ["PIN"],
            },
        },
        # ch 9 — Navigation
        {
            "channel_id": 9,
            "navigation_channel": {
                "minimum_interval_ms": 500,
                "type": "TURN_BY_TURN",
                "image_options": {
                    "width":              64,
                    "height":             64,
                    "colour_depth_bits":  32,
                },
            },
        },
        # ch 10 — MediaStatus
        {
            "channel_id": 10,
            "media_info_channel": {},
        },
        # ch 14 — WiFi (bssid injected at runtime)
        {
            "channel_id": 14,
            "wifi_channel": {
                "bssid": "",
            },
        },
    ],
}


def _apply_defaults_to_schema(
    schema: dict[str, AnyFieldSchema],
    overrides: dict[str, Any],
) -> None:
    """Apply semantic default overrides to schema nodes in-place.

    Handles:
      - ConfigFieldSchema  (scalar): replaces .default with the override value.
      - ConfigFieldList:             replaces .default with the override list,
                                     preserving the existing .item_schema.

    Other node types (ConfigFieldMessage, ConfigFieldOneof) are skipped—
    their structure comes from the proto descriptor, not from overrides.
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
        elif isinstance(node, ConfigFieldList):
            schema[key] = ConfigFieldList(
                item_schema=node.item_schema,
                default=value if isinstance(value, list) else [],
            )
        else:
            log.debug(
                "_apply_defaults_to_schema: key %r — unsupported node type %s, skipped",
                key,
                type(node).__name__ if node is not None else "<missing>",
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
# Primary builder — proto-driven (new API)
# ---------------------------------------------------------------------------

def build_from_schema_cfg(
    schema_cfg: dict,
    bt_mac:     str = "00:00:00:00:00:00",
    wifi_bssid: str = "",
) -> bytes:
    """Build and serialise a ServiceDiscoveryResponse from a nested schema cfg dict.

    *schema_cfg* must mirror the proto field-name tree (keys as in SEMANTIC_DEFAULTS).
    bt_mac and wifi_bssid are injected at runtime into the BT/WiFi channel
    descriptors and are never read from or written to the YAML config.

    Args:
        schema_cfg:  nested dict with proto field names as keys.
        bt_mac:      local BT adapter MAC (runtime, not persisted).
        wifi_bssid:  local WiFi BSSID (runtime, not persisted).

    Returns:
        Serialised proto bytes ready to send on the wire.
    """
    resp = ServiceDiscoveryResponse()
    dict_to_proto(resp, schema_cfg)

    # Inject runtime-only values into the BT and WiFi channel descriptors.
    # These are NOT in the YAML; they come from the BT/WiFi modules at boot.
    for ch in resp.channels:
        if ch.HasField("bluetooth_channel"):
            ch.bluetooth_channel.adapter_address = bt_mac
        if ch.HasField("wifi_channel"):
            ch.wifi_channel.bssid = wifi_bssid

    return encode_proto(resp)


# ---------------------------------------------------------------------------
# channel_manager helper
# ---------------------------------------------------------------------------

# Mapping from ChannelDescriptor oneof field name to the key used in the
# dict representation returned by channels_from_sdr_bytes().
_ONEOF_CHANNEL_FIELDS = (
    "av_channel",
    "sensor_channel",
    "input_channel",
    "bluetooth_channel",
    "wifi_channel",
    "navigation_channel",
    "media_info_channel",
    "av_input_channel",
    "phone_status_channel",
)


def channels_from_sdr_bytes(sdr_bytes: bytes) -> list[dict]:
    """Parse serialised ServiceDiscoveryResponse bytes and return the channel
    list as plain dicts.

    Each dict contains at minimum:
        {"channel_id": <int>, "<oneof_field>": {}}

    For av_channel the dict also includes "av_type" at the top level so
    registry.resolve_module_type() can distinguish VIDEO from AUDIO without
    needing protobuf enums.

    This function is the bridge between handshake.py (which holds sdr_bytes)
    and channel_manager (which needs a JSON-serialisable channel list).

    Args:
        sdr_bytes: raw proto bytes from build_from_schema_cfg().

    Returns:
        List of channel dicts, one per ChannelDescriptor in the SDR.
        Returns an empty list on parse errors.
    """
    try:
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
    except Exception as exc:
        log.error("channels_from_sdr_bytes: parse error — %s", exc)
        return []

    result: list[dict] = []
    for ch in resp.channels:
        entry: dict = {"channel_id": ch.channel_id}

        # Identify which oneof field is set
        for field_name in _ONEOF_CHANNEL_FIELDS:
            if ch.HasField(field_name):
                sub = getattr(ch, field_name)
                # For av_channel expose av_type at the top level so
                # registry.resolve_module_type() can distinguish video / audio
                # without importing proto enums.
                if field_name == "av_channel":
                    entry["av_channel"] = {"av_type": sub.stream_type}
                else:
                    entry[field_name] = {}
                break

        result.append(entry)

    return result


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
