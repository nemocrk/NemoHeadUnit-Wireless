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
import queue
import runpy
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import zmq

BASE_DIR = Path(__file__).parent
repo_root = BASE_DIR.parent
proto_dir = repo_root / "protos"

for path in [str(BASE_DIR), str(repo_root), str(proto_dir)]:
    if path not in sys.path:
        sys.path.insert(0, path)

from shared.logger import get_logger, add_log_listener, remove_log_listener
from shared.ipc_utils import get_bus_address
from shared.bus_client import BusClient

MODULES_DIR = BASE_DIR / "modules"
GRACE_PERIOD = 10.0
READYTOSTART_WINDOW = 30.0   # max seconds to collect system.module_ready replies (exits early once all modules reply)
MODULE_READY_TIMEOUT = 10.0  # seconds per level wait

log = get_logger("main")


def get_execution_mode(argv: list[str] | None = None) -> str:
    import argparse
    parser = argparse.ArgumentParser(description="NemoHeadUnit-Wireless Backend Orchestrator")
    default_mode = os.environ.get("NEMO_EXECUTION_MODE", os.environ.get("NEMO_MODE", "multiprocessing")).lower().strip()
    parser.add_argument(
        "-m",
        "--mode",
        dest="mode",
        default=default_mode,
        choices=["multiprocessing", "multithreading", "threading", "thread", "threads"],
        help="Execution isolation mode: multiprocessing (separate processes) or multithreading (shared threads)",
    )
    args, _ = parser.parse_known_args(argv)
    raw_mode = (args.mode or "multiprocessing").lower().strip()
    if raw_mode in ("multithreading", "threading", "thread", "threads"):
        return "multithreading"
    return "multiprocessing"



class ModuleHandle:
    def __init__(self, label: str, mode: str, proc: subprocess.Popen | None = None, thread: threading.Thread | None = None):
        self.label = label
        self.mode = mode
        self.proc = proc
        self.thread = thread


def discover_modules() -> list[Path]:
    found = sorted(
        m for m in MODULES_DIR.glob("*/main.py")
        if not m.parent.name.startswith("_")
    )
    for m in found:
        log.info(f"Discovered backend module: {m.parent.name}")
    return found


def _run_thread_module(script: Path, label: str) -> None:
    log.info(f"Started thread worker for module '{label}'")
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit:
        log.info(f"Module thread '{label}' exited cleanly via SystemExit.")
    except Exception as exc:
        log.error(f"Module thread '{label}' exited with error: {exc}", exc_info=True)


def _start_module(script: Path, label: str, mode: str, ready_event: threading.Event | None = None) -> ModuleHandle:
    if mode == "multithreading":
        if ready_event is not None:
            def _check_ready(entry: dict):
                msg = entry.get("message", "")
                if "ZMQ Proxy thread active" in msg or "Module 'bus_broker' is ready" in msg or "In-memory event bus active" in msg:
                    ready_event.set()
                    remove_log_listener(_check_ready)
            add_log_listener(_check_ready)
        t = threading.Thread(target=_run_thread_module, args=(script, label), daemon=True, name=f"mod_{label}")
        t.start()
        return ModuleHandle(label=label, mode=mode, thread=t)
    else:
        env = os.environ.copy()
        env["NEMO_EXECUTION_MODE"] = mode
        pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{BASE_DIR}{os.pathsep}{repo_root}{os.pathsep}{proto_dir}{os.pathsep}{pythonpath}".rstrip(os.pathsep)

        if ready_event is not None:
            # Capture stdout with a pipe to detect deterministic readiness signal,
            # while continuously streaming lines to sys.stdout in real time
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE,
                stderr=sys.stderr,
                env=env,
                text=True,
                bufsize=1,
            )

            def _pump_stdout():
                for line in iter(proc.stdout.readline, ""):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    if "ZMQ Proxy thread active" in line or "Module 'bus_broker' is ready" in line or "In-memory event bus active" in line:
                        ready_event.set()
                proc.stdout.close()

            t_pump = threading.Thread(target=_pump_stdout, daemon=True, name=f"pump_{label}")
            t_pump.start()
        else:
            proc = subprocess.Popen([sys.executable, str(script)], stdout=sys.stdout, stderr=sys.stderr, env=env)

        log.info(f"Started process '{label}' (PID {proc.pid})")
        return ModuleHandle(label=label, mode=mode, proc=proc)


def _terminate_all(handles: list[ModuleHandle]) -> None:
    deadline = time.monotonic() + GRACE_PERIOD

    # Process mode cleanup
    proc_handles = [h for h in handles if h.proc is not None]
    if proc_handles:
        for h in proc_handles:
            if h.proc.poll() is not None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                h.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass

        for h in reversed(proc_handles):
            if h.proc.poll() is None:
                log.info(f"Sending SIGTERM to module process '{h.label}' (PID {h.proc.pid})...")
                h.proc.terminate()

        for h in proc_handles:
            if h.proc.poll() is None:
                log.info(f"Waiting for module process '{h.label}' (PID {h.proc.pid}) to exit...")
                try:
                    h.proc.wait(timeout=3.0)
                    log.info(f"Module process '{h.label}' (PID {h.proc.pid}) exited gracefully.")
                except subprocess.TimeoutExpired:
                    log.warning(f"Module process '{h.label}' (PID {h.proc.pid}) did not exit in 3.0s — force killing (SIGKILL)...")
                    h.proc.kill()
            else:
                log.info(f"Module process '{h.label}' (PID {h.proc.pid}) already exited with code {h.proc.poll()}.")

    # Thread mode cleanup
    thread_handles = [h for h in handles if h.thread is not None]
    if thread_handles:
        for h in thread_handles:
            if not h.thread.is_alive():
                continue
            remaining = max(0.1, deadline - time.monotonic())
            h.thread.join(timeout=min(remaining, 1.0))
            if h.thread.is_alive():
                log.warning(f"Module thread '{h.label}' did not finish after join timeout.")
            else:
                log.info(f"Module thread '{h.label}' exited gracefully.")


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
        try:
            if not sub_sock.poll(timeout=min(remaining_ms, 100)):
                continue
        except zmq.ZMQError:
            break

        try:
            frames = sub_sock.recv_multipart(flags=zmq.NOBLOCK)
            if len(frames) >= 2:
                topic = frames[0].decode("utf-8", errors="ignore")
                if topic == "system.module_ready":
                    payload = json.loads(frames[1].decode("utf-8"))
                    name = payload.get("name")
                    priority = payload.get("priority", 1)
                    if name and name not in replied and name != external_handled_module:
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
        try:
            if not sub_sock.poll(timeout=min(remaining_ms, 100)):
                continue
        except zmq.ZMQError:
            break

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


def _collect_module_ready_inmemory(
    bus: BusClient,
    ready_q: queue.Queue,
    module_names: list[str],
    external_handled_module: str,
    window: float,
) -> dict[int, list[str]]:
    """Publishes system.readytostart and collects system.module_ready replies via in-memory bus."""
    log.info(f"Publishing system.readytostart and collecting module priorities (in-memory, window={window}s)...")
    bus.publish("system.readytostart", {"requester": "main"})

    priority_map: dict[int, list[str]] = defaultdict(list)
    replied: set[str] = set()
    deadline = time.monotonic() + window

    while time.monotonic() < deadline and len(replied) < len(module_names):
        remaining = max(0.0, deadline - time.monotonic())
        try:
            payload = ready_q.get(timeout=min(remaining, 0.05))
            name = payload.get("name")
            priority = payload.get("priority", 1)
            if name and name not in replied and name != external_handled_module:
                priority_map[priority].append(name)
                replied.add(name)
                log.info(f"  module_ready received: '{name}' (priority={priority})")
        except queue.Empty:
            continue

    log.info(f"Total modules = {len(module_names)}, total replied = {len(replied)}")
    for name in module_names:
        if name not in replied:
            log.warning(f"  '{name}' did not reply to system.readytostart — assigned fallback priority 1")
            priority_map[1].append(name)

    return dict(priority_map)


def _wait_for_level_ready_inmemory(
    bus: BusClient,
    level_ready_q: queue.Queue,
    priority: int,
    expected: list[str],
    timeout_per_module: float,
) -> None:
    """Publishes system.start {priority} then waits for system.ready from expected modules via in-memory bus."""
    if not expected:
        return

    log.info(f"Boot: starting priority {priority} modules (in-memory): {expected}")
    bus.publish("system.start", {"priority": priority})

    pending = set(expected)
    deadline = time.monotonic() + (timeout_per_module * len(expected))

    while pending and time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            payload = level_ready_q.get(timeout=min(remaining, 0.05))
            name = payload.get("name")
            p_level = payload.get("priority")
            if name in pending and p_level == priority:
                pending.discard(name)
                log.info(f"  system.ready received: '{name}' (priority={priority})")
        except queue.Empty:
            continue

    for name in pending:
        log.warning(f"  '{name}' (priority={priority}) timeout waiting for system.ready — continuing boot.")


def run(argv: list[str] | None = None):
    mode = get_execution_mode(argv)
    os.environ["NEMO_EXECUTION_MODE"] = mode
    log.info(f"Starting Web Browser Head Unit Backend Orchestrator (Mode: {mode.upper()})...")

    modules = discover_modules()
    module_handles: list[ModuleHandle] = []

    shutdown_requested = False
    shutdown_reason = "shutdown"

    def _signal_handler(signum, frame):
        nonlocal shutdown_requested, shutdown_reason
        shutdown_reason = f"signal_{signum}"
        shutdown_requested = True
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, AttributeError):
        pass

    if mode == "multithreading":
        orch_bus = BusClient("main_orchestrator")
        orch_bus.start(blocking=False)
        shutdown_q: queue.Queue = queue.Queue()
        module_ready_q: queue.Queue = queue.Queue()
        level_ready_q: queue.Queue = queue.Queue()
        orch_bus.subscribe("system.shutdown", lambda topic, payload: shutdown_q.put(payload))
        orch_bus.subscribe("system.module_ready", lambda topic, payload: module_ready_q.put(payload))
        orch_bus.subscribe("system.ready", lambda topic, payload: level_ready_q.put(payload))
        ctx = None
        pub_sock = None
        sub_sock = None
    else:
        orch_bus = None
        shutdown_q = None
        module_ready_q = None
        level_ready_q = None
        ctx = zmq.Context()
        pub_sock = ctx.socket(zmq.PUB)
        pub_sock.setsockopt(zmq.LINGER, 0)
        sub_sock = ctx.socket(zmq.SUB)
        sub_sock.setsockopt(zmq.LINGER, 0)

    def _shutdown(reason: str = "shutdown"):
        nonlocal shutdown_requested
        shutdown_requested = True
        log.info(f"Shutdown requested ({reason}) — stopping modules...")
        if mode == "multithreading" and orch_bus:
            try:
                orch_bus.publish("system.stop", {"reason": reason})
                time.sleep(0.2)
            except Exception:
                pass
            _terminate_all(module_handles)
            try:
                orch_bus.stop()
            except Exception:
                pass
        else:
            try:
                if pub_sock:
                    pub_sock.send_multipart([b"system.stop", json.dumps({"reason": reason}).encode("utf-8")])
                time.sleep(0.2)
            except Exception:
                pass
            _terminate_all(module_handles)
            try:
                if pub_sock:
                    pub_sock.close(linger=0)
                if sub_sock:
                    sub_sock.close(linger=0)
                if ctx:
                    ctx.term()
            except Exception:
                pass
        log.info("Backend orchestrator stopped.")
        os._exit(0)

    gui_module = next((m for m in modules if m.parent.name == "qt6_gui"), None)

    def _run_orchestrator():
        nonlocal shutdown_requested, shutdown_reason
        try:
            # 1. Start Priority 0 bus_broker process/thread first and wait deterministically for it to bind sockets
            broker_ready_event = threading.Event()
            broker_path = BASE_DIR / "modules" / "bus_broker" / "main.py"
            broker_handle = _start_module(broker_path, "bus_broker", mode, ready_event=broker_ready_event)
            module_handles.append(broker_handle)

            log.info("Waiting for bus_broker to initialize and activate...")
            if not broker_ready_event.wait(timeout=30.0):
                log.warning("bus_broker readiness event timed out after 30s — proceeding anyway")
            else:
                log.info("bus_broker confirmed ready via handshake!")

            if mode != "multithreading":
                # Setup orchestrator ZMQ sockets (now guaranteed to connect to active broker)
                pub_sock.connect(get_bus_address("main_orchestrator", "sub"))
                sub_sock.connect(get_bus_address("main_orchestrator", "pub"))
                sub_sock.setsockopt_string(zmq.SUBSCRIBE, "system.module_ready")
                sub_sock.setsockopt_string(zmq.SUBSCRIBE, "system.ready")
                sub_sock.setsockopt_string(zmq.SUBSCRIBE, "system.shutdown")
                time.sleep(0.5)  # allow SUB/PUB sockets to complete ZMQ connection handshake
            else:
                time.sleep(0.1)

            # 2. Launch non-broker module processes/threads (excluding qt6_gui if it runs on main thread)
            modules_to_start = [
                m for m in modules
                if m.parent.name != "bus_broker"
                and not (mode == "multithreading" and gui_module and m.parent.name == "qt6_gui")
            ]
            module_names = [m.parent.name for m in modules if m.parent.name != "bus_broker"]

            for m in modules_to_start:
                if shutdown_requested:
                    break
                label = m.parent.name
                handle = _start_module(m, label, mode)
                module_handles.append(handle)

            if not shutdown_requested:
                # 3. Multi-step priority boot sequence
                if mode == "multithreading":
                    time.sleep(0.2)
                    priority_map = _collect_module_ready_inmemory(orch_bus, module_ready_q, module_names, "bus_broker", READYTOSTART_WINDOW)
                    log.info(f"Boot priority map → {priority_map}")

                    for level in sorted(priority_map.keys()):
                        if shutdown_requested:
                            break
                        expected = priority_map[level]
                        _wait_for_level_ready_inmemory(orch_bus, level_ready_q, level, expected, MODULE_READY_TIMEOUT)
                        log.info(f"Boot: priority {level} complete.")
                else:
                    time.sleep(0.5)
                    priority_map = _collect_module_ready(pub_sock, sub_sock, module_names, "bus_broker", READYTOSTART_WINDOW)
                    log.info(f"Boot priority map → {priority_map}")

                    for level in sorted(priority_map.keys()):
                        if shutdown_requested:
                            break
                        expected = priority_map[level]
                        _wait_for_level_ready(pub_sock, sub_sock, level, expected, MODULE_READY_TIMEOUT)
                        log.info(f"Boot: priority {level} complete.")

                if not shutdown_requested:
                    log.info("Boot sequence complete — all priority levels initialised.")
                    log.info(f"Backend orchestrator active with {mode}-isolated modules.")

            while not shutdown_requested:
                if mode == "multithreading":
                    try:
                        payload = shutdown_q.get(timeout=0.5)
                        shutdown_reason = payload.get("reason", "user_exit")
                        shutdown_requested = True
                        break
                    except queue.Empty:
                        continue
                else:
                    try:
                        if not sub_sock.poll(timeout=500):
                            continue
                    except zmq.ZMQError:
                        break

                    try:
                        frames = sub_sock.recv_multipart(flags=zmq.NOBLOCK)
                        if len(frames) >= 2:
                            topic = frames[0].decode("utf-8", errors="ignore")
                            if topic == "system.shutdown":
                                payload = json.loads(frames[1].decode("utf-8"))
                                shutdown_reason = payload.get("reason", "user_exit")
                                shutdown_requested = True
                                break
                    except Exception:
                        continue

        except (KeyboardInterrupt, SystemExit):
            shutdown_reason = "keyboard_interrupt"
        except Exception as exc:
            log.error(f"Error in orchestrator run loop: {exc}", exc_info=True)
            shutdown_reason = f"error_{exc}"
        finally:
            if not (mode == "multithreading" and gui_module):
                _shutdown(shutdown_reason)

    if mode == "multithreading" and gui_module:
        orch_thread = threading.Thread(target=_run_orchestrator, daemon=True, name="orchestrator")
        orch_thread.start()
        try:
            _run_thread_module(gui_module, "qt6_gui")
        except (KeyboardInterrupt, SystemExit):
            shutdown_reason = "keyboard_interrupt"
        except Exception as exc:
            log.error(f"Error in main GUI thread: {exc}", exc_info=True)
            shutdown_reason = f"error_{exc}"
        _shutdown(shutdown_reason)
    else:
        _run_orchestrator()


if __name__ == "__main__":
    run()
