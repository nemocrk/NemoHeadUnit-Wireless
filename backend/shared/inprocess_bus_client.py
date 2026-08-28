"""BusClient-compatible facade backed by ``InProcessBus`` in embedded mode."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from shared.inprocess_bus import InProcessBus


class InProcessBusClient:
    def __init__(self, module_name: str, bus: InProcessBus):
        self.module_name = module_name
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscriptions: list[Callable[[], None]] = []

    def subscribe(self, topic: str, callback: Callable[[str, Any], None]) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._subscriptions.append(self._bus.subscribe(topic, callback, loop=self._loop))

    def publish(self, topic: str, payload: Any) -> None:
        self._bus.publish(topic, payload)

    def start(self, blocking: bool = False) -> None:
        self._loop = asyncio.get_running_loop()

    def stop(self) -> None:
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
