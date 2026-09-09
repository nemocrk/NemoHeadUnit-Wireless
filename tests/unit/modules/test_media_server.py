"""
test_media_server.py — Unit tests for MediaServerModule (modules.media_server.main)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiohttp import web

from modules.media_server.main import MediaServerModule, _is_loopback

pytestmark = pytest.mark.unit


def test_is_loopback():
    assert _is_loopback("127.0.0.1:8080") is True
    assert _is_loopback("localhost:3000") is True
    assert _is_loopback("[::1]:8080") is True
    assert _is_loopback("192.168.1.50:8080") is False
    assert _is_loopback("10.0.0.1") is False
    assert _is_loopback("") is False


@pytest.fixture
def mock_media_server():
    with patch("shared.base_module.BusClient"), \
         patch("modules.media_server.main.BidirectionalMediaSHM"), \
         patch("modules.media_server.main.get_audio_adapter") as mock_audio_cls:
        mock_audio = MagicMock()
        mock_audio_cls.return_value = mock_audio
        mod = MediaServerModule()
        mod.audio_adapter = mock_audio
        yield mod


def test_media_server_config_and_schema(mock_media_server):
    defaults = mock_media_server.get_default_config()
    assert defaults["transport_mode"] == "auto"
    assert defaults["jpeg_quality"] == 75

    schema = mock_media_server.get_schema()
    assert "transport_mode" in schema
    assert "jpeg_quality" in schema


@pytest.mark.asyncio
async def test_media_server_handle_volume_get(mock_media_server):
    mock_media_server.audio_adapter.get_volume = AsyncMock(return_value={
        "volume": 65, "muted": False, "sink": "default"
    })
    req = MagicMock(spec=web.Request)
    req.method = "GET"

    resp = await mock_media_server._handle_volume(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["volume"] == 65
    assert data["muted"] is False


@pytest.mark.asyncio
async def test_media_server_handle_volume_post_actions(mock_media_server):
    mock_media_server.audio_adapter.volume_up = AsyncMock(return_value={"volume": 75, "muted": False})
    mock_media_server.audio_adapter.volume_down = AsyncMock(return_value={"volume": 55, "muted": False})
    mock_media_server.audio_adapter.toggle_mute = AsyncMock(return_value={"volume": 65, "muted": True})
    mock_media_server.audio_adapter.set_volume = AsyncMock(return_value={"volume": 90, "muted": False})

    req_up = MagicMock(spec=web.Request)
    req_up.method = "POST"
    req_up.can_read_body = True
    req_up.json = AsyncMock(return_value={"action": "up", "step": 10})
    resp_up = await mock_media_server._handle_volume(req_up)
    assert json.loads(resp_up.text)["volume"] == 75

    req_down = MagicMock(spec=web.Request)
    req_down.method = "POST"
    req_down.can_read_body = True
    req_down.json = AsyncMock(return_value={"action": "down", "step": 10})
    resp_down = await mock_media_server._handle_volume(req_down)
    assert json.loads(resp_down.text)["volume"] == 55

    req_mute = MagicMock(spec=web.Request)
    req_mute.method = "POST"
    req_mute.can_read_body = True
    req_mute.json = AsyncMock(return_value={"action": "mute"})
    resp_mute = await mock_media_server._handle_volume(req_mute)
    assert json.loads(resp_mute.text)["muted"] is True

    req_set = MagicMock(spec=web.Request)
    req_set.method = "POST"
    req_set.can_read_body = True
    req_set.json = AsyncMock(return_value={"volume": 90})
    resp_set = await mock_media_server._handle_volume(req_set)
    assert json.loads(resp_set.text)["volume"] == 90


@pytest.mark.asyncio
async def test_media_server_handle_audio_devices(mock_media_server):
    mock_media_server.audio_adapter.get_available_sinks = AsyncMock(return_value=[{"id": "default", "name": "Default"}])
    mock_media_server.audio_adapter.get_available_sources = AsyncMock(return_value=[{"id": "default", "name": "Default"}])
    mock_media_server.audio_adapter.get_volume = AsyncMock(return_value={"volume": 80, "muted": False})
    mock_media_server.audio_adapter.set_active_sink = AsyncMock(return_value=True)
    mock_media_server.audio_adapter.set_active_source = AsyncMock(return_value=True)

    # GET
    req_get = MagicMock(spec=web.Request)
    req_get.method = "GET"
    resp_get = await mock_media_server._handle_audio_devices(req_get)
    assert resp_get.status == 200
    data = json.loads(resp_get.text)
    assert "sinks" in data
    assert "sources" in data

    # POST select devices
    req_post = MagicMock(spec=web.Request)
    req_post.method = "POST"
    req_post.can_read_body = True
    req_post.json = AsyncMock(return_value={"sink": "custom_sink", "source": "custom_source"})
    resp_post = await mock_media_server._handle_audio_devices(req_post)
    assert resp_post.status == 200
    mock_media_server.audio_adapter.set_active_sink.assert_called_once_with("custom_sink")
    mock_media_server.audio_adapter.set_active_source.assert_called_once_with("custom_source")


def test_media_server_resolve_fallback_chain(mock_media_server):
    chain_yuv = mock_media_server._resolve_fallback_chain("yuv420")
    assert chain_yuv[0] == "yuv420"
    assert "rgba" in chain_yuv

    chain_webp = mock_media_server._resolve_fallback_chain("webp")
    assert chain_webp[0] == "webp"
    assert "mjpeg" in chain_webp

