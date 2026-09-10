"""
Web Browser Head Unit — InMemoryBusHub & InMemoryBusClient
In-process, zero-socket event bus for multithreading execution mode.
"""

from __future__ import annotations

import copy
import threading
from typing import Callable

from shared.logger import get_logger


class InMemoryBusHub:
    """
    Central in-memory publish/subscribe event hub.
    Used in multithreading mode to route events across modules without ZMQ sockets.
    """

    _instance: InMemoryBusHub | None = None
    _lock = threading.RLock()

    @classmethod
    def default(cls) -> InMemoryBusHub:
        import sys
        with cls._lock:
            inst = getattr(sys, "_nemo_inmemory_bus_hub", None)
            if inst is None:
                inst = cls()
                sys._nemo_inmemory_bus_hub = inst
            cls._instance = inst
            return inst

    @classmethod
    def reset_default(cls) -> None:
        import sys
        with cls._lock:
            cls._instance = None
            if hasattr(sys, "_nemo_inmemory_bus_hub"):
                delattr(sys, "_nemo_inmemory_bus_hub")

    def __init__(self):
        self._lock = threading.RLock()
        self._subscriptions: dict[str, list[Callable[[str, dict], None]]] = {}
        self.log = get_logger("bus_inmemory")

    def subscribe(self, topic: str, callback: Callable[[str, dict], None]) -> None:
        with self._lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            if callback not in self._subscriptions[topic]:
                self._subscriptions[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[str, dict], None]) -> None:
        with self._lock:
            if topic in self._subscriptions:
                if callback in self._subscriptions[topic]:
                    self._subscriptions[topic].remove(callback)
                if not self._subscriptions[topic]:
                    del self._subscriptions[topic]

    def publish(self, topic: str, payload: dict) -> None:
        callbacks_to_invoke: list[Callable[[str, dict], None]] = []

        with self._lock:
            for sub_topic, callbacks in self._subscriptions.items():
                pattern = sub_topic.rstrip("*")
                if topic == sub_topic or topic.startswith(pattern):
                    callbacks_to_invoke.extend(callbacks)

        # Dispatch outside lock
        for cb in callbacks_to_invoke:
            try:
                # Provide a shallow/defensive copy so mutating payload in one subscriber doesn't affect others
                cb(topic, copy.copy(payload) if isinstance(payload, dict) else payload)
            except Exception as exc:
                self.log.error(f"Error in in-memory bus subscriber for '{topic}': {exc}", exc_info=True)


class InMemoryBusClient:
    """
    In-memory BusClient implementation matching BusClient interface.
    """

    def __init__(self, module_name: str, hub: InMemoryBusHub | None = None):
        self.module_name = module_name
        self.log = get_logger(module_name)
        self.hub = hub or InMemoryBusHub.default()
        self._subscriptions: dict[str, Callable[[str, dict], None]] = {}
        self._running = False

    def subscribe(self, topic: str, callback: Callable[[str, dict], None]) -> None:
        self._subscriptions[topic] = callback
        self.hub.subscribe(topic, callback)
        self.log.info(f"[In-Memory] Subscribed module '{self.module_name}' to topic: '{topic}'")

    def unsubscribe(self, topic: str, callback: Callable[[str, dict], None] | None = None) -> None:
        target_cb = callback or self._subscriptions.get(topic)
        if target_cb:
            self.hub.unsubscribe(topic, target_cb)
            if topic in self._subscriptions and self._subscriptions[topic] == target_cb:
                del self._subscriptions[topic]

    def publish(self, topic: str, payload: dict) -> None:
        self.hub.publish(topic, payload)

    def start(self, blocking: bool = False) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
        for topic, cb in list(self._subscriptions.items()):
            self.hub.unsubscribe(topic, cb)
        self._subscriptions.clear()
