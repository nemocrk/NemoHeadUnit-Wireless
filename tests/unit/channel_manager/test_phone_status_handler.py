# tests/unit/channel_manager/test_phone_status_handler.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.common.StatusEnum_pb2 import Status
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.phone.PhoneStatusMessage_pb2 import PhoneStatusUpdate, PhoneCall
from protos.oaa.phone.PhoneStatusInputMessage_pb2 import PhoneStatusInput, PhoneInputAction
from protos.oaa.phone.PhoneCallStateEnum_pb2 import PhoneCallState

from modules.channel_manager.handlers.phone_status_handler import (
    PhoneStatusHandler,
    MSG_CHANNEL_OPEN_REQUEST,
    MSG_CHANNEL_OPEN_RESPONSE,
    MSG_PHONE_STATUS_UPDATE,
    MSG_PHONE_STATUS_INPUT,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_manager():
    mgr = MagicMock()
    mgr.send_wire_frame = AsyncMock()
    mgr.publish = MagicMock()
    mgr.broadcast_ws_json = AsyncMock()
    mgr.get_channel_id_for_type = MagicMock(return_value=10)
    return mgr


@pytest.fixture
def handler(mock_manager):
    return PhoneStatusHandler(mock_manager)


@pytest.mark.asyncio
async def test_phone_status_channel_open_request(handler, mock_manager):
    """Verify channel open request sends ChannelOpenResponse OK on wire."""
    await handler.handle_message(channel_id=10, message_id=MSG_CHANNEL_OPEN_REQUEST, body=b"")
    mock_manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_manager.send_wire_frame.call_args[0][:3]
    assert ch == 10
    assert msg_id == MSG_CHANNEL_OPEN_RESPONSE
    resp = ChannelOpenResponse()
    resp.ParseFromString(payload)
    assert resp.status == Status.OK


@pytest.mark.asyncio
async def test_phone_status_update_signal_and_telemetry(handler, mock_manager):
    """Verify phone status telemetry (signal strength, battery, charging)."""
    update = PhoneStatusUpdate()
    update.signal_strength = 4

    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, update.SerializeToString())
    assert handler.current_state["signal_strength"] == 4

    # Signal 0 placeholder should not overwrite known signal
    update_zero = PhoneStatusUpdate()
    update_zero.signal_strength = 0
    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, update_zero.SerializeToString())
    assert handler.current_state["signal_strength"] == 4

    # Clamping signal > 5
    update_max = PhoneStatusUpdate()
    update_max.signal_strength = 10
    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, update_max.SerializeToString())
    assert handler.current_state["signal_strength"] == 5

    # Battery update via direct helper
    await handler.update_battery_status(85, is_charging=True)
    assert handler.current_state["battery_level"] == 85
    assert handler.current_state["is_charging"] is True

    # Telemetry merge from BT
    await handler.update_telemetry({
        "battery_pct": 92,
        "operator_name": "Vodafone",
        "is_roaming": False,
        "device_name": "Pixel 9",
        "device_address": "AA:BB:CC:DD:EE:FF",
    })
    assert handler.current_state["battery_level"] == 92
    assert handler.current_state["operator_name"] == "Vodafone"
    assert handler.current_state["device_name"] == "Pixel 9"


@pytest.mark.asyncio
async def test_phone_status_update_call_states(handler, mock_manager):
    """Verify incoming, active, and ended call states update internal state and publish bus events."""
    # 1. Incoming call with contact photo
    update_inc = PhoneStatusUpdate()
    call = update_inc.calls.add()
    call.call_state = 4  # INCOMING
    call.display_name = "Alice"
    call.phone_number = "+123456789"
    call.call_duration_seconds = 0
    call.contact_photo = b"JPEG_DATA"

    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, update_inc.SerializeToString())
    assert handler.current_state["is_in_call"] is True
    assert handler.current_state["call_state"] == "INCOMING"
    assert handler.current_state["caller_name"] == "Alice"
    assert handler.current_state["caller_number"] == "+123456789"
    assert handler.current_state["has_photo"] is True
    mock_manager.publish.assert_called_with("phone.status", handler.current_state)

    # 2. Active call in progress
    update_active = PhoneStatusUpdate()
    call2 = update_active.calls.add()
    call2.call_state = 1  # IN_CALL
    call2.display_name = "Alice"
    call2.phone_number = "+123456789"
    call2.call_duration_seconds = 42

    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, update_active.SerializeToString())
    assert handler.current_state["is_in_call"] is True
    assert handler.current_state["call_state"] == "IN_CALL"
    assert handler.current_state["call_duration_seconds"] == 42

    # 3. Call terminated (empty calls list)
    update_idle = PhoneStatusUpdate()
    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, update_idle.SerializeToString())
    assert handler.current_state["is_in_call"] is False
    assert handler.current_state["call_state"] == "IDLE"


@pytest.mark.asyncio
async def test_phone_status_actions(handler, mock_manager):
    """Verify answer, reject, and end call send PhoneStatusInput proto on wire."""
    handler.active_channel_id = 10
    handler.current_state["caller_name"] = "Alice"
    handler.current_state["caller_number"] = "+123456789"

    # Answer
    res = await handler.send_phone_action("ANSWER")
    assert res is True
    mock_manager.send_wire_frame.assert_called()
    ch, msg_id, payload = mock_manager.send_wire_frame.call_args[0][:3]
    assert ch == 10
    assert msg_id == MSG_PHONE_STATUS_INPUT
    inp = PhoneStatusInput()
    inp.ParseFromString(payload)
    assert inp.input_type.action == PhoneInputAction.PHONE_INPUT_CALL
    assert inp.display_name == "Alice"
    assert inp.caller_id == "+123456789"

    # Reject / Hangup
    res = await handler.send_phone_action("REJECT")
    assert res is True
    ch, msg_id, payload = mock_manager.send_wire_frame.call_args[0][:3]
    inp.ParseFromString(payload)
    assert inp.input_type.action == PhoneInputAction.PHONE_INPUT_BACK

    # Navigation actions (UP/DOWN/ENTER)
    await handler.send_phone_action("ENTER")
    ch, msg_id, payload = mock_manager.send_wire_frame.call_args[0][:3]
    inp.ParseFromString(payload)
    assert inp.input_type.action == PhoneInputAction.PHONE_INPUT_ENTER


@pytest.mark.asyncio
async def test_phone_status_malformed_payload(handler):
    """Verify malformed wire frame does not crash the handler."""
    await handler.handle_message(10, MSG_PHONE_STATUS_UPDATE, b"\xff\xff\xff\xff")
    assert isinstance(handler.current_state, dict)
