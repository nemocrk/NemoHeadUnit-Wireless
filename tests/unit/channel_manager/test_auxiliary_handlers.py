import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.input.InputChannelMessageIdsEnum_pb2 import InputChannelMessage
from protos.oaa.input.InputBindingResponseMessage_pb2 import InputBindingResponse
from protos.oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
from protos.oaa.sensor.SensorChannelMessageIdsEnum_pb2 import SensorChannelMessage
from protos.oaa.sensor.SensorStartRequestMessage_pb2 import SensorStartRequestMessage
from protos.oaa.sensor.SensorStartResponseMessage_pb2 import SensorStartResponseMessage
from protos.oaa.sensor.SensorEventIndicationMessage_pb2 import SensorEventIndication
from protos.oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2 import BluetoothChannelMessage
from protos.oaa.bluetooth.BluetoothPairingResponseMessage_pb2 import BluetoothPairingResponse
from protos.oaa.bluetooth.BluetoothAuthenticationResultMessage_pb2 import BluetoothAuthenticationResult
from protos.oaa.wifi.WifiChannelMessageIdsEnum_pb2 import WifiChannelMessage
from protos.oaa.wifi.WifiCredentialsResponseMessage_pb2 import (
    WifiCredentialsResponse,
    WifiCredentialSecurityMode,
    WifiCredentialStatus,
)
from protos.oaa.navigation.NavigationTurnEventMessage_pb2 import NavigationTurnEvent
from protos.oaa.navigation.ManeuverTypeEnum_pb2 import ManeuverType
from protos.oaa.common.StatusEnum_pb2 import Status
from shared.constants import ChannelType
from modules.channel_manager.handlers.input_handler import InputChannelHandler
from modules.channel_manager.handlers.sensor_handler import SensorChannelHandler
from modules.channel_manager.handlers.bluetooth_handler import BluetoothChannelHandler
from modules.channel_manager.handlers.wifi_handler import WifiChannelHandler
from modules.channel_manager.handlers.navigation_handler import NavigationChannelHandler

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_aux_manager():
    mgr = MagicMock()
    mgr.send_wire_frame = AsyncMock()
    mgr.publish = MagicMock()
    mgr._notify_status_changed = MagicMock()
    mgr.get_channel_id_for_type.side_effect = lambda t: {
        ChannelType.INPUT: 1,
        ChannelType.SENSOR: 2,
        ChannelType.BLUETOOTH: 8,
        ChannelType.WIFI: 9,
        ChannelType.NAVIGATION: 10,
    }.get(t, 1)
    return mgr


@pytest.mark.asyncio
async def test_input_handler_touch_and_media_keys(mock_aux_manager):
    input_h = InputChannelHandler(mock_aux_manager)
    ch_id = mock_aux_manager.get_channel_id_for_type(ChannelType.INPUT)

    # 1. BINDING_REQUEST -> BINDING_RESPONSE(OK)
    await input_h.handle_frame(ch_id, InputChannelMessage.Enum.BINDING_REQUEST, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][0] == ch_id
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == InputChannelMessage.Enum.BINDING_RESPONSE
    resp = InputBindingResponse()
    resp.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert resp.status == Status.OK

    # 2. handle_touch_event -> INPUT_EVENT_INDICATION with TouchLocation
    mock_aux_manager.send_wire_frame.reset_mock()
    await input_h.handle_touch_event(action=0, x=640, y=360, pointer_id=1)
    mock_aux_manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_aux_manager.send_wire_frame.call_args[0][:3]
    assert ch == ch_id
    assert msg_id == InputChannelMessage.Enum.INPUT_EVENT_INDICATION
    ind = InputEventIndication()
    ind.ParseFromString(payload)
    assert ind.touch_event.touch_action == 0
    assert len(ind.touch_event.touch_location) == 1
    assert ind.touch_event.touch_location[0].x == 640
    assert ind.touch_event.touch_location[0].y == 360
    assert ind.touch_event.touch_location[0].pointer_id == 1

    # 3. handle_media_key -> ButtonEvents (press and release frames)
    mock_aux_manager.send_wire_frame.reset_mock()
    await input_h.handle_media_key(key_code=85)
    assert mock_aux_manager.send_wire_frame.call_count == 2
    # First frame: press
    ch_press, msg_id_press, payload_press = mock_aux_manager.send_wire_frame.call_args_list[0][0][:3]
    assert ch_press == ch_id
    assert msg_id_press == InputChannelMessage.Enum.INPUT_EVENT_INDICATION
    ind_press = InputEventIndication()
    ind_press.ParseFromString(payload_press)
    assert ind_press.button_event.button_events[0].keycode == 85
    assert ind_press.button_event.button_events[0].is_pressed is True
    # Second frame: release
    ch_rel, msg_id_rel, payload_rel = mock_aux_manager.send_wire_frame.call_args_list[1][0][:3]
    assert ch_rel == ch_id
    assert msg_id_rel == InputChannelMessage.Enum.INPUT_EVENT_INDICATION
    ind_rel = InputEventIndication()
    ind_rel.ParseFromString(payload_rel)
    assert ind_rel.button_event.button_events[0].keycode == 85
    assert ind_rel.button_event.button_events[0].is_pressed is False


@pytest.mark.asyncio
async def test_sensor_handler_driving_status(mock_aux_manager):
    sensor = SensorChannelHandler(mock_aux_manager)
    ch_id = mock_aux_manager.get_channel_id_for_type(ChannelType.SENSOR)

    # SENSOR_REQUEST for driving status (type 13)
    req = SensorStartRequestMessage(sensor_type=13)
    await sensor.handle_frame(ch_id, SensorChannelMessage.Enum.SENSOR_REQUEST, req.SerializeToString())

    assert mock_aux_manager.send_wire_frame.call_count == 2
    # 1st: SENSOR_START_RESPONSE (OK)
    ch1, msg_id1, payload1 = mock_aux_manager.send_wire_frame.call_args_list[0][0][:3]
    assert ch1 == ch_id
    assert msg_id1 == SensorChannelMessage.Enum.SENSOR_START_RESPONSE
    resp = SensorStartResponseMessage()
    resp.ParseFromString(payload1)
    assert resp.status == Status.OK

    # 2nd: SENSOR_EVENT_INDICATION
    ch2, msg_id2, payload2 = mock_aux_manager.send_wire_frame.call_args_list[1][0][:3]
    assert ch2 == ch_id
    assert msg_id2 == SensorChannelMessage.Enum.SENSOR_EVENT_INDICATION
    event = SensorEventIndication()
    event.ParseFromString(payload2)
    assert len(event.driving_status) > 0
    assert event.driving_status[0].status == 0  # UNRESTRICTED


@pytest.mark.asyncio
async def test_bluetooth_handler_pairing_and_auth(mock_aux_manager):
    bt = BluetoothChannelHandler(mock_aux_manager)
    ch_id = mock_aux_manager.get_channel_id_for_type(ChannelType.BLUETOOTH)

    # 1. PAIRING_REQUEST -> PAIRING_RESPONSE(already_paired=True)
    await bt.handle_frame(ch_id, BluetoothChannelMessage.Enum.PAIRING_REQUEST, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][0] == ch_id
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == BluetoothChannelMessage.Enum.PAIRING_RESPONSE
    resp = BluetoothPairingResponse()
    resp.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert resp.already_paired is True
    assert resp.status == Status.OK

    # 2. AUTH_DATA -> AUTH_RESULT(OK)
    mock_aux_manager.send_wire_frame.reset_mock()
    await bt.handle_frame(ch_id, BluetoothChannelMessage.Enum.AUTH_DATA, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][0] == ch_id
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == BluetoothChannelMessage.Enum.AUTH_RESULT
    auth_resp = BluetoothAuthenticationResult()
    auth_resp.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert auth_resp.status == Status.OK


@pytest.mark.asyncio
async def test_wifi_handler_credentials(mock_aux_manager):
    wifi = WifiChannelHandler(mock_aux_manager)
    ch_id = mock_aux_manager.get_channel_id_for_type(ChannelType.WIFI)

    await wifi.handle_frame(ch_id, WifiChannelMessage.Enum.CREDENTIALS_REQUEST, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][0] == ch_id
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == WifiChannelMessage.Enum.CREDENTIALS_RESPONSE
    resp = WifiCredentialsResponse()
    resp.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert resp.ssid == "AndroidAutoAP"
    assert resp.passphrase == "12345678"
    assert resp.security_mode == WifiCredentialSecurityMode.SECURITY_WPA2_PERSONAL
    assert resp.status == WifiCredentialStatus.CREDENTIAL_STATUS_OK


@pytest.mark.asyncio
async def test_navigation_handler_turn_event(mock_aux_manager):
    nav = NavigationChannelHandler(mock_aux_manager)
    ch_id = mock_aux_manager.get_channel_id_for_type(ChannelType.NAVIGATION)

    turn = NavigationTurnEvent()
    turn.road_name = "Highway 101"
    turn.maneuver_type = ManeuverType.TURN_NORMAL_RIGHT

    # 0x8004 (32772) is legacy turn details message ID
    await nav.handle_frame(ch_id, 0x8004, turn.SerializeToString())
    assert nav.active_road == "Highway 101"
    assert nav.last_maneuver_type == ManeuverType.TURN_NORMAL_RIGHT
    mock_aux_manager.publish.assert_called_with(
        "navigation.turn_event",
        {
            "road": "Highway 101",
            "maneuver_type": ManeuverType.TURN_NORMAL_RIGHT,
            "maneuver_name": "turn-normal-right",
            "turn_side": 0,
            "turn_icon": "",
            "event_name": "",
            "distance_meters": 0.0,
        },
    )
    mock_aux_manager._notify_status_changed.assert_called_once()
