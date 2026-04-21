"""
NemoHeadUnit-Wireless v2 — Main Orchestrator

Responsibilities:
  1. Start the bus broker (bus_broker.py) as an independent subprocess
  2. Start each module as an independent subprocess
  3. Publish system.start after all processes are up
  4. On SIGINT (Ctrl+C): publish system.stop, then terminate all subprocesses

Modules are defined in MODULES list as paths to their own main.py.
Each module is fully standalone and communicates only via the ZMQ bus.
"""

import signal
import subprocess
import sys
import time
import json
import logging
import zmq
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
BROKER_SCRIPT = BASE_DIR / "bus_broker.py"
BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"

# Add module main.py paths here as they are created, e.g.:
# MODULES = [
#     BASE_DIR / "modules" / "aa_wireless" / "main.py",
#     BASE_DIR / "modules" / "ui" / "main.py",
# ]
MODULES: list[Path] = []

BROKER_STARTUP_DELAY = 0.5   # seconds to wait for broker to bind
MODULE_STARTUP_DELAY = 0.2   # seconds between module launches

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[main] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("main")

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
    for label, proc in reversed(processes):
        if proc.poll() is None:
            log.info(f"Terminating {label} (PID {proc.pid})...")
            proc.terminate()
    for label, proc in processes:
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
        _terminate_all(processes)
        if ctx is not None:
            ctx.term()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 1. Start broker
    broker_proc = _start_process(BROKER_SCRIPT, "bus_broker")
    processes.append(("bus_broker", broker_proc))
    time.sleep(BROKER_STARTUP_DELAY)

    # 2. Connect publisher
    ctx, pub = _make_publisher()
    time.sleep(0.1)  # allow PUB socket to connect

    # 3. Start modules
    for module_script in MODULES:
        label = module_script.parent.name
        proc = _start_process(module_script, label)
        processes.append((label, proc))
        time.sleep(MODULE_STARTUP_DELAY)

    # 4. Publish system.start
    _publish(pub, "system.start", {"modules": [m.parent.name for m in MODULES]})

    log.info("All processes started. Press Ctrl+C to stop.")

    # 5. Wait — keep main alive, restart crashed modules if needed
    while True:
        time.sleep(1)
        for label, proc in processes:
            if proc.poll() is not None:
                log.warning(f"{label} exited unexpectedly (code {proc.returncode})")


if __name__ == "__main__":
    run()
