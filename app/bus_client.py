"""
NemoHeadUnit-Wireless — BusClient
Drop-in replacement for MessageBus using ZeroMQ XPUB/XSUB.

Public API is intentionally identical to the old MessageBus:
    bus.subscribe(topic, callback)
    bus.subscribe(topic, callback, thread="main")  # Qt-safe dispatch
    bus.on(topic, callback)                         # alias
    bus.publish(topic, sender, payload)
    bus.publish(topic, sender, payload, binary=b"")  # with raw bytes
    bus.wait_for_shutdown()

Message format (ZMQ multipart, 3 frames):
    [0] topic   bytes          routing key
    [1] header  msgpack dict   {sender, ts, priority, ...}
    [2] payload bytes          msgpack-encoded dict OR raw binary
"""

import threading
import time
import logging
from typing import Any, Callable, Dict, List, Optional

import msgpack
import zmq

BROKER_PUB_ADDR = "ipc:///tmp/nemobus.pub"  # client publishes here
BROKER_SUB_ADDR = "ipc:///tmp/nemobus.sub"  # client subscribes here
HEARTBEAT_INTERVAL = 0.5                    # seconds — keeps socket warm

log = logging.getLogger("app.bus_client")


class BusClient:
    """
    ZeroMQ-backed IPC bus client.

    Thread-safe. A background daemon thread handles all incoming messages
    and dispatches callbacks. Callbacks marked thread='main' are routed
    through QtBusBridge when available.
    """

    def __init__(
        self,
        pub_addr: str = BROKER_PUB_ADDR,
        sub_addr: str = BROKER_SUB_ADDR,
        name: str = "anonymous",
    ):
        self._name = name
        self._pub_addr = pub_addr
        self._sub_addr = sub_addr

        self._context = zmq.Context()
        self._pub_sock: zmq.Socket = self._context.socket(zmq.PUB)
        self._sub_sock: zmq.Socket = self._context.socket(zmq.SUB)

        self._pub_sock.connect(pub_addr)
        self._sub_sock.connect(sub_addr)

        # topic -> list of (callback, thread_affinity)
        self._handlers: Dict[str, List[tuple]] = {}
        self._handlers_lock = threading.Lock()

        # Optional QtBusBridge factory — injected by gui/qt_bridge.py
        self._qt_bridge_factory: Optional[Callable] = None
        self._qt_bridges: Dict[str, Any] = {}

        self._shutdown_event = threading.Event()
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        self._start()
        self._announce("hello")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, callback: Callable, thread: str = "") -> None:
        """Subscribe callback to topic. Use thread='main' for Qt-safe dispatch."""
        with self._handlers_lock:
            if topic not in self._handlers:
                self._handlers[topic] = []
                self._sub_sock.setsockopt_string(zmq.SUBSCRIBE, topic)
                log.debug(f"Subscribed to topic: {topic}")
            self._handlers[topic].append((callback, thread))

    def on(self, topic: str, callback: Callable, thread: str = "") -> None:
        """Alias for subscribe."""
        self.subscribe(topic, callback, thread)

    def publish(
        self,
        topic: str,
        sender: str,
        payload: Any,
        priority: int = 0,
        binary: bytes = b"",
    ) -> None:
        """
        Publish a message to the bus.

        Args:
            topic:    routing key string
            sender:   identifier of the publishing component
            payload:  dict (will be msgpack-encoded) or ignored if binary set
            priority: optional priority hint stored in header
            binary:   raw bytes payload (e.g. AAC/H.264 frame); if provided,
                      frame[2] contains this instead of msgpack(payload)
        """
        header = msgpack.packb({
            "sender": sender,
            "ts": time.time(),
            "priority": priority,
        })
        body = binary if binary else msgpack.packb(payload, use_bin_type=True)
        self._pub_sock.send_multipart([
            topic.encode(),
            header,
            body,
        ])

    def wait_for_shutdown(self) -> None:
        """Block until app.shutdown is published (mirrors old MessageBus API)."""
        self._shutdown_event.wait()

    def stop(self) -> None:
        """Disconnect from broker and stop background threads."""
        self._announce("bye")
        self._running = False
        self._shutdown_event.set()
        self._pub_sock.close(linger=100)
        self._sub_sock.close(linger=100)
        self._context.term()

    def set_qt_bridge_factory(self, factory: Callable) -> None:
        """
        Inject Qt bridge factory.
        Called by qt_bridge.py after QApplication is ready.
        factory(callback) -> object with .dispatch(payload) method
        """
        self._qt_bridge_factory = factory

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start(self) -> None:
        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="bus_recv"
        )
        self._recv_thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="bus_heartbeat"
        )
        self._heartbeat_thread.start()

    def _recv_loop(self) -> None:
        """Background thread: receive and dispatch messages."""
        # Subscribe to heartbeat topic so the socket stays warm
        self._sub_sock.setsockopt_string(zmq.SUBSCRIBE, "_bus.")
        poller = zmq.Poller()
        poller.register(self._sub_sock, zmq.POLLIN)

        while self._running:
            try:
                events = poller.poll(timeout=500)  # ms
                if not events:
                    continue
                frames = self._sub_sock.recv_multipart()
                if len(frames) < 3:
                    continue
                topic = frames[0].decode(errors="replace")
                # header = frames[1]  — available if needed
                body = frames[2]

                if topic == "app.shutdown":
                    self._shutdown_event.set()

                self._dispatch(topic, body)
            except zmq.ZMQError:
                break
            except Exception as e:
                log.error(f"recv_loop error: {e}")

    def _dispatch(self, topic: str, body: bytes) -> None:
        """Decode payload and call registered handlers."""
        try:
            payload = msgpack.unpackb(body, raw=False)
        except Exception:
            payload = body  # raw binary (audio/video frames)

        with self._handlers_lock:
            handlers = list(self._handlers.get(topic, []))

        for callback, thread in handlers:
            try:
                if thread == "main" and self._qt_bridge_factory:
                    key = f"{topic}:{callback.__qualname__}"
                    if key not in self._qt_bridges:
                        self._qt_bridges[key] = self._qt_bridge_factory(callback)
                    self._qt_bridges[key].dispatch(payload)
                else:
                    callback(payload)
            except Exception as e:
                log.error(f"handler error [{topic}] {callback}: {e}")

    def _heartbeat_loop(self) -> None:
        """Send periodic ping to keep ZMQ socket warm and avoid idle gap latency."""
        while self._running:
            try:
                self._pub_sock.send_multipart([
                    b"_bus.ping",
                    msgpack.packb({"sender": self._name, "ts": time.time()}),
                    b"",
                ])
            except zmq.ZMQError:
                break
            time.sleep(HEARTBEAT_INTERVAL)

    def _announce(self, event: str) -> None:
        """Publish hello/bye lifecycle event."""
        try:
            topic = f"_bus.{event}.{self._name}"
            self._pub_sock.send_multipart([
                topic.encode(),
                msgpack.packb({"sender": self._name, "ts": time.time()}),
                b"",
            ])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Stats (mirrors old MessageBus.get_stats)
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        with self._handlers_lock:
            return {
                "topics": list(self._handlers.keys()),
                "handlers_count": sum(len(v) for v in self._handlers.values()),
                "running": self._running,
            }
