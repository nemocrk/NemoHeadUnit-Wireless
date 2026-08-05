#!/usr/bin/env python3
"""
Web Browser Head Unit — Bus Broker Module

Cross-platform Priority 0 Core Module extending `BaseBackendModule`.
Creates central ZMQ XPUB/XSUB proxy broker (`zmq.proxy`) and system heartbeat broadcaster.

Sockets:
  - XSUB `get_bus_address(kind='sub')`: receives messages from module publishers
  - XPUB `get_bus_address(kind='pub')`: forwards messages to module subscribers
"""

import asyncio
import json
import threading
import time
import zmq
from pathlib import Path
from typing import Any

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_float
from shared.ipc_utils import get_bus_address

HWM = 5000


class BusBrokerModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="bus_broker",
            priority=0,
            path_prefix=None,  # Pure IPC router; no HTTP path prefix
        )
        self.bus_ctx = zmq.Context()
        self.xsub_addr = get_bus_address("bus_broker", "sub")
        self.xpub_addr = get_bus_address("bus_broker", "pub")

        self.xsub = self.bus_ctx.socket(zmq.XSUB)
        self.xsub.setsockopt(zmq.RCVHWM, HWM)
        self.xsub.setsockopt(zmq.LINGER, 0)
        self.xsub.bind(self.xsub_addr)

        self.xpub = self.bus_ctx.socket(zmq.XPUB)
        self.xpub.setsockopt(zmq.SNDHWM, HWM)
        self.xpub.setsockopt(zmq.LINGER, 0)
        self.xpub.bind(self.xpub_addr)

        self.proxy_thread: threading.Thread | None = None
        self.bus_registry: dict[str, dict] = {}
        self._last_heartbeat = 0.0

    def get_default_config(self) -> dict[str, Any]:
        return {
            "heartbeat_interval": 2.0,
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "heartbeat_interval": field_float(default=2.0, min=0.1, max=60.0),
        }

    async def setup(self) -> None:
        """Starts background zmq.proxy thread and subscribes to readiness notifications."""
        self.log.info(f"XSUB listening on {self.xsub_addr}")
        self.log.info(f"XPUB listening on {self.xpub_addr}")

        def _proxy():
            try:
                zmq.proxy(self.xsub, self.xpub)
            except zmq.ZMQError:
                pass

        self.proxy_thread = threading.Thread(target=_proxy, daemon=True, name="zmq_proxy")
        self.proxy_thread.start()
        self.log.info("ZMQ Proxy thread active — routing messages.")

        self.subscribe("system.module_ready", self._on_module_event)
        self.subscribe("system.ready", self._on_module_event)
        self.subscribe("proxy.register_route", self._on_module_event)

    def _on_module_event(self, topic: str, payload: dict) -> None:
        mod_name = payload.get("name")
        prefix = payload.get("path_prefix")
        target = payload.get("target_url")

        if mod_name:
            if mod_name not in self.bus_registry:
                self.bus_registry[mod_name] = {"name": mod_name}
            self.bus_registry[mod_name].update({
                "priority": payload.get("priority", 1),
                "path_prefix": prefix or self.bus_registry[mod_name].get("path_prefix"),
                "target_url": target or self.bus_registry[mod_name].get("target_url"),
                "last_seen": time.time(),
            })
        elif prefix and target:
            found = False
            for entry in self.bus_registry.values():
                if entry.get("target_url") == target or entry.get("path_prefix") == prefix:
                    entry["path_prefix"] = prefix
                    entry["target_url"] = target
                    found = True
                    break
            if not found:
                self.bus_registry[prefix] = {
                    "name": prefix,
                    "path_prefix": prefix,
                    "target_url": target,
                    "last_seen": time.time(),
                }

    async def run(self) -> None:
        """Periodically broadcasts `system.heartbeat` containing active module directory."""
        self.log.info("Bus Broker Heartbeat Broadcaster active...")

        while self._running:
            interval = self.config.get("heartbeat_interval", 2.0)
            now = time.time()
            if now - self._last_heartbeat >= interval:
                self._last_heartbeat = now
                self.publish("system.heartbeat", {
                    "timestamp": now,
                    "modules": self.bus_registry,
                })
            await asyncio.sleep(0.5)

    async def teardown(self) -> None:
        """Close ZMQ proxy sockets cleanly."""
        self.xsub.close(linger=0)
        self.xpub.close(linger=0)
        self.bus_ctx.term()
        if self.proxy_thread and self.proxy_thread.is_alive():
            self.proxy_thread.join(timeout=1.0)
        self.log.info("Bus Broker stopped.")


if __name__ == "__main__":
    run_module(BusBrokerModule)
