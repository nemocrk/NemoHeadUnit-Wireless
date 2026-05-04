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
_SCHEMA is a hand-crafted flat-scalar dict.  Every configurable parameter
for all channel descriptors is exposed as a top-level ConfigFieldSchema so
that config_manager can seed, persist, and render it without needing
ConfigFieldList / ConfigFieldMessage support.

Channel descriptors are structural / runtime: they are built at handshake
time by build_from_schema_cfg() from the flat scalar values.  They are
never stored in the YAML config.

Entry points
------------
build_from_schema_cfg(schema_cfg, bt_mac, wifi_bssid)
    Primary API.  Reads the flat scalar keys from *schema_cfg* and
    constructs all channel descriptors.  bt_mac / wifi_bssid are injected
    at runtime and never persisted.

build_service_discovery_response(cfg, bt_mac, wifi_bssid)
    Legacy flat-dict API.  Still fully functional for backward compat.
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

from shared.proto_utils import encode_proto  # noqa: E402
from shared.config_schema import (           # noqa: E402
    AnyFieldSchema,
    field_bool,
    field_enum,
    field_int,
    field_string,
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
# Hand-crafted flat-scalar schema
# ---------------------------------------------------------------------------
# Every configurable parameter for all channel descriptors is a top-level
# ConfigFieldSchema so config_manager can seed, persist, and render it
# without needing ConfigFieldList / ConfigFieldMessage support.
#
# Keys use snake_case and are grouped by logical area for readability.
# They are NOT proto field names — they are the keys used in _cfg / YAML.
#
# Channel descriptors are built at handshake time by build_from_schema_cfg().
# ---------------------------------------------------------------------------

_VIDEO_RESOLUTIONS = [
    "VIDEO_800x480",
    "VIDEO_1280x720",
    "VIDEO_1920x1080",
]

_VIDEO_FPS = ["_30", "_60"]

_DRIVER_POSITIONS = ["LEFT", "RIGHT"]

_SCHEMA: dict[str, AnyFieldSchema] = {
    # --- Head-unit identity ---
    "head_unit_name":        field_string(default="NemoHeadUnit"),
    "headunit_manufacturer": field_string(default="Nemo"),
    "headunit_model":        field_string(default="NemoHeadUnit-Wireless"),
    "sw_version":            field_string(default="2.0"),
    "sw_build":              field_string(default="1"),
    # --- Vehicle identity ---
    "car_model":             field_string(default="Universal"),
    "car_year":              field_string(default="2025"),
    "car_serial":            field_string(default="20250101"),
    "driver_position":       field_enum(default="LEFT", choices=_DRIVER_POSITIONS),
    # --- Session control ---
    "can_play_native_media_during_vr": field_bool(default=True),
    # --- Video (ch 3) ---
    "video_resolution": field_enum(default="VIDEO_1280x720", choices=_VIDEO_RESOLUTIONS),
    "video_fps":        field_enum(default="_30",            choices=_VIDEO_FPS),
    "video_dpi":        field_int(default=140, min=72, max=600),
    # --- Touch / Input (ch 1) ---
    "touch_width":  field_int(default=1280, min=320, max=7680),
    "touch_height": field_int(default=720,  min=240, max=4320),
    # --- Audio — Media (ch 4) ---
    "audio_media_sample_rate":   field_enum(default="48000", choices=["44100", "48000"]),
    "audio_media_channel_count": field_enum(default="2",     choices=["1", "2"]),
    # --- Audio — Speech (ch 5) ---
    "audio_speech_sample_rate": field_enum(default="48000", choices=["16000", "48000"]),
    # --- Audio — System (ch 6) ---
    "audio_system_sample_rate": field_enum(default="16000", choices=["16000", "48000"]),
    # --- Navigation (ch 9) ---
    "nav_min_interval_ms": field_int(default=500,  min=100,  max=5000),
    "nav_image_width":     field_int(default=64,   min=32,   max=256),
    "nav_image_height":    field_int(default=64,   min=32,   max=256),
    # --- Bluetooth (ch 8) — informational; bt_mac is injected at runtime ---
    "bt_pairing_pin": field_string(default=""),
    # --- WiFi (ch 14) — bssid_override overrides the runtime bssid when non-empty ---
    "wifi_bssid_override": field_string(default=""),
}

# SEMANTIC_DEFAULTS mirrors _SCHEMA defaults verbatim so that _cfg in
# main.py is always fully populated from boot even if no YAML is present.
SEMANTIC_DEFAULTS: dict[str, Any] = {k: v.default for k, v in _SCHEMA.items()}  # type: ignore[union-attr]


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
# Primary builder — reads flat scalar _cfg, builds all channel descriptors
# ---------------------------------------------------------------------------

def build_from_schema_cfg(
    schema_cfg: dict,
    bt_mac:     str = "00:00:00:00:00:00",
    wifi_bssid: str = "",
) -> bytes:
    """Build and serialise a ServiceDiscoveryResponse from a flat scalar cfg dict.

    *schema_cfg* must use the keys defined in _SCHEMA / SEMANTIC_DEFAULTS.
    bt_mac and wifi_bssid are injected at runtime and are never persisted.

    If wifi_bssid_override is non-empty in schema_cfg, it takes precedence
    over the runtime wifi_bssid argument.

    Args:
        schema_cfg:  flat dict with _SCHEMA keys as keys.
        bt_mac:      local BT adapter MAC (runtime, not persisted).
        wifi_bssid:  local WiFi BSSID (runtime, not persisted).

    Returns:
        Serialised proto bytes ready to send on the wire.
    """
    def _g(key: str) -> Any:
        """Get a value from schema_cfg, falling back to SEMANTIC_DEFAULTS."""
        return schema_cfg.get(key, SEMANTIC_DEFAULTS[key])

    resp = ServiceDiscoveryResponse()

    # --- Head-unit identity scalars ---
    resp.head_unit_name              = str(_g("head_unit_name"))
    resp.headunit_manufacturer       = str(_g("headunit_manufacturer"))
    resp.headunit_model              = str(_g("headunit_model"))
    resp.sw_version                  = str(_g("sw_version"))
    resp.sw_build                    = str(_g("sw_build"))
    resp.car_model                   = str(_g("car_model"))
    resp.car_year                    = str(_g("car_year"))
    resp.car_serial                  = str(_g("car_serial"))
    resp.can_play_native_media_during_vr = bool(_g("can_play_native_media_during_vr"))

    driver_pos_str = str(_g("driver_position"))
    resp.driver_position = (
        DriverPosition.RIGHT if driver_pos_str == "RIGHT" else DriverPosition.LEFT
    )

    # --- Resolve runtime WiFi BSSID ---
    bssid_override = str(_g("wifi_bssid_override"))
    effective_bssid = bssid_override if bssid_override else wifi_bssid

    # --- Channel descriptors (flat scalars → proto) ---
    descriptors = [
        _build_video_descriptor_from_cfg(schema_cfg),
        _build_media_audio_descriptor_from_cfg(schema_cfg),
        _build_speech_audio_descriptor_from_cfg(schema_cfg),
        _build_system_audio_descriptor_from_cfg(schema_cfg),
        _build_input_descriptor_from_cfg(schema_cfg),
        _build_sensor_descriptor(),
        _build_bluetooth_descriptor(bt_mac),
        _build_wifi_descriptor(effective_bssid),
        _build_av_input_descriptor(),
        _build_navigation_descriptor_from_cfg(schema_cfg),
        _build_media_status_descriptor(),
    ]
    if _HAS_PHONE_STATUS:
        descriptors.append(_build_phone_status_descriptor())

    for desc in descriptors:
        resp.channels.add().MergeFrom(desc)

    return encode_proto(resp)


# ---------------------------------------------------------------------------
# New channel descriptor builders (read flat scalar _cfg)
# ---------------------------------------------------------------------------

def _build_video_descriptor_from_cfg(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 3
    av = desc.av_channel
    av.stream_type = AVStreamType.VIDEO

    resolution_name = str(cfg.get("video_resolution", SEMANTIC_DEFAULTS["video_resolution"]))
    fps_name        = str(cfg.get("video_fps",        SEMANTIC_DEFAULTS["video_fps"]))

    cfg_pb = av.video_configs.add()
    cfg_pb.video_resolution = VideoResolution.Enum.Value(resolution_name)
    cfg_pb.video_fps        = VideoFPS.Enum.Value(fps_name)
    cfg_pb.margin_width     = 0
    cfg_pb.margin_height    = 0
    cfg_pb.dpi              = int(cfg.get("video_dpi", SEMANTIC_DEFAULTS["video_dpi"]))
    cfg_pb.codec            = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP
    return desc


def _build_media_audio_descriptor_from_cfg(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 4
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.MEDIA
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio_media_sample_rate",   SEMANTIC_DEFAULTS["audio_media_sample_rate"]))
    ac.bit_depth     = 16
    ac.channel_count = int(cfg.get("audio_media_channel_count", SEMANTIC_DEFAULTS["audio_media_channel_count"]))
    return desc


def _build_speech_audio_descriptor_from_cfg(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 5
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.SPEECH
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio_speech_sample_rate", SEMANTIC_DEFAULTS["audio_speech_sample_rate"]))
    ac.bit_depth     = 16
    ac.channel_count = 1
    return desc


def _build_system_audio_descriptor_from_cfg(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 6
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.SYSTEM
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio_system_sample_rate", SEMANTIC_DEFAULTS["audio_system_sample_rate"]))
    ac.bit_depth     = 16
    ac.channel_count = 1
    return desc


def _build_input_descriptor_from_cfg(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 1
    inp = desc.input_channel
    ts = inp.touch_screen_configs.add()
    ts.width  = int(cfg.get("touch_width",  SEMANTIC_DEFAULTS["touch_width"]))
    ts.height = int(cfg.get("touch_height", SEMANTIC_DEFAULTS["touch_height"]))
    for kc in [3, 4, 84, 85, 86, 87, 88, 126, 127, 219, 231]:
        inp.supported_keycodes.append(kc)
    return desc


def _build_navigation_descriptor_from_cfg(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 9
    nav = desc.navigation_channel
    nav.minimum_interval_ms             = int(cfg.get("nav_min_interval_ms", SEMANTIC_DEFAULTS["nav_min_interval_ms"]))
    nav.type                            = NavigationType.TURN_BY_TURN
    nav.image_options.width             = int(cfg.get("nav_image_width",     SEMANTIC_DEFAULTS["nav_image_width"]))
    nav.image_options.height            = int(cfg.get("nav_image_height",    SEMANTIC_DEFAULTS["nav_image_height"]))
    nav.image_options.colour_depth_bits = 32
    return desc


# ---------------------------------------------------------------------------
# Shared channel descriptor builders (no config params — reused by both APIs)
# ---------------------------------------------------------------------------

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
