"""
NemoHeadUnit-Wireless — BusClient
Drop-in replacement for MessageBus using ZeroMQ XPUB/XSUB.

Public API:
    bus.subscribe(topic, callback)
    bus.subscribe(topic, callback, thread="main")  # Qt-safe dispatch
    bus.on(topic, callback)                         # alias
    bus.publish(topic, sender, payload)
    bus.publish(topic, sender, payload, binary=b"")  # with raw bytes
    bus.wait_for_shutdown()
    bus.stop()                                       # graceful shutdown

Message format (ZMQ multipart, 3 frames):
    [0] topic   bytes          routing key
    [1] header  msgpack dict   {sender, ts, priority, ...}
    [2] payload bytes          msgpack-encoded dict OR raw binary

Shutdown design:
    stop() sends a KILL frame through an inproc PAIR socket so that
    _recv_loop wakes immediately from poller.poll() without waiting
    for the next timeout. Both threads are then joined with a timeout
    before the ZMQ context is terminated.
"""

import threading
import time
import logging
from typing import Any, Callable, Dict, List, Optional

import msgpack
import zmq

BROKER_PUB_ADDR = "ipc:///tmp/nemobus.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus.sub"
CTRL_ADDR       = "inproc://bus_client_ctrl"  # inproc PAIR for stop signal
HEARTBEAT_INTERVAL = 0.5
JOIN_TIMEOUT = 2.0  # seconds to wait for threads on stop()

log = logging.getLogger("app.bus_client")


class BusClient:
    """
    ZeroMQ-backed IPC bus client.

    A background daemon thread handles all incoming messages and dispatches
    callbacks. Callbacks marked thread='main' are routed through QtBusBridge.

    Shutdown is reliable: stop() wakes the recv thread via an inproc PAIR
    socket and joins both threads before terminating the ZMQ context.
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

        # Publisher socket
        self._pub_sock: zmq.Socket = self._context.socket(zmq.PUB)
        self._pub_sock.setsockopt(zmq.LINGER, 100)
        self._pub_sock.connect(pub_addr)

        # Subscriber socket
        self._sub_sock: zmq.Socket = self._context.socket(zmq.SUB)
        self._sub_sock.setsockopt(zmq.LINGER, 0)
        self._sub_sock.connect(sub_addr)

        # Inproc PAIR: writer side (stop signal sender)
        self._ctrl_send: zmq.Socket = self._context.socket(zmq.PAIR)
        self._ctrl_send.bind(CTRL_ADDR)

        # Inproc PAIR: reader side (recv thread listens here)
        self._ctrl_recv: zmq.Socket = self._context.socket(zmq.PAIR)
        self._ctrl_recv.connect(CTRL_ADDR)

        # topic -> list of (callback, thread_affinity)
        self._handlers: Dict[str, List[tuple]] = {}
        self._handlers_lock = threading.Lock()

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
        with self._handlers_lock:
            if topic not in self._handlers:
                self._handlers[topic] = []
                self._sub_sock.setsockopt_string(zmq.SUBSCRIBE, topic)
                log.debug(f"Subscribed to topic: {topic}")
            self._handlers[topic].append((callback, thread))

    def on(self, topic: str, callback: Callable, thread: str = "") -> None:
        self.subscribe(topic, callback, thread)

    def publish(
        self,
        topic: str,
        sender: str,
        payload: Any,
        priority: int = 0,
        binary: bytes = b"",
    ) -> None:
        if not self._running:
            return
        header = msgpack.packb({"sender": sender, "ts": time.time(), "priority": priority})
        body = binary if binary else msgpack.packb(payload, use_bin_type=True)
        try:
            self._pub_sock.send_multipart([topic.encode(), header, body])
        except zmq.ZMQError as e:
            log.warning(f"publish failed [{topic}]: {e}")

    def wait_for_shutdown(self) -> None:
        """Block until stop() is called or app.shutdown is received."""
        self._shutdown_event.wait()

    def stop(self) -> None:
        """
        Graceful shutdown:
        1. Mark as not running so publish() is a no-op
        2. Send KILL frame to wake _recv_loop from poller.poll()
        3. Set shutdown event to unblock wait_for_shutdown()
        4. Join threads (with timeout)
        5. Close sockets and terminate context
        """
        if not self._running:
            return
        log.info(f"BusClient '{self._name}' stopping...")
        self._running = False

        # Wake recv thread immediately
        try:
            self._ctrl_send.send(b"KILL", zmq.NOBLOCK)
        except zmq.ZMQError:
            pass

        self._shutdown_event.set()

        # Wait for heartbeat thread
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=JOIN_TIMEOUT)

        # Wait for recv thread
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=JOIN_TIMEOUT)

        # Close all sockets
        for sock in (self._ctrl_recv, self._ctrl_send,
                     self._sub_sock, self._pub_sock):
            try:
                sock.close(linger=0)
            except Exception:
                pass

        # Terminate context (blocks until all sockets are closed)
        try:
            self._context.term()
        except Exception:
            pass

        log.info(f"BusClient '{self._name}' stopped.")

    def set_qt_bridge_factory(self, factory: Callable) -> None:
        self._qt_bridge_factory = factory

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start(self) -> None:
        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name=f"bus_recv_{self._name}"
        )
        self._recv_thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name=f"bus_hb_{self._name}"
        )
        self._heartbeat_thread.start()

    def _recv_loop(self) -> None:
        """Background thread: receive and dispatch messages."""
        self._sub_sock.setsockopt_string(zmq.SUBSCRIBE, "_bus.")
        poller = zmq.Poller()
        poller.register(self._sub_sock, zmq.POLLIN)
        poller.register(self._ctrl_recv, zmq.POLLIN)  # wakeup pipe

        while self._running:
            try:
                events = dict(poller.poll(timeout=1000))  # ms
            except zmq.ZMQError:
                break

            # Control message — stop() sent KILL
            if self._ctrl_recv in events:
                try:
                    self._ctrl_recv.recv(zmq.NOBLOCK)
                except zmq.ZMQError:
                    pass
                break  # exit loop immediately

            if self._sub_sock not in events:
                continue

            try:
                frames = self._sub_sock.recv_multipart(zmq.NOBLOCK)
            except zmq.ZMQError:
                continue

            if len(frames) < 3:
                continue

            topic = frames[0].decode(errors="replace")
            body  = frames[2]

            if topic == "app.shutdown":
                self._shutdown_event.set()

            self._dispatch(topic, body)

        log.debug(f"_recv_loop '{self._name}' exited")

    def _dispatch(self, topic: str, body: bytes) -> None:
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
        """Send periodic ping to keep ZMQ socket warm."""
        while self._running:
            try:
                self._pub_sock.send_multipart([
                    b"_bus.ping",
                    msgpack.packb({"sender": self._name, "ts": time.time()}),
                    b"",
                ], zmq.NOBLOCK)
            except zmq.ZMQError:
                break
            # Sleep in small increments so _running is checked frequently
            for _ in range(int(HEARTBEAT_INTERVAL / 0.05)):
                if not self._running:
                    break
                time.sleep(0.05)

        log.debug(f"_heartbeat_loop '{self._name}' exited")

    def _announce(self, event: str) -> None:
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
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        with self._handlers_lock:
            return {
                "topics": list(self._handlers.keys()),
                "handlers_count": sum(len(v) for v in self._handlers.values()),
                "running": self._running,
            }
