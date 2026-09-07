# tests/unit/modules/test_proxy.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.proxy.main import ProxyModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_proxy():
    with patch("shared.base_module.BusClient"):
        proxy = ProxyModule()
        yield proxy


def test_proxy_config_and_schema(mock_proxy):
    cfg = mock_proxy.get_default_config()
    assert cfg["public_port"] == 8000
    assert cfg["host"] == "0.0.0.0"
    schema = mock_proxy.get_schema()
    assert "public_port" in schema
    assert "host" in schema


def test_proxy_route_registration_events(mock_proxy):
    # Route registration via proxy.register_route
    mock_proxy.on_register_route("proxy.register_route", {
        "path_prefix": "/api/audio",
        "target_url": "http://127.0.0.1:8081",
    })
    assert mock_proxy.routes.get("/api/audio") == "http://127.0.0.1:8081"

    # Route registration via system.module_ready
    mock_proxy.on_module_ready("system.module_ready", {
        "name": "connectivity",
        "priority": 3,
        "path_prefix": "/api/connectivity",
        "target_url": "http://127.0.0.1:8082",
    })
    assert mock_proxy.routes.get("/api/connectivity") == "http://127.0.0.1:8082"
    assert "connectivity" in mock_proxy.module_registry


@pytest.mark.asyncio
async def test_proxy_get_modules_endpoint(mock_proxy):
    mock_proxy.module_registry = {
        "tcp_server": {
            "name": "tcp_server",
            "priority": 3,
            "path_prefix": "/api/tcp",
        }
    }
    req = MagicMock()
    resp = await mock_proxy.handle_get_modules(req)
    assert resp.status == 200
    import json
    data = json.loads(resp.text)
    assert "modules" in data
    assert "proxy" in data["modules"]
    assert "tcp_server" in data["modules"]
    assert data["modules"]["tcp_server"]["path_prefix"] == "/api/tcp"


@pytest.mark.asyncio
async def test_proxy_request_forwarding_success(mock_proxy):
    mock_proxy.routes["/api/sample"] = "http://127.0.0.1:9090"

    # Mock downstream response
    mock_downstream_resp = AsyncMock()
    mock_downstream_resp.status = 200
    mock_downstream_resp.headers = {"Content-Type": "application/json"}
    mock_downstream_resp.read.return_value = b'{"result": "ok"}'

    async def iter_any():
        yield b'{"result": "ok"}'

    mock_downstream_resp.content.iter_any = iter_any

    mock_client = MagicMock()
    mock_req_ctx = AsyncMock()
    mock_req_ctx.__aenter__.return_value = mock_downstream_resp
    mock_client.request.return_value = mock_req_ctx
    mock_proxy.proxy_client_session = mock_client

    # Build incoming request
    req = MagicMock()
    req.method = "GET"
    req.path = "/api/sample/info"
    req.query_string = "key=val"
    req.headers = {"User-Agent": "test-client"}
    req.can_read_body = False
    req.read = AsyncMock(return_value=b"")

    with patch("modules.proxy.main.web.StreamResponse") as mock_stream_cls:
        mock_stream_instance = AsyncMock()
        mock_stream_instance.status = 200
        mock_stream_instance.body = b'{"result": "ok"}'
        mock_stream_cls.return_value = mock_stream_instance

        resp = await mock_proxy.handle_proxy_request(req)
        assert resp.status == 200
        assert resp.body == b'{"result": "ok"}'
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert call_args[1]["url"] == "http://127.0.0.1:9090/api/sample/info?key=val"
        mock_stream_instance.prepare.assert_awaited_once_with(req)
        mock_stream_instance.write.assert_awaited_once_with(b'{"result": "ok"}')


@pytest.mark.asyncio
async def test_proxy_request_downstream_error_502(mock_proxy):
    mock_proxy.routes["/api/down"] = "http://127.0.0.1:9091"

    mock_client = MagicMock()
    mock_client.request.side_effect = Exception("Connection refused")
    mock_proxy.proxy_client_session = mock_client

    req = MagicMock()
    req.method = "GET"
    req.path = "/api/down/test"
    req.query_string = ""
    req.headers = {}
    req.can_read_body = False
    req.read = AsyncMock(return_value=b"")

    resp = await mock_proxy.handle_proxy_request(req)
    assert resp.status == 502
