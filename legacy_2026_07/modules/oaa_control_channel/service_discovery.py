"""
service_discovery.py — Build the ServiceDiscoveryResponse for channel 0 handshake.

Port of openauto-prodigy ServiceDiscoveryBuilder.cpp to Python.
Proto classes are pre-compiled _pb2.py files in protos/oaa/.

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
    Parse serialised SDR *bytes* and return the channel list as plain dicts.
    Used by handshake.py (which holds raw bytes, not hex).
    NOTE: for hex-encoded SDR use proto_utils.channels_from_sdr_bytes(hex_str).
          For per-channel config use proto_utils.channel_config_from_sdr().
"""

from __future__ import annotations

from typing import Any


from shared.logger import get_logger                                                    # noqa: E402
from shared.proto_utils import encode_proto, proto_to_dict, schema_from_proto_message, dict_to_proto  # noqa: E402
from shared.config_schema import (                                                      # noqa: E402
    AnyFieldSchema,
    ConfigFieldList,
    ConfigFieldSchema,
)

# Import all proto dependencies BEFORE importing ChannelDescriptorData_pb2
# to ensure the descriptor pool has all required dependencies loaded
# (ChannelDescriptorData_pb2 depends on these files)

# Sensor
from protos.oaa.sensor.SensorChannelData_pb2 import SensorChannel                   # noqa: E402
from protos.oaa.sensor.SensorTypeEnum_pb2 import SensorType                         # noqa: E402

# AV / Video / Audio enums
from protos.oaa.av.AVChannelData_pb2 import AVChannel                               # noqa
from protos.oaa.av.AVStreamTypeEnum_pb2 import AVStreamType                         # noqa: E402
from protos.oaa.av.AVInputChannelData_pb2 import AVInputChannel                     # noqa: E402
from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType                     # noqa: E402
from protos.oaa.audio.AudioTypeEnum_pb2 import AudioType                            # noqa: E402
from protos.oaa.audio.AudioConfigData_pb2 import AudioConfig                        # noqa: E402
from protos.oaa.video.VideoConfigData_pb2 import VideoConfig                        # noqa: E402
from protos.oaa.video.VideoResolutionEnum_pb2 import VideoResolution                # noqa: E402
from protos.oaa.video.VideoFPSEnum_pb2 import VideoFPS                              # noqa: E402

# Bluetooth
from protos.oaa.bluetooth.BluetoothChannelData_pb2 import BluetoothChannel          # noqa: E402
from protos.oaa.bluetooth.BluetoothPairingMethodEnum_pb2 import BluetoothPairingMethod  # noqa: E402

# Input
from protos.oaa.input.InputChannelConfigData_pb2 import InputChannelConfig          # noqa: E402

# WiFi
from protos.oaa.wifi.WifiChannelData_pb2 import WifiChannel                         # noqa: E402

# Navigation
from protos.oaa.navigation.NavigationChannelData_pb2 import NavigationChannel       # noqa: E402
from protos.oaa.navigation.NavigationTypeEnum_pb2 import NavigationType             # noqa: E402
from protos.oaa.navigation.NavigationImageOptionsData_pb2 import NavigationImageOptions  # noqa: E402

# Media
from protos.oaa.media.MediaChannelData_pb2 import MediaInfoChannel                  # noqa: E402

# Radio and Vendor extensions
from protos.oaa.control.RadioChannelData_pb2 import RadioChannelConfig              # noqa: E402
from protos.oaa.control.VendorExtensionChannelData_pb2 import VendorExtensionChannel  # noqa: E402

# Car control
from protos.oaa.carcontrol.CarPropertyData_pb2 import CarPropertyConfig             # noqa: E402
from protos.oaa.carcontrol.CarControlMessages_pb2 import CarControl                 # noqa: E402

# Notification
from protos.oaa.notification.NotificationChannelData_pb2 import NotificationChannel  # noqa: E402

# Phone status
try:
    from protos.oaa.phone.PhoneStatusChannelData_pb2 import PhoneStatusChannel
    _HAS_PHONE_STATUS = True
except ImportError:
    _HAS_PHONE_STATUS = False

# Control / discovery (NOW import after dependencies)
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import (                # noqa: E402
    ServiceDiscoveryResponse,
)
from protos.oaa.control.ChannelDescriptorData_pb2 import ChannelDescriptor          # noqa: E402
from protos.oaa.common.DriverPositionEnum_pb2 import DriverPosition                 # noqa: E402

log = get_logger("oaa_control_channel.service_discovery")


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
        # ch 5 — SpeechAudio (PCM 48kHz mono)
        {
            "channel_id": 5,
            "av_channel": {
                "codec": "MEDIA_CODEC_AUDIO_PCM",
                "audio_type":  "SPEECH",
                "audio_configs": [
                    {"sample_rate": 48000, "bit_depth": 16, "channel_count": 1},
                ],
            },
        },
        # ch 6 — SystemAudio (PCM 48kHz mono)
        {
            "channel_id": 6,
            "av_channel": {
                "codec": "MEDIA_CODEC_AUDIO_PCM",
                "audio_type":  "SYSTEM",
                "audio_configs": [
                    {"sample_rate": 48000, "bit_depth": 16, "channel_count": 1},
                ],
            },
        },
        # ch 7 — AVInput (PCM 48kHz mono)
        {
            "channel_id": 7,
            "av_input_channel": {
                "codec": "MEDIA_CODEC_AUDIO_PCM",
                "audio_config": {"sample_rate": 48000, "bit_depth": 16, "channel_count": 1},
            },
        },
        # # ch 8 — Bluetooth (bt_mac injected at runtime)
        # {
        #     "channel_id": 8,
        #     "bluetooth_channel": {
        #         "adapter_address": "",
        #         "supported_pairing_methods": ["PIN"],
        #     },
        # },
        # # ch 9 — Navigation
        # {
        #     "channel_id": 9,
        #     "navigation_channel": {
        #         "minimum_interval_ms": 500,
        #         "type": 1,  # TURN_BY_TURN
        #         "image_options": {
        #             "width":              64,
        #             "height":             64,
        #             "colour_depth_bits":  32,
        #         },
        #     },
        # },
        # # ch 10 — MediaStatus
        # {
        #     "channel_id": 10,
        #     "media_info_channel": {},
        # },
        # ch 14 — WiFi (bssid injected at runtime)
        # {
        #     "channel_id": 14,
        #     "wifi_channel": {
        #         "bssid": "",
        #     },
        # },
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

# Audio codec enum values that identify a PCM/AAC audio av_channel.
_AUDIO_CODEC_VALUES = frozenset({
    MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC_ADTS,
    MediaCodecType.MEDIA_CODEC_AUDIO_PCM,
    MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC,
})


def channels_from_sdr_bytes(sdr_bytes: bytes) -> list[dict]:
    """Parse serialised ServiceDiscoveryResponse *bytes* and return the channel
    list as plain dicts.  Used by handshake.py which holds raw bytes.

    NOTE: channel_modules should use proto_utils.channel_config_from_sdr()
          (which accepts a hex string) instead of this function.

    Each dict contains at minimum:
        {"channel_id": <int>, "<oneof_field>": {}}

    For av_channel the dict exposes both "av_type" (AVStreamType int) and
    "audio_type" (AudioType int, only when av_type == AUDIO) so that
    registry.resolve_module_type() can distinguish VIDEO from the three
    audio stream types without importing proto enums.

    Audio channels are identified by EITHER:
      - codec being one of the audio codec enum values (ch 4 — MediaAudio), OR
      - stream_type == AVStreamType.AUDIO (ch 5/6 — SpeechAudio / SystemAudio,
        which omit a codec but set stream_type explicitly).

    Args:
        sdr_bytes: raw proto bytes from build_from_schema_cfg().

    Returns:
        List of channel dicts, one per ChannelDescriptor in the SDR.
        Returns an empty list on parse errors.
    """
    try:
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
        log.info("channels_from_sdr_bytes: parsed SDR proto with %d channels", len(resp.channels))
        log.debug("channels_from_sdr_bytes: full proto message:\n%s", resp)
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
                if field_name == "av_channel":
                    # Expose stream_type (VIDEO vs AUDIO) and, for AUDIO
                    # channels, also audio_type (MEDIA / SPEECH / SYSTEM)
                    # so registry.resolve_module_type() can route correctly.
                    #
                    # An av_channel is an audio channel when:
                    #   (a) its codec is one of the known audio codec values
                    #       (e.g. ch 4 — MediaAudio with AAC codec), OR
                    #   (b) its stream_type is AVStreamType.AUDIO
                    #       (e.g. ch 5/6 — SpeechAudio / SystemAudio which
                    #        omit a codec and rely on stream_type instead).
                    av_dict: dict = {"av_type": sub.codec}
                    is_audio_codec  = sub.codec in _AUDIO_CODEC_VALUES
                    if is_audio_codec:
                        av_dict["audio_type"] = sub.audio_type
                    entry["av_channel"] = av_dict
                else:
                    entry[field_name] = {}
                break

        result.append(entry)

    return result

def message_from_sdr_bytes(sdr_bytes: bytes) -> ServiceDiscoveryResponse | None:
    """Parse serialised ServiceDiscoveryResponse *bytes* and return the proto message.

    Used by handshake.py which holds raw bytes.

    Args:
        sdr_bytes: raw proto bytes from build_from_schema_cfg().
    Returns:
        Parsed ServiceDiscoveryResponse proto message, or None on parse errors.
    """
    try:
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
        return resp
    except Exception as exc:
        log.error("message_from_sdr_bytes: parse error — %s", exc)
        return None
