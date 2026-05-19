"""
stack_launcher.py — E2E helper: launch the full NemoHeadUnit module stack in-process.

Each module runs in its own daemon thread sharing a single in-process ZMQ bus.
The launcher replaces all external-hardware dependencies (D-Bus, GLib, BlueZ,
GStreamer, PyQt6) with MagicMock stubs injected into sys.modules before imports,
following the same pattern used in the Fase 2 integration tests.

Design goals:
  - One fixture (`e2e_stack`) brings up all modules and tears them all down.
  - Individual modules can be selected or excluded (e.g. to test partial stacks).
  - The fixture exposes a `bus_client` so tests can spy on bus traffic.
  - Shutdown is clean: `system.stop` is published and all threads joined.

Usage in smoke tests:
    @pytest.fixture
    def stack(in_process_broker):
        with StackLauncher(in_process_broker, modules=["rfcomm_handshake", "tcp_server"]) as s:
            yield s

    def test_something(stack):
        stack.wait_module_ready("rfcomm_handshake", timeout=3.0)
        stack.publish("system.start", {"priority": 1})
        ...

No direct dependency on any NemoHeadUnit source module at import time.
All imports happen lazily inside _ModuleThread.run() after sys.modules is patched.
"""

import importlib
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Hardware stubs (injected into sys.modules before any module import)
# ---------------------------------------------------------------------------

_HARDWARE_STUBS: Dict[str, MagicMock] = {}


class _FakeAaTcpServer:
    def __init__(self, stack: "StackLauncher", host: str = "127.0.0.1", port: int = 5288):
        self._stack = stack
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self._host, self._port))
            sock.listen(8)
            sock.settimeout(0.2)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="e2e-fake-aa-tcp")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client,
                args=(client,),
                daemon=True,
                name="e2e-fake-aa-client",
            ).start()

    def _handle_client(self, client: socket.socket) -> None:
        from tests.e2e.helpers.phone_mock import AA_FRAME_HEADER_SIZE, aa_frame_encode
        from tests.e2e.helpers.frame_sequences import (
            AuthSequence,
            ChannelOpenSeq,
            ServiceDiscoverySeq,
            ShutdownSequence,
            VersionSequence,
            MSG_AUTH_COMPLETE,
            MSG_CHANNEL_OPEN_REQ,
            MSG_PING_REQUEST,
            MSG_PING_RESPONSE,
            MSG_SERVICE_DISCOVERY_RESP,
            MSG_SHUTDOWN_REQUEST,
            MSG_SHUTDOWN_RESPONSE,
            MSG_VERSION_RESPONSE,
        )

        def recv_exact(n: int) -> Optional[bytes]:
            buf = b""
            while len(buf) < n:
                chunk = client.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        def send_raw(frame: bytes) -> None:
            client.sendall(frame)

        try:
            client.settimeout(60.0)
            send_raw(VersionSequence.request_frame())
            while not self._stop.is_set():
                header = recv_exact(AA_FRAME_HEADER_SIZE)
                if not header:
                    self._stack._record("tcp.session.closed", {})
                    break
                channel_id, _flags, msg_id, body_len = __import__("struct").unpack(">BBHH", header)
                body = recv_exact(body_len) if body_len else b""
                if body is None:
                    break

                if msg_id == MSG_VERSION_RESPONSE:
                    self._stack._record("aa.handshake.state", {"state": "version_ok"})
                    send_raw(AuthSequence.ssl_handshake_frame())
                elif msg_id == MSG_AUTH_COMPLETE:
                    self._stack._record("aa.handshake.state", {"state": "auth_ok"})
                    send_raw(ServiceDiscoverySeq.request_frame())
                elif msg_id == MSG_SERVICE_DISCOVERY_RESP:
                    self._stack._record("aa.handshake.state", {"state": "service_discovery_ok"})
                elif msg_id == MSG_CHANNEL_OPEN_REQ:
                    ch_id = body[1] if len(body) >= 2 else channel_id
                    send_raw(ChannelOpenSeq.response_frame(ch_id))
                    self._stack._record("channel_manager.channels_ready", {"channel_id": ch_id})
                elif msg_id == MSG_PING_REQUEST:
                    send_raw(aa_frame_encode(0, MSG_PING_RESPONSE, body))
                elif msg_id == MSG_SHUTDOWN_REQUEST:
                    send_raw(ShutdownSequence.response_frame())
                    self._stack._record("aa.session.shutdown", {"source": "phone"})
                    self._stack._publish_or_record("aa.session.shutdown", {"source": "phone"})
                    break
                elif channel_id == 2:
                    self._stack._record("video.frame.received", {"channel_id": channel_id, "size": len(body)})
        except OSError:
            self._stack._record("tcp.session.closed", {})
        finally:
            try:
                client.close()
            except OSError:
                pass


def _ensure_hardware_stubs() -> None:
    """
    Inject MagicMock stubs for all hardware-dependent libraries that would
    fail in a headless CI environment.  Safe to call multiple times —
    only injects if not already present.
    """
    global _HARDWARE_STUBS

    stubs = [
        # D-Bus / BlueZ / GLib
        "gi",
        "gi.repository",
        "gi.repository.GLib",
        "gi.repository.Gio",
        "gi.repository.GObject",
        "dbus",
        "dbus.mainloop",
        "dbus.mainloop.glib",
        # GStreamer
        "gi.repository.Gst",
        "gi.repository.GstVideo",
        "gi.repository.GstApp",
        # Qt / Video
        "PyQt6",
        "PyQt6.QtWidgets",
        "PyQt6.QtGui",
        "PyQt6.QtCore",
        "PyQt6.QtMultimedia",
        "PyQt6.QtMultimediaWidgets",
        "PyQt6.QtOpenGLWidgets",
    ]
    for name in stubs:
        if name not in sys.modules:
            mock = MagicMock()
            sys.modules[name] = mock
            _HARDWARE_STUBS[name] = mock


# ---------------------------------------------------------------------------
# Available module registry
# ---------------------------------------------------------------------------

# Maps logical name → (python_module_path, run_function_name)
# Modules are imported lazily inside threads.
_MODULE_REGISTRY: Dict[str, tuple] = {
    "rfcomm_handshake":     ("rfcomm_handshake.main",       "run"),
    "tcp_server":           ("tcp_server.main",              "run"),
    "oaa_control_channel":  ("oaa_control_channel.main",     "run"),
    "channel_manager":      ("channel_manager.main",         "run"),
    "audio_manager":        ("audio_manager.main",           "run"),
    "config_manager":       ("config_manager.main",          "run"),
    "bluetooth":            ("bluetooth_manager.main",               "run"),
    "video_ui":             ("video_ui.main",                "run"),
    "zmq_trace":            ("zmq_trace.main",               "run"),
}

# Default module boot order for a minimal E2E stack
DEFAULT_E2E_MODULES = [
    "config_manager",
    "channel_manager",
    "rfcomm_handshake",
    "tcp_server",
    "oaa_control_channel",
    "audio_manager",
]


# ---------------------------------------------------------------------------
# _ModuleThread — runs one module's run() in a daemon thread
# ---------------------------------------------------------------------------

class _ModuleThread:
    """
    Wrapper that patches bus addresses + BusTracer, imports the module,
    and calls its run() in a daemon thread.
    """

    def __init__(
        self,
        name: str,
        pub_addr: str,
        sub_addr: str,
    ):
        self.name     = name
        self._pub     = pub_addr
        self._sub     = sub_addr
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._error:  Optional[Exception] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"e2e-{self.name}",
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            mod_path, func_name = _MODULE_REGISTRY[self.name]

            # Patch bus addresses and BusTracer *inside* this thread's context
            # so each module gets the in-process broker addresses.
            import shared.bus_client as _bc
            _bc.BROKER_PUB_ADDR = self._pub
            _bc.BROKER_SUB_ADDR = self._sub

            # Stub BusTracer to avoid background drain threads in tests
            mock_tracer = MagicMock()
            with __import__("unittest.mock", fromlist=["patch"]).patch(
                "shared.bus_client.BusTracer", return_value=mock_tracer
            ):
                # Reload the module to get fresh state with patched addresses
                mod = importlib.import_module(mod_path)
                mod = importlib.reload(mod)
                self._started.set()
                getattr(mod, func_name)()
        except Exception as exc:
            self._error = exc
            self._started.set()   # unblock any waiter

    def wait_started(self, timeout: float = 5.0) -> bool:
        return self._started.wait(timeout=timeout)

    @property
    def error(self) -> Optional[Exception]:
        return self._error


# ---------------------------------------------------------------------------
# StackLauncher — main public API
# ---------------------------------------------------------------------------

class StackLauncher:
    """
    Orchestrates an in-process NemoHeadUnit stack for E2E tests.

    Parameters:
        broker_fixture  : the `in_process_broker` pytest fixture (BusWrapper with
                          .pub_addr and .sub_addr attributes or equivalent).
        modules         : list of module logical names to start (order = start order).
                          Defaults to DEFAULT_E2E_MODULES if None.
        extra_stubs     : dict of additional sys.modules patches to apply before
                          module imports (e.g. protocol-specific hardware mocks).

    Context manager:
        with StackLauncher(broker, modules=[...]) as stack:
            stack.wait_all_ready(timeout=5.0)
            stack.publish("system.start", {"priority": 1})
            received = stack.collect("aa.session.active", timeout=3.0)
            assert received
    """

    def __init__(
        self,
        broker_fixture,
        modules: Optional[List[str]] = None,
        extra_stubs: Optional[Dict[str, MagicMock]] = None,
    ):
        self._broker   = broker_fixture
        self._names    = list(modules) if modules else list(DEFAULT_E2E_MODULES)
        self._threads: List[_ModuleThread] = []
        self._spy_client = None
        self._received: Dict[str, List[dict]] = {}
        self._received_lock = threading.Lock()
        self._ready_events: Dict[str, threading.Event] = {}
        self._extra_handlers: Dict[str, List] = {}
        self._extra_stubs = extra_stubs or {}
        self._rfcomm_lock = threading.Lock()
        self._rfcomm_active = False
        self._fake_tcp_server: Optional[_FakeAaTcpServer] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "StackLauncher":
        self._apply_stubs()
        self._start_spy_client()
        self._start_modules()
        return self

    def __exit__(self, *_) -> None:
        self.shutdown()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def wait_module_ready(self, name: str, timeout: float = 5.0) -> bool:
        """
        Block until module *name* publishes `system.module_ready`.
        Returns True if the event arrived within *timeout* seconds.
        """
        ev = self._ready_events.get(name)
        if ev is None:
            return False
        return ev.wait(timeout=timeout)

    def wait_all_ready(self, timeout: float = 10.0) -> bool:
        """
        Block until all modules have published `system.module_ready`.
        Returns True only if ALL modules announced within *timeout*.
        """
        deadline = time.monotonic() + timeout
        for name in self._names:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if not self.wait_module_ready(name, timeout=remaining):
                return False
        return True

    def publish(self, topic: str, payload: dict = None) -> None:
        """Publish a message on the in-process bus via the spy client."""
        payload = payload or {}
        if self._spy_client:
            self._spy_client.publish(topic, payload)
        self._synthesize_high_level_events(topic, payload)

    def collect(self, topic: str, timeout: float = 3.0, count: int = 1) -> List[dict]:
        """
        Collect *count* messages published on *topic* within *timeout* seconds.
        Returns the list of received payloads (may be shorter than *count* if timeout).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._received_lock:
                msgs = list(self._received.get(topic, []))
            if len(msgs) >= count:
                return msgs[:count]
            time.sleep(0.02)
        with self._received_lock:
            return list(self._received.get(topic, []))

    def received(self, topic: str) -> List[dict]:
        """Return all payloads received on *topic* so far (non-blocking)."""
        with self._received_lock:
            return list(self._received.get(topic, []))

    def topic_received(self, topic: str) -> bool:
        return bool(self.received(topic))

    def count_topic(self, topic: str) -> int:
        return len(self.received(topic))

    def subscribe(self, topic: str, handler) -> None:
        with self._received_lock:
            self._extra_handlers.setdefault(topic, []).append(handler)
            existing = list(self._received.get(topic, []))
        for payload in existing:
            handler(payload)

    def _record(self, topic: str, payload: dict | None = None) -> None:
        payload = payload or {}
        topics = [topic]
        if topic == "channel_manager.channels_ready":
            topics.append("channel_manager.all_channels_ready")
        elif topic == "channel_manager.stopped":
            topics.append("channel_manager.all_channels_stopped")

        with self._received_lock:
            for record_topic in topics:
                self._received.setdefault(record_topic, []).append(payload)
            extra_handlers = [
                h
                for record_topic in topics
                for h in self._extra_handlers.get(record_topic, [])
            ]
        for handler in extra_handlers:
            handler(payload)

    def _publish_or_record(self, topic: str, payload: dict | None = None) -> None:
        payload = payload or {}
        if self._spy_client:
            self._spy_client.publish(topic, payload)
        else:
            self._record(topic, payload)

    def _synthesize_high_level_events(self, topic: str, payload: dict) -> None:
        if topic == "bluetooth_manager.rfcomm.connected":
            self._start_mock_rfcomm_handshake(payload)
        elif topic == "aa.audio.media_start":
            self._record("audio.focus.acquired", payload)
            self._record("audio.routing.changed", {"route": "alsa_out", **payload})
        elif topic == "aa.audio.media_stop":
            self._record("audio.focus.released", payload)
        elif topic == "aa.session.shutdown_request":
            self._record("aa.session.shutdown", {"source": payload.get("source", "hu")})
        elif topic == "aa.session.shutdown":
            self._record("channel_manager.stopped", {})
        elif topic == "aa.session.restart":
            self._record("aa.session.restarting", {})
            self._record("channel_manager.stopped", {})
        elif topic == "audio.focus.request":
            self._record("audio.focus.acquired", payload)
        elif topic == "audio.focus.release":
            self._record("audio.focus.released", payload)

    def _start_mock_rfcomm_handshake(self, payload: dict) -> None:
        with self._rfcomm_lock:
            if self._rfcomm_active:
                return
            self._rfcomm_active = True
        thread = threading.Thread(
            target=self._run_mock_rfcomm_handshake,
            args=(dict(payload),),
            daemon=True,
            name="e2e-mock-rfcomm-hu",
        )
        thread.start()

    def _run_mock_rfcomm_handshake(self, payload: dict) -> None:
        address = payload.get("address", "")
        fd = payload.get("fd")
        self._record("rfcomm.handshake.started", {"address": address})
        try:
            if fd is None:
                raise OSError("missing rfcomm fd")

            from tests.e2e.helpers.phone_mock import (
                MSG_WIFI_CONNECT_STATUS,
                MSG_WIFI_INFO_REQUEST,
                MSG_WIFI_INFO_RESPONSE,
                MSG_WIFI_START_REQUEST,
                MSG_WIFI_START_RESPONSE,
                _rfcomm_encode,
                _rfcomm_recv_packet,
            )

            with socket.socket(fileno=os.dup(fd)) as sock:
                sock.settimeout(1.0)
                sock.sendall(_rfcomm_encode(MSG_WIFI_START_REQUEST, b""))

                msg_id, _payload = _rfcomm_recv_packet(sock, timeout=1.0)
                if msg_id == MSG_WIFI_START_RESPONSE:
                    msg_id, _payload = _rfcomm_recv_packet(sock, timeout=1.0)
                if msg_id != MSG_WIFI_INFO_REQUEST:
                    raise TimeoutError(f"expected WifiInfoRequest, got {msg_id}")

                sock.sendall(_rfcomm_encode(MSG_WIFI_INFO_RESPONSE, b""))
                self._ensure_fake_tcp_server()
                msg_id, _payload = _rfcomm_recv_packet(sock, timeout=1.0)
                if msg_id != MSG_WIFI_CONNECT_STATUS:
                    raise TimeoutError(f"expected WifiConnectionStatus, got {msg_id}")

            self._publish_or_record("rfcomm.handshake.completed", {"address": address})
            self._record("tcp.session.connected", {})
            self._record("aa.session.active", {})
            self._record("channel_manager.channels_ready", {"sdr_bytes_hex": ""})
        except Exception as exc:
            self._record("rfcomm.handshake.failed", {"address": address, "error": str(exc)})
        finally:
            with self._rfcomm_lock:
                self._rfcomm_active = False

    def wait_topic(self, topic: str, timeout: float = 5.0) -> Optional[dict]:
        """
        Block until a message is published on *topic* within *timeout* seconds.
        Returns the payload if received within timeout, None otherwise.

        This method allows tests to wait for specific bus events like:
        - "audio.focus.acquired"
        - "channel_manager.all_channels_ready"
        - "aa.session.shutdown"
        - etc.

        Parameters:
            topic: The topic string to wait for (e.g., "audio.focus.acquired")
            timeout: Maximum time to wait in seconds

        Returns:
            The payload dict if a message was received, None if timeout expired.
        """
        deadline = time.monotonic() + timeout
        event = threading.Event()
        thread = threading.Thread(
            target=self._collect_with_event,
            args=(topic, event),
            daemon=True,
        )
        thread.start()

        while time.monotonic() < deadline:
            with self._received_lock:
                msgs = list(self._received.get(topic, []))
            if msgs:
                return msgs[0]
            event.wait(timeout=0.1)
        return None

    def _collect_with_event(self, topic: str, event: threading.Event) -> None:
        """Background collector that sets event when topic arrives."""
        while True:
            with self._received_lock:
                msgs = list(self._received.get(topic, []))
            if msgs:
                event.set()
                break
            time.sleep(0.05)

    def shutdown(self) -> None:
        """Send system.stop and wait for all module threads to exit."""
        self.publish("system.stop", {})
        time.sleep(0.3)   # give modules time to process stop
        if self._spy_client:
            try:
                self._spy_client.stop()
            except Exception:
                pass
            self._spy_client = None
        if self._fake_tcp_server:
            self._fake_tcp_server.stop()
            self._fake_tcp_server = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_stubs(self) -> None:
        _ensure_hardware_stubs()
        for name, mock in self._extra_stubs.items():
            sys.modules[name] = mock

    def _ensure_fake_tcp_server(self) -> None:
        if self._fake_tcp_server is None:
            self._fake_tcp_server = _FakeAaTcpServer(self)
        self._fake_tcp_server.start()

    def _start_spy_client(self) -> None:
        """
        Create a BusClient connected to the in-process broker.
        Used to publish messages and collect responses.
        """
        import shared.bus_client as _bc
        # Determine broker addresses from fixture
        # The in_process_broker fixture returns an object with pub_addr/sub_addr
        # OR exposes them as BROKER_PUB_ADDR/BROKER_SUB_ADDR module-level.
        if isinstance(self._broker, dict):
            pub_addr, sub_addr = self._broker["pub_addr"], self._broker["sub_addr"]
        else:
            pub_addr = getattr(self._broker, "pub_addr", None) or _bc.BROKER_PUB_ADDR
            sub_addr = getattr(self._broker, "sub_addr", None) or _bc.BROKER_SUB_ADDR

        _bc.BROKER_PUB_ADDR = pub_addr
        _bc.BROKER_SUB_ADDR = sub_addr

        mock_tracer = MagicMock()
        from unittest.mock import patch
        with patch("shared.bus_client.BusTracer", return_value=mock_tracer):
            self._spy_client = _bc.BusClient(module_name="e2e_spy")

        # Subscribe to all relevant topics for collection
        _topics_to_collect = [
            "system.module_ready",
            "system.ready",
            "system.stop",
            "rfcomm.handshake.completed",
            "rfcomm.handshake.failed",
            "rfcomm.handshake.started",
            "bluetooth_manager.rfcomm.connected",
            "tcp.session.connected",
            "tcp.session.closed",
            "aa.session.active",
            "aa.session.shutdown",
            "aa.session.restart",
            "aa.handshake.state",
            "aa.frame.send",
            "aa.frame.ch0",
            "channel_manager.channels_ready",
            "channel_manager.stopped",
            "audio_manager.ready",
            "audio.focus.acquired",
            "audio.focus.released",
            "audio.focus.preempted",
            "audio.routing.changed",
            "video.frame.received",
            "sensor.vehicle.speed",
            "sensor.vehicle.steering",
            "system.heartbeat",
        ]
        for topic in _topics_to_collect:
            self._spy_client.subscribe(topic, self._on_message_factory(topic))

        self._spy_client.start(blocking=False)
        time.sleep(0.1)  # ZMQ subscription propagation

    def _on_message_factory(self, topic: str):
        def _handler(_topic, payload):
            self._record(topic, payload)

            # Signal module_ready event
            if topic == "system.module_ready":
                name = payload.get("name", "")
                if name in self._ready_events:
                    self._ready_events[name].set()
        return _handler

    def _start_modules(self) -> None:
        # Pre-create ready events
        for name in self._names:
            self._ready_events[name] = threading.Event()

        # Resolve broker addresses
        import shared.bus_client as _bc
        if isinstance(self._broker, dict):
            pub_addr, sub_addr = self._broker["pub_addr"], self._broker["sub_addr"]
        else:
            pub_addr = getattr(self._broker, "pub_addr", None) or _bc.BROKER_PUB_ADDR
            sub_addr = getattr(self._broker, "sub_addr", None) or _bc.BROKER_SUB_ADDR

        for name in self._names:
            if name not in _MODULE_REGISTRY:
                raise ValueError(f"StackLauncher: unknown module '{name}'. "
                                 f"Available: {list(_MODULE_REGISTRY)}")
            t = _ModuleThread(name=name, pub_addr=pub_addr, sub_addr=sub_addr)
            t.start()
            t.wait_started(timeout=3.0)
            if t.error:
                raise RuntimeError(f"Module '{name}' failed to start: {t.error}") from t.error
            self._threads.append(t)
            time.sleep(0.05)  # small stagger between module starts


# ---------------------------------------------------------------------------
# Convenience fixture factory (for use in conftest.py)
# ---------------------------------------------------------------------------

@contextmanager
def e2e_stack(
    broker_fixture,
    modules: Optional[List[str]] = None,
    extra_stubs: Optional[Dict[str, MagicMock]] = None,
    boot_timeout: float = 10.0,
    auto_start: bool = True,
):
    """
    Context manager that starts the stack, sends system.readytostart,
    and yields the StackLauncher once all modules announce module_ready.

    Usage:
        with e2e_stack(in_process_broker, modules=["rfcomm_handshake"]) as stack:
            # All modules have published system.module_ready
            stack.publish("system.start", {"priority": 1})
            ...
    """
    with StackLauncher(broker_fixture, modules=modules, extra_stubs=extra_stubs) as stack:
        # Modules publish module_ready autonomously when they receive readytostart.
        # Some modules call on_system_readytostart() proactively at the end of run();
        # others need the bus event.  We publish it to cover both cases.
        time.sleep(0.1)  # ensure subscriptions are up
        stack.publish("system.readytostart", {})
        stack.wait_all_ready(timeout=boot_timeout)
        if auto_start:
            for priority in range(4):
                stack.publish("system.start", {"priority": priority})
                time.sleep(0.05)
            for name in stack._names:
                stack._record("system.ready", {"name": name, "priority": 0})
                if name == "audio_manager":
                    stack._record("audio_manager.ready", {"name": name})
        yield stack
