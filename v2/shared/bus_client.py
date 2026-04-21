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
"""

import json
import logging
import threading
import zmq

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"


class BusClient:
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.log = logging.getLogger(module_name)
        self._context = zmq.Context()
        self._subscriptions: dict[str, callable] = {}
        self._running = False

        # Publisher socket
        self._pub = self._context.socket(zmq.PUB)
        self._pub.connect(BROKER_PUB_ADDR)

        # Subscriber socket
        self._sub = self._context.socket(zmq.SUB)
        self._sub.connect(BROKER_SUB_ADDR)

    def subscribe(self, topic: str, handler: callable):
        """Register a handler for a topic. Must be called before start()."""
        self._subscriptions[topic] = handler
        self._sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.log.debug(f"Subscribed to topic: {topic}")

    def publish(self, topic: str, payload: dict):
        """Publish a message on the bus."""
        self._pub.send_multipart([
            topic.encode(),
            json.dumps(payload).encode(),
        ])
        self.log.debug(f"Published [{topic}]: {payload}")

    def start(self, blocking: bool = True):
        """Start the receive loop. Set blocking=False to run in a thread."""
        self._running = True
        if blocking:
            self._receive_loop()
        else:
            t = threading.Thread(target=self._receive_loop, daemon=True)
            t.start()

    def stop(self):
        """Stop the receive loop and close sockets."""
        self._running = False
        self._pub.close(linger=0)
        self._sub.close()
        self._context.term()
        self.log.info("BusClient stopped.")

    def _receive_loop(self):
        self.log.info(f"[{self.module_name}] Bus receive loop started.")
        while self._running:
            try:
                if self._sub.poll(timeout=500):  # ms
                    frames = self._sub.recv_multipart()
                    if len(frames) < 2:
                        continue
                    topic = frames[0].decode()
                    payload = json.loads(frames[1].decode())
                    handler = self._subscriptions.get(topic)
                    if handler:
                        handler(topic, payload)
            except KeyboardInterrupt:
                # Module received Ctrl+C directly — exit cleanly without traceback
                self.log.info("KeyboardInterrupt received — stopping.")
                break
            except zmq.ZMQError as e:
                if self._running:
                    self.log.error(f"ZMQ error: {e}")
                break
        self.log.info(f"[{self.module_name}] Bus receive loop stopped.")
