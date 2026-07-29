#!/usr/bin/env python3
"""
Web Browser Head Unit — Backend Orchestrator

Responsibilities:
  1. Start `bus_broker` (Priority 0 core IPC router).
  2. Autodiscover and start all backend modules in `modules/*/main.py`.
  3. Multi-step priority boot sequence:
       a. Publish `system.readytostart` over ZMQ.
       b. Collect `system.module_ready` `{name, priority}` from all modules.
       c. For each priority level P (ascending):
            - Publish `system.start` `{priority: P}`.
            - Wait for `system.ready` `{name, priority: P}` from all P-level modules.
  4. Orderly process lifecycle and SIGINT/SIGTERM shutdown.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import zmq

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from shared.logger import get_logger
from shared.ipc_utils import get_bus_address

MODULES_DIR = BASE_DIR / "modules"
GRACE_PERIOD = 10.0
READYTOSTART_WINDOW = 15.0   # max seconds to collect system.module_ready replies (exits early once all modules reply)
MODULE_READY_TIMEOUT = 10.0  # seconds per level wait

log = get_logger("main")


def discover_modules() -> list[Path]:
    found = sorted(
        m for m in MODULES_DIR.glob("*/main.py")
        if not m.parent.name.startswith("_")
    )
    for m in found:
        log.info(f"Discovered backend module: {m.parent.name}")
    return found


def _start_process(script: Path, label: str) -> subprocess.Popen:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    repo_root = BASE_DIR.parent
    proto_dir = repo_root / "protos"
    env["PYTHONPATH"] = f"{BASE_DIR}{os.pathsep}{repo_root}{os.pathsep}{proto_dir}{os.pathsep}{pythonpath}".rstrip(os.pathsep)

    proc = subprocess.Popen([sys.executable, str(script)], stdout=sys.stdout, stderr=sys.stderr, env=env)
    log.info(f"Started process '{label}' (PID {proc.pid})")
    return proc


def _terminate_all(processes: list[tuple[str, subprocess.Popen]]) -> None:
    deadline = time.monotonic() + GRACE_PERIOD
    for label, proc in processes:
        if proc.poll() is not None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass

    for label, proc in reversed(processes):
        if proc.poll() is None:
            log.info(f"Sending SIGTERM to module '{label}' (PID {proc.pid})...")
            proc.terminate()

    for label, proc in processes:
        if proc.poll() is None:
            log.info(f"Waiting for module '{label}' (PID {proc.pid}) to exit...")
            try:
                proc.wait(timeout=3.0)
                log.info(f"Module '{label}' (PID {proc.pid}) exited gracefully.")
            except subprocess.TimeoutExpired:
                log.warning(f"Module '{label}' (PID {proc.pid}) did not exit in 3.0s — force killing (SIGKILL)...")
                proc.kill()
        else:
            log.info(f"Module '{label}' (PID {proc.pid}) already exited with code {proc.poll()}.")


def _collect_module_ready(
    pub_sock: zmq.Socket,
    sub_sock: zmq.Socket,
    module_names: list[str],
    external_handled_module: str,
    window: float,
) -> dict[int, list[str]]:
    """Publishes system.readytostart, then collects system.module_ready replies."""
    log.info(f"Publishing system.readytostart and collecting module priorities (window={window}s)...")
    pub_sock.send_multipart([b"system.readytostart", json.dumps({"requester": "main"}).encode("utf-8")])

    priority_map: dict[int, list[str]] = defaultdict(list)
    replied: set[str] = set()
    deadline = time.monotonic() + window

    while time.monotonic() < deadline and len(replied) < len(module_names):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        if not sub_sock.poll(timeout=min(remaining_ms, 100)):
            continue
        try:
            frames = sub_sock.recv_multipart(flags=zmq.NOBLOCK)
            if len(frames) >= 2:
                topic = frames[0].decode("utf-8", errors="ignore")
                if topic == "system.module_ready":
                    payload = json.loads(frames[1].decode("utf-8"))
                    name = payload.get("name")
                    priority = payload.get("priority", 1)
                    if name and name not in replied and name!=external_handled_module:
                        priority_map[priority].append(name)
                        replied.add(name)
                        log.info(f"  module_ready received: '{name}' (priority={priority})")
        except Exception:
            continue
    log.info(f"Total modules = {len(module_names)}, total replied = {len(replied)}")
    # Fallback: any module that did not reply gets priority 1
    for name in module_names:
        if name not in replied:
            log.warning(f"  '{name}' did not reply to system.readytostart — assigned fallback priority 1")
            priority_map[1].append(name)

    return dict(priority_map)


def _wait_for_level_ready(
    pub_sock: zmq.Socket,
    sub_sock: zmq.Socket,
    priority: int,
    expected: list[str],
    timeout_per_module: float,
) -> None:
    """Publishes system.start {priority} then waits for system.ready from expected modules."""
    if not expected:
        return

    log.info(f"Boot: starting priority {priority} modules: {expected}")
    pub_sock.send_multipart([b"system.start", json.dumps({"priority": priority}).encode("utf-8")])

    pending = set(expected)
    deadline = time.monotonic() + (timeout_per_module * len(expected))

    while pending and time.monotonic() < deadline:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        if not sub_sock.poll(timeout=min(remaining_ms, 100)):
            continue
        try:
            frames = sub_sock.recv_multipart(flags=zmq.NOBLOCK)
            if len(frames) >= 2:
                topic = frames[0].decode("utf-8", errors="ignore")
                if topic == "system.ready":
                    payload = json.loads(frames[1].decode("utf-8"))
                    name = payload.get("name")
                    p_level = payload.get("priority")
                    if name in pending and p_level == priority:
                        pending.discard(name)
                        log.info(f"  system.ready received: '{name}' (priority={priority})")
        except Exception:
            continue

    for name in pending:
        log.warning(f"  '{name}' (priority={priority}) timeout waiting for system.ready — continuing boot.")


def run():
    log.info("Starting Web Browser Head Unit Backend Orchestrator...")
    modules = discover_modules()
    module_processes: list[tuple[str, subprocess.Popen]] = []

    # 1. Start Priority 0 bus_broker process first
    broker_path = BASE_DIR / "modules" / "bus_broker" / "main.py"
    broker_proc = _start_process(broker_path, "bus_broker")
    module_processes.append(("bus_broker", broker_proc))
    time.sleep(0.5)  # Allow bus_broker to bind sockets

    # Setup orchestrator ZMQ sockets
    ctx = zmq.Context()
    pub_sock = ctx.socket(zmq.PUB)
    pub_sock.setsockopt(zmq.LINGER, 0)
    pub_sock.connect(get_bus_address("main_orchestrator", "sub"))

    sub_sock = ctx.socket(zmq.SUB)
    sub_sock.setsockopt(zmq.LINGER, 0)
    sub_sock.connect(get_bus_address("main_orchestrator", "pub"))
    sub_sock.setsockopt_string(zmq.SUBSCRIBE, "system.module_ready")
    sub_sock.setsockopt_string(zmq.SUBSCRIBE, "system.ready")
    time.sleep(0.5)  # allow SUB/PUB sockets to complete ZMQ connection handshake

    # 2. Launch non-broker module processes
    other_modules = [m for m in modules if m.parent.name != "bus_broker"]
    module_names = [m.parent.name for m in other_modules]

    for m in other_modules:
        label = m.parent.name
        proc = _start_process(m, label)
        module_processes.append((label, proc))

    # 3. Multi-step priority boot sequence
    time.sleep(0.5)
    priority_map = _collect_module_ready(pub_sock, sub_sock, module_names, "bus_broker", READYTOSTART_WINDOW)
    log.info(f"Boot priority map → {priority_map}")

    for level in sorted(priority_map.keys()):
        expected = priority_map[level]
        _wait_for_level_ready(pub_sock, sub_sock, level, expected, MODULE_READY_TIMEOUT)
        log.info(f"Boot: priority {level} complete.")

    log.info("Boot sequence complete — all priority levels initialised.")

    def _shutdown(signum, frame):
        log.info("Shutdown signal received — stopping modules...")
        try:
            pub_sock.send_multipart([b"system.stop", json.dumps({"reason": "shutdown"}).encode("utf-8")])
            time.sleep(0.2)
        except Exception:
            pass
        _terminate_all(module_processes)
        pub_sock.close(linger=0)
        sub_sock.close(linger=0)
        ctx.term()
        log.info("Backend orchestrator stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Backend orchestrator active with process-isolated modules.")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    run()
