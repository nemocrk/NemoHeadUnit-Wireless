"""
NemoHeadUnit-Wireless v2 — Main Orchestrator

Responsibilities:
  1. Start the bus broker as an independent subprocess
  2. Discover and start all modules found in modules/*/main.py
  3. Multi-step priority boot:
       a. Publish system.readytostart
       b. Collect system.module_ready {name, priority} from all modules
       c. For each priority level P (ascending):
            - Publish system.start {priority: P}
            - Wait for system.ready {name, priority: P} from all P-level modules
            - On timeout per module: log warning and proceed
  4. On SIGINT/SIGTERM: publish system.stop, terminate all subprocesses
  5. Respond to system.get_modules with module list and status

Module autodiscovery:
  Any subfolder inside v2/modules/ that contains a main.py is treated as a module.
  Folders whose name starts with '_' are excluded (e.g. _template).
  To disable a module, rename its main.py to main.py.disabled.

Boot timing constants:
  READYTOSTART_WINDOW   : seconds to collect system.module_ready replies
  MODULE_READY_TIMEOUT  : seconds to wait for each module's system.ready per level
"""

import signal
import subprocess
import sys
import threading
import time
import json
import zmq
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shared.logger import get_logger  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR        = Path(__file__).parent
BROKER_SCRIPT   = BASE_DIR / "bus_broker.py"
MODULES_DIR     = BASE_DIR / "modules"
BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"

BROKER_STARTUP_DELAY  = 0.5   # s — wait for broker to bind
MODULE_STARTUP_DELAY  = 0.2   # s — between module launches
GRACE_PERIOD          = 1.0   # s — wait for self-exit before SIGTERM
READYTOSTART_WINDOW   = 3.0   # s — collect system.module_ready replies
MODULE_READY_TIMEOUT  = 5.0   # s — per-module timeout for system.ready

log = get_logger("main")

# ---------------------------------------------------------------------------
# Module autodiscovery
# ---------------------------------------------------------------------------

def discover_modules() -> list[Path]:
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
# ZMQ helpers
# ---------------------------------------------------------------------------

def _make_publisher() -> tuple[zmq.Context, zmq.Socket]:
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.connect(BROKER_PUB_ADDR)
    return ctx, pub


def _publish(pub: zmq.Socket, topic: str, payload: dict) -> None:
    pub.send_multipart([
        topic.encode(),
        json.dumps(payload).encode(),
    ])
    log.info(f"Published [{topic}]: {payload}")


# ---------------------------------------------------------------------------
# Multi-step boot helpers
# ---------------------------------------------------------------------------

def _collect_module_ready(
    pub: zmq.Socket,
    module_names: list[str],
    window: float,
) -> dict[int, list[str]]:
    """
    Publish system.readytostart, then collect system.module_ready replies
    for `window` seconds.

    Returns a dict mapping priority → [module_names].
    Modules that do not reply within the window are assigned priority 1
    (service level) as a safe fallback so the boot can still proceed.
    """
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect(BROKER_SUB_ADDR)
    sub.setsockopt_string(zmq.SUBSCRIBE, "system.module_ready")
    time.sleep(0.1)  # allow SUB to connect before publishing

    _publish(pub, "system.readytostart", {})

    priority_map: dict[int, list[str]] = defaultdict(list)
    replied: set[str] = set()
    deadline = time.monotonic() + window

    while time.monotonic() < deadline:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        if not sub.poll(timeout=min(remaining_ms, 200)):
            continue
        try:
            frames = sub.recv_multipart(nowait=True)
            if len(frames) < 2:
                continue
            payload = json.loads(frames[1].decode())
            name     = payload.get("name")
            priority = payload.get("priority", 1)
            if name and name not in replied:
                priority_map[priority].append(name)
                replied.add(name)
                log.info(f"  module_ready: '{name}' priority={priority}")
        except (zmq.ZMQError, json.JSONDecodeError):
            continue

    sub.close(linger=0)
    ctx.term()

    # Fallback: modules that never replied get priority 1
    for name in module_names:
        if name not in replied:
            log.warning(
                f"  '{name}' did not reply to system.readytostart — "
                "assigned fallback priority 1"
            )
            priority_map[1].append(name)

    return dict(priority_map)


def _wait_for_level_ready(
    pub: zmq.Socket,
    priority: int,
    expected: list[str],
    timeout_per_module: float,
) -> None:
    """
    Publish system.start {priority} then wait for system.ready from every
    module in `expected`. Modules that exceed the timeout are logged and
    skipped so the boot can proceed to the next level.
    """
    if not expected:
        return

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect(BROKER_SUB_ADDR)
    sub.setsockopt_string(zmq.SUBSCRIBE, "system.ready")
    time.sleep(0.05)  # allow SUB to connect

    _publish(pub, "system.start", {"priority": priority})

    pending  = set(expected)
    deadline = time.monotonic() + timeout_per_module * len(expected)

    while pending and time.monotonic() < deadline:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        if not sub.poll(timeout=min(remaining_ms, 200)):
            continue
        try:
            frames = sub.recv_multipart(nowait=True)
            if len(frames) < 2:
                continue
            payload  = json.loads(frames[1].decode())
            name     = payload.get("name")
            p_level  = payload.get("priority")
            if name in pending and p_level == priority:
                pending.discard(name)
                log.info(f"  system.ready: '{name}' priority={priority}")
        except (zmq.ZMQError, json.JSONDecodeError):
            continue

    for name in pending:
        log.warning(
            f"  '{name}' (priority={priority}) did not publish system.ready — "
            "timeout, continuing boot."
        )

    sub.close(linger=0)
    ctx.term()


# ---------------------------------------------------------------------------
# system.get_modules responder
# ---------------------------------------------------------------------------

def _module_status(proc: subprocess.Popen) -> str:
    code = proc.poll()
    if code is None:
        return "active"
    return f"exited ({code})"


def _start_get_modules_responder(
    processes: list[tuple[str, subprocess.Popen]],
    stop_event: threading.Event,
) -> threading.Thread:
    def _responder():
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.connect(BROKER_SUB_ADDR)
        sub.setsockopt_string(zmq.SUBSCRIBE, "system.get_modules")
        pub = ctx.socket(zmq.PUB)
        pub.connect(BROKER_PUB_ADDR)
        log.info("system.get_modules responder ready")

        while not stop_event.is_set():
            if sub.poll(timeout=500):
                try:
                    frames = sub.recv_multipart()
                    if len(frames) < 2:
                        continue
                    log.info("system.get_modules received — building response")
                    modules_info = [
                        {
                            "name":   label,
                            "pid":    proc.pid,
                            "status": _module_status(proc),
                        }
                        for label, proc in processes
                    ]
                    pub.send_multipart([
                        b"system.modules_response",
                        json.dumps({"modules": modules_info}).encode(),
                    ])
                except zmq.ZMQError as e:
                    log.error(f"get_modules responder ZMQ error: {e}")
                    break

        sub.close(linger=0)
        pub.close(linger=0)
        ctx.term()
        log.info("system.get_modules responder stopped")

    t = threading.Thread(target=_responder, daemon=True, name="get_modules_responder")
    t.start()
    return t


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


def _terminate_all(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for label, proc in processes:
        try:
            proc.wait(timeout=GRACE_PERIOD)
            log.info(f"{label} exited on its own (code {proc.returncode})")
        except subprocess.TimeoutExpired:
            pass
    for label, proc in reversed(processes):
        if proc.poll() is None:
            log.info(f"Terminating {label} (PID {proc.pid})...")
            proc.terminate()
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

def run() -> None:
    processes: list[tuple[str, subprocess.Popen]] = []
    ctx = None
    pub = None
    _stop_responder = threading.Event()

    def _shutdown(signum, frame):
        log.info("Ctrl+C received — shutting down...")
        _stop_responder.set()
        if pub is not None:
            try:
                _publish(pub, "system.stop", {"reason": "user_interrupt"})
                time.sleep(0.2)
            except Exception:
                pass
            pub.close(linger=0)
        _terminate_all(processes)
        if ctx is not None:
            ctx.term()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 1. Discover modules
    modules      = discover_modules()
    module_names = [m.parent.name for m in modules]

    # 2. Start broker
    broker_proc = _start_process(BROKER_SCRIPT, "bus_broker")
    processes.append(("bus_broker", broker_proc))
    time.sleep(BROKER_STARTUP_DELAY)

    # 3. Connect publisher
    ctx, pub = _make_publisher()
    time.sleep(0.1)

    # 4. Start all module processes
    for module_script in modules:
        label = module_script.parent.name
        proc  = _start_process(module_script, label)
        processes.append((label, proc))
        time.sleep(MODULE_STARTUP_DELAY)

    # 5. Multi-step boot
    #    a. Collect priorities via system.readytostart / system.module_ready
    log.info(f"Boot: collecting module priorities (window={READYTOSTART_WINDOW}s)...")
    priority_map = _collect_module_ready(pub, module_names, READYTOSTART_WINDOW)
    log.info(f"Boot: priority map → {dict(priority_map)}")

    #    b. Start each priority level in order
    for level in sorted(priority_map):
        expected = priority_map[level]
        log.info(f"Boot: starting priority {level} modules: {expected}")
        _wait_for_level_ready(pub, level, expected, MODULE_READY_TIMEOUT)
        log.info(f"Boot: priority {level} complete.")

    log.info("Boot sequence complete — all priority levels initialised.")

    # 6. Start system.get_modules responder
    _start_get_modules_responder(processes, _stop_responder)

    log.info("All processes started. Press Ctrl+C to stop.")

    # 7. Keep main alive — log unexpected exits
    while True:
        time.sleep(1)
        for label, proc in processes:
            if proc.poll() is not None:
                log.warning(f"{label} exited unexpectedly (code {proc.returncode})")


if __name__ == "__main__":
    run()
