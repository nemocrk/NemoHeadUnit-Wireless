"""
Web Browser Head Unit — BusClient
Cross-platform IPC communication wrapper over per-module ZeroMQ pub/sub sockets.
"""

from __future__ import annotations

import json
import threading
from typing import Callable

import zmq

from shared.logger import get_logger
from shared.ipc_utils import get_bus_address

BUS_HWM = 5000


class BusClient:
    def __new__(cls, module_name: str):
        # Embedded mode is selected by the composition root before modules exist.
        # Returning a facade preserves the public module API without mixing
        # transport conditionals into every provider.
        if cls is BusClient:
            try:
                from shared.runtime import get_inprocess_bus
                from shared.inprocess_bus_client import InProcessBusClient
                bus = get_inprocess_bus()
                if bus is not None:
                    return InProcessBusClient(module_name, bus)
            except ImportError:
                pass
        return super().__new__(cls)

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.log = get_logger(module_name)
        self._context = zmq.Context()
        self._subscriptions: dict[str, Callable[[str, dict], None]] = {}
        self._running = False

        # Cross-platform per-module ZMQ sockets
        self.pub_addr = get_bus_address(module_name, "sub")  # module PUB connects to broker SUB
        self.sub_addr = get_bus_address(module_name, "pub")  # module SUB connects to broker PUB

        self._pub_lock = threading.RLock()
        self._pub = self._context.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, BUS_HWM)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.connect(self.pub_addr)

        self._sub = self._context.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, BUS_HWM)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.connect(self.sub_addr)

        self._sub_thread: threading.Thread | None = None

    def subscribe(self, topic: str, callback: Callable[[str, dict], None]) -> None:
        self._subscriptions[topic] = callback
        self._sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.log.info(f"Subscribed module '{self.module_name}' to topic: '{topic}'")

    def publish(self, topic: str, payload: dict) -> None:
        with self._pub_lock:
            try:
                body = json.dumps(payload).encode("utf-8")
                self._pub.send_multipart([topic.encode("utf-8"), body])
            except zmq.Again:
                self.log.warning(f"Publish dropped for topic '{topic}' (HWM saturated)")
            except Exception as e:
                self.log.error(f"Error publishing to '{topic}': {e}")

    def start(self, blocking: bool = False) -> None:
        self._running = True

        def _listen():
            while self._running:
                try:
                    if not self._running:
                        break
                    if self._sub.poll(timeout=200):
                        frames = self._sub.recv_multipart(flags=zmq.NOBLOCK)
                        if len(frames) < 2:
                            continue
                        topic = frames[0].decode("utf-8")
                        payload = json.loads(frames[1].decode("utf-8"))

                        for sub_topic, cb in list(self._subscriptions.items()):
                            if topic == sub_topic or topic.startswith(sub_topic.rstrip("*")):
                                cb(topic, payload)
                except (zmq.ZMQError, json.JSONDecodeError, zmq.Again, Exception):
                    if not self._running:
                        break
                    continue

        if blocking:
            _listen()
        else:
            self._sub_thread = threading.Thread(target=_listen, daemon=True, name=f"bus_sub_{self.module_name}")
            self._sub_thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            self._pub.close(linger=0)
            self._sub.close(linger=0)
            self._context.term()
        except Exception:
            pass
        if self._sub_thread and self._sub_thread.is_alive() and threading.current_thread() != self._sub_thread:
            self._sub_thread.join(timeout=0.5)
