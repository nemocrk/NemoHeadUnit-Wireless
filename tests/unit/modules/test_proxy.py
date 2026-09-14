# tests/unit/modules/test_proxy.py
import asyncio
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


@pytest.mark.asyncio
async def test_proxy_serve_static_fallback(mock_proxy):
    """Verify static asset serving and SPA fallback."""
    # 1. Unknown API route returns 502 Bad Gateway
    req_api = MagicMock()
    req_api.path = "/api/unknown/service"
    req_api.headers = {}
    resp_api = await mock_proxy.handle_proxy_request(req_api)
    assert resp_api.status == 502

    # 2. Non-existent static route falls back to index.html or 404
    req_static = MagicMock()
    req_static.path = "/dashboard"
    req_static.headers = {}
    resp_static = await mock_proxy.handle_proxy_request(req_static)
    assert resp_static.status in (200, 404)


@pytest.mark.asyncio
async def test_proxy_on_heartbeat_route_registration(mock_proxy):
    """Verify on_heartbeat dynamically registers routes from all active modules."""
    mock_proxy.on_heartbeat("system.heartbeat", {
        "modules": {
            "tcp_server": {
                "name": "tcp_server",
                "path_prefix": "/api/tcp",
                "target_url": "http://127.0.0.1:8088",
            },
            "channel_manager": {
                "name": "channel_manager",
                "path_prefix": "/api/channels",
                "target_url": "http://127.0.0.1:8089",
            }
        }
    })
    assert mock_proxy.routes.get("/api/tcp") == "http://127.0.0.1:8088"
    assert mock_proxy.routes.get("/api/channels") == "http://127.0.0.1:8089"


@pytest.mark.asyncio
async def test_proxy_websocket_forwarding(mock_proxy):
    """Verify WebSocket upgrade requests route to _proxy_websocket."""
    mock_proxy.routes["/api/ws_test"] = "http://127.0.0.1:9095"

    req = MagicMock()
    req.method = "GET"
    req.path = "/api/ws_test/stream"
    req.query_string = ""
    req.headers = {"Upgrade": "websocket", "Connection": "Upgrade"}

    with patch.object(mock_proxy, "_proxy_websocket", new_callable=AsyncMock) as mock_proxy_ws:
        mock_ws_resp = MagicMock()
        mock_proxy_ws.return_value = mock_ws_resp

        resp = await mock_proxy.handle_proxy_request(req)
        assert resp is mock_ws_resp
        mock_proxy_ws.assert_called_once_with(req, "http://127.0.0.1:9095/api/ws_test/stream")


@pytest.mark.asyncio
async def test_proxy_teardown(mock_proxy):
    """Verify clean teardown closes proxy client session."""
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    mock_session.connector = MagicMock()
    mock_session.connector.close = AsyncMock()
    mock_proxy.proxy_client_session = mock_session

    await mock_proxy.teardown()
    mock_session.connector.close.assert_called_once()
    mock_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_proxy_setup(mock_proxy):
    """Verify proxy setup registers core routes and topics."""
    with patch("shared.base_module.BaseBackendModule.setup", new_callable=AsyncMock):
        await mock_proxy.setup()
        assert mock_proxy.proxy_client_session is not None
        # Clean up session
        await mock_proxy.proxy_client_session.close()

    routes = [r.resource.canonical for r in mock_proxy.web_app.router.routes()]
    assert "/api/system/modules" in routes
    assert "/api/logs" in routes
    assert "/api/proxy/logs" in routes


def test_proxy_register_route_edge_cases(mock_proxy):
    """Verify edge cases: root prefix ignored, and duplicate unchanged route skipped."""
    # 1. Root prefix
    mock_proxy.register_route("/", "http://127.0.0.1:8000")
    assert "/" not in mock_proxy.routes

    # 2. Duplicate unchanged route
    mock_proxy.register_route("/api/test", "http://127.0.0.1:8080")
    assert mock_proxy.routes["/api/test"] == "http://127.0.0.1:8080"
    # Re-register same route should return early without modifying
    mock_proxy.register_route("/api/test", "http://127.0.0.1:8080")
    assert mock_proxy.routes["/api/test"] == "http://127.0.0.1:8080"


@pytest.mark.asyncio
async def test_proxy_start_web_server(mock_proxy):
    """Verify _start_web_server initializes runner and site."""
    mock_proxy.config = {"public_port": 8000, "host": "127.0.0.1"}

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()

    mock_site = MagicMock()
    mock_site.start = AsyncMock()
    mock_site._server = MagicMock()
    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 8000)
    mock_site._server.sockets = [mock_socket]

    with patch("aiohttp.web.AppRunner", return_value=mock_runner), \
         patch("aiohttp.web.TCPSite", return_value=mock_site):
        await mock_proxy._start_web_server()
        assert mock_proxy.port == 8000
        assert mock_proxy.target_url == "http://127.0.0.1:8000"


@pytest.mark.asyncio
async def test_proxy_run_loop(mock_proxy):
    """Verify proxy run loop terminates when _running becomes False."""
    mock_proxy._running = True

    async def stop_soon():
        await asyncio.sleep(0.02)
        mock_proxy._running = False

    asyncio.create_task(stop_soon())
    with patch("asyncio.sleep", new=AsyncMock(side_effect=[None, None, asyncio.CancelledError])):
        try:
            await mock_proxy.run()
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_proxy_serve_static_advanced(mock_proxy, tmp_path):
    """Verify static file serving for root path and 404 fallback."""
    from pathlib import Path
    from aiohttp import web

    # 1. Root path -> falls back to index.html
    req_root = MagicMock()
    req_root.path = "/"
    with patch("modules.proxy.main.FRONTEND_DIR", tmp_path):
        # Create a dummy index.html
        dummy_index = tmp_path / "index.html"
        dummy_index.write_text("<html>hello</html>")

        resp = await mock_proxy._serve_static(req_root)
        assert isinstance(resp, web.FileResponse)

        # 2. Existing sub-file
        dummy_css = tmp_path / "style.css"
        dummy_css.write_text("body { color: red; }")
        req_css = MagicMock()
        req_css.path = "/style.css"
        resp_css = await mock_proxy._serve_static(req_css)
        assert isinstance(resp_css, web.FileResponse)

        # 3. Neither file nor index.html exists -> 404
        dummy_index.unlink()
        req_404 = MagicMock()
        req_404.path = "/missing.js"
        resp_404 = await mock_proxy._serve_static(req_404)
        assert resp_404.status == 404


@pytest.mark.asyncio
async def test_proxy_websocket_full_streaming(mock_proxy):
    """Verify _proxy_websocket forwards messages both ways."""
    from aiohttp import WSMsgType, web

    # Mock server WS
    mock_server_ws = MagicMock(spec=web.WebSocketResponse)
    mock_server_ws.prepare = AsyncMock()
    mock_server_ws.closed = False
    mock_server_ws.send_str = AsyncMock()
    mock_server_ws.send_bytes = AsyncMock()

    class AsyncMsgIter:
        def __init__(self, msgs):
            self.msgs = list(msgs)
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

    # Server incoming msgs (server -> client forwarder)
    msg1 = MagicMock()
    msg1.type = WSMsgType.TEXT
    msg1.data = "hello from client"
    mock_server_ws.__aiter__ = lambda s: AsyncMsgIter([msg1]).__aiter__()

    # Mock client WS (downstream)
    mock_client_ws = MagicMock()
    mock_client_ws.closed = False
    mock_client_ws.send_str = AsyncMock()
    mock_client_ws.send_bytes = AsyncMock()

    # Downstream incoming msgs (client -> server forwarder)
    msg2 = MagicMock()
    msg2.type = WSMsgType.BINARY
    msg2.data = b"\x01\x02\x03"
    msg_close = MagicMock()
    msg_close.type = WSMsgType.CLOSE
    mock_client_ws.__aiter__ = lambda s: AsyncMsgIter([msg2, msg_close]).__aiter__()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__.return_value = mock_client_ws
    mock_client_session = MagicMock()
    mock_client_session.ws_connect.return_value = mock_client_ctx
    mock_proxy.proxy_client_session = mock_client_session

    req = MagicMock()
    with patch("aiohttp.web.WebSocketResponse", return_value=mock_server_ws):
        res = await mock_proxy._proxy_websocket(req, "http://127.0.0.1:8080/ws")
        assert res is mock_server_ws
        mock_server_ws.send_bytes.assert_awaited_with(b"\x01\x02\x03")
        mock_client_ws.send_str.assert_awaited_with("hello from client")


