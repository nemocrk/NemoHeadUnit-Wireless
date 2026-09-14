import struct
import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from protos.oaa.control.PingResponseMessage_pb2 import PingResponse
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.audio.AudioFocusRequestMessage_pb2 import AudioFocusRequest
from protos.oaa.audio.AudioFocusResponseMessage_pb2 import AudioFocusResponse
from protos.oaa.audio.AudioFocusTypeEnum_pb2 import AudioFocusType
from protos.oaa.audio.AudioFocusStateEnum_pb2 import AudioFocusState
from protos.oaa.common.StatusEnum_pb2 import Status
from modules.channel_manager.handlers.control_handler import ControlChannelHandler

pytestmark = pytest.mark.unit
MSG = ControlMessage.Enum


@pytest.fixture
def mock_control_handler():
    mock_mgr = MagicMock()
    mock_mgr.send_wire_frame = AsyncMock()
    mock_mgr.publish = MagicMock()
    mock_mgr.config = {}
    mock_mgr.set_channel_type_map = MagicMock()
    handler = ControlChannelHandler(mock_mgr)
    return handler


@pytest.mark.asyncio
async def test_control_handler_version_exchange(mock_control_handler):
    # 1. Phone sends VERSION_REQUEST
    await mock_control_handler.handle_frame(MSG.VERSION_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.VERSION_RESPONSE
    # Check payload has status OK (1.1)
    status, maj, min_ = struct.unpack(">HHH", payload)
    assert maj == 1 and min_ == 1

    # 2. Phone sends VERSION_RESPONSE
    mock_control_handler.manager.send_wire_frame.reset_mock()
    assert mock_control_handler.tls_started is False
    await mock_control_handler.handle_frame(MSG.VERSION_RESPONSE, b"")
    assert mock_control_handler.tls_started is True
    mock_control_handler.manager.publish.assert_called_once_with("aa.handshake.start_tls", {})


@pytest.mark.asyncio
async def test_control_handler_ssl_handshake_and_sdr(mock_control_handler):
    # 1. Phone sends SSL_HANDSHAKE
    handshake_bytes = b"\x16\x03\x03data"
    await mock_control_handler.handle_frame(MSG.SSL_HANDSHAKE, handshake_bytes)
    mock_control_handler.manager.publish.assert_called_once_with(
        "aa.handshake.feed_input",
        {"payload_hex": handshake_bytes.hex()},
    )

    # 2. Phone sends SERVICE_DISCOVERY_REQUEST
    await mock_control_handler.handle_frame(MSG.SERVICE_DISCOVERY_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, sdr_bytes = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.SERVICE_DISCOVERY_RESPONSE
    assert len(sdr_bytes) > 0
    mock_control_handler.manager.publish.assert_any_call("aa.sdr.channels", mock_control_handler.manager.publish.call_args_list[-1][0][1])


@pytest.mark.asyncio
async def test_control_handler_channel_open_and_ping(mock_control_handler):
    # 1. CHANNEL_OPEN_REQUEST
    await mock_control_handler.handle_frame(MSG.CHANNEL_OPEN_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.CHANNEL_OPEN_RESPONSE
    resp = ChannelOpenResponse()
    resp.ParseFromString(payload)
    assert resp.status == Status.OK

    # 2. PING_REQUEST -> PING_RESPONSE with same timestamp
    mock_control_handler.manager.send_wire_frame.reset_mock()
    ping_req = PingRequest(timestamp=987654321)
    await mock_control_handler.handle_frame(MSG.PING_REQUEST, ping_req.SerializeToString())
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.PING_RESPONSE
    pong = PingResponse()
    pong.ParseFromString(payload)
    assert pong.timestamp == 987654321


@pytest.mark.asyncio
async def test_control_handler_audio_focus_request(mock_control_handler):
    req = AudioFocusRequest()
    req.audio_focus_type = AudioFocusType.Enum.GAIN

    await mock_control_handler.handle_frame(MSG.AUDIO_FOCUS_REQUEST, req.SerializeToString())
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.AUDIO_FOCUS_RESPONSE
    resp = AudioFocusResponse()
    resp.ParseFromString(payload)
    assert resp.audio_focus_state == AudioFocusState.GAIN
    assert resp.granted is True

    mock_control_handler.manager.publish.assert_called_with("media.audio.focus", {
        "channel_id": 0,
        "focus_type": AudioFocusType.Enum.GAIN,
        "focus_state": AudioFocusState.GAIN,
        "is_paused": False,
    })


@pytest.mark.asyncio
async def test_control_handler_shutdown_request(mock_control_handler):
    await mock_control_handler.handle_frame(MSG.SHUTDOWN_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once_with(
        0, MSG.SHUTDOWN_RESPONSE, b"", encrypted=True
    )
