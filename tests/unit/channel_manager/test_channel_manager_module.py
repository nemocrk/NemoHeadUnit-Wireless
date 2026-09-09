import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shared.constants import ChannelType
from modules.channel_manager.main import ChannelManagerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_channel_manager():
    with patch("shared.base_module.BusClient"), \
         patch("modules.channel_manager.main.BidirectionalMediaSHM"):
        mgr = ChannelManagerModule()
        yield mgr


def test_channel_manager_config_and_schema(mock_channel_manager):
    defaults = mock_channel_manager.get_default_config()
    assert defaults["head_unit_name"] == "NemoHeadUnit"
    assert defaults["autoclose_on_shutdown"] is True

    schema = mock_channel_manager.get_schema()
    assert isinstance(schema, dict)


def test_channel_manager_dynamic_channel_registry(mock_channel_manager):
    # Initial state: 0 is CONTROL
    assert mock_channel_manager.get_channel_type(0) == ChannelType.CONTROL
    assert mock_channel_manager.get_channel_type(999) == ChannelType.UNKNOWN

    # Dynamic SDR population
    mock_channel_manager.set_channel_type_map({
        "1": "INPUT",
        "2": "SENSOR",
        "3": "VIDEO",
        "4": "AUDIO",
    })
    assert mock_channel_manager.get_channel_type(1) == ChannelType.INPUT
    assert mock_channel_manager.get_channel_type(3) == ChannelType.VIDEO
    assert mock_channel_manager.get_channel_id_for_type(ChannelType.VIDEO) == 3
    assert mock_channel_manager.get_channel_id_for_type(ChannelType.INPUT) == 1

    # Fallback lookup for unmapped types
    assert mock_channel_manager.get_channel_id_for_type(ChannelType.BLUETOOTH) == 8


@pytest.mark.asyncio
async def test_channel_manager_rest_status(mock_channel_manager):
    mock_channel_manager.active_channels = {
        3: {
            "channel_id": 3,
            "av_channel": {
                "codec": "MEDIA_CODEC_VIDEO_H264_BP",
                "video_configs": [{"video_resolution": "VIDEO_1280x720"}],
            }
        }
    }
    mock_channel_manager.channel_type_map[3] = ChannelType.VIDEO

    req = MagicMock()
    resp = await mock_channel_manager.handle_get_status(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert 3 in data["active_channels"]
    assert "3" in data["stream_config"]["streams"]
    assert data["stream_config"]["streams"]["3"]["codec"] == "avc1.42E01E"
    assert "H264" in data["stream_config"]["streams"]["3"]["codec_name"]


@pytest.mark.asyncio
async def test_channel_manager_session_events(mock_channel_manager):
    mock_channel_manager.send_wire_frame = AsyncMock()

    # 1. TCP Session connected triggers VERSION_REQUEST on channel 0
    await mock_channel_manager.on_tcp_session_connected({"address": "127.0.0.1:5288"})
    mock_channel_manager.send_wire_frame.assert_called_once()
    args = mock_channel_manager.send_wire_frame.call_args[0]
    assert args[0] == 0  # channel 0
    assert args[1] == 1  # VERSION_REQUEST

    # 2. TLS handshake completed triggers AUTH_COMPLETE on channel 0
    mock_channel_manager.send_wire_frame.reset_mock()
    await mock_channel_manager.on_tls_handshake_completed({})
    mock_channel_manager.send_wire_frame.assert_called_once()
    args = mock_channel_manager.send_wire_frame.call_args[0]
    assert args[0] == 0  # channel 0
    assert args[1] == 4  # AUTH_COMPLETE


@pytest.mark.asyncio
async def test_channel_manager_frame_shm_dispatch(mock_channel_manager):
    """Verify on_frame_shm routes video and audio frames to respective handlers."""
    mock_channel_manager.channel_type_map[3] = ChannelType.VIDEO
    mock_channel_manager.channel_type_map[4] = ChannelType.AUDIO

    mock_channel_manager.video_handler.process_shm_frame = AsyncMock()
    mock_channel_manager.audio_handler.process_shm_frame = AsyncMock()

    # Video frame
    await mock_channel_manager.on_frame_shm({
        "channel_id": 3,
        "message_id": 1,
        "shm_offset": 1024,
        "timestamp_us": 123456,
        "payload_len": 500,
    })
    mock_channel_manager.video_handler.process_shm_frame.assert_called_once_with(1, 1024, 123456, 500)

    # Audio frame
    await mock_channel_manager.on_frame_shm({
        "channel_id": 4,
        "message_id": 1,
        "shm_offset": 2048,
        "timestamp_us": 654321,
        "payload_len": 200,
    })
    mock_channel_manager.audio_handler.process_shm_frame.assert_called_once_with(4, 1, 2048, 654321, 200)

    # Invalid offset ignored
    mock_channel_manager.video_handler.process_shm_frame.reset_mock()
    await mock_channel_manager.on_frame_shm({"channel_id": 3, "shm_offset": -1})
    mock_channel_manager.video_handler.process_shm_frame.assert_not_called()


@pytest.mark.asyncio
async def test_channel_manager_on_frame_received_routing(mock_channel_manager):
    """Verify on_frame_received routes frames to handlers based on ChannelType."""
    mock_channel_manager.channel_type_map[1] = ChannelType.INPUT
    mock_channel_manager.channel_type_map[2] = ChannelType.SENSOR
    mock_channel_manager.channel_type_map[8] = ChannelType.BLUETOOTH
    mock_channel_manager.channel_type_map[10] = ChannelType.PHONE_STATUS
    mock_channel_manager.channel_type_map[11] = ChannelType.NOTIFICATION

    mock_channel_manager.input_handler.handle_frame = AsyncMock()
    mock_channel_manager.sensor_handler.handle_frame = AsyncMock()
    mock_channel_manager.bluetooth_handler.handle_frame = AsyncMock()
    mock_channel_manager.phone_status_handler.handle_message = AsyncMock()
    mock_channel_manager.notification_handler.handle_message = AsyncMock()

    # INPUT frame
    await mock_channel_manager.on_frame_received({
        "channel_id": 1,
        "message_id": 0x8001,
        "payload_hex": "aabb",
    })
    mock_channel_manager.input_handler.handle_frame.assert_called_once_with(1, 0x8001, b"\xaa\xbb")

    # SENSOR frame
    await mock_channel_manager.on_frame_received({
        "channel_id": 2,
        "message_id": 0x8002,
        "payload_hex": "ccdd",
    })
    mock_channel_manager.sensor_handler.handle_frame.assert_called_once_with(2, 0x8002, b"\xcc\xdd")

    # PHONE_STATUS frame
    await mock_channel_manager.on_frame_received({
        "channel_id": 10,
        "message_id": 0x8001,
        "payload_hex": "0102",
    })
    mock_channel_manager.phone_status_handler.handle_message.assert_called_once_with(10, 0x8001, b"\x01\x02")


@pytest.mark.asyncio
async def test_channel_manager_phone_and_media_rest_endpoints(mock_channel_manager):
    """Verify handle_phone_action, touch, focus, and media_key REST endpoints."""
    mock_channel_manager.phone_status_handler.send_phone_action = AsyncMock(return_value=True)
    mock_channel_manager.input_handler.handle_touch_event = AsyncMock()
    mock_channel_manager.input_handler.handle_media_key = AsyncMock()
    mock_channel_manager.video_handler.send_focus_indication = AsyncMock()

    # 1. Phone action
    req_phone = MagicMock()
    req_phone.json = AsyncMock(return_value={"action": "ANSWER"})
    resp = await mock_channel_manager.handle_phone_action(req_phone)
    assert resp.status == 200
    mock_channel_manager.phone_status_handler.send_phone_action.assert_called_once_with("ANSWER")

    # 2. Touch input
    req_touch = MagicMock()
    req_touch.json = AsyncMock(return_value={"x": 640, "y": 360, "action": 0})
    resp_touch = await mock_channel_manager.handle_post_touch(req_touch)
    assert resp_touch.status == 200
    mock_channel_manager.input_handler.handle_touch_event.assert_called_once()

    # 3. Media key
    req_media = MagicMock()
    req_media.json = AsyncMock(return_value={"key_code": 85})
    resp_media = await mock_channel_manager.handle_post_media_key(req_media)
    assert resp_media.status == 200
    mock_channel_manager.input_handler.handle_media_key.assert_called_once_with(85)

    # 4. Video focus
    req_focus = MagicMock()
    req_focus.json = AsyncMock(return_value={"mode": "PROJECTED"})
    resp_focus = await mock_channel_manager.handle_post_focus(req_focus)
    assert resp_focus.status == 200
    mock_channel_manager.video_handler.send_focus_indication.assert_called_once()


@pytest.mark.asyncio
async def test_channel_manager_focus_and_input_events(mock_channel_manager):
    """Verify media.video.request_focus, release_focus, and input.event subscriptions."""
    mock_channel_manager.video_handler.send_focus_indication = AsyncMock()
    mock_channel_manager.input_handler.handle_touch_event = AsyncMock()
    mock_channel_manager.input_handler.handle_media_key = AsyncMock()

    # Request focus
    await mock_channel_manager.on_video_request_focus({"mode": "PROJECTED"})
    mock_channel_manager.video_handler.send_focus_indication.assert_called_once()

    # Release focus
    await mock_channel_manager.on_video_release_focus({})
    assert mock_channel_manager.video_handler.send_focus_indication.call_count == 2

    # Input touch event (press)
    await mock_channel_manager.on_input_event({
        "type": "press",
        "x": 100,
        "y": 200,
    })
    mock_channel_manager.input_handler.handle_touch_event.assert_called_once()

    # Input touch event (release)
    await mock_channel_manager.on_input_event({
        "type": "release",
        "x": 100,
        "y": 200,
    })
    assert mock_channel_manager.input_handler.handle_touch_event.call_count == 2


