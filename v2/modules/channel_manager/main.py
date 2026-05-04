"""
channel_manager/main.py — orchestrator for AA channel module subprocesses.

Responsibilities:
  1. Receive oaa_control_channel.open_channels {sdr_bytes_hex, channels}
  2. Resolve each channel to a module_type via registry.py
  3. Spawn one channel_module subprocess per channel via launcher.py
  4. Collect channel_manager.module_ready {name} from every child
  5. When all children are ready: publish channel_manager.channels_ready {sdr_bytes_hex}
  6. On aa.session.shutdown | aa.session.restart:
       a. publish channel_manager.shutdown to all children
       b. wait for orderly termination (launcher.stop_all)
       c. publish channel_manager.stopped
  7. While running: log unexpected child crashes to console (mirror of main.py)

Bus events (subscribe):
  oaa_control_channel.open_channels  {sdr_bytes_hex, channels}  ← start session
  channel_manager.module_ready        {name}                     ← child ready
  aa.session.shutdown                 {}                         ← stop session
  aa.session.restart                  {}                         ← stop + restart
  system.stop                         {}                         ← process exit

Bus events (publish):
  channel_manager.channels_ready      {sdr_bytes_hex}            → all children up
  channel_manager.shutdown            {}                         → tell children to stop
  channel_manager.stopped             {}                         → all children stopped
  system.module_ready                 {name, priority}           → boot handshake
  system.ready                        {name, priority}           → boot handshake

PRIORITY is set to 2 so channel_manager starts after config_manager (1)
but before any UI modules (3+).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

import zmq

_V2_ROOT = Path(__file__).parent.parent.parent
if str(_V2_ROOT) not in sys.path:
    sys.path.insert(0, str(_V2_ROOT))

from shared.logger import get_logger, attach_bus  # noqa: E402
from shared.bus_client import BusClient           # noqa: E402
from modules.channel_manager.registry import resolve_module_type, module_name  # noqa: E402
from modules.channel_manager.launcher import Launcher  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODULE_NAME  = "channel_manager"
PRIORITY     = 2

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"

# Seconds to wait for all children to publish channel_manager.module_ready
CHILDREN_READY_TIMEOUT = 15.0  # per child
CRASH_POLL_INTERVAL    = 1.0   # s

log = get_logger(MODULE_NAME)

# ---------------------------------------------------------------------------
# ZMQ helpers
# ---------------------------------------------------------------------------

def _make_pub() -> tuple[zmq.Context, zmq.Socket]:
    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUB)
    sock.connect(BROKER_PUB_ADDR)
    return ctx, sock


def _publish(pub: zmq.Socket, topic: str, payload: dict) -> None:
    pub.send_multipart([topic.encode(), json.dumps(payload).encode()])
    log.debug("Published [%s]: %s", topic, payload)


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class ChannelManagerSession:
    """
    Manages one AA session: spawns children, waits for readiness, handles shutdown.
    Instantiated fresh for each new oaa_control_channel.open_channels event.
    """

    def __init__(self, pub: zmq.Socket) -> None:
        self._pub      = pub
        self._launcher = Launcher()
        self._expected: set[str] = set()
        self._ready:    set[str] = set()
        self._lock = threading.Lock()
        self._all_ready = threading.Event()

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self, sdr_bytes_hex: str, channels: list[dict]) -> None:
        """
        Resolve channels → module_types, spawn subprocesses.

        Args:
            sdr_bytes_hex: hex-encoded ServiceDiscoveryResponse bytes.
            channels:      list of channel descriptor dicts from the SDR.
        """
        launch_list: list[dict] = []

        for ch in channels:
            ch_id = ch.get("channel_id")
            if ch_id == 0:
                # Control channel — handled by oaa_control_channel itself
                continue
            try:
                mtype = resolve_module_type(ch_id, ch)
            except KeyError as exc:
                log.error("Cannot resolve channel: %s — aborting session startup", exc)
                raise

            mname = module_name(mtype, ch_id)
            launch_list.append({
                "module_name":   mname,
                "module_type":   mtype,
                "channel_id":    ch_id,
                "sdr_bytes_hex": sdr_bytes_hex,
            })

        started = self._launcher.start_all(launch_list)
        with self._lock:
            self._expected = set(started)
            self._ready.clear()
            self._all_ready.clear()

        log.info(
            "Waiting for %d channel module(s) to become ready: %s",
            len(started), sorted(started),
        )

    # ------------------------------------------------------------------
    # Readiness tracking
    # ------------------------------------------------------------------

    def on_module_ready(self, name: str) -> None:
        """Called when channel_manager.module_ready {name} arrives on the bus."""
        with self._lock:
            if name not in self._expected:
                log.debug("on_module_ready: unexpected name=%r — ignored", name)
                return
            self._ready.add(name)
            pending = self._expected - self._ready
            log.info("module_ready: %s (%d/%d)", name, len(self._ready), len(self._expected))
            if not pending:
                self._all_ready.set()

    def wait_all_ready(self, sdr_bytes_hex: str) -> bool:
        """
        Block until all children are ready or timeout.
        On success publishes channel_manager.channels_ready.
        Returns True on success, False on timeout.
        """
        n = len(self._expected)
        timeout = CHILDREN_READY_TIMEOUT * max(n, 1)
        if self._all_ready.wait(timeout=timeout):
            log.info("All %d channel module(s) ready — publishing channels_ready", n)
            _publish(self._pub, "channel_manager.channels_ready",
                     {"sdr_bytes_hex": sdr_bytes_hex})
            return True

        with self._lock:
            missing = sorted(self._expected - self._ready)
        log.error(
            "Timeout waiting for channel modules: missing=%s — session startup failed",
            missing,
        )
        return False

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Orderly shutdown: signal children, wait, then publish stopped."""
        log.info("Shutting down channel modules...")
        _publish(self._pub, "channel_manager.shutdown", {})
        time.sleep(0.3)  # give children a moment to handle the event
        self._launcher.stop_all()
        _publish(self._pub, "channel_manager.stopped", {})
        log.info("All channel modules stopped.")

    # ------------------------------------------------------------------
    # Crash monitoring
    # ------------------------------------------------------------------

    def check_crashes(self) -> None:
        """Log any children that have exited unexpectedly."""
        for name in self._launcher.check_crashes():
            log.warning("%s exited unexpectedly", name)


# ---------------------------------------------------------------------------
# Main module class
# ---------------------------------------------------------------------------

class ChannelManager:
    def __init__(self) -> None:
        self._ctx, self._pub = _make_pub()
        self._session: ChannelManagerSession | None = None
        self._stop_event = threading.Event()
        self._pending_restart: str | None = None  # sdr_bytes_hex for restart

    # ------------------------------------------------------------------
    # Boot handshake (mirrors v2/main.py pattern)
    # ------------------------------------------------------------------

    def _announce_ready(self) -> None:
        """Reply to system.readytostart with our priority, then wait for system.start."""
        sub_ctx = zmq.Context()
        sub = sub_ctx.socket(zmq.SUB)
        sub.connect(BROKER_SUB_ADDR)
        sub.setsockopt_string(zmq.SUBSCRIBE, "system.readytostart")
        sub.setsockopt_string(zmq.SUBSCRIBE, "system.start")

        # Wait for readytostart
        while not self._stop_event.is_set():
            if sub.poll(timeout=500):
                frames = sub.recv_multipart()
                topic = frames[0].decode()
                if topic == "system.readytostart":
                    _publish(self._pub, "system.module_ready",
                             {"name": MODULE_NAME, "priority": PRIORITY})
                    log.info("system.module_ready sent (priority=%d)", PRIORITY)
                    break

        # Wait for system.start at our priority level
        deadline = time.monotonic() + 10.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            if sub.poll(timeout=500):
                frames = sub.recv_multipart()
                topic = frames[0].decode()
                if topic == "system.start":
                    try:
                        payload = json.loads(frames[1].decode())
                    except Exception:
                        payload = {}
                    if payload.get("priority") == PRIORITY:
                        _publish(self._pub, "system.ready",
                                 {"name": MODULE_NAME, "priority": PRIORITY})
                        log.info("system.ready sent (priority=%d)", PRIORITY)
                        break

        sub.close(linger=0)
        sub_ctx.term()

    # ------------------------------------------------------------------
    # Bus listener thread
    # ------------------------------------------------------------------

    def _run_listener(self) -> None:
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.connect(BROKER_SUB_ADDR)
        for topic in (
            "oaa_control_channel.open_channels",
            "channel_manager.module_ready",
            "aa.session.shutdown",
            "aa.session.restart",
            "system.stop",
        ):
            sub.setsockopt_string(zmq.SUBSCRIBE, topic)

        log.info("Listener ready")

        while not self._stop_event.is_set():
            if not sub.poll(timeout=500):
                if self._session:
                    self._session.check_crashes()
                continue
            try:
                frames = sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                continue
            if len(frames) < 2:
                continue

            topic = frames[0].decode()
            try:
                payload = json.loads(frames[1].decode())
            except Exception:
                payload = {}

            if topic == "oaa_control_channel.open_channels":
                self._on_open_channels(payload)

            elif topic == "channel_manager.module_ready":
                name = payload.get("name", "")
                if self._session:
                    self._session.on_module_ready(name)

            elif topic in ("aa.session.shutdown", "aa.session.restart"):
                log.info("%s received — initiating channel shutdown", topic)
                if self._session:
                    self._session.shutdown()
                    self._session = None

            elif topic == "system.stop":
                log.info("system.stop received — exiting")
                if self._session:
                    self._session.shutdown()
                    self._session = None
                self._stop_event.set()

        sub.close(linger=0)
        ctx.term()

    # ------------------------------------------------------------------
    # open_channels handler (runs inside listener thread)
    # ------------------------------------------------------------------

    def _on_open_channels(self, payload: dict) -> None:
        sdr_bytes_hex = payload.get("sdr_bytes_hex", "")
        channels      = payload.get("channels", [])

        if not sdr_bytes_hex or not channels:
            log.error("open_channels: missing sdr_bytes_hex or channels — ignored")
            return

        # Kill any stale session
        if self._session:
            log.warning("New open_channels while session active — shutting down old session")
            self._session.shutdown()

        session = ChannelManagerSession(self._pub)
        self._session = session

        try:
            session.start(sdr_bytes_hex, channels)
        except FileNotFoundError as exc:
            log.error("Session startup failed: %s", exc)
            self._session = None
            return
        except KeyError as exc:
            log.error("Session startup failed (registry): %s", exc)
            self._session = None
            return

        # Wait for readiness in a dedicated thread to avoid blocking the listener
        def _wait() -> None:
            ok = session.wait_all_ready(sdr_bytes_hex)
            if not ok:
                log.error("Session startup timed out — shutting down partial session")
                session.shutdown()
                if self._session is session:
                    self._session = None

        threading.Thread(target=_wait, daemon=True, name="cm_wait_ready").start()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        time.sleep(0.1)  # allow PUB socket to connect

        # Attach bus logging
        _bus = BusClient(module_name=MODULE_NAME)
        _bus.start(blocking=False)
        attach_bus(_bus)

        self._announce_ready()

        # Run listener in main thread
        self._run_listener()

        self._pub.close(linger=0)
        self._ctx.term()
        log.info("%s exited", MODULE_NAME)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ChannelManager().run()
