"""
NemoHeadUnit-Wireless v2 — Main Orchestrator

Responsibilities:
  1. Start the bus broker (bus_broker.py) as an independent subprocess
  2. Discover and start all modules found in modules/*/main.py
  3. Publish system.start after all processes are up
  4. On SIGINT (Ctrl+C): publish system.stop, then terminate all subprocesses

Module autodiscovery:
  Any subfolder inside v2/modules/ that contains a main.py is treated as a module
  and started automatically. No manual registration needed.

  Folders whose name starts with '_' are excluded (e.g. _template).
  To disable a module temporarily, rename its main.py to main.py.disabled.
"""

import signal
import subprocess
import sys
import time
import json
import zmq
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shared.logger import get_logger  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
BROKER_SCRIPT = BASE_DIR / "bus_broker.py"
MODULES_DIR = BASE_DIR / "modules"
BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"

BROKER_STARTUP_DELAY = 0.5   # seconds to wait for broker to bind
MODULE_STARTUP_DELAY = 0.2   # seconds between module launches
GRACE_PERIOD = 1.0            # seconds to wait for self-exit before SIGTERM

log = get_logger("main")

# ---------------------------------------------------------------------------
# Module autodiscovery
# ---------------------------------------------------------------------------

def discover_modules() -> list[Path]:
    """
    Scan modules/ and return sorted list of main.py paths.
    Folders starting with '_' are excluded (reserved for templates/internals).
    """
    found = sorted(
        m for m in MODULES_DIR.glob("*/main.py")
        if not m.parent.name.startswith("_")
    )
    if not found:
        log.warning(f"No modules found in {MODULES_DIR}")
    for m in found:
        log.info(f"Discovered module: {m.parent.name}")
    return found


# ---------------------------------------------------------------------------
# Bus publisher (used only by main to send system.start / system.stop)
# ---------------------------------------------------------------------------

def _make_publisher() -> tuple[zmq.Context, zmq.Socket]:
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.connect(BROKER_PUB_ADDR)
    return ctx, pub


def _publish(pub: zmq.Socket, topic: str, payload: dict):
    pub.send_multipart([
        topic.encode(),
        json.dumps(payload).encode(),
    ])
    log.info(f"Published [{topic}]: {payload}")


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

def _start_process(script: Path, label: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    log.info(f"Started {label} (PID {proc.pid})")
    return proc


def _terminate_all(processes: list[tuple[str, subprocess.Popen]]):
    # Phase 1: grace period — collect processes that self-exited on Ctrl+C
    for label, proc in processes:
        try:
            proc.wait(timeout=GRACE_PERIOD)
            log.info(f"{label} exited on its own (code {proc.returncode})")
        except subprocess.TimeoutExpired:
            pass

    # Phase 2: SIGTERM anything still alive
    for label, proc in reversed(processes):
        if proc.poll() is None:
            log.info(f"Terminating {label} (PID {proc.pid})...")
            proc.terminate()

    # Phase 3: final wait with hard kill fallback
    for label, proc in processes:
        if proc.returncode is not None:
            continue
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning(f"{label} did not exit cleanly, killing...")
            proc.kill()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    processes: list[tuple[str, subprocess.Popen]] = []
    ctx = None
    pub = None

    def _shutdown(signum, frame):
        log.info("Ctrl+C received — shutting down...")
        if pub is not None:
            try:
                _publish(pub, "system.stop", {"reason": "user_interrupt"})
                time.sleep(0.2)  # allow subscribers to receive stop
            except Exception:
                pass
            pub.close(linger=0)
        _terminate_all(processes)
        if ctx is not None:
            ctx.term()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 1. Discover modules
    modules = discover_modules()

    # 2. Start broker
    broker_proc = _start_process(BROKER_SCRIPT, "bus_broker")
    processes.append(("bus_broker", broker_proc))
    time.sleep(BROKER_STARTUP_DELAY)

    # 3. Connect publisher
    ctx, pub = _make_publisher()
    time.sleep(0.1)  # allow PUB socket to connect

    # 4. Start modules
    for module_script in modules:
        label = module_script.parent.name
        proc = _start_process(module_script, label)
        processes.append((label, proc))
        time.sleep(MODULE_STARTUP_DELAY)

    # 5. Publish system.start
    _publish(pub, "system.start", {"modules": [m.parent.name for m in modules]})

    log.info("All processes started. Press Ctrl+C to stop.")

    # 6. Keep main alive — log unexpected exits
    while True:
        time.sleep(1)
        for label, proc in processes:
            if proc.poll() is not None:
                log.warning(f"{label} exited unexpectedly (code {proc.returncode})")


if __name__ == "__main__":
    run()
