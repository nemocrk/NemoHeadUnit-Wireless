import asyncio
import os
import pytest
from aiohttp import web
from shared.base_module import BaseBackendModule, SHARED_MODULE_REGISTRY

pytestmark = pytest.mark.unit


class SampleServiceModule(BaseBackendModule):
    def __init__(self, name="service_a", priority=3, prefix="/api/service_a"):
        super().__init__(name=name, priority=priority, path_prefix=prefix)

    async def setup(self):
        self.add_http_route("GET", "/status", self.handle_status)
        self.add_http_route("POST", "/data", self.handle_post_data)

    async def handle_status(self, request):
        return web.json_response({"status": "ok", "module": self.name})

    async def handle_post_data(self, request):
        body = await request.json()
        return web.json_response({"received": body, "echo": True})

    async def run(self):
        while self._running:
            await asyncio.sleep(0.01)

    async def teardown(self):
        pass


class SampleCallerModule(BaseBackendModule):
    def __init__(self, name="caller", priority=4, prefix="/api/caller"):
        super().__init__(name=name, priority=priority, path_prefix=prefix)

    async def setup(self):
        pass

    async def run(self):
        while self._running:
            await asyncio.sleep(0.01)

    async def teardown(self):
        pass


@pytest.mark.asyncio
async def test_base_module_zero_sockets_in_multithreading(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")
    mod = SampleServiceModule()
    mod._running = True
    task = asyncio.create_task(mod.start())
    for _ in range(50):
        if mod.target_url:
            break
        await asyncio.sleep(0.02)

    # Verify no loopback port or TCPSite was created
    assert mod.port == 0
    assert mod.site is None
    assert mod.target_url == "inmemory://service_a"
    assert "service_a" in SHARED_MODULE_REGISTRY

    await mod.stop()
    await task
    assert "service_a" not in SHARED_MODULE_REGISTRY


@pytest.mark.asyncio
async def test_base_module_call_module_inmemory(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")
    service = SampleServiceModule()
    caller = SampleCallerModule()
    service._running = True
    caller._running = True

    t_service = asyncio.create_task(service.start())
    t_caller = asyncio.create_task(caller.start())
    for _ in range(50):
        if service.target_url and caller.target_url:
            break
        await asyncio.sleep(0.02)

    # Caller calls service_a via in-memory direct RPC
    status_resp = await caller.call_module("service_a", "GET", "/status")
    assert status_resp == {"status": "ok", "module": "service_a"}

    post_resp = await caller.call_module("service_a", "POST", "/data", data={"value": 42})
    assert post_resp == {"received": {"value": 42}, "echo": True}

    await service.stop()
    await caller.stop()
    await t_service
    await t_caller
