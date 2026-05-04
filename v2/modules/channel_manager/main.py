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
  system.readytostart                 {}                         ← boot handshake
  system.start                        {priority}                 ← boot handshake
  system.stop                         {}                         ← process exit
  oaa_control_channel.open_channels   {sdr_bytes_hex, channels}  ← start session
  channel_manager.module_ready        {name}                     ← child ready
  aa.session.shutdown                 {}                         ← stop session
  aa.session.restart                  {}                         ← stop + restart

Bus events (publish):
  system.module_ready                 {name, priority}           → boot handshake
  system.ready                        {name, priority}           → boot handshake
  channel_manager.channels_ready      {sdr_bytes_hex}            → all children up
  channel_manager.shutdown            {}                         → tell children to stop
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

_HERE    = Path(__file__).parent        # v2/modules/channel_manager/
_MODULES = _HERE.parent                 # v2/modules/
_V2      = _MODULES.parent              # v2/

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
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

# Seconds to wait for all children to publish channel_manager.module_ready
CHILDREN_READY_TIMEOUT = 15.0  # per child
CRASH_POLL_INTERVAL    = 1.0   # s

# ---------------------------------------------------------------------------
# Session manager (intentional OOP — see module docstring)
# ---------------------------------------------------------------------------

class ChannelManagerSession:
    """
    Manages one AA session: spawns children, waits for readiness, handles shutdown.
    Instantiated fresh for each new oaa_control_channel.open_channels event.
    """

    def __init__(self) -> None:
        self._launcher = Launcher()
        self._expected: set[str] = set()
        self._ready:    set[str] = set()
        self._lock      = threading.Lock()
        self._all_ready = threading.Event()

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
            bus.publish("channel_manager.channels_ready", {"sdr_bytes_hex": sdr_bytes_hex})
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
        bus.publish("channel_manager.shutdown", {})
        time.sleep(0.3)  # give children a moment to handle the event
        self._launcher.stop_all()
        bus.publish("channel_manager.stopped", {})
        log.info("All channel modules stopped.")

    # ------------------------------------------------------------------
    # Crash monitoring
    # ------------------------------------------------------------------

    def check_crashes(self) -> None:
        """Log any children that have exited unexpectedly."""
        for name in self._launcher.check_crashes():
            log.warning("%s exited unexpectedly", name)


# ---------------------------------------------------------------------------
# Module-level session state
# ---------------------------------------------------------------------------

_session: ChannelManagerSession | None = None

# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    """
    Orchestrator is ready to begin the multi-step boot.
    Announce this module's name and priority so main.py can build
    the startup plan before issuing system.start messages.
    """
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    """
    Orchestrator fires system.start for each priority level in order.
    Only act when payload["priority"] matches this module's PRIORITY.
    After completing init, publish system.ready so main.py can advance
    to the next priority level.
    """
    if payload.get("priority") != PRIORITY:
        return  # not our turn yet (or already past)

    log.info(f"system.start priority={PRIORITY} received — initialising...")
    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info(f"system.ready published (priority={PRIORITY})")


def on_system_stop(topic: str, payload: dict) -> None:
    """Graceful shutdown — called for all modules simultaneously."""
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
    """Start a new AA session: resolve channels, spawn subprocesses."""
    global _session

    sdr_bytes_hex = payload.get("sdr_bytes_hex", "")
    channels      = payload.get("channels", [])

    if not sdr_bytes_hex or not channels:
        log.error("open_channels: missing sdr_bytes_hex or channels — ignored")
        return

    # Kill any stale session
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

    # Wait for readiness in a dedicated thread to avoid blocking the bus loop
    def _wait() -> None:
        global _session
        ok = session.wait_all_ready(sdr_bytes_hex)
        if not ok:
            log.error("Session startup timed out — shutting down partial session")
            session.shutdown()
            if _session is session:
                _session = None

    threading.Thread(target=_wait, daemon=True, name="cm_wait_ready").start()


def on_channel_manager_module_ready(topic: str, payload: dict) -> None:
    """Track readiness of a spawned channel module child."""
    name = payload.get("name", "")
    if _session:
        _session.on_module_ready(name)


def on_aa_session_shutdown(topic: str, payload: dict) -> None:
    """AA session ended cleanly — stop all channel modules."""
    global _session
    log.info("aa.session.shutdown received — initiating channel shutdown")
    if _session:
        _session.shutdown()
        _session = None


def on_aa_session_restart(topic: str, payload: dict) -> None:
    """AA session restarting — stop all channel modules (new session will follow)."""
    global _session
    log.info("aa.session.restart received — initiating channel shutdown")
    if _session:
        _session.shutdown()
        _session = None


# ---------------------------------------------------------------------------
# Crash monitor (runs in background thread)
# ---------------------------------------------------------------------------

def _crash_monitor() -> None:
    """Periodically check for unexpected child process exits."""
    while True:
        time.sleep(CRASH_POLL_INTERVAL)
        if _session:
            _session.check_crashes()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    # Boot protocol
    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    # Topic subscriptions
    bus.subscribe("oaa_control_channel.open_channels",  on_oaa_control_channel_open_channels)
    bus.subscribe("channel_manager.module_ready",       on_channel_manager_module_ready)
    bus.subscribe("aa.session.shutdown",                on_aa_session_shutdown)
    bus.subscribe("aa.session.restart",                 on_aa_session_restart)

    # Start crash monitor
    threading.Thread(target=_crash_monitor, daemon=True, name="cm_crash_monitor").start()

    log.info("Module started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass  # gestito dal main via system.stop


if __name__ == "__main__":
    run()
