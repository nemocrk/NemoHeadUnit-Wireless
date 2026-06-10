"""
channel_manager/main.py — orchestrator for AA channel module subprocesses.

Responsibilities:
  1. Receive oaa_control_channel.open_channels {sdr_bytes_hex, channels}
  2. Resolve each channel to a module_type via registry.py
  3. Spawn one channel_module subprocess per channel via launcher.py
  4. Collect channel_manager.module_ready {name} from every child
  5. When all children are ready: publish channel_manager.channels_ready {sdr_bytes_hex}
  6. On aa.session.shutdown | aa.session.restart:
       a. publish aa.channel.close for each active channel
       b. publish channel_manager.module_stop to all children
       c. wait for channel_manager.module_stopped ACK from each child (or timeout)
       d. call launcher.stop_all() and publish channel_manager.stopped
  7. While running: log unexpected child crashes to console (mirror of main.py)

Bus events (subscribe):
  system.readytostart                 {}                         ← boot handshake
  system.start                        {priority}                 ← boot handshake
  system.stop                         {}                         ← process exit
  oaa_control_channel.open_channels   {sdr_bytes_hex, channels}  ← start session
  channel_manager.module_ready_to_start {name, priority}         ← child boot ACK
  channel_manager.module_ready        {name, priority}           ← child ready
  channel_manager.module_stopped      {name}                     ← child stop ACK
  aa.session.shutdown                 {}                         ← stop session
  aa.session.restart                  {}                         ← stop + restart

Bus events (publish):
  system.module_ready                 {name, priority}           → boot handshake
  system.ready                        {name, priority}           → boot handshake
  channel_manager.module_start        {priority}                 → boot children by priority
  channel_manager.module_stop         {}                         → tell children to stop
  channel_manager.channels_ready      {sdr_bytes_hex}            → all children up
  channel_manager.stopped             {}                         → all children stopped

PRIORITY is set to 2 so channel_manager starts after config_manager (1)
but before any UI modules (3+).

NOTE — Intentional deviation from _template:
  Session logic is encapsulated in ChannelManagerSession (OOP) rather than
  plain module-level functions. This is justified by the per-session lifecycle
  (spawn → wait → shutdown) that requires shared mutable state across multiple
  event handlers. All bus subscriptions still follow the on_<topic> naming
  convention defined in the template.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
import sys

_HERE      = Path(__file__).parent   # modules/channel_manager/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient                                                     # noqa: E402
from shared.logger import get_logger                                                        # noqa: E402
from modules.channel_manager.registry import resolve_module_type, module_name, SkipChannel  # noqa: E402
from modules.channel_manager.launcher import Launcher                                       # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "channel_manager"
PRIORITY    = 2

# No ConfigClient — channel_manager has no user-configurable keys.

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# Fixed timeout for all children to publish channel_manager.module_ready.
# Not scaled by child count — all children boot in parallel.
CHILDREN_READY_TIMEOUT = 10.0  # seconds
CRASH_POLL_INTERVAL    = 1.0   # seconds

# ---------------------------------------------------------------------------
# Session manager (intentional OOP — see module docstring)
# ---------------------------------------------------------------------------

class ChannelManagerSession:
    """
    Manages one AA session: spawns children, waits for readiness, handles shutdown.
    Instantiated fresh for each new oaa_control_channel.open_channels event.
    """

    def __init__(self) -> None:
        self._is_active = False  # True between start() and shutdown()
        self._launcher = Launcher()
        self._expected: set[str] = set()
        self._ready_to_start:    set[str] = set()
        self._ready:    set[str] = set()
        self._stopped:  set[str] = set()  # tracks channel_manager.module_stopped ACKs
        self._lock      = threading.Lock()
        self._all_ready = threading.Event()
        self._all_ready_to_start = threading.Event()
        self._all_stopped = threading.Event()
        self._all_started_channels: list[dict] = []
        self._all_ready_channels: list[dict] = []
        self._all_active_channels: list[dict] = []
        self._priorities: list[int] = []

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self, sdr_bytes_hex: str, channels: list[dict]) -> None:
        """
        Resolve channels → module_types, spawn subprocesses.

        Channels whose descriptor key is recognised but has no module yet
        (SkipChannel) are skipped with a warning.  Channels with a fully
        unknown descriptor raise KeyError and abort session startup.

        Args:
            sdr_bytes_hex: hex-encoded ServiceDiscoveryResponse bytes.
            channels:      list of channel descriptor dicts from the SDR.
        """
        launch_list: list[dict] = []

        import json
        log.info("Starting channel modules for channels: %s", json.dumps(channels, indent=2))

        self._is_active = True

        for ch in channels:
            ch_id = ch.get("channel_id")
            if ch_id == 0:
                # Control channel — handled by oaa_control_channel itself
                continue
            try:
                mtype = resolve_module_type(ch_id, ch)
            except SkipChannel as exc:
                log.warning("Skipping channel: %s", exc)
                continue
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
            self._all_started_channels.append({
                "module_name":   mname,
                "module_type":   mtype,
                "channel_id":    ch_id,
            })

        started = self._launcher.start_all(launch_list)
        with self._lock:
            self._expected = set(started)
            self._ready_to_start.clear()
            self._ready.clear()
            self._stopped.clear()
            self._all_ready.clear()
            self._all_ready_to_start.clear()
            self._all_stopped.clear()
            # If no children were started, the session is immediately ready.
            if not self._expected:
                self._all_ready.set()
                self._all_ready_to_start.set()

        log.info(
            "Waiting for %d channel module(s) to become ready: %s",
            len(started), sorted(started),
        )

    # ------------------------------------------------------------------
    # Readiness tracking
    # ------------------------------------------------------------------

    def on_module_ready_to_start(self, name: str, priority: int) -> None:
        """Called when channel_manager.module_ready_to_start {name, priority} arrives."""
        with self._lock:
            if name not in self._expected:
                log.debug("on_module_ready_to_start: unexpected name=%r — ignored", name)
                return
            self._ready_to_start.add(name)
            self._all_ready_channels.append(
                {**next(ch for ch in self._all_started_channels if ch["module_name"] == name), "priority": priority}
            )
            pending = self._expected - self._ready_to_start
            log.info("module_ready_to_start: %s (%d/%d)", name, len(self._ready_to_start), len(self._expected))
            if not pending:
                self._all_ready_to_start.set()

    def wait_all_ready_to_start(self, sdr_bytes_hex: str) -> bool:
        """
        Block until all children are ready to start or timeout.
        An empty session (zero expected children) is considered immediately ready.
        On success publishes channel_manager.channels_ready.
        Returns True on success, False on timeout.
        """
        n = len(self._expected)
        if self._all_ready_to_start.wait(timeout=CHILDREN_READY_TIMEOUT):
            log.info("All %d channel module(s) ready to start — publishing module_start", n)

            self._priorities = list({channel["priority"] for channel in self._all_ready_channels})

            for ready_channel in self._all_ready_channels:
                log.info(
                    "Ready to Start channel: name=%s priority=%s",
                    ready_channel["module_name"],
                    ready_channel["priority"],
                )
            return True

        with self._lock:
            missing = sorted(self._expected - self._ready_to_start)
        log.error(
            "Timeout waiting for channel modules: missing=%s — session startup failed",
            missing,
        )
        return False

    def on_module_ready(self, name: str) -> None:
        """Called when channel_manager.module_ready {name} arrives on the bus."""
        with self._lock:
            if name not in self._expected:
                log.debug("on_module_ready: unexpected name=%r — ignored", name)
                return
            self._ready.add(name)
            self._all_active_channels.append(
                next(ch for ch in self._all_started_channels if ch["module_name"] == name)
            )
            # 1. Trova la priorità del modulo target
            target_priority = next(
                (ch["priority"] for ch in self._all_ready_channels if ch["module_name"] == name),
                None
            )

            # 2. Crea un SET di identificativi (es. i nomi dei moduli) con la stessa priorità
            if target_priority is not None:
                same_priority_channels = {
                    ch["module_name"] for ch in self._all_ready_channels
                    if ch["priority"] == target_priority
                }
            else:
                same_priority_channels = set()

            # 3. Ora la sottrazione tra SET funziona correttamente
            pending = same_priority_channels - self._ready

            log.info("module_ready: %s (%d/%d)", name, len(self._ready), len(self._expected))
            if not pending:
                self._all_ready.set()

    def on_module_stopped(self, name: str) -> None:
        """Called when channel_manager.module_stopped {name} arrives on the bus."""
        with self._lock:
            if name not in self._expected:
                log.debug("on_module_stopped: unexpected name=%r — ignored", name)
                return
            self._stopped.add(name)
            pending = self._expected - self._stopped
            log.info("module_stopped ACK: %s (%d/%d)", name, len(self._stopped), len(self._expected))
            if not pending:
                self._all_stopped.set()

    def wait_all_ready(self, sdr_bytes_hex: str, priority: int) -> bool:
        """
        Block until all children are ready or timeout.
        An empty session (zero expected children) is considered immediately ready.
        On success publishes channel_manager.channels_ready.
        Returns True on success, False on timeout.
        """
        this_priority_channels = [ch for ch in self._all_ready_channels if ch["priority"] == priority]
        n = len(this_priority_channels)
        if self._all_ready.wait(timeout=CHILDREN_READY_TIMEOUT):
            log.info("All %d channel module(s) ready — publishing channels_ready", n)
            for active_channel in self._all_active_channels:
                log.info(
                    "Active channel: id=%d module=%s type=%s",
                    active_channel["channel_id"],
                    active_channel["module_name"],
                    active_channel["module_type"],
                )
                bus.publish("aa.channel.open", active_channel)
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
        """Orderly shutdown: close AA channels, signal children, wait for ACKs, stop."""
        self._is_active = False
        log.info("Shutting down channel modules...")

        for active_channel in self._all_active_channels:
            log.info(
                "Closing channel: id=%d module=%s type=%s",
                active_channel["channel_id"],
                active_channel["module_name"],
                active_channel["module_type"],
            )
            bus.publish("aa.channel.close", active_channel)
        self._all_active_channels.clear()
        self._all_started_channels.clear()

        bus.publish("channel_manager.module_stop", {})
        time.sleep(0.5)  # give children a moment to handle module_stop before stop_all
        self._launcher.stop_all()
        bus.publish("channel_manager.stopped", {})
        log.info("All channel modules stopped.")

    # ------------------------------------------------------------------
    # Crash monitoring
    # ------------------------------------------------------------------

    def check_crashes(self) -> bool:
        """Log any children that have exited unexpectedly."""
        crashes = False
        if self._is_active:
            for name in self._launcher.check_crashes():
                log.warning("%s exited unexpectedly", name)
                crashes = True
        return crashes


# ---------------------------------------------------------------------------
# Module-level session state
# ---------------------------------------------------------------------------

_session: ChannelManagerSession | None = None

# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} received — initialising...")

    # Topic subscriptions
    bus.subscribe("oaa_control_channel.open_channels",         on_oaa_control_channel_open_channels)
    bus.subscribe("channel_manager.module_ready_to_start",     on_channel_manager_module_ready_to_start)
    bus.subscribe("channel_manager.module_ready",              on_channel_manager_module_ready)
    bus.subscribe("channel_manager.module_stopped",            on_channel_manager_module_stopped)
    bus.subscribe("aa.session.shutdown",                       on_aa_session_shutdown)
    bus.subscribe("aa.session.restart",                        on_aa_session_restart)

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info(f"system.ready published (priority={PRIORITY})")


def on_system_stop(topic: str, payload: dict) -> None:
    global _session
    log.info("system.stop — cleaning up...")
    if _session:
        _session.shutdown()
        _session = None
    bus.stop()


# ---------------------------------------------------------------------------
# Topic handlers  (naming: on_<snake_case_topic>)
# ---------------------------------------------------------------------------

def on_oaa_control_channel_open_channels(topic: str, payload: dict) -> None:
    global _session

    sdr_bytes_hex = payload.get("sdr_bytes_hex", "")
    channels      = payload.get("channels", [])

    if not sdr_bytes_hex or not channels:
        log.error("open_channels: missing sdr_bytes_hex or channels — ignored")
        return

    if _session:
        log.warning("New open_channels while session active — shutting down old session")
        _session.shutdown()

    session = ChannelManagerSession()
    _session = session

    try:
        session.start(sdr_bytes_hex, channels)
    except FileNotFoundError as exc:
        log.error("Session startup failed: %s", exc)
        _session = None
        return
    except KeyError as exc:
        log.error("Session startup failed (registry): %s", exc)
        _session = None
        return

    def _wait() -> None:
        global _session
        ok = session.wait_all_ready_to_start(sdr_bytes_hex)
        for priority in session._priorities:
            log.info(f"Publishing module_start for priority {priority}")
            bus.publish("channel_manager.module_start", {"priority": priority})
            ok = session.wait_all_ready(sdr_bytes_hex, priority=priority)
            session._all_ready.clear()  # reset for next priority level
        log.info(f"Startup wait thread finished with ok={ok} — session is {'ready' if ok else 'not ready'}")
        if not ok:
            log.error("Session startup timed out — shutting down partial session")
            session.shutdown()
            if _session is session:
                _session = None
        else:
            log.info("All channel modules ready — session startup complete publishing channel_manager.channels_ready")
            bus.publish("channel_manager.channels_ready", {"sdr_bytes_hex": sdr_bytes_hex})

    threading.Thread(target=_wait, daemon=True, name="cm_wait_ready").start()


def on_channel_manager_module_ready_to_start(topic: str, payload: dict) -> None:
    """Child announced it is ready to start — respond with module_start {priority}."""
    name     = payload.get("module_name", "")
    priority = payload.get("priority", 1)
    if _session:
        log.info(f"module_ready_to_start received from {name} (priority={priority})")
        _session.on_module_ready_to_start(name, priority)


def on_channel_manager_module_ready(topic: str, payload: dict) -> None:
    """Track readiness of a spawned channel module child."""
    name = payload.get("module_name", "")
    if _session:
        _session.on_module_ready(name)


def on_channel_manager_module_stopped(topic: str, payload: dict) -> None:
    """Track stop ACK from a spawned channel module child."""
    name = payload.get("module_name", "")
    if _session:
        _session.on_module_stopped(name)


def on_aa_session_shutdown(topic: str, payload: dict) -> None:
    global _session
    log.info("aa.session.shutdown received — initiating channel shutdown")
    if _session:
        _session.shutdown()
        _session = None


def on_aa_session_restart(topic: str, payload: dict) -> None:
    global _session
    log.info("aa.session.restart received — initiating channel shutdown")
    if _session:
        _session.shutdown()
        _session = None


# ---------------------------------------------------------------------------
# Crash monitor
# ---------------------------------------------------------------------------

def _crash_monitor() -> None:
    while True:
        time.sleep(CRASH_POLL_INTERVAL)
        if _session:
            if _session.check_crashes():
                log.warning("One or more channel modules have crashed during an active session — closing app.")
                bus.publish("system.shutdown", {})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    # Boot protocol
    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    threading.Thread(target=_crash_monitor, daemon=True, name="cm_crash_monitor").start()

    log.info("Module started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
