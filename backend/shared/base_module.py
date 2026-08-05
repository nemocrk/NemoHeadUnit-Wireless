"""
Web Browser Head Unit — Base Backend Module Abstraction

Provides `BaseBackendModule`, the foundation for all process-isolated backend modules,
and `run_module()` entry point launcher.

Features:
  1. Object-Oriented Lifecycle (`setup`, `run`, `teardown`, `on_config_updated`).
  2. Integrated Config Workflow (`ConfigClient`, `get_default_config()`, `get_schema()`, runtime hot-reload).
  3. Dynamic Inter-Module RPC (`call_module` querying `system.heartbeat` module registry).
  4. Integrated Async web server (`aiohttp.web.Application`) bound to dynamic OS port (`127.0.0.1:0`).
  5. Automatic Gateway Proxy route registration over ZMQ (`proxy.register_route` & `system.module_ready`).
  6. Cross-platform ZMQ messaging (`BusClient` + `ipc_utils.py`).
  7. Non-blocking Loguru logging.
"""

from abc import ABC, abstractmethod
import asyncio
import signal
import sys
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import web

from shared.logger import get_logger, add_log_listener, remove_log_listener
from shared.bus_client import BusClient
from shared.config_client import ConfigClient


class BaseBackendModule(ABC):
    def __init__(
        self,
        name: str,
        priority: int = 1,
        path_prefix: Optional[str] = None,
    ):
        self.name = name
        self.priority = priority
        self.path_prefix = f"/{path_prefix.strip('/')}" if path_prefix else None

        self.log = get_logger(self.name)
        self.bus = BusClient(self.name)

        default_config = self.get_default_config()
        schema = self.get_schema()
        self.config_client = ConfigClient(self.name, self.bus, default_config=default_config, schema=schema)
        self.config: dict[str, Any] = dict(default_config)

        self.web_app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.port: int = 0
        self.target_url: str = ""

        # Automatically register standard module WebSocket log stream & client log REST routes
        self.add_ws_route("/logs", self._handle_ws_logs)
        self.add_ws_route("/api/logs", self._handle_ws_logs)
        self.add_http_route("POST", "/api/system/client_log", self._handle_client_log)
        self.add_http_route("POST", "/api/system/close_window", self._handle_close_window)
        if self.path_prefix in ("/", None):
            self.add_ws_route("/api/proxy/logs", self._handle_ws_logs)

        self.module_registry: dict[str, dict] = {}
        self.client_session: aiohttp.ClientSession | None = None
        self._running = False

    def get_default_config(self) -> dict[str, Any]:
        """Sub-modules override this method to provide local default configuration."""
        return {}

    def get_schema(self) -> dict[str, Any]:
        """Sub-modules override this method to declare strongly-typed configuration schema descriptors."""
        return {}

    def on_config_updated(self, new_config: dict[str, Any]) -> None:
        """Hook overridden by sub-modules to react to runtime configuration changes."""
        pass

    def add_http_route(self, method: str, path: str, handler: Callable) -> None:
        """Helper to register HTTP endpoint on internal aiohttp web server."""
        raw_path = path if path.startswith("/") else f"/{path}"
        full_path = raw_path
        rel_path = raw_path

        if self.path_prefix:
            if not raw_path.startswith(self.path_prefix):
                full_path = f"{self.path_prefix.rstrip('/')}/{raw_path.lstrip('/')}"
            else:
                rel_path = raw_path[len(self.path_prefix):]
                if not rel_path.startswith("/"):
                    rel_path = f"/{rel_path}"

        self.web_app.router.add_route(method.upper(), full_path, handler)
        if rel_path != full_path and rel_path:
            try:
                self.web_app.router.add_route(method.upper(), rel_path, handler)
            except Exception:
                pass
        self.log.info(f"Registered HTTP route: [{method.upper()}] {full_path} (alt: {rel_path})")

    def add_ws_route(self, path: str, handler: Callable) -> None:
        """Helper to register WebSocket endpoint on internal aiohttp web server."""
        raw_path = path if path.startswith("/") else f"/{path}"
        full_path = raw_path
        rel_path = raw_path

        if self.path_prefix:
            if not raw_path.startswith(self.path_prefix):
                full_path = f"{self.path_prefix.rstrip('/')}/{raw_path.lstrip('/')}"
            else:
                rel_path = raw_path[len(self.path_prefix):]
                if not rel_path.startswith("/"):
                    rel_path = f"/{rel_path}"

        self.web_app.router.add_get(full_path, handler)
        if rel_path != full_path and rel_path:
            try:
                self.web_app.router.add_get(rel_path, handler)
            except Exception:
                pass
        self.log.info(f"Registered WebSocket route: {full_path} (alt: {rel_path})")


    async def _handle_ws_logs(self, request: web.Request) -> web.WebSocketResponse:
        """Standard module WebSocket log stream handler with module and level filtering."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        filter_module = request.query.get("module", "").lower()
        min_level_str = request.query.get("level", "INFO").upper()

        level_weights = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        min_weight = level_weights.get(min_level_str, 1)

        def on_log(log_dict: dict):
            # Log level filtering
            log_level = log_dict.get("level", "INFO").upper()
            if level_weights.get(log_level, 1) < min_weight:
                return

            # Module filtering
            log_mod = log_dict.get("module", "").lower()
            if filter_module and filter_module not in ("all", "*") and log_mod != filter_module:
                return

            formatted = f"{log_dict['timestamp']} | {log_dict['level']:<8} | {log_dict['module']} - {log_dict['message']}"
            loop.call_soon_threadsafe(queue.put_nowait, formatted)

        add_log_listener(on_log)
        self.log.info(f"Client connected to live log stream (filter: module={filter_module or 'all'}, level={min_level_str})")

        async def send_loop():
            try:
                while True:
                    log_line = await queue.get()
                    await ws.send_str(log_line)
            except Exception:
                pass

        send_task = asyncio.create_task(send_loop())
        try:
            async for msg in ws:
                pass
        finally:
            send_task.cancel()
            remove_log_listener(on_log)
            self.log.info("Client disconnected from live log stream")
        return ws

    async def _handle_client_log(self, request: web.Request) -> web.Response:
        """REST endpoint for frontend web client to forward warnings/errors directly into system loguru logs."""
        try:
            body = await request.json()
            level = str(body.get("level", "WARNING")).upper()
            msg = str(body.get("message", ""))
            client_module = str(body.get("module", "webclient"))
            if not client_module.startswith("webclient"):
                client_module = f"webclient:{client_module}"

            client_log = get_logger(client_module)
            if level == "DEBUG":
                client_log.debug(msg)
            elif level in ("WARN", "WARNING"):
                client_log.warning(msg)
            elif level in ("ERR", "ERROR"):
                client_log.error(msg)
            elif level in ("CRIT", "CRITICAL"):
                client_log.critical(msg)
            else:
                client_log.info(msg)

            return web.json_response({"status": "ok"})
        except Exception as exc:
            return web.json_response({"status": "error", "reason": str(exc)}, status=400)

    async def _handle_close_window(self, request: web.Request) -> web.Response:
        """REST endpoint to trigger kiosk browser window exit or application shutdown."""
        self.log.info("Close window requested by frontend web client.")
        try:
            # Publish system shutdown / exit event on ZMQ bus
            self.publish("system.shutdown", {"sender": self.name, "reason": "user_exit_button"})
        except Exception:
            pass
        return web.json_response({"status": "ok"})

    def publish(self, topic: str, payload: dict) -> None:
        """Shortcut to publish message on ZMQ bus."""
        self.bus.publish(topic, payload)

    def subscribe(self, topic: str, callback: Callable) -> None:
        """Shortcut to subscribe to ZMQ bus topic, supporting both sync callbacks and async coroutine functions thread-safely."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if asyncio.iscoroutinefunction(callback):
            import inspect
            sig = inspect.signature(callback)
            num_params = len(sig.parameters)

            def _async_wrapper(top: str, pay: dict) -> None:
                try:
                    target_loop = loop
                    if target_loop is None or not target_loop.is_running():
                        try:
                            target_loop = asyncio.get_event_loop()
                        except Exception:
                            target_loop = None

                    if target_loop and target_loop.is_running():
                        if num_params == 1:
                            asyncio.run_coroutine_threadsafe(callback(pay), target_loop)
                        else:
                            asyncio.run_coroutine_threadsafe(callback(top, pay), target_loop)
                except Exception as exc:
                    self.log.error(f"Error dispatching async callback for '{top}': {exc}")

            self.bus.subscribe(topic, _async_wrapper)
        else:
            self.bus.subscribe(topic, callback)

    async def call_module(
        self,
        target_module: str,
        method: str,
        path: str,
        data: Optional[dict] = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """
        Inter-Module RPC: Calls internal HTTP endpoint of another active backend module.
        Uses system heartbeat registry to resolve dynamic loopback target URLs.
        """
        mod_info = self.module_registry.get(target_module)
        if not mod_info or not mod_info.get("target_url"):
            raise RuntimeError(f"Target module '{target_module}' is not currently available in system registry")

        target_base = mod_info["target_url"]
        target_full = f"{target_base}/{path.lstrip('/')}"

        if not self.client_session:
            self.client_session = aiohttp.ClientSession()

        try:
            async with self.client_session.request(
                method=method.upper(),
                url=target_full,
                json=data,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                return await resp.json()
        except Exception as e:
            self.log.error(f"Error calling target module '{target_module}' at {target_full}: {e}")
            raise

    @abstractmethod
    async def setup(self) -> None:
        """Module specific initialization."""
        pass

    @abstractmethod
    async def run(self) -> None:
        """Main module execution loop."""
        pass

    @abstractmethod
    async def teardown(self) -> None:
        """Clean resource deallocation on module shutdown."""
        pass

    def _handle_config_sync(self, new_config: dict[str, Any]) -> None:
        self.config = new_config
        self.on_config_updated(new_config)

    def _handle_heartbeat(self, topic: str, payload: dict[str, Any]) -> None:
        modules = payload.get("modules", {})
        if modules:
            self.module_registry.update(modules)

    async def _start_web_server(self) -> None:
        """Starts internal aiohttp server on ephemeral loopback port and advertises route to proxy."""
        if not self.path_prefix:
            return

        self.runner = web.AppRunner(self.web_app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()

        if self.site._server and self.site._server.sockets:
            self.port = self.site._server.sockets[0].getsockname()[1]
            self.target_url = f"http://127.0.0.1:{self.port}"
            self.log.info(f"Internal web server listening on {self.target_url} (Route Prefix: '{self.path_prefix}')")

    def _announce_readiness(self, level: str) -> None:
        """Announces module readiness and proxy route details to ZMQ broker."""
        if level == "ready_to_start":
            self.log.info(f"Module '{self.name}' is ready to start")
            topic = "system.module_ready"
        elif level == "ready":
            self.log.info(f"Module '{self.name}' is ready")
            topic = "system.ready"

        payload = {
            "name": self.name,
            "priority": self.priority,
        }
        if self.path_prefix and self.target_url:
            payload["path_prefix"] = self.path_prefix
            payload["target_url"] = self.target_url

        self.bus.publish(topic, payload)
        if level == "ready" and self.path_prefix and self.target_url:
            self.bus.publish("proxy.register_route", {
                "name": self.name,
                "priority": self.priority,
                "path_prefix": self.path_prefix,
                "target_url": self.target_url,
            })

    async def start(self) -> None:
        """Orchestrates module boot sequence, config fetch, web server binding, and signal handling."""
        self.log.info(f"Starting module '{self.name}' (Priority {self.priority})...")
        self.bus.start(blocking=False)

        self.loop = asyncio.get_running_loop()
        # 1. Subscribe to system lifecycle and heartbeat topics immediately on bus start
        self.subscribe("system.heartbeat", self._handle_heartbeat)

        def _on_start(topic, payload):
            if payload.get("priority") == self.priority:
                self.log.info(f"Received system.start for priority {self.priority}")
                self._running = True

        def _on_stop(topic, payload):
            self.log.info("Received system.stop — triggering teardown...")
            self._running = False

        self.subscribe("system.start", _on_start)
        self.subscribe("system.stop", _on_stop)

        await asyncio.sleep(0.5)

        if self.name == "bus_broker":
            self._running = True

        self._announce_readiness("ready_to_start")
        self.log.info("Announced readiness to main process, waiting for system.start...")

        while not self._running:
            await asyncio.sleep(0.1)

        # 2. Initialize & subscribe ConfigClient with default fallback config and schema
        if self.name != "bus_broker":
            self.config_client.on_update(self._handle_config_sync)
            self.config_client.subscribe_updates()
            self.config_client.fetch_config()

        # 3. Execute custom module setup
        await self.setup()

        # 4. Start internal web server
        await self._start_web_server()

        self._announce_readiness("ready")

        try:
            await self.run()
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        self.log.info(f"Teardown module '{self.name}'...")
        self._running = False
        try:
            await self.teardown()
        except Exception as e:
            self.log.error(f"Error in module '{self.name}' teardown: {e}")

        if self.client_session:
            try:
                await self.client_session.close()
            except Exception:
                pass

        if self.site:
            try:
                await self.site.stop()
            except Exception:
                pass

        if self.runner:
            try:
                await asyncio.wait_for(self.runner.shutdown(), timeout=0.5)
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.runner.cleanup(), timeout=0.5)
            except Exception:
                pass

        self.bus.stop()
        self.log.info(f"Module '{self.name}' stopped.")
        try:
            from loguru import logger as _root_logger
            _root_logger.complete()
        except Exception:
            pass

    def run_main(self) -> None:
        """Entry point launcher for module main.py scripts."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        main_task = loop.create_task(self.start())

        def _signal_handler(signum, frame):
            self.log.info(f"Signal {signum} received, shutting down...")
            main_task.cancel()

        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except (ValueError, AttributeError):
            pass

        try:
            loop.run_until_complete(main_task)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()
                sys.exit(0)


def run_module(module_cls: type[BaseBackendModule]) -> None:
    """
    Convenience launcher function for module main.py scripts.
    Instantiates module_cls and executes run_main().
    """
    instance = module_cls()
    instance.run_main()
