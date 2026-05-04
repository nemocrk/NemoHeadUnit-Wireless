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

Configuration keys (all under module "oaa_control_channel"):
    hu.name                   — HU name shown on phone
    hu.make                   — manufacturer string
    hu.model                  — model string
    hu.sw_version             — SW version string
    video.resolution          — VideoResolution enum name (e.g. "VIDEO_1280x720")
    video.fps                 — VideoFPS enum name (e.g. "_30")
    video.dpi                 — integer DPI
    touch.width               — touch surface width in pixels
    touch.height              — touch surface height in pixels
    audio.media.sample_rate   — media audio sample rate (Hz)
    audio.media.channel_count — media audio channel count
    audio.speech.sample_rate  — speech audio sample rate (Hz)
    audio.system.sample_rate  — system audio sample rate (Hz)
    nav.min_interval_ms       — navigation minimum interval (ms)
    nav.image.width           — navigation image width (px)
    nav.image.height          — navigation image height (px)

Protocol constants (NOT configurable — changing them breaks AA compatibility):
    channel_id values, supported_keycodes, BluetoothPairingMethod.PIN,
    AVStreamType per channel, AudioType per channel, bit_depth=16,
    MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP, NavigationType.TURN_BY_TURN,
    nav.image.colour_depth_bits=32
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT  = Path(__file__).parent.parent.parent.parent
_PROTO_ROOT = _REPO_ROOT / "v2" / "protos"

for _p in (_REPO_ROOT, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.proto_utils import encode_proto  # noqa: E402  — used only in build_service_discovery_response

# Control / discovery
from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import (
    ServiceDiscoveryResponse,
)
from v2.protos.oaa.control.ChannelDescriptorData_pb2 import ChannelDescriptor

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


# ---------------------------------------------------------------------------
# Default configuration (used as seeding defaults for config_manager)
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
# Public API
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
    resp.left_hand_drive  = True
    resp.manufacturer     = cfg.get("hu.make",       DEFAULTS["hu.make"])
    resp.model            = cfg.get("hu.model",      DEFAULTS["hu.model"])
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
# Channel descriptor builders (mirror ServiceDiscoveryBuilder.cpp)
# Each builder returns a ChannelDescriptor (not bytes) so resp.channels.add()
# can MergeFrom it directly.
# ---------------------------------------------------------------------------

def _build_video_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 3  # protocol constant
    av = desc.av_channel
    av.stream_type = AVStreamType.VIDEO  # protocol constant

    resolution_name = cfg.get("video.resolution", DEFAULTS["video.resolution"])
    fps_name        = cfg.get("video.fps",         DEFAULTS["video.fps"])

    cfg_pb = av.video_configs.add()
    cfg_pb.video_resolution = VideoResolution.Value(resolution_name)
    cfg_pb.video_fps        = VideoFPS.Value(fps_name)
    cfg_pb.margin_width     = 0
    cfg_pb.margin_height    = 0
    cfg_pb.dpi              = int(cfg.get("video.dpi", DEFAULTS["video.dpi"]))
    cfg_pb.codec            = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP  # protocol constant

    return desc


def _build_media_audio_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 4  # protocol constant
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO   # protocol constant
    av.audio_type  = AudioType.MEDIA      # protocol constant
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio.media.sample_rate",   DEFAULTS["audio.media.sample_rate"]))
    ac.bit_depth     = 16  # protocol constant
    ac.channel_count = int(cfg.get("audio.media.channel_count", DEFAULTS["audio.media.channel_count"]))
    return desc


def _build_speech_audio_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 5  # protocol constant
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO   # protocol constant
    av.audio_type  = AudioType.SPEECH     # protocol constant
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio.speech.sample_rate", DEFAULTS["audio.speech.sample_rate"]))
    ac.bit_depth     = 16  # protocol constant
    ac.channel_count = 1   # protocol constant — speech is always mono
    return desc


def _build_system_audio_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 6  # protocol constant
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO   # protocol constant
    av.audio_type  = AudioType.SYSTEM     # protocol constant
    ac = av.audio_configs.add()
    ac.sample_rate   = int(cfg.get("audio.system.sample_rate", DEFAULTS["audio.system.sample_rate"]))
    ac.bit_depth     = 16  # protocol constant
    ac.channel_count = 1   # protocol constant — system is always mono
    return desc


def _build_input_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 1  # protocol constant
    inp = desc.input_channel
    ts = inp.touch_screen_configs.add()
    ts.width  = int(cfg.get("touch.width",  DEFAULTS["touch.width"]))
    ts.height = int(cfg.get("touch.height", DEFAULTS["touch.height"]))
    for kc in [3, 4, 84, 85, 86, 87, 88, 126, 127, 219, 231]:  # protocol constant
        inp.supported_keycodes.append(kc)
    return desc


def _build_sensor_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 2  # protocol constant
    sc = desc.sensor_channel
    for st in [
        SensorType.NIGHT_DATA,
        SensorType.DRIVING_STATUS,
        SensorType.PARKING_BRAKE,
    ]:  # protocol constants
        sc.sensors.add().type = st
    return desc


def _build_bluetooth_descriptor(bt_mac: str) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 8  # protocol constant
    bt = desc.bluetooth_channel
    bt.adapter_address = bt_mac
    bt.supported_pairing_methods.append(BluetoothPairingMethod.PIN)  # protocol constant
    return desc


def _build_wifi_descriptor(bssid: str) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 14  # protocol constant
    wf = desc.wifi_channel
    wf.bssid = bssid
    return desc


def _build_av_input_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 7  # protocol constant
    av = desc.av_input_channel
    av.stream_type = AVStreamType.AUDIO  # protocol constant
    ac = av.audio_config
    ac.sample_rate   = 16000  # protocol constant — AVInput is always 16kHz mono
    ac.bit_depth     = 16     # protocol constant
    ac.channel_count = 1      # protocol constant
    return desc


def _build_navigation_descriptor(cfg: dict) -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 9  # protocol constant
    nav = desc.navigation_channel
    nav.minimum_interval_ms             = int(cfg.get("nav.min_interval_ms", DEFAULTS["nav.min_interval_ms"]))
    nav.type                            = NavigationType.TURN_BY_TURN  # protocol constant
    nav.image_options.width             = int(cfg.get("nav.image.width",  DEFAULTS["nav.image.width"]))
    nav.image_options.height            = int(cfg.get("nav.image.height", DEFAULTS["nav.image.height"]))
    nav.image_options.colour_depth_bits = 32  # protocol constant
    return desc


def _build_media_status_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 10  # protocol constant
    desc.media_info_channel.SetInParent()  # empty — just advertise support
    return desc


def _build_phone_status_descriptor() -> ChannelDescriptor:
    desc = ChannelDescriptor()
    desc.channel_id = 11  # protocol constant
    desc.phone_status_channel.SetInParent()
    return desc
