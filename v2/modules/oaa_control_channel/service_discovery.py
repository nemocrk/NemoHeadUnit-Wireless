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
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT  = Path(__file__).parent.parent.parent.parent
_PROTO_ROOT = _REPO_ROOT / "v2" / "protos"

for _p in (_REPO_ROOT, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.proto_utils import encode_proto  # noqa: E402

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
# Public API
# ---------------------------------------------------------------------------

HU_NAME       = "NemoHeadUnit"
HU_MAKE       = "Nemo"
HU_MODEL      = "NemoHeadUnit-Wireless"
HU_SW_VERSION = "2.0"


def build_service_discovery_response(
    bt_mac:       str = "00:00:00:00:00:00",
    wifi_bssid:   str = "",
    wifi_ssid:    str = "",
    wifi_password: str = "",
) -> bytes:
    """Return the serialised ServiceDiscoveryResponse protobuf bytes."""
    resp = ServiceDiscoveryResponse()
    resp.head_unit_name   = HU_NAME
    resp.car_model        = "Universal"
    resp.car_year         = "2025"
    resp.car_serial       = "20250101"
    resp.left_hand_drive  = True
    resp.manufacturer     = HU_MAKE
    resp.model            = HU_MODEL
    resp.sw_build         = "1"
    resp.sw_version       = HU_SW_VERSION
    resp.can_play_native_media_during_vr = True

    for descriptor_bytes in [
        _build_video_descriptor(),
        _build_media_audio_descriptor(),
        _build_speech_audio_descriptor(),
        _build_system_audio_descriptor(),
        _build_input_descriptor(),
        _build_sensor_descriptor(),
        _build_bluetooth_descriptor(bt_mac),
        _build_wifi_descriptor(wifi_bssid),
        _build_av_input_descriptor(),
        _build_navigation_descriptor(),
        _build_media_status_descriptor(),
    ]:
        resp.channels.append(descriptor_bytes)

    if _HAS_PHONE_STATUS:
        resp.channels.append(_build_phone_status_descriptor())

    return encode_proto(resp)


# ---------------------------------------------------------------------------
# Channel descriptor builders (mirror ServiceDiscoveryBuilder.cpp)
# ---------------------------------------------------------------------------

def _build_video_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 3
    av = desc.av_channel
    av.stream_type = AVStreamType.VIDEO

    cfg = av.video_configs.add()
    cfg.video_resolution = VideoResolution.VIDEO_1280x720
    cfg.video_fps        = VideoFPS._30
    cfg.margin_width     = 0
    cfg.margin_height    = 0
    cfg.dpi              = 140
    cfg.codec            = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP

    return encode_proto(desc)


def _build_media_audio_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 4
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.MEDIA
    ac = av.audio_configs.add()
    ac.sample_rate    = 48000
    ac.bit_depth      = 16
    ac.channel_count  = 2
    return encode_proto(desc)


def _build_speech_audio_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 5
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.SPEECH
    ac = av.audio_configs.add()
    ac.sample_rate    = 48000
    ac.bit_depth      = 16
    ac.channel_count  = 1
    return encode_proto(desc)


def _build_system_audio_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 6
    av = desc.av_channel
    av.stream_type = AVStreamType.AUDIO
    av.audio_type  = AudioType.SYSTEM
    ac = av.audio_configs.add()
    ac.sample_rate    = 16000
    ac.bit_depth      = 16
    ac.channel_count  = 1
    return encode_proto(desc)


def _build_input_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 1
    inp = desc.input_channel
    ts = inp.touch_screen_configs.add()
    ts.width  = 1280
    ts.height = 720
    for kc in [3, 4, 84, 85, 86, 87, 88, 126, 127, 219, 231]:
        inp.supported_keycodes.append(kc)
    return encode_proto(desc)


def _build_sensor_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 2
    sc = desc.sensor_channel
    for st in [
        SensorType.NIGHT_DATA,
        SensorType.DRIVING_STATUS,
        SensorType.PARKING_BRAKE,
    ]:
        sc.sensors.add().type = st
    return encode_proto(desc)


def _build_bluetooth_descriptor(bt_mac: str) -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 8
    bt = desc.bluetooth_channel
    bt.adapter_address = bt_mac
    bt.supported_pairing_methods.append(BluetoothPairingMethod.PIN)
    return encode_proto(desc)


def _build_wifi_descriptor(bssid: str) -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 14
    wf = desc.wifi_channel
    wf.bssid = bssid
    return encode_proto(desc)


def _build_av_input_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 7
    av = desc.av_input_channel
    av.stream_type = AVStreamType.AUDIO
    ac = av.audio_config
    ac.sample_rate   = 16000
    ac.bit_depth     = 16
    ac.channel_count = 1
    return encode_proto(desc)


def _build_navigation_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 9
    nav = desc.navigation_channel
    nav.minimum_interval_ms = 500
    nav.type = NavigationType.TURN_BY_TURN
    nav.image_options.width         = 64
    nav.image_options.height        = 64
    nav.image_options.colour_depth_bits = 32
    return encode_proto(desc)


def _build_media_status_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 10
    desc.media_info_channel.SetInParent()  # empty — just advertise support
    return encode_proto(desc)


def _build_phone_status_descriptor() -> bytes:
    desc = ChannelDescriptor()
    desc.channel_id = 11
    desc.phone_status_channel.SetInParent()
    return encode_proto(desc)
