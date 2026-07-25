"""
service_discovery.py — Build the ServiceDiscoveryResponse for Android Auto channel 0 handshake.

Port of openauto-prodigy ServiceDiscoveryBuilder to Python.
"""

from __future__ import annotations
from typing import Any

from shared.logger import get_logger
from shared.proto_utils import encode_proto, schema_from_proto_message, dict_to_proto, proto_to_dict
from shared.config_schema import (
    AnyFieldSchema,
    ConfigFieldList,
    ConfigFieldSchema,
)

# Sensors
from protos.oaa.sensor.SensorChannelData_pb2 import SensorChannel
from protos.oaa.sensor.SensorTypeEnum_pb2 import SensorType

# AV / Video / Audio enums
from protos.oaa.av.AVChannelData_pb2 import AVChannel
from protos.oaa.av.AVStreamTypeEnum_pb2 import AVStreamType
from protos.oaa.av.AVInputChannelData_pb2 import AVInputChannel
from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
from protos.oaa.audio.AudioTypeEnum_pb2 import AudioType
from protos.oaa.audio.AudioConfigData_pb2 import AudioConfig
from protos.oaa.video.VideoConfigData_pb2 import VideoConfig
from protos.oaa.video.VideoResolutionEnum_pb2 import VideoResolution
from protos.oaa.video.VideoFPSEnum_pb2 import VideoFPS

# Bluetooth / Input / WiFi / Navigation / Media
from protos.oaa.bluetooth.BluetoothChannelData_pb2 import BluetoothChannel
from protos.oaa.bluetooth.BluetoothPairingMethodEnum_pb2 import BluetoothPairingMethod
from protos.oaa.input.InputChannelConfigData_pb2 import InputChannelConfig
from protos.oaa.wifi.WifiChannelData_pb2 import WifiChannel
from protos.oaa.navigation.NavigationChannelData_pb2 import NavigationChannel
from protos.oaa.navigation.NavigationTypeEnum_pb2 import NavigationType
from protos.oaa.navigation.NavigationImageOptionsData_pb2 import NavigationImageOptions
from protos.oaa.media.MediaChannelData_pb2 import MediaInfoChannel

# Control / Discovery
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from protos.oaa.control.ChannelDescriptorData_pb2 import ChannelDescriptor
from protos.oaa.common.DriverPositionEnum_pb2 import DriverPosition

log = get_logger("channel_manager.service_discovery")

_SCHEMA: dict[str, AnyFieldSchema] = schema_from_proto_message(
    ServiceDiscoveryResponse.DESCRIPTOR
)

SEMANTIC_DEFAULTS: dict[str, Any] = {
    "head_unit_name":        "NemoHeadUnit",
    "headunit_manufacturer": "Nemo",
    "headunit_model":        "NemoHeadUnit-Wireless",
    "sw_version":            "2.0",
    "sw_build":              "1",
    "car_model":  "Universal",
    "car_year":   "2025",
    "car_serial": "20250101",
    "driver_position": "LEFT",
    "can_play_native_media_during_vr": True,
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
                "codec":            "MEDIA_CODEC_VIDEO_H264_BP",
                "video_configs": [
                    {
                        "video_resolution": "VIDEO_1280x720",
                        "video_fps":        "_30",
                        "margin_width":     0,
                        "margin_height":    0,
                        "dpi":              140,
                    },
                ],
            },
        },
        # ch 4 — MediaAudio (PCM 48kHz stereo)
        {
            "channel_id": 4,
            "av_channel": {
                "codec": "MEDIA_CODEC_AUDIO_AAC_LC_ADTS",
                "audio_type":  "MEDIA",
                "audio_configs": [
                    {"sample_rate": 48000, "bit_depth": 16, "channel_count": 2},
                ],
            },
        },
        # ch 5 — SpeechAudio (PCM 16kHz mono)
        {
            "channel_id": 5,
            "av_channel": {
                "codec": "MEDIA_CODEC_AUDIO_PCM",
                "audio_type":  "SPEECH",
                "audio_configs": [
                    {"sample_rate": 16000, "bit_depth": 16, "channel_count": 1},
                ],
            },
        },
        # ch 6 — SystemAudio (PCM 16kHz mono)
        {
            "channel_id": 6,
            "av_channel": {
                "codec": "MEDIA_CODEC_AUDIO_PCM",
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
                "codec": "MEDIA_CODEC_AUDIO_PCM",
                "audio_config": {"sample_rate": 16000, "bit_depth": 16, "channel_count": 1},
            },
        },
    ],
}


def build_service_discovery_response(
    cfg: dict | None = None,
    bt_mac: str = "00:00:00:00:00:00",
    wifi_bssid: str = "",
) -> tuple[bytes, dict]:
    """Build full binary ServiceDiscoveryResponse protobuf payload and return (bytes, dict)."""
    config_tree = dict(SEMANTIC_DEFAULTS)
    if cfg:
        config_tree.update(cfg)

    resp = ServiceDiscoveryResponse()
    dict_to_proto(resp, config_tree)

    for ch in resp.channels:
        if ch.HasField("bluetooth_channel"):
            ch.bluetooth_channel.adapter_address = bt_mac
        if ch.HasField("wifi_channel"):
            ch.wifi_channel.bssid = wifi_bssid

    sdr_dict = proto_to_dict(resp)
    return encode_proto(resp), sdr_dict
