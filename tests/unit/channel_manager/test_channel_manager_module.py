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
