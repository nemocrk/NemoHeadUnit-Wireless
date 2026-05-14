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
            _bc.BROKER_PUB_ADDR = self._sub  # module subscribes to broker SUB
            _bc.BROKER_SUB_ADDR = self._pub  # module publishes to broker PUB

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
        self._extra_stubs = extra_stubs or {}

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
        if self._spy_client:
            self._spy_client.publish(topic, payload or {})

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_stubs(self) -> None:
        _ensure_hardware_stubs()
        for name, mock in self._extra_stubs.items():
            sys.modules[name] = mock

    def _start_spy_client(self) -> None:
        """
        Create a BusClient connected to the in-process broker.
        Used to publish messages and collect responses.
        """
        import shared.bus_client as _bc
        # Determine broker addresses from fixture
        # The in_process_broker fixture returns an object with pub_addr/sub_addr
        # OR exposes them as BROKER_PUB_ADDR/BROKER_SUB_ADDR module-level.
        pub_addr = getattr(self._broker, "pub_addr", None) or _bc.BROKER_PUB_ADDR
        sub_addr = getattr(self._broker, "sub_addr", None) or _bc.BROKER_SUB_ADDR

        _bc.BROKER_PUB_ADDR = sub_addr
        _bc.BROKER_SUB_ADDR = pub_addr

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
        ]
        for topic in _topics_to_collect:
            self._spy_client.subscribe(topic, self._on_message_factory(topic))

        self._spy_client.start(blocking=False)
        time.sleep(0.1)  # ZMQ subscription propagation

    def _on_message_factory(self, topic: str):
        def _handler(_topic, payload):
            with self._received_lock:
                self._received.setdefault(topic, []).append(payload)
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
        yield stack
