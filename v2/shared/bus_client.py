"""
NemoHeadUnit-Wireless v2 — BusClient
Reusable helper for modules to publish and subscribe on the ZMQ bus.

Usage (inside any module):
    from shared.bus_client import BusClient

    client = BusClient(module_name="my_module")
    client.subscribe("system.start", handler)
    client.subscribe("some.topic", handler)
    client.start()   # blocking loop

Publishing from anywhere:
    client.publish("my.topic", {"key": "value"})

Message format (multipart ZMQ frames):
    frame[0] = topic (bytes)
    frame[1] = json payload (bytes)

Saturation diagnostics:
    Every BUS_STATS_INTERVAL seconds the receive loop logs a one-liner with:
      - total messages received
      - total publishes attempted / dropped (HWM)
      - drop rate %
    Set BUS_STATS_INTERVAL = 0 to disable.
"""

import json
import threading
import time
import zmq
from shared.logger import get_logger

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"

# Raised from 1000 to reduce drop probability under heavy video+log traffic.
BUS_HWM = 5000

# How often (seconds) to emit the saturation summary log. 0 = disabled.
BUS_STATS_INTERVAL = 10.0


class BusClient:
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.log = get_logger(module_name)
        self._context = zmq.Context()
        self._subscriptions: dict[str, callable] = {}
        self._running = False

        # Publisher socket
        self._pub = self._context.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, BUS_HWM)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.connect(BROKER_PUB_ADDR)

        # Subscriber socket
        self._sub = self._context.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, BUS_HWM)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.connect(BROKER_SUB_ADDR)

        # Saturation counters (thread-safe via GIL for simple int increments)
        self._stat_pub_ok:    int = 0
        self._stat_pub_drop:  int = 0
        self._stat_recv:      int = 0
        self._stat_next_log:  float = time.monotonic() + BUS_STATS_INTERVAL

        # Per-topic drop counters for detailed diagnostics
        self._drop_by_topic: dict[str, int] = {}

    def subscribe(self, topic: str, handler: callable):
        """Register a handler for a topic. Must be called before start()."""
        self._subscriptions[topic] = handler
        self._sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.log.debug(f"Subscribed to topic: {topic}")

    def publish(self, topic: str, payload: dict):
        """Publish a message on the bus."""
        try:
            self._pub.send_multipart([
                topic.encode(),
                json.dumps(payload).encode(),
            ], flags=zmq.NOBLOCK)
            self._stat_pub_ok += 1
        except zmq.Again:
            self._stat_pub_drop += 1
            self._drop_by_topic[topic] = self._drop_by_topic.get(topic, 0) + 1
            self.log.warning(
                "publish DROPPED (HWM saturated): topic=%s  "
                "[total_drops=%d this_topic=%d]",
                topic, self._stat_pub_drop, self._drop_by_topic[topic],
            )
            return False
        return True

    def start(self, blocking: bool = True):
        """Start the receive loop. Set blocking=False to run in a thread."""
        self._running = True
        if blocking:
            self._receive_loop()
        else:
            t = threading.Thread(target=self._receive_loop, daemon=True)
            t.start()
            return t

    def stop(self):
        """Stop the receive loop and close sockets."""
        self._running = False
        self._pub.close(linger=0)
        self._sub.close(linger=0)
        self._context.term()
        self.log.info("BusClient stopped.")

    def _log_stats(self) -> None:
        total = self._stat_pub_ok + self._stat_pub_drop
        drop_pct = (self._stat_pub_drop / total * 100) if total else 0.0
        top_drops = sorted(
            self._drop_by_topic.items(), key=lambda x: x[1], reverse=True
        )[:3]
        top_str = ", ".join(f"{t}:{n}" for t, n in top_drops) if top_drops else "none"
        self.log.debug(
            "BUS STATS | recv=%d  pub_ok=%d  pub_drop=%d (%.1f%%)  "
            "top_drop_topics=[%s]",
            self._stat_recv, self._stat_pub_ok, self._stat_pub_drop,
            drop_pct, top_str,
        )

    def _receive_loop(self):
        self.log.info("Bus receive loop started.")
        while self._running:
            try:
                if self._sub.poll(timeout=500):  # ms
                    frames = self._sub.recv_multipart()
                    if len(frames) < 2:
                        continue
                    topic = frames[0].decode()
                    try:
                        payload = json.loads(frames[1].decode())
                    except json.JSONDecodeError:
                        self.log.warning(
                            f"Received invalid JSON payload on topic '{topic}', "
                            f"skipping. Payload: {frames[1]}"
                        )
                        continue
                    self._stat_recv += 1
                    handler = self._subscriptions.get(topic)
                    if handler:
                        handler(topic, payload)

                # Periodic saturation report
                if BUS_STATS_INTERVAL > 0:
                    now = time.monotonic()
                    if now >= self._stat_next_log:
                        self._log_stats()
                        self._stat_next_log = now + BUS_STATS_INTERVAL

            except KeyboardInterrupt:
                self.log.info("KeyboardInterrupt received — stopping.")
                break
            except zmq.ZMQError as e:
                if self._running:
                    self.log.error(f"ZMQ error: {e}")
                break
        self.log.info("Bus receive loop stopped.")
