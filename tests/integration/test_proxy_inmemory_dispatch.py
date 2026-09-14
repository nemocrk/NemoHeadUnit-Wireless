"""
test_proxy_inmemory_dispatch.py — Integration tests for ProxyModule dispatching in-memory routes in multithreading mode.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiohttp import web
from modules.proxy.main import ProxyModule
from shared.base_module import BaseBackendModule, SHARED_MODULE_REGISTRY

pytestmark = pytest.mark.integration


class DummyMicroservice(BaseBackendModule):
    def __init__(self, name="dummy_svc", priority=3, prefix="/api/dummy_svc"):
        super().__init__(name=name, priority=priority, path_prefix=prefix)
        self.ws_called = False

    async def setup(self):
        self.add_http_route("GET", "/status", self.handle_status)
        self.add_http_route("POST", "/echo", self.handle_echo)
        self.add_ws_route("/ws", self.handle_ws)

    async def handle_status(self, request):
        return web.json_response({"status": "healthy", "in_memory": True})

    async def handle_echo(self, request):
        body = await request.json()
        return web.json_response({"echo": body})

    async def handle_ws(self, request):
        self.ws_called = True
        ws = web.WebSocketResponse()
        return ws

    async def run(self):
        while self._running:
            await asyncio.sleep(0.01)

    async def teardown(self):
        pass


@pytest.mark.asyncio
async def test_proxy_dispatches_inmemory_http_and_ws(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")

    # Start dummy microservice
    svc = DummyMicroservice()
    svc._running = True
    t_svc = asyncio.create_task(svc.start())

    for _ in range(50):
        if svc.target_url == "inmemory://dummy_svc":
            break
        await asyncio.sleep(0.02)

    # Initialize proxy
    proxy = ProxyModule()
    await proxy.setup()
    proxy.register_route(svc.path_prefix, svc.target_url, name=svc.name)

    # 1. Test GET request forwarded in-memory
    req_get = MagicMock(spec=web.Request)
    req_get.method = "GET"
    req_get.path = "/api/dummy_svc/status"
    req_get.headers = {}
    req_get.query_string = ""
    req_get.match_info = {}
    req_get.json = AsyncMock(return_value={})

    resp_get = await proxy.handle_proxy_request(req_get)
    assert resp_get.status == 200
    assert json.loads(resp_get.body.decode("utf-8")) == {"status": "healthy", "in_memory": True}

    # 2. Test POST request forwarded in-memory
    req_post = MagicMock(spec=web.Request)
    req_post.method = "POST"
    req_post.path = "/api/dummy_svc/echo"
    req_post.headers = {"Content-Type": "application/json"}
    req_post.query_string = ""
    req_post.match_info = {}
    req_post.json = AsyncMock(return_value={"test_key": 123})

    resp_post = await proxy.handle_proxy_request(req_post)
    assert resp_post.status == 200
    assert json.loads(resp_post.body.decode("utf-8")) == {"echo": {"test_key": 123}}

    # 3. Test WebSocket upgrade forwarded in-memory
    req_ws = MagicMock(spec=web.Request)
    req_ws.method = "GET"
    req_ws.path = "/api/dummy_svc/ws"
    req_ws.headers = {"Upgrade": "websocket"}
    req_ws.query_string = ""

    resp_ws = await proxy.handle_proxy_request(req_ws)
    assert svc.ws_called is True
    assert isinstance(resp_ws, web.WebSocketResponse)

    await svc.stop()
    await t_svc
    await proxy.stop()
