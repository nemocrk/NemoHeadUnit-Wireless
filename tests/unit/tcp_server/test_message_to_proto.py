import pytest
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from protos.oaa.bluetooth.BluetoothPairingRequestMessage_pb2 import BluetoothPairingRequest
from protos.oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
from protos.oaa.sensor.SensorRequestMessage_pb2 import SensorRequest
from protos.oaa.wifi.WifiSecurityRequestMessage_pb2 import WifiSecurityRequest
from modules.tcp_server.message_to_proto import (
    message_id_to_proto_name,
    proto_name_to_class,
    frame_data_to_dict,
)

pytestmark = pytest.mark.unit


def test_message_id_to_proto_name():
    assert message_id_to_proto_name(ControlMessage.Enum.VERSION_REQUEST) == "VERSION_REQUEST"
    assert message_id_to_proto_name(ControlMessage.Enum.CHANNEL_OPEN_REQUEST) == "CHANNEL_OPEN_REQUEST"
    assert message_id_to_proto_name(AVChannelMessage.Enum.SETUP_REQUEST) == "SETUP_REQUEST"
    assert message_id_to_proto_name(999999) == "UnknownMessageId 999999"

    # Explicit channel_id disambiguation when IDs overlap (e.g. ID 1 in Control vs AV)
    assert message_id_to_proto_name(1, channel_id=0) == "VERSION_REQUEST"
    assert message_id_to_proto_name(1, channel_id=3) == "AV_MEDIA_INDICATION"


def test_proto_name_to_class():
    cls_open = proto_name_to_class("CHANNEL_OPEN_REQUEST")
    assert cls_open is ChannelOpenRequest

    cls_ping = proto_name_to_class("PING_REQUEST")
    assert cls_ping is PingRequest

    assert proto_name_to_class("PAIRING_REQUEST") is BluetoothPairingRequest
    assert proto_name_to_class("INPUT_EVENT_INDICATION") is InputEventIndication
    assert proto_name_to_class("SENSOR_REQUEST") is SensorRequest
    assert proto_name_to_class("CREDENTIALS_REQUEST") is WifiSecurityRequest
    assert proto_name_to_class("AV_MEDIA_WITH_TIMESTAMP_INDICATION") is bytes

    with pytest.raises(ValueError, match="Unknown proto_name"):
        proto_name_to_class("NONEXISTENT_PROTO_MESSAGE")


def test_frame_data_to_dict_valid_protobuf():
    req = PingRequest(timestamp=123456789)
    req_hex = req.SerializeToString().hex()

    frame_data = {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.PING_REQUEST,
        "payload_hex": req_hex,
    }

    parsed = frame_data_to_dict(frame_data)
    assert parsed["message_name"] == "PING_REQUEST"
    assert isinstance(parsed["payload_as_dict"], dict)
    assert str(parsed["payload_as_dict"]["timestamp"]) == "123456789"


def test_frame_data_to_dict_fallback_on_corrupt():
    frame_data = {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.PING_REQUEST,
        "payload_hex": "corrupt_hex_not_valid",
    }
    parsed = frame_data_to_dict(frame_data)
    # Corrupt hex triggers error handling fallback to original payload_hex
    assert parsed["payload_as_dict"] == "corrupt_hex_not_valid"


def test_frame_data_to_dict_raw_bytes():
    frame_data = {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.VERSION_REQUEST,
        "payload_hex": "010203",
    }
    parsed = frame_data_to_dict(frame_data)
    assert parsed["message_name"] == "VERSION_REQUEST"
    assert parsed["payload_as_dict"] == {"raw_bytes": "0x..."}


def test_frame_data_to_dict_unknown_message_id():
    frame_data = {
        "channel_id": 99,
        "message_id": 999999,
        "payload_hex": "deadbeef",
    }
    parsed = frame_data_to_dict(frame_data)
    assert parsed["message_name"] == "UnknownMessageId 999999"
    assert parsed["payload_as_dict"] == "deadbeef"
