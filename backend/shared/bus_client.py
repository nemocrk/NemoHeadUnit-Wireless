"""
Web Browser Head Unit — BusClient
Cross-platform IPC communication wrapper over per-module ZeroMQ pub/sub sockets
or in-memory bus hub (multithreading mode).
"""

from __future__ import annotations

import json
import os
import threading
from typing import Callable

import zmq

from shared.bus_inmemory import InMemoryBusClient
from shared.ipc_utils import get_bus_address
from shared.logger import get_logger

BUS_HWM = 5000


class ZmqBusClient:
    """ZeroMQ implementation of BusClient for multiprocessing mode."""

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

    def unsubscribe(self, topic: str, callback: Callable[[str, dict], None] | None = None) -> None:
        if topic in self._subscriptions:
            del self._subscriptions[topic]
        try:
            self._sub.setsockopt_string(zmq.UNSUBSCRIBE, topic)
        except Exception:
            pass

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
                except (zmq.ZMQError, zmq.Again):
                    if not self._running:
                        break
                    continue
                except Exception as e:
                    self.log.error(f"Error in bus listener loop: {e}")
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
        if self._sub_thread and self._sub_thread.is_alive() and threading.current_thread() != self._sub_thread:
            self._sub_thread.join(timeout=0.5)
        try:
            self._pub.close(linger=0)
            self._sub.close(linger=0)
        except Exception:
            pass
        try:
            self._context.destroy(linger=0)
        except Exception:
            pass


class BusClient:
    """
    Facade for BusClient routing to either InMemoryBusClient or ZmqBusClient
    based on NEMO_EXECUTION_MODE environment variable.
    """

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.log = get_logger(module_name)
        mode = os.environ.get("NEMO_EXECUTION_MODE", os.environ.get("NEMO_MODE", "multiprocessing")).lower().strip()
        if mode in ("multithreading", "threading", "thread", "threads"):
            self._impl = InMemoryBusClient(module_name)
        else:
            self._impl = ZmqBusClient(module_name)

    @property
    def _subscriptions(self):
        return self._impl._subscriptions

    @_subscriptions.setter
    def _subscriptions(self, val):
        self._impl._subscriptions = val

    @property
    def _running(self):
        return self._impl._running

    @_running.setter
    def _running(self, val: bool):
        self._impl._running = val

    def subscribe(self, topic: str, callback: Callable[[str, dict], None]) -> None:
        self._impl.subscribe(topic, callback)

    def unsubscribe(self, topic: str, callback: Callable[[str, dict], None] | None = None) -> None:
        if hasattr(self._impl, "unsubscribe"):
            self._impl.unsubscribe(topic, callback)

    def publish(self, topic: str, payload: dict) -> None:
        self._impl.publish(topic, payload)

    def start(self, blocking: bool = False) -> None:
        self._impl.start(blocking=blocking)

    def stop(self) -> None:
        self._impl.stop()

    def __getattr__(self, name: str):
        return getattr(self._impl, name)

    def __setattr__(self, name: str, value):
        if name in ("module_name", "log", "_impl"):
            super().__setattr__(name, value)
        elif hasattr(self, "_impl") and hasattr(self._impl, name):
            setattr(self._impl, name, value)
        else:
            super().__setattr__(name, value)
