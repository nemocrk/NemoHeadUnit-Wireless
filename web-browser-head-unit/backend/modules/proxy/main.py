#!/usr/bin/env python3
"""
Web Browser Head Unit — Gateway Proxy Module

Priority 0 Core Module extending `BaseBackendModule`.
The only exposed public webserver operating on configurable port (default 8000).

Responsibilities:
  1. Serves static frontend assets from `frontend/` at root `/`.
  2. Dynamically registers module route prefixes published over ZMQ topic `proxy.register_route`.
  3. Reverse-proxies HTTP requests and WebSocket streams to downstream module endpoints (e.g. http://127.0.0.1:808X).
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_int, field_string

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"


class ProxyModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="proxy",
            priority=2,
            path_prefix="/",
        )
        self.routes: Dict[str, str] = {}
        self.proxy_client_session: aiohttp.ClientSession | None = None

    def get_default_config(self) -> dict[str, Any]:
        return {
            "public_port": 8000,
            "host": "0.0.0.0",
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "public_port": field_int(default=8000, min=1, max=65535),
            "host": field_string(default="0.0.0.0"),
        }

    async def setup(self) -> None:
        """Configures proxy fallback handler and ZMQ topic subscriptions."""
        self.proxy_client_session = aiohttp.ClientSession()

        # System discovery endpoints
        self.web_app.router.add_get("/api/system/modules", self.handle_get_modules)

        # Route matching handler: checks registered proxies or falls back to static assets
        self.web_app.router.add_route("*", "/{tail:.*}", self.handle_proxy_request)

        self.subscribe("proxy.register_route", self.on_register_route)
        self.subscribe("system.module_ready", self.on_module_ready)
        self.subscribe("system.heartbeat", self.on_heartbeat)

    async def handle_get_modules(self, request: web.Request) -> web.Response:
        """REST API: GET /api/system/modules — Returns active module metadata and log stream endpoints."""
        modules_info = {}
        for mod_name, mod_info in self.module_registry.items():
            prefix = mod_info.get("path_prefix", f"/api/{mod_name}")
            log_url = f"{prefix.rstrip('/')}/logs" if prefix != "/api/proxy" else "/api/logs"
            modules_info[mod_name] = {
                "name": mod_name,
                "priority": mod_info.get("priority", 3),
                "path_prefix": prefix,
                "log_ws_url": log_url
            }

        # Include proxy itself
        modules_info["proxy"] = {
            "name": "proxy",
            "priority": 2,
            "path_prefix": "/api/proxy",
            "log_ws_url": "/api/logs"
        }

        return web.json_response({
            "all_logs_ws_url": "all",
            "modules": modules_info
        })

    def on_heartbeat(self, topic: str, payload: dict) -> None:
        modules = payload.get("modules", {})
        for mod_info in modules.values():
            prefix = mod_info.get("path_prefix")
            target = mod_info.get("target_url")
            name = mod_info.get("name")
            priority = mod_info.get("priority", 3)
            if prefix and target:
                self.register_route(prefix, target, name=name, priority=priority)

    def on_register_route(self, topic: str, payload: dict) -> None:
        prefix = payload.get("path_prefix")
        target = payload.get("target_url")
        name = payload.get("name")
        priority = payload.get("priority", 3)
        if prefix and target:
            self.register_route(prefix, target, name=name, priority=priority)

    def on_module_ready(self, topic: str, payload: dict) -> None:
        prefix = payload.get("path_prefix")
        target = payload.get("target_url")
        name = payload.get("name")
        priority = payload.get("priority", 3)
        if prefix and target:
            self.register_route(prefix, target, name=name, priority=priority)

    def register_route(self, path_prefix: str, target_url: str, name: Optional[str] = None, priority: int = 3) -> None:
        normalized = "/" + path_prefix.strip("/")
        if normalized == "/":
            return  # Ignore root prefix (proxy itself)

        target_clean = target_url.rstrip("/")

        # Deduplicate module names mapping prefix to canonical module name
        prefix_map = {
            "/api/channels": "channel_manager",
            "/api/config": "config_manager",
            "/api/tcp": "tcp_server",
            "/api/connectivity": "connectivity_manager",
            "/api/proxy": "proxy"
        }
        mod_name = name or prefix_map.get(normalized, normalized.strip("/").split("/")[-1])

        self.module_registry[mod_name] = {
            "name": mod_name,
            "path_prefix": normalized,
            "target_url": target_clean,
            "priority": priority,
        }

        if self.routes.get(normalized) == target_clean:
            return  # Delta check: route already registered and unchanged

        self.routes[normalized] = target_clean
        self.log.info(f"Registered proxy route: '{normalized}' → '{target_clean}'")

    async def handle_proxy_request(self, request: web.Request) -> web.StreamResponse:
        path = request.path

        # Check for dynamic proxy route matches
        for prefix in sorted(self.routes.keys(), key=len, reverse=True):
            if prefix != "/" and (path == prefix or path.startswith(prefix + "/")):
                target_base = self.routes[prefix]
                target_url = f"{target_base}{path}"
                if request.query_string:
                    target_url += f"?{request.query_string}"

                self.log.info(f"Proxying [{request.method}] {path} → {target_url}")
                return await self._proxy_http(request, target_url)

        # Fallback: Serve static assets from frontend/
        return await self._serve_static(request)

    async def _proxy_http(self, request: web.Request, target_url: str) -> web.StreamResponse:
        if not self.proxy_client_session:
            self.proxy_client_session = aiohttp.ClientSession()

        # Handle WebSocket upgrade proxying
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_websocket(request, target_url)

        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        body = await request.read()

        try:
            async with self.proxy_client_session.request(
                method=request.method,
                url=target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
            ) as resp:
                response = web.StreamResponse(status=resp.status, headers=resp.headers)
                await response.prepare(request)
                async for chunk in resp.content.iter_any():
                    await response.write(chunk)
                return response
        except Exception as e:
            self.log.error(f"Proxy error for {target_url}: {e}")
            return web.Response(status=502, text=f"Bad Gateway: {e}")

    async def _proxy_websocket(self, request: web.Request, target_url: str) -> web.WebSocketResponse:
        ws_server = web.WebSocketResponse()
        await ws_server.prepare(request)

        ws_target = target_url.replace("http://", "ws://").replace("https://", "wss://")

        try:
            async with self.proxy_client_session.ws_connect(ws_target) as ws_client:
                async def forward_client_to_server():
                    try:
                        async for msg in ws_client:
                            if ws_server.closed:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await ws_server.send_str(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await ws_server.send_bytes(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                                break
                    except (RuntimeError, ConnectionResetError, asyncio.CancelledError):
                        pass

                async def forward_server_to_client():
                    try:
                        async for msg in ws_server:
                            if ws_client.closed:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await ws_client.send_str(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await ws_client.send_bytes(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                                break
                    except (RuntimeError, ConnectionResetError, asyncio.CancelledError):
                        pass

                await asyncio.gather(forward_client_to_server(), forward_server_to_client(), return_exceptions=True)
        except (RuntimeError, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            if "closing transport" not in str(e).lower():
                self.log.error(f"WebSocket proxy error for {ws_target}: {e}")

        return ws_server

    async def _serve_static(self, request: web.Request) -> web.StreamResponse:
        rel_path = request.path.lstrip("/")
        if not rel_path:
            rel_path = "index.html"

        file_path = FRONTEND_DIR / rel_path
        if file_path.exists() and file_path.is_file():
            return web.FileResponse(file_path)

        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            return web.FileResponse(index_path)

        return web.Response(status=404, text="404 Not Found")

    async def _start_web_server(self) -> None:
        """Binds webserver using configuration settings (public_port and host)."""
        port = self.config.get("public_port", 8000)
        host = self.config.get("host", "0.0.0.0")

        self.runner = web.AppRunner(self.web_app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()

        self.port = port
        self.target_url = f"http://127.0.0.1:{port}"
        self.log.info(f"Gateway Proxy active — Public HTTP webserver listening on http://{host}:{port}")

    async def run(self) -> None:
        while self._running:
            await asyncio.sleep(1)

    async def teardown(self) -> None:
        self.log.info("ProxyModule teardown starting...")
        if self.proxy_client_session and not self.proxy_client_session.closed:
            try:
                if self.proxy_client_session.connector:
                    await self.proxy_client_session.connector.close()
            except Exception:
                pass
            try:
                await self.proxy_client_session.close()
            except Exception:
                pass
        self.log.info("ProxyModule teardown complete.")


if __name__ == "__main__":
    run_module(ProxyModule)
