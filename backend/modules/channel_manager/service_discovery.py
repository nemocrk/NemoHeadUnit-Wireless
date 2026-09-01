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
    "session_configuration": 0x07,  # 0x01 (hide clock) | 0x02 (phone signal) | 0x04 (battery level)
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
                        "margin_height":    64,
                        "dpi":              140,
                    },
                ],
            },
        },
        # ch 4 — MediaAudio (PCM 48kHz stereo)
        {
            "channel_id": 4,
            "av_channel": {
                "codec": "MEDIA_CODEC_AUDIO_PCM",
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
        # ch 8 — Navigation (Turn-by-turn HUD data)
        {
            "channel_id": 8,
            "navigation_channel": {
                "minimum_interval_ms": 1000,
                "type": 1,
                "image_options": {
                    "width": 128,
                    "height": 128,
                    "colour_depth_bits": 32,
                },
            },
        },
        # ch 9 — Media Playback Metadata
        {
            "channel_id": 9,
            "media_info_channel": {},
        },
        # ch 10 — Phone Status & In-Call State
        {
            "channel_id": 10,
            "phone_status_channel": {},
        },
        # ch 11 — Notifications & Heads-Up Alerts
        {
            "channel_id": 11,
            "notification_channel": {},
        },
    ],
}


from shared.constants import ChannelType


def classify_channel_descriptor(descriptor_dict: dict) -> ChannelType:
    """Classify a channel descriptor dict into a ChannelType enum."""
    if "input_channel" in descriptor_dict:
        return ChannelType.INPUT
    elif "sensor_channel" in descriptor_dict:
        return ChannelType.SENSOR
    elif "bluetooth_channel" in descriptor_dict:
        return ChannelType.BLUETOOTH
    elif "wifi_channel" in descriptor_dict:
        return ChannelType.WIFI
    elif "navigation_channel" in descriptor_dict:
        return ChannelType.NAVIGATION
    elif "media_info_channel" in descriptor_dict:
        return ChannelType.MEDIA_PLAYBACK
    elif "phone_status_channel" in descriptor_dict:
        return ChannelType.PHONE_STATUS
    elif "notification_channel" in descriptor_dict or "generic_notification_channel" in descriptor_dict:
        return ChannelType.NOTIFICATION
    elif "av_input_channel" in descriptor_dict:
        return ChannelType.AUDIO_MIC
    elif "av_channel" in descriptor_dict:
        av = descriptor_dict["av_channel"]
        if "video_configs" in av or av.get("stream_type") == "VIDEO":
            return ChannelType.VIDEO
        else:
            return ChannelType.AUDIO
    return ChannelType.UNKNOWN


CODEC_ALIASES: dict[str, str] = {
    "H264": "MEDIA_CODEC_VIDEO_H264_BP",
    "H264_BP": "MEDIA_CODEC_VIDEO_H264_BP",
    "H265": "MEDIA_CODEC_VIDEO_H265",
    "HEVC": "MEDIA_CODEC_VIDEO_H265",
    "VP9": "MEDIA_CODEC_VIDEO_VP9",
    "AV1": "MEDIA_CODEC_VIDEO_AV1",
    "AAC": "MEDIA_CODEC_AUDIO_AAC_LC",
    "AAC_LC": "MEDIA_CODEC_AUDIO_AAC_LC",
    "AAC_ADTS": "MEDIA_CODEC_AUDIO_AAC_LC_ADTS",
    "AAC_LC_ADTS": "MEDIA_CODEC_AUDIO_AAC_LC_ADTS",
    "PCM": "MEDIA_CODEC_AUDIO_PCM",
}


def build_service_discovery_response(
    cfg: dict | None = None,
    bt_mac: str = "00:00:00:00:00:00",
    wifi_bssid: str = "",
) -> tuple[bytes, dict, dict[int, str]]:
    """Build full binary ServiceDiscoveryResponse protobuf payload and return (bytes, dict, channel_type_map)."""
    import copy
    config_tree = copy.deepcopy(SEMANTIC_DEFAULTS)
    if cfg:
        config_tree.update(cfg)

    # Apply global video_codec and audio_codec overrides if configured
    video_codec_pref = cfg.get("video_codec") if cfg else None
    audio_codec_pref = cfg.get("audio_codec") if cfg else None

    if video_codec_pref or audio_codec_pref:
        v_codec_str = CODEC_ALIASES.get(str(video_codec_pref).upper(), video_codec_pref)
        a_codec_str = CODEC_ALIASES.get(str(audio_codec_pref).upper(), audio_codec_pref)
        for ch in config_tree.get("channels", []):
            if "av_channel" in ch:
                av = ch["av_channel"]
                if "video_configs" in av and v_codec_str:
                    av["codec"] = v_codec_str
                elif "audio_configs" in av and a_codec_str and av.get("audio_type") == "MEDIA":
                    av["codec"] = a_codec_str

    resp = ServiceDiscoveryResponse()
    dict_to_proto(resp, config_tree)

    for ch in resp.channels:
        if ch.HasField("bluetooth_channel"):
            ch.bluetooth_channel.adapter_address = bt_mac
        if ch.HasField("wifi_channel"):
            ch.wifi_channel.bssid = wifi_bssid

    sdr_dict = proto_to_dict(resp)
    channel_type_map: dict[int, str] = {0: ChannelType.CONTROL.name}
    for ch_entry in sdr_dict.get("channels", []):
        ch_id = ch_entry.get("channel_id")
        if ch_id is not None:
            c_type = classify_channel_descriptor(ch_entry)
            channel_type_map[ch_id] = c_type.name

    return encode_proto(resp), sdr_dict, channel_type_map
