#!/usr/bin/env python3
"""
Web Browser Head Unit — Module Template

Copy this folder to create new process-isolated backend modules.
Extends `BaseBackendModule` with REST API, WebSocket, ZMQ, ConfigClient, Schema & Inter-Module RPC support.
"""

import asyncio
from typing import Any
from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_bool, field_int, field_float, field_enum


class SampleModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="sample_module",
            priority=1,
            path_prefix="/api/sample",  # Route prefix exposed via Gateway Proxy
        )

    def get_default_config(self) -> dict[str, Any]:
        """Provides default fallback configuration if config_manager is not present."""
        return {
            "feature_enabled": True,
            "max_connections": 5,
            "timeout_seconds": 10.0,
            "mode": "auto",
        }

    def get_schema(self) -> dict[str, Any]:
        """Declares strongly-typed configuration schema descriptors."""
        return {
            "feature_enabled": field_bool(default=True),
            "max_connections": field_int(default=5, min=1, max=100),
            "timeout_seconds": field_float(default=10.0, min=0.5, max=60.0),
            "mode": field_enum(default="auto", choices=["off", "auto", "on"]),
        }

    async def setup(self) -> None:
        """Register REST API, WebSocket routes, and ZMQ topic subscriptions."""
        self.add_http_route("GET", "/status", self.handle_status)
        self.add_ws_route("/ws", self.handle_websocket)
        self.subscribe("system.ping", self.on_ping)

    def on_config_updated(self, new_config: dict[str, Any]) -> None:
        """Called automatically whenever module configuration is updated at runtime."""
        self.log.info(f"SampleModule received updated configuration: {new_config}")

    async def handle_status(self, request: web.Request) -> web.Response:
        """Sample REST API endpoint: GET /api/sample/status"""
        return web.json_response({
            "module": self.name,
            "status": "active",
            "port": self.port,
            "config": self.config,
            "registry_modules": list(self.module_registry.keys()),
        })

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Sample WebSocket endpoint: ws://.../api/sample/ws"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"event": "connected", "module": self.name})

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await ws.send_json({"echo": msg.data})

        return ws

    def on_ping(self, topic: str, payload: dict) -> None:
        self.log.info(f"Ping received: {payload}")
        self.publish("system.pong", {"module": self.name})

    async def run(self) -> None:
        """Main loop execution."""
        self.log.info(f"Module '{self.name}' main loop running...")
        while self._running:
            await asyncio.sleep(5)

    async def teardown(self) -> None:
        """Clean resource deallocation on module shutdown."""
        self.log.info(f"Module '{self.name}' tearing down resources...")


if __name__ == "__main__":
    run_module(SampleModule)
