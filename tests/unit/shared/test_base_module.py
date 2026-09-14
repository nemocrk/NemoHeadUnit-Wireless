import asyncio
import pytest
import aiohttp
from aiohttp import web
from unittest.mock import AsyncMock, MagicMock, patch
from shared.base_module import BaseBackendModule

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def autouse_mock_bus_client():
    with patch("shared.base_module.BusClient") as mock:
        yield mock


class DummyModule(BaseBackendModule):
    def __init__(self):
        super().__init__(name="dummy", priority=2, path_prefix="/api/dummy")
        self.setup_called = False
        self.teardown_called = False

    def get_default_config(self):
        return {"param": 100}

    def get_schema(self):
        return {}

    async def setup(self):
        self.setup_called = True

    async def run(self):
        pass

    async def teardown(self):
        self.teardown_called = True


def test_base_module_initialization():
    mod = DummyModule()
    assert mod.name == "dummy"
    assert mod.priority == 2
    assert mod.path_prefix == "/api/dummy"
    assert mod.config == {"param": 100}


def test_base_module_add_http_and_ws_routes():
    mod = DummyModule()

    async def sample_handler(req):
        return web.Response(text="ok")

    mod.add_http_route("GET", "/status", sample_handler)
    mod.add_ws_route("/stream", sample_handler)
    # Test path already starting with path_prefix
    mod.add_http_route("POST", "/api/dummy/action", sample_handler)
    mod.add_ws_route("/api/dummy/live", sample_handler)

    routes = [r.resource.canonical for r in mod.web_app.router.routes()]
    assert "/api/dummy/status" in routes
    assert "/api/dummy/stream" in routes
    assert "/api/dummy/action" in routes
    assert "/api/dummy/live" in routes


def test_base_module_base_methods_and_root_prefix():
    # Test root path_prefix
    class RootModule(BaseBackendModule):
        async def setup(self): pass
        async def run(self): pass
        async def teardown(self): pass

    rm = RootModule(name="root_mod", priority=1, path_prefix="/")
    assert rm.get_default_config() == {}
    assert rm.get_schema() == {}
    rm.on_config_updated({"key": "val"})

    rm.publish("test.topic", {"a": 1})
    rm.bus.publish.assert_called_with("test.topic", {"a": 1})



@pytest.mark.asyncio
async def test_base_module_call_module_rpc():
    mod = DummyModule()
    # Mock system registry
    mod.module_registry = {
        "target_mod": {"target_url": "http://127.0.0.1:9999"}
    }

    mock_resp = AsyncMock()
    mock_resp.json.return_value = {"success": True}

    mock_session = MagicMock()
    mock_req_ctx = AsyncMock()
    mock_req_ctx.__aenter__.return_value = mock_resp
    mock_session.request.return_value = mock_req_ctx
    mod.client_session = mock_session

    res = await mod.call_module("target_mod", "GET", "/status")
    assert res == {"success": True}
    mock_session.request.assert_called_with(
        method="GET",
        url="http://127.0.0.1:9999/status",
        json=None,
        timeout=aiohttp.ClientTimeout(total=pytest.approx(3.0)),
    )


@pytest.mark.asyncio
async def test_base_module_call_module_missing_registry():
    mod = DummyModule()
    with pytest.raises(RuntimeError, match="Target module 'unknown' is not currently available"):
        await mod.call_module("unknown", "GET", "/status")


@pytest.mark.asyncio
async def test_base_module_client_log():
    mod = DummyModule()
    req = MagicMock()
    req.json = AsyncMock(return_value={"level": "ERROR", "message": "JS Exception", "module": "frontend"})
    resp = await mod._handle_client_log(req)
    assert resp.status == 200

    # Bad payload
    req_bad = MagicMock()
    req_bad.json = AsyncMock(side_effect=Exception("parse error"))
    resp_bad = await mod._handle_client_log(req_bad)
    assert resp_bad.status == 400


@pytest.mark.asyncio
async def test_base_module_close_window():
    mod = DummyModule()
    mod.publish = MagicMock()
    req = MagicMock()
    resp = await mod._handle_close_window(req)
    assert resp.status == 200
    mod.publish.assert_called_once_with("system.shutdown", {"sender": "dummy", "reason": "user_exit_button"})


def test_base_module_config_sync_and_heartbeat():
    mod = DummyModule()
    mod.on_config_updated = MagicMock()

    # Config sync
    mod._handle_config_sync({"param": 200})
    assert mod.config == {"param": 200}
    mod.on_config_updated.assert_called_once_with({"param": 200})

    # Heartbeat registry update
    mod._handle_heartbeat("system.heartbeat", {
        "modules": {
            "tcp_server": {"target_url": "http://127.0.0.1:8080"}
        }
    })
    assert "tcp_server" in mod.module_registry
    assert mod.module_registry["tcp_server"]["target_url"] == "http://127.0.0.1:8080"


def test_base_module_readiness_announcement():
    mod = DummyModule()
    mod.target_url = "http://127.0.0.1:8088"
    mod.bus.publish = MagicMock()

    # ready_to_start
    mod._announce_readiness("ready_to_start")
    mod.bus.publish.assert_called_with("system.module_ready", {
        "name": "dummy",
        "priority": 2,
        "path_prefix": "/api/dummy",
        "target_url": "http://127.0.0.1:8088",
    })

    # ready (also registers proxy route)
    mod._announce_readiness("ready")
    assert mod.bus.publish.call_count == 3  # system.module_ready + system.ready + proxy.register_route


@pytest.mark.asyncio
async def test_base_module_async_subscribe():
    mod = DummyModule()
    called_payload = None

    async def async_cb(payload):
        nonlocal called_payload
        called_payload = payload

    mod.subscribe("custom.topic", async_cb)
    assert mod.bus.subscribe.called
    topic, wrapper = mod.bus.subscribe.call_args[0]
    assert topic == "custom.topic"

    # Invoke wrapper
    wrapper("custom.topic", {"key": "val"})
    await asyncio.sleep(0.05)
    assert called_payload == {"key": "val"}


@pytest.mark.asyncio
async def test_base_module_web_server_and_cleanup():
    mod = DummyModule()
    await mod._start_web_server()
    assert mod.port > 0
    assert mod.target_url == f"http://127.0.0.1:{mod.port}"

    await mod._cleanup()
    assert mod._running is False
    assert mod.teardown_called is True


@pytest.mark.asyncio
async def test_base_module_start_lifecycle():
    mod = DummyModule()
    mod.setup = AsyncMock()
    mod.teardown = AsyncMock()

    # Make run exit immediately
    async def mock_run():
        pass
    mod.run = mock_run

    # Simulate system.start arrival
    async def trigger_start():
        await asyncio.sleep(0.05)
        mod._running = True

    asyncio.create_task(trigger_start())

    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        mod._running = True  # Ensure while loop exits
        await mod.start()

    assert mod.setup.called
    assert mod.teardown.called


def test_base_module_run_module_launcher():
    from shared.base_module import run_module
    with patch.object(DummyModule, "run_main") as mock_run_main:
        run_module(DummyModule)
        mock_run_main.assert_called_once()


@pytest.mark.asyncio
async def test_base_module_handle_ws_logs():
    from aiohttp import web
    mod = DummyModule()

    req = MagicMock(spec=web.Request)
    req.query = {"module": "testmod", "level": "WARNING"}

    sent_lines = []
    mock_ws = MagicMock()
    mock_ws.prepare = AsyncMock()
    
    async def mock_send(msg):
        sent_lines.append(msg)
    mock_ws.send_str = mock_send

    # Provide an async iterator that exits quickly
    class AsyncIter:
        def __aiter__(self):
            return self
        async def __anext__(self):
            await asyncio.sleep(0.05)
            raise StopAsyncIteration

    mock_ws.__aiter__ = lambda s: AsyncIter().__aiter__()

    captured_listeners = []
    with patch("aiohttp.web.WebSocketResponse", return_value=mock_ws), \
         patch("shared.base_module.add_log_listener", side_effect=lambda fn: captured_listeners.append(fn)), \
         patch("shared.base_module.remove_log_listener") as m_rm:

        ws_task = asyncio.create_task(mod._handle_ws_logs(req))
        await asyncio.sleep(0.01)

        # Trigger listener callbacks
        assert len(captured_listeners) == 1
        listener = captured_listeners[0]

        # 1. Debug level: dropped
        listener({"timestamp": "12:00:00", "level": "DEBUG", "module": "testmod", "message": "debug msg"})
        # 2. Wrong module: dropped
        listener({"timestamp": "12:00:00", "level": "ERROR", "module": "othermod", "message": "err msg"})
        # 3. Matching module and level: sent
        listener({"timestamp": "12:00:00", "level": "WARNING", "module": "testmod", "message": "warn msg"})

        await ws_task
        m_rm.assert_called_once()
        assert len(sent_lines) == 1
        assert "warn msg" in sent_lines[0]


def test_base_module_run_main():
    mod = DummyModule()
    with patch.object(mod, "start", new_callable=AsyncMock), \
         patch("sys.exit") as m_exit:
        mod.run_main()
        m_exit.assert_called_with(0)


@pytest.mark.asyncio
async def test_base_module_client_log_and_close_window():
    mod = DummyModule()

    # Test client log levels
    for lvl in ("DEBUG", "WARN", "ERROR", "CRIT", "INFO"):
        req = MagicMock(spec=web.Request)
        req.json = AsyncMock(return_value={"level": lvl, "message": f"test {lvl}", "module": "testui"})
        resp = await mod._handle_client_log(req)
        assert resp.status == 200

    # Test client log exception handling
    bad_req = MagicMock(spec=web.Request)
    bad_req.json = AsyncMock(side_effect=ValueError("bad json"))
    bad_resp = await mod._handle_client_log(bad_req)
    assert bad_resp.status == 400

    # Test close window
    close_req = MagicMock(spec=web.Request)
    with patch.object(mod, "publish") as m_pub:
        resp = await mod._handle_close_window(close_req)
        assert resp.status == 200
        m_pub.assert_called_with("system.shutdown", {"sender": "dummy", "reason": "user_exit_button"})


@pytest.mark.asyncio
async def test_base_module_async_subscribe_delivery():
    mod = DummyModule()
    received = []

    async def my_async_cb(payload):
        received.append(payload)

    mod.subscribe("test.topic", my_async_cb)
    mod.bus.subscribe.assert_called_once()

    # Call the wrapper directly
    wrapper = mod.bus.subscribe.call_args[0][1]
    wrapper("test.topic", {"data": 42})
    await asyncio.sleep(0.05)
    assert {"data": 42} in received


@pytest.mark.asyncio
async def test_base_module_call_module():
    mod = DummyModule()

    # Not found -> raises RuntimeError
    with pytest.raises(RuntimeError):
        await mod.call_module("nonexistent_service", "GET", "/status")

    # Found in registry
    mod.module_registry["active_service"] = {"target_url": "http://127.0.0.1:1234"}
    mock_resp = AsyncMock()
    mock_resp.json.return_value = {"status": "ok"}
    
    mock_session = MagicMock()
    mock_session.request.return_value.__aenter__.return_value = mock_resp

    mod.client_session = mock_session
    res = await mod.call_module("active_service", "GET", "/status")
    assert res == {"status": "ok"}


@pytest.mark.asyncio
async def test_base_module_cleanup_and_teardown_error():
    mod = DummyModule()
    mod.teardown = AsyncMock(side_effect=RuntimeError("Teardown error"))
    mod.client_session = AsyncMock()
    mod.site = AsyncMock()
    mod.runner = AsyncMock()

    await mod._cleanup()
    mod.client_session.close.assert_called_once()
    mod.site.stop.assert_called_once()
    mod.runner.shutdown.assert_called_once()
    mod.runner.cleanup.assert_called_once()
    mod.bus.stop.assert_called_once()


@pytest.mark.asyncio
async def test_base_module_async_subscribe_two_params():
    mod = DummyModule()
    received = []

    async def my_2param_cb(topic, payload):
        received.append((topic, payload))

    mod.subscribe("test.topic2", my_2param_cb)
    wrapper = mod.bus.subscribe.call_args[0][1]
    wrapper("test.topic2", {"val": 99})
    await asyncio.sleep(0.05)
    assert ("test.topic2", {"val": 99}) in received






