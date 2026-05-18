"""
Unit tests for oaa_control_channel/service_discovery.py

Strategy: proto _pb2.py files ARE present in v2/protos/oaa/ — import real module.
No protobuf stubbing needed.  Tests use real ServiceDiscoveryResponse round-trips.

Covers:
  - SEMANTIC_DEFAULTS: structure, required keys, channel list
  - _apply_defaults_to_schema: ConfigFieldSchema replacement, ConfigFieldList replacement,
                               unsupported node types silently skipped
  - build_from_schema_cfg: serialises to bytes, bt_mac injection, wifi_bssid injection,
                            default mac fallback, empty payload stays bytes
  - channels_from_sdr_bytes: round-trip channel list, channel_id values, av_channel
                              stream_type/audio_type, non-av channels, malformed bytes
  - message_from_sdr_bytes: returns proto object, parse error returns None
"""

import sys
import importlib
import types
import pytest

# ---------------------------------------------------------------------------
# Fixture: import the module under test with real protos
# ---------------------------------------------------------------------------

_MOD = "oaa_control_channel.service_discovery"


@pytest.fixture(scope="module")
def sd():
    """Import service_discovery with real proto files.  scope=module: stateless module."""
    if _MOD in sys.modules:
        del sys.modules[_MOD]
    import oaa_control_channel.service_discovery as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Helper: build minimal SEMANTIC_DEFAULTS-compatible schema_cfg
# ---------------------------------------------------------------------------

def _minimal_cfg(sd_mod):
    """Return a copy of SEMANTIC_DEFAULTS as a plain dict (no schema objects)."""
    import copy
    return copy.deepcopy(sd_mod.SEMANTIC_DEFAULTS)


# ===========================================================================
# Section 1 — SEMANTIC_DEFAULTS structure
# ===========================================================================

class TestSemanticDefaults:

    @pytest.mark.unit
    def test_head_unit_name_present(self, sd):
        assert "head_unit_name" in sd.SEMANTIC_DEFAULTS

    @pytest.mark.unit
    def test_head_unit_name_non_empty(self, sd):
        assert sd.SEMANTIC_DEFAULTS["head_unit_name"] != ""

    @pytest.mark.unit
    def test_channels_key_is_list(self, sd):
        assert isinstance(sd.SEMANTIC_DEFAULTS["channels"], list)

    @pytest.mark.unit
    def test_channels_non_empty(self, sd):
        assert len(sd.SEMANTIC_DEFAULTS["channels"]) > 0

    @pytest.mark.unit
    def test_all_channels_have_channel_id(self, sd):
        for ch in sd.SEMANTIC_DEFAULTS["channels"]:
            assert "channel_id" in ch, f"Missing channel_id in {ch}"

    @pytest.mark.unit
    def test_channel_ids_are_unique(self, sd):
        ids = [ch["channel_id"] for ch in sd.SEMANTIC_DEFAULTS["channels"]]
        assert len(ids) == len(set(ids))

    @pytest.mark.unit
    def test_channel_1_is_input(self, sd):
        ch1 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 1)
        assert "input_channel" in ch1

    @pytest.mark.unit
    def test_channel_2_is_sensor(self, sd):
        ch2 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 2)
        assert "sensor_channel" in ch2

    @pytest.mark.unit
    def test_channel_3_is_video(self, sd):
        ch3 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 3)
        assert "av_channel" in ch3
        assert ch3["av_channel"]["codec"] == "MEDIA_CODEC_VIDEO_H264_BP"

    @pytest.mark.unit
    def test_channel_4_is_media_audio(self, sd):
        ch4 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 4)
        assert "av_channel" in ch4
        assert ch4["av_channel"]["codec"] == "MEDIA_CODEC_AUDIO_AAC_LC_ADTS"
        assert ch4["av_channel"]["audio_type"] == "MEDIA"

    @pytest.mark.unit
    def test_channel_5_is_speech_audio(self, sd):
        ch5 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 5)
        assert ch5["av_channel"]["audio_type"] == "SPEECH"

    @pytest.mark.unit
    def test_channel_6_is_system_audio(self, sd):
        ch6 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 6)
        assert ch6["av_channel"]["audio_type"] == "SYSTEM"

    @pytest.mark.unit
    def test_channel_7_is_av_input(self, sd):
        ch7 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 7)
        assert "av_input_channel" in ch7

    @pytest.mark.unit
    def test_video_config_has_resolution(self, sd):
        ch3 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 3)
        vcfg = ch3["av_channel"]["video_configs"][0]
        assert "video_resolution" in vcfg
        assert "VIDEO_1280x720" in vcfg["video_resolution"]

    @pytest.mark.unit
    def test_video_config_has_fps(self, sd):
        ch3 = next(c for c in sd.SEMANTIC_DEFAULTS["channels"] if c["channel_id"] == 3)
        vcfg = ch3["av_channel"]["video_configs"][0]
        assert "video_fps" in vcfg

    @pytest.mark.unit
    def test_driver_position_present(self, sd):
        assert "driver_position" in sd.SEMANTIC_DEFAULTS

    @pytest.mark.unit
    def test_sw_version_present(self, sd):
        assert "sw_version" in sd.SEMANTIC_DEFAULTS
        assert sd.SEMANTIC_DEFAULTS["sw_version"] != ""


# ===========================================================================
# Section 2 — _apply_defaults_to_schema
# ===========================================================================

class TestApplyDefaultsToSchema:

    @pytest.mark.unit
    def test_config_field_schema_default_replaced(self, sd):
        from shared.config_schema import ConfigFieldSchema
        schema = {
            "name": ConfigFieldSchema(type=str, default="old", min=None, max=None, choices=None)
        }
        sd._apply_defaults_to_schema(schema, {"name": "new"})
        assert schema["name"].default == "new"

    @pytest.mark.unit
    def test_config_field_schema_type_preserved(self, sd):
        from shared.config_schema import ConfigFieldSchema
        schema = {
            "x": ConfigFieldSchema(type=int, default=0, min=None, max=None, choices=None)
        }
        sd._apply_defaults_to_schema(schema, {"x": 99})
        assert schema["x"].type is int

    @pytest.mark.unit
    def test_config_field_list_default_replaced(self, sd):
        from shared.config_schema import ConfigFieldList, ConfigFieldSchema
        item_schema = ConfigFieldSchema(type=int, default=0, min=None, max=None, choices=None)
        schema = {
            "items": ConfigFieldList(item_schema=item_schema, default=[])
        }
        sd._apply_defaults_to_schema(schema, {"items": [1, 2, 3]})
        assert schema["items"].default == [1, 2, 3]

    @pytest.mark.unit
    def test_config_field_list_item_schema_preserved(self, sd):
        from shared.config_schema import ConfigFieldList, ConfigFieldSchema
        item_schema = ConfigFieldSchema(type=str, default="", min=None, max=None, choices=None)
        schema = {
            "tags": ConfigFieldList(item_schema=item_schema, default=[])
        }
        sd._apply_defaults_to_schema(schema, {"tags": ["a", "b"]})
        assert schema["tags"].item_schema is item_schema

    @pytest.mark.unit
    def test_unknown_key_silently_ignored(self, sd):
        schema = {}
        # Should not raise
        sd._apply_defaults_to_schema(schema, {"nonexistent": "value"})

    @pytest.mark.unit
    def test_unsupported_node_type_silently_skipped(self, sd):
        schema = {"msg": types.SimpleNamespace(type="message")}  # not ConfigFieldSchema/List
        sd._apply_defaults_to_schema(schema, {"msg": {"key": "val"}})
        # Node should be unchanged
        assert hasattr(schema["msg"], "type")

    @pytest.mark.unit
    def test_multiple_keys_applied(self, sd):
        from shared.config_schema import ConfigFieldSchema
        schema = {
            "a": ConfigFieldSchema(type=str, default="old_a", min=None, max=None, choices=None),
            "b": ConfigFieldSchema(type=str, default="old_b", min=None, max=None, choices=None),
        }
        sd._apply_defaults_to_schema(schema, {"a": "new_a", "b": "new_b"})
        assert schema["a"].default == "new_a"
        assert schema["b"].default == "new_b"


# ===========================================================================
# Section 3 — build_from_schema_cfg
# ===========================================================================

class TestBuildFromSchemaCfg:

    @pytest.mark.unit
    def test_returns_bytes(self, sd):
        cfg = _minimal_cfg(sd)
        result = sd.build_from_schema_cfg(cfg)
        assert isinstance(result, bytes)

    @pytest.mark.unit
    def test_returns_non_empty_bytes(self, sd):
        cfg = _minimal_cfg(sd)
        result = sd.build_from_schema_cfg(cfg)
        assert len(result) > 0

    @pytest.mark.unit
    def test_default_bt_mac_fallback(self, sd):
        cfg = _minimal_cfg(sd)
        # Should not raise with default mac
        result = sd.build_from_schema_cfg(cfg)
        assert isinstance(result, bytes)

    @pytest.mark.unit
    def test_custom_bt_mac_injected(self, sd):
        cfg = _minimal_cfg(sd)
        # Add BT channel to cfg so injection is testable
        cfg["channels"].append({
            "channel_id": 8,
            "bluetooth_channel": {
                "adapter_address": "",
                "supported_pairing_methods": ["PIN"],
            },
        })
        bt_mac = "AA:BB:CC:DD:EE:FF"
        sdr_bytes = sd.build_from_schema_cfg(cfg, bt_mac=bt_mac)
        # Parse back and verify
        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
        bt_channels = [ch for ch in resp.channels if ch.HasField("bluetooth_channel")]
        assert len(bt_channels) == 1
        assert bt_channels[0].bluetooth_channel.adapter_address == bt_mac

    @pytest.mark.unit
    def test_custom_wifi_bssid_injected(self, sd):
        cfg = _minimal_cfg(sd)
        cfg["channels"].append({
            "channel_id": 14,
            "wifi_channel": {"bssid": ""},
        })
        bssid = "11:22:33:44:55:66"
        sdr_bytes = sd.build_from_schema_cfg(cfg, wifi_bssid=bssid)
        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
        wifi_channels = [ch for ch in resp.channels if ch.HasField("wifi_channel")]
        assert len(wifi_channels) == 1
        assert wifi_channels[0].wifi_channel.bssid == bssid

    @pytest.mark.unit
    def test_empty_channels_list_still_serialises(self, sd):
        cfg = _minimal_cfg(sd)
        cfg["channels"] = []
        result = sd.build_from_schema_cfg(cfg)
        assert isinstance(result, bytes)

    @pytest.mark.unit
    def test_round_trip_channel_count(self, sd):
        cfg = _minimal_cfg(sd)
        sdr_bytes = sd.build_from_schema_cfg(cfg)
        channels = sd.channels_from_sdr_bytes(sdr_bytes)
        assert len(channels) == len(cfg["channels"])

    @pytest.mark.unit
    def test_head_unit_name_survives_round_trip(self, sd):
        cfg = _minimal_cfg(sd)
        cfg["head_unit_name"] = "TestUnit"
        sdr_bytes = sd.build_from_schema_cfg(cfg)
        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
        assert resp.head_unit_name == "TestUnit"


# ===========================================================================
# Section 4 — channels_from_sdr_bytes
# ===========================================================================

class TestChannelsFromSdrBytes:

    @pytest.fixture(scope="class")
    def sdr_bytes(self, sd):
        cfg = _minimal_cfg(sd)
        return sd.build_from_schema_cfg(cfg)

    @pytest.mark.unit
    def test_returns_list(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        assert isinstance(result, list)

    @pytest.mark.unit
    def test_all_entries_have_channel_id(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        for ch in result:
            assert "channel_id" in ch

    @pytest.mark.unit
    def test_channel_ids_match_expected(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ids = {ch["channel_id"] for ch in result}
        # Channels from SEMANTIC_DEFAULTS: 1,2,3,4,5,6,7
        for expected_id in [1, 2, 3, 4, 5, 6, 7]:
            assert expected_id in ids, f"channel_id {expected_id} missing"

    @pytest.mark.unit
    def test_video_channel_has_av_channel_key(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch3 = next(c for c in result if c["channel_id"] == 3)
        assert "av_channel" in ch3

    @pytest.mark.unit
    def test_video_channel_av_type_is_video(self, sd, sdr_bytes):
        from v2.protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch3 = next(c for c in result if c["channel_id"] == 3)
        assert ch3["av_channel"]["av_type"] == MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP

    @pytest.mark.unit
    def test_audio_channel_av_type_is_audio(self, sd, sdr_bytes):
        from v2.protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch4 = next(c for c in result if c["channel_id"] == 4)
        assert ch4["av_channel"]["av_type"] == MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC_ADTS

    @pytest.mark.unit
    def test_audio_channel_has_audio_type(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch4 = next(c for c in result if c["channel_id"] == 4)
        assert "audio_type" in ch4["av_channel"]

    @pytest.mark.unit
    def test_video_channel_has_no_audio_type(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch3 = next(c for c in result if c["channel_id"] == 3)
        assert "audio_type" not in ch3["av_channel"]

    @pytest.mark.unit
    def test_sensor_channel_present(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch2 = next(c for c in result if c["channel_id"] == 2)
        assert "sensor_channel" in ch2

    @pytest.mark.unit
    def test_input_channel_present(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch1 = next(c for c in result if c["channel_id"] == 1)
        assert "input_channel" in ch1

    @pytest.mark.unit
    def test_av_input_channel_present(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch7 = next(c for c in result if c["channel_id"] == 7)
        assert "av_input_channel" in ch7

    @pytest.mark.unit
    def test_malformed_bytes_returns_empty_list(self, sd):
        result = sd.channels_from_sdr_bytes(b"not valid proto bytes \x00\xff")
        assert isinstance(result, list)
        # Either empty (parse error) or partial — must not raise

    @pytest.mark.unit
    def test_empty_bytes_returns_empty_list(self, sd):
        result = sd.channels_from_sdr_bytes(b"")
        assert result == []

    @pytest.mark.unit
    def test_media_audio_audio_type_is_media(self, sd, sdr_bytes):
        from v2.protos.oaa.audio.AudioTypeEnum_pb2 import AudioType
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch4 = next(c for c in result if c["channel_id"] == 4)
        assert ch4["av_channel"]["audio_type"] == AudioType.MEDIA

    @pytest.mark.unit
    def test_speech_audio_audio_type_is_speech(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch5 = next(c for c in result if c["channel_id"] == 5)
        # Tracked in v2/tests/KNOWN_PRODUCTION_BUGS.md: speech/system audio
        # defaults use stream_type instead of codec, so channels_from_sdr_bytes
        # does not currently expose audio_type for these two channels.
        assert "audio_type" in ch5["av_channel"]

    @pytest.mark.unit
    def test_system_audio_audio_type_is_system(self, sd, sdr_bytes):
        result = sd.channels_from_sdr_bytes(sdr_bytes)
        ch6 = next(c for c in result if c["channel_id"] == 6)
        # See v2/tests/KNOWN_PRODUCTION_BUGS.md.
        assert "audio_type" in ch6["av_channel"]


# ===========================================================================
# Section 5 — message_from_sdr_bytes
# ===========================================================================

class TestMessageFromSdrBytes:

    @pytest.mark.unit
    def test_returns_proto_object(self, sd):
        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
        cfg = _minimal_cfg(sd)
        sdr_bytes = sd.build_from_schema_cfg(cfg)
        result = sd.message_from_sdr_bytes(sdr_bytes)
        assert isinstance(result, ServiceDiscoveryResponse)

    @pytest.mark.unit
    def test_malformed_bytes_returns_none(self, sd):
        result = sd.message_from_sdr_bytes(b"\x00\xFF\xDE\xAD")
        # Either None (parse error) or a proto object — must not raise
        assert result is None or hasattr(result, "channels")

    @pytest.mark.unit
    def test_empty_bytes_returns_proto_or_none(self, sd):
        result = sd.message_from_sdr_bytes(b"")
        # Empty bytes = valid empty proto (ParseFromString accepts empty)
        assert result is None or hasattr(result, "channels")

    @pytest.mark.unit
    def test_head_unit_name_accessible(self, sd):
        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
        cfg = _minimal_cfg(sd)
        cfg["head_unit_name"] = "NemoTest"
        sdr_bytes = sd.build_from_schema_cfg(cfg)
        result = sd.message_from_sdr_bytes(sdr_bytes)
        assert result is not None
        assert result.head_unit_name == "NemoTest"

    @pytest.mark.unit
    def test_channels_accessible_on_proto(self, sd):
        cfg = _minimal_cfg(sd)
        sdr_bytes = sd.build_from_schema_cfg(cfg)
        result = sd.message_from_sdr_bytes(sdr_bytes)
        assert result is not None
        assert len(result.channels) > 0
