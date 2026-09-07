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

    routes = [r.resource.canonical for r in mod.web_app.router.routes()]
    assert "/api/dummy/status" in routes
    assert "/api/dummy/stream" in routes


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
