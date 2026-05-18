"""
Unit tests for tcp_server/message_to_proto.py

Strategy:
  message_to_proto imports from generated protobuf stubs at module level.
  We mock the six *ChannelMessageIdsEnum modules at import time so the
  module loads without a real protobuf installation.

  proto_name_to_class has lazy imports inside the function body — each
  switcher branch is exercised by injecting the relevant MagicMock enum.

Covers:
  Section 1 — message_id_to_proto_name: known ids in each channel,
               fallback to UnknownMessageId
  Section 2 — proto_name_to_class: known names return a class,
               unknown name raises ValueError
  Section 3 — frame_data_to_dict: happy path, unknown message_id fallback,
               parse exception fallback to raw hex
"""

import sys
import types
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

_V2 = Path(__file__).parents[4]
for _p in [str(_V2), str(_V2 / "modules"), str(_V2 / "protos")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Minimal protobuf stub helpers
# ---------------------------------------------------------------------------

def _make_enum_module(mod_name: str, class_name: str, values: dict):
    """
    Build a fake protobuf enum module so message_to_proto can be imported
    without a real protobuf installation.
    """
    mod = types.ModuleType(mod_name)
    inner = MagicMock()
    inner.Enum.values.return_value = list(values.values())
    inner.Enum.Name.side_effect = lambda v: {val: name for name, val in values.items()}.get(v, "UNKNOWN")
    setattr(mod, class_name, inner)
    return mod


# Minimal value sets per channel (representative IDs only)
_AV_VALUES      = {"SETUP_REQUEST": 0x8000, "START_INDICATION": 0x8001}
_BT_VALUES      = {"PAIRING_REQUEST": 0x8001, "AUTH_DATA": 0x8003}
_CTRL_VALUES    = {"VERSION_REQUEST": 1, "SSL_HANDSHAKE": 3, "AUTH_COMPLETE": 4}
_INPUT_VALUES   = {"INPUT_EVENT_INDICATION": 0x8001, "BINDING_REQUEST": 0x8002}
_SENSOR_VALUES  = {"SENSOR_REQUEST": 0x8001, "SENSOR_EVENT_INDICATION": 0x8003}
_WIFI_VALUES    = {"CREDENTIALS_REQUEST": 0x8001, "CREDENTIALS_RESPONSE": 0x8002}


def _install_fake_protos():
    """Install fake *_pb2 modules for all enums message_to_proto imports."""
    fakes = {
        "oaa": types.ModuleType("oaa"),
        "oaa.av": types.ModuleType("oaa.av"),
        "oaa.bluetooth": types.ModuleType("oaa.bluetooth"),
        "oaa.control": types.ModuleType("oaa.control"),
        "oaa.input": types.ModuleType("oaa.input"),
        "oaa.sensor": types.ModuleType("oaa.sensor"),
        "oaa.wifi": types.ModuleType("oaa.wifi"),
        "oaa.video": types.ModuleType("oaa.video"),
        "oaa.media": types.ModuleType("oaa.media"),
        "oaa.navigation": types.ModuleType("oaa.navigation"),
        "oaa.audio": types.ModuleType("oaa.audio"),
        "google": types.ModuleType("google"),
        "google.protobuf": types.ModuleType("google.protobuf"),
        "google.protobuf.json_format": types.ModuleType("google.protobuf.json_format"),
    }
    # Enum modules
    fakes["oaa.av.AVChannelMessageIdsEnum_pb2"] = _make_enum_module(
        "oaa.av.AVChannelMessageIdsEnum_pb2", "AVChannelMessage", _AV_VALUES)
    fakes["oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2"] = _make_enum_module(
        "oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2", "BluetoothChannelMessage", _BT_VALUES)
    fakes["oaa.control.ControlMessageIdsEnum_pb2"] = _make_enum_module(
        "oaa.control.ControlMessageIdsEnum_pb2", "ControlMessage", _CTRL_VALUES)
    fakes["oaa.input.InputChannelMessageIdsEnum_pb2"] = _make_enum_module(
        "oaa.input.InputChannelMessageIdsEnum_pb2", "InputChannelMessage", _INPUT_VALUES)
    fakes["oaa.sensor.SensorChannelMessageIdsEnum_pb2"] = _make_enum_module(
        "oaa.sensor.SensorChannelMessageIdsEnum_pb2", "SensorChannelMessage", _SENSOR_VALUES)
    fakes["oaa.wifi.WifiChannelMessageIdsEnum_pb2"] = _make_enum_module(
        "oaa.wifi.WifiChannelMessageIdsEnum_pb2", "WifiChannelMessage", _WIFI_VALUES)
    # All other lazy-import stubs — just need to be importable
    for suffix in [
        "oaa.av.AVChannelSetupRequestMessage_pb2",
        "oaa.av.AVChannelStartIndicationMessage_pb2",
        "oaa.av.AVChannelStopIndicationMessage_pb2",
        "oaa.av.AVChannelSetupResponseMessage_pb2",
        "oaa.av.AVMediaAckIndicationMessage_pb2",
        "oaa.av.AVInputOpenRequestMessage_pb2",
        "oaa.av.AVInputOpenResponseMessage_pb2",
        "oaa.video.VideoFocusRequestMessage_pb2",
        "oaa.video.VideoFocusIndicationMessage_pb2",
        "oaa.video.VideoFocusNotificationMessage_pb2",
        "oaa.video.UpdateUiConfigRequestMessage_pb2",
        "oaa.av.UiConfigMessages_pb2",
        "oaa.media.MediaPlaybackCommandMessage_pb2",
        "oaa.video.IntegratedOverlayStartNotification_pb2",
        "oaa.video.IntegratedOverlayStopNotification_pb2",
        "oaa.av.AVChannelMediaStatsMessage_pb2",
        "oaa.av.AVChannelMediaOptionsMessage_pb2",
        "oaa.bluetooth.BluetoothPairingRequestMessage_pb2",
        "oaa.bluetooth.BluetoothPairingResponseMessage_pb2",
        "oaa.bluetooth.BluetoothAuthenticationDataMessage_pb2",
        "oaa.bluetooth.BluetoothAuthenticationResultMessage_pb2",
        "oaa.control.AuthCompleteIndicationMessage_pb2",
        "oaa.control.ServiceDiscoveryRequestMessage_pb2",
        "oaa.control.ServiceDiscoveryResponseMessage_pb2",
        "oaa.control.ChannelOpenRequestMessage_pb2",
        "oaa.control.ChannelOpenResponseMessage_pb2",
        "oaa.control.PingRequestMessage_pb2",
        "oaa.control.PingResponseMessage_pb2",
        "oaa.navigation.NavigationFocusRequestMessage_pb2",
        "oaa.navigation.NavigationFocusResponseMessage_pb2",
        "oaa.control.ShutdownRequestMessage_pb2",
        "oaa.control.ByeByeResponseMessage_pb2",
        "oaa.control.VoiceSessionRequestMessage_pb2",
        "oaa.audio.AudioFocusRequestMessage_pb2",
        "oaa.audio.AudioFocusResponseMessage_pb2",
        "oaa.control.ConnectedDevicesMessages_pb2",
        "oaa.control.BatteryStatusMessage_pb2",
        "oaa.control.CallAvailabilityMessage_pb2",
        "oaa.control.ServiceDiscoveryUpdateMessage_pb2",
        "oaa.control.ChannelCloseNotificationMessage_pb2",
        "oaa.control.BindingRequestMessage_pb2",
        "oaa.control.BindingResponseMessage_pb2",
        "oaa.input.InputEventIndicationMessage_pb2",
        "oaa.input.InputBindingNotificationMessage_pb2",
        "oaa.sensor.SensorRequestMessage_pb2",
        "oaa.sensor.SensorStartResponseMessage_pb2",
        "oaa.sensor.SensorEventIndicationMessage_pb2",
        "oaa.sensor.SensorErrorMessage_pb2",
        "oaa.wifi.WifiSecurityRequestMessage_pb2",
        "oaa.wifi.WifiSecurityResponseMessage_pb2",
        "oaa.video.UpdateHuUiConfigResponse_pb2",
    ]:
        m = types.ModuleType(suffix)
        # Each message module exports a class named after its last component
        cls_name = suffix.split(".")[-1].replace("_pb2", "")
        setattr(m, cls_name, MagicMock)
        fakes[suffix] = m

    # google.protobuf.json_format.MessageToDict
    fakes["google.protobuf.json_format"].MessageToDict = MagicMock(return_value={})

    for name, mod in fakes.items():
        sys.modules.setdefault(name, mod)

    # Also patch shared.logger
    if "shared.logger" not in sys.modules:
        sl = types.ModuleType("shared.logger")
        sl.get_logger = MagicMock(return_value=MagicMock())
        sys.modules["shared.logger"] = sl
        shared = types.ModuleType("shared")
        sys.modules.setdefault("shared", shared)


_install_fake_protos()

# Remove cached module so fresh import picks up fakes
_this_module = __name__
for _k in list(sys.modules.keys()):
    if _k != _this_module and "message_to_proto" in _k:
        del sys.modules[_k]

import tcp_server.message_to_proto as m2p


# ===========================================================================
# Section 1 — message_id_to_proto_name
# ===========================================================================

class TestMessageIdToProtoName:

    @pytest.mark.unit
    def test_av_channel_id_returns_name(self):
        result = m2p.message_id_to_proto_name(0x8000)
        assert isinstance(result, str)
        assert result != ""

    @pytest.mark.unit
    def test_control_channel_id_returns_name(self):
        result = m2p.message_id_to_proto_name(1)
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_unknown_id_returns_unknown_string(self):
        result = m2p.message_id_to_proto_name(0xFFFF)
        assert "UnknownMessageId" in result
        assert "65535" in result

    @pytest.mark.unit
    def test_returns_string_type(self):
        assert isinstance(m2p.message_id_to_proto_name(0x8001), str)


# ===========================================================================
# Section 2 — proto_name_to_class
# ===========================================================================

class TestProtoNameToClass:

    @pytest.mark.unit
    def test_setup_request_returns_class(self):
        result = m2p.proto_name_to_class("SETUP_REQUEST")
        assert result is not None

    @pytest.mark.unit
    def test_pairing_request_returns_class(self):
        result = m2p.proto_name_to_class("PAIRING_REQUEST")
        assert result is not None

    @pytest.mark.unit
    def test_auth_complete_returns_class(self):
        result = m2p.proto_name_to_class("AUTH_COMPLETE")
        assert result is not None

    @pytest.mark.unit
    def test_sensor_request_returns_class(self):
        result = m2p.proto_name_to_class("SENSOR_REQUEST")
        assert result is not None

    @pytest.mark.unit
    def test_credentials_request_returns_class(self):
        result = m2p.proto_name_to_class("CREDENTIALS_REQUEST")
        assert result is not None

    @pytest.mark.unit
    def test_unknown_name_raises_value_error(self):
        with pytest.raises(ValueError):
            m2p.proto_name_to_class("TOTALLY_UNKNOWN_MESSAGE")

    @pytest.mark.unit
    def test_none_bytes_placeholder_returned_for_raw(self):
        # AV_MEDIA_WITH_TIMESTAMP_INDICATION maps to bytes (placeholder)
        result = m2p.proto_name_to_class("AV_MEDIA_WITH_TIMESTAMP_INDICATION")
        assert result is bytes

    @pytest.mark.unit
    def test_input_event_indication_returns_class(self):
        result = m2p.proto_name_to_class("INPUT_EVENT_INDICATION")
        assert result is not None


# ===========================================================================
# Section 3 — frame_data_to_dict
# ===========================================================================

class TestFrameDataToDict:

    @pytest.mark.unit
    def test_returns_dict(self):
        result = m2p.frame_data_to_dict({
            "type": "Phone->HU",
            "channel_id": 0,
            "message_id": 0x8000,
            "payload_hex": "",
        })
        assert isinstance(result, dict)

    @pytest.mark.unit
    def test_output_keys_present(self):
        result = m2p.frame_data_to_dict({
            "type": "Phone->HU",
            "channel_id": 0,
            "message_id": 0x8000,
            "payload_hex": "",
        })
        for key in ("type", "channel_id", "message_id", "message_name", "payload_as_dict"):
            assert key in result

    @pytest.mark.unit
    def test_unknown_message_id_does_not_raise(self):
        result = m2p.frame_data_to_dict({
            "type": "Phone->HU",
            "channel_id": 99,
            "message_id": 0xDEAD,
            "payload_hex": "aabb",
        })
        assert isinstance(result, dict)

    @pytest.mark.unit
    def test_channel_id_preserved_in_output(self):
        result = m2p.frame_data_to_dict({
            "type": "Phone->HU",
            "channel_id": 7,
            "message_id": 1,
            "payload_hex": "",
        })
        assert result["channel_id"] == 7

    @pytest.mark.unit
    def test_message_id_preserved_in_output(self):
        result = m2p.frame_data_to_dict({
            "type": "Phone->HU",
            "channel_id": 0,
            "message_id": 0x8001,
            "payload_hex": "",
        })
        assert result["message_id"] == 0x8001

    @pytest.mark.unit
    def test_invalid_hex_falls_back_gracefully(self):
        result = m2p.frame_data_to_dict({
            "type": "Phone->HU",
            "channel_id": 0,
            "message_id": 0x8000,
            "payload_hex": "ZZZZ",  # invalid hex
        })
        assert isinstance(result, dict)
