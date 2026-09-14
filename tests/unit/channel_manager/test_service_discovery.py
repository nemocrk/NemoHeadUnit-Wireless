import pytest
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
from protos.oaa.audio.AudioTypeEnum_pb2 import AudioType
from shared.constants import ChannelType
from modules.channel_manager.service_discovery import (
    classify_channel_descriptor,
    build_service_discovery_response,
    SEMANTIC_DEFAULTS,
)

pytestmark = pytest.mark.unit


def test_classify_channel_descriptor():
    assert classify_channel_descriptor({"input_channel": {}}) == ChannelType.INPUT
    assert classify_channel_descriptor({"sensor_channel": {}}) == ChannelType.SENSOR
    assert classify_channel_descriptor({"bluetooth_channel": {}}) == ChannelType.BLUETOOTH
    assert classify_channel_descriptor({"wifi_channel": {}}) == ChannelType.WIFI
    assert classify_channel_descriptor({"navigation_channel": {}}) == ChannelType.NAVIGATION
    assert classify_channel_descriptor({"media_info_channel": {}}) == ChannelType.MEDIA_PLAYBACK
    assert classify_channel_descriptor({"phone_status_channel": {}}) == ChannelType.PHONE_STATUS
    assert classify_channel_descriptor({"notification_channel": {}}) == ChannelType.NOTIFICATION
    assert classify_channel_descriptor({"av_input_channel": {}}) == ChannelType.AUDIO_MIC
    assert classify_channel_descriptor({"av_channel": {"video_configs": []}}) == ChannelType.VIDEO
    assert classify_channel_descriptor({"av_channel": {"stream_type": "VIDEO"}}) == ChannelType.VIDEO
    assert classify_channel_descriptor({"av_channel": {"audio_configs": []}}) == ChannelType.AUDIO
    assert classify_channel_descriptor({}) == ChannelType.UNKNOWN


def test_build_service_discovery_response_defaults():
    sdr_bytes, sdr_dict, channel_type_map = build_service_discovery_response()
    assert len(sdr_bytes) > 0
    assert isinstance(sdr_dict, dict)
    assert sdr_dict["head_unit_name"] == "NemoHeadUnit"

    # Decode binary protobuf to ensure round-trip integrity
    resp = ServiceDiscoveryResponse()
    resp.ParseFromString(sdr_bytes)
    assert resp.head_unit_name == "NemoHeadUnit"
    assert len(resp.channels) == len(SEMANTIC_DEFAULTS["channels"])

    # Channel 0 must map to CONTROL
    assert channel_type_map[0] == "CONTROL"
    assert 1 in channel_type_map
    assert channel_type_map[1] == "INPUT"


def test_build_service_discovery_response_param_injection():
    bt_mac = "AA:BB:CC:DD:EE:FF"
    wifi_bssid = "00:11:22:33:44:55"

    custom_cfg = {
        "head_unit_name": "CustomNemoUnit",
        "channels": [
            {"channel_id": 8, "bluetooth_channel": {}},
            {"channel_id": 9, "wifi_channel": {}},
        ]
    }

    sdr_bytes, sdr_dict, type_map = build_service_discovery_response(
        cfg=custom_cfg,
        bt_mac=bt_mac,
        wifi_bssid=wifi_bssid,
    )

    resp = ServiceDiscoveryResponse()
    resp.ParseFromString(sdr_bytes)
    assert resp.head_unit_name == "CustomNemoUnit"

    bt_found = False
    wifi_found = False
    for ch in resp.channels:
        if ch.HasField("bluetooth_channel"):
            assert ch.bluetooth_channel.adapter_address == bt_mac
            bt_found = True
        if ch.HasField("wifi_channel"):
            assert ch.wifi_channel.bssid == wifi_bssid
            wifi_found = True
    assert bt_found and wifi_found


def test_build_service_discovery_response_codec_overrides():
    custom_cfg = {
        "video_codec": "H265",
        "audio_codec": "AAC",
    }
    sdr_bytes, sdr_dict, type_map = build_service_discovery_response(cfg=custom_cfg)

    resp = ServiceDiscoveryResponse()
    resp.ParseFromString(sdr_bytes)

    # Check video channel has H265 and media audio has AAC
    video_checked = False
    audio_checked = False
    for ch in resp.channels:
        if ch.HasField("av_channel"):
            codec_name = MediaCodecType.Enum.Name(ch.av_channel.codec)
            if ch.av_channel.video_configs:
                assert "H265" in codec_name
                video_checked = True
            elif ch.av_channel.audio_type == AudioType.MEDIA:  # MEDIA
                assert "AAC" in codec_name
                audio_checked = True
    assert video_checked and audio_checked
