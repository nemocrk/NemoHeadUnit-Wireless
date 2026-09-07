# Phase 6: Process Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement isolated, high-speed unit tests covering backend orchestrator CLI mode resolution, module autodiscovery, process/thread handle management, stdout readiness pumping, priority boot sequencing, and clean process/thread lifecycle termination (`backend/main.py`).

**Architecture:** The orchestrator module (`backend/main.py`) is tested with zero real OS process spawning, zero real filesystem execution, and zero real ZMQ daemons. Tests mock `subprocess.Popen`, `threading.Thread`, and `zmq.Socket`, asserting wire messages and process control signals directly.

**Tech Stack:** Python 3.13 / 3.14, `pytest`, `pytest-asyncio`, `unittest.mock`, `zmq`.

**Spec:** `docs/superpowers/specs/2026-09-07-unit-tests-phase6-orchestrator-design.md`

## Global Constraints
- Target execution time: Phase 6 suite < 1.5s total.
- Strict isolation: Unit tests must never spawn real subprocesses, kill host processes, or bind live ZMQ ports.
- Universal cross-platform compatibility: Linux and Windows (`pathlib.Path`, cross-platform mocks).
- All unit test files must define `pytestmark = pytest.mark.unit`.
- All test runs must use `micromamba run -n NemoHeadUnit-Wireless pytest ...`.

---

### Task 1: Orchestrator CLI Mode, Module Discovery & Handle Management

**Files:**
- Create: `tests/unit/orchestrator/__init__.py`
- Create: `tests/unit/orchestrator/test_orchestrator_discovery.py`

**Interfaces:**
- Consumes: `backend.main` (`get_execution_mode`, `discover_modules`, `_start_module`, `ModuleHandle`, `MODULES_DIR`, `BASE_DIR`)
- Produces: 5 unit tests verifying CLI arguments, env fallbacks, module discovery filtering, thread mode startup, process mode startup, and stdout readiness monitoring.

- [ ] **Step 1: Write failing test file `tests/unit/orchestrator/test_orchestrator_discovery.py`**

```python
import os
import sys
import threading
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

import backend.main as main_mod
from backend.main import (
    get_execution_mode,
    discover_modules,
    _start_module,
    ModuleHandle,
)

pytestmark = pytest.mark.unit


def test_get_execution_mode_cli_and_env():
    # 1. Default fallback
    with patch.dict(os.environ, {}, clear=True):
        assert get_execution_mode([]) == "multiprocessing"

    # 2. CLI -m flag
    assert get_execution_mode(["-m", "multithreading"]) == "multithreading"
    assert get_execution_mode(["--mode", "threads"]) == "multithreading"
    assert get_execution_mode(["--mode", "thread"]) == "multithreading"
    assert get_execution_mode(["-m", "multiprocessing"]) == "multiprocessing"

    # 3. Environment variable fallback
    with patch.dict(os.environ, {"NEMO_EXECUTION_MODE": "multithreading"}):
        assert get_execution_mode([]) == "multithreading"

    with patch.dict(os.environ, {"NEMO_MODE": "threading"}):
        assert get_execution_mode([]) == "multithreading"


def test_discover_modules(tmp_path):
    # Create synthetic modules directory structure
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    # Valid modules
    (modules_dir / "bus_broker").mkdir()
    (modules_dir / "bus_broker" / "main.py").write_text("# broker")
    (modules_dir / "proxy").mkdir()
    (modules_dir / "proxy" / "main.py").write_text("# proxy")

    # Template and hidden directories (should be ignored)
    (modules_dir / "_template").mkdir()
    (modules_dir / "_template" / "main.py").write_text("# template")
    (modules_dir / "_hidden").mkdir()
    (modules_dir / "_hidden" / "main.py").write_text("# hidden")

    with patch.object(main_mod, "MODULES_DIR", modules_dir):
        discovered = discover_modules()
        discovered_names = [m.parent.name for m in discovered]
        assert discovered_names == ["bus_broker", "proxy"]
        assert "_template" not in discovered_names
        assert "_hidden" not in discovered_names


def test_start_module_thread_mode():
    mock_thread = MagicMock()
    with patch("threading.Thread", return_value=mock_thread):
        handle = _start_module(Path("/dummy/path/main.py"), "dummy_label", "multithreading")
        assert handle.label == "dummy_label"
        assert handle.mode == "multithreading"
        assert handle.thread == mock_thread
        mock_thread.start.assert_called_once()


def test_start_module_process_mode():
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        handle = _start_module(Path("/dummy/path/main.py"), "dummy_proc", "multiprocessing")
        assert handle.label == "dummy_proc"
        assert handle.mode == "multiprocessing"
        assert handle.proc == mock_proc
        mock_popen.assert_called_once()
        env_passed = mock_popen.call_args[1]["env"]
        assert "PYTHONPATH" in env_passed
        assert str(main_mod.BASE_DIR) in env_passed["PYTHONPATH"]


def test_start_module_stdout_pump_ready_event():
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    # Simulate stdout lines with readiness confirmation
    mock_proc.stdout.readline.side_effect = [
        "Initializing bus_broker...\n",
        "ZMQ Proxy thread active (XPUB/XSUB ready)\n",
        "",
    ]

    ready_event = threading.Event()
    with patch("subprocess.Popen", return_value=mock_proc):
        handle = _start_module(
            Path("/dummy/bus_broker/main.py"),
            "bus_broker",
            "multiprocessing",
            ready_event=ready_event,
        )
        assert handle.label == "bus_broker"
        # Wait briefly for pump thread to process lines
        assert ready_event.wait(timeout=1.0) is True
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/orchestrator/test_orchestrator_discovery.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/orchestrator/__init__.py tests/unit/orchestrator/test_orchestrator_discovery.py
git commit -m "test(unit): add orchestrator execution mode and discovery unit tests"
```

---

### Task 2: Orchestrator Priority Boot Sequence & Lifecycle Termination

**Files:**
- Create: `tests/unit/orchestrator/test_orchestrator_boot.py`

**Interfaces:**
- Consumes: `backend.main` (`_collect_module_ready`, `_wait_for_level_ready`, `_terminate_all`, `ModuleHandle`)
- Produces: 5 unit tests verifying `system.readytostart` collection, priority 1 fallback, priority level gating, level timeout resilience, and graceful process/thread termination.

- [ ] **Step 1: Write failing test file `tests/unit/orchestrator/test_orchestrator_boot.py`**

```python
import json
import subprocess
import time
from unittest.mock import MagicMock, patch
import pytest

from backend.main import (
    _collect_module_ready,
    _wait_for_level_ready,
    _terminate_all,
    ModuleHandle,
)

pytestmark = pytest.mark.unit


def test_collect_module_ready_all_reply():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    sub_sock.poll.return_value = True
    sub_sock.recv_multipart.side_effect = [
        [b"system.module_ready", json.dumps({"name": "config_manager", "priority": 1}).encode("utf-8")],
        [b"system.module_ready", json.dumps({"name": "proxy", "priority": 2}).encode("utf-8")],
    ]

    module_names = ["config_manager", "proxy"]
    priority_map = _collect_module_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        module_names=module_names,
        external_handled_module="bus_broker",
        window=1.0,
    )

    # Verify system.readytostart was sent
    pub_sock.send_multipart.assert_called_once()
    assert pub_sock.send_multipart.call_args[0][0][0] == b"system.readytostart"

    # Verify priority mapping
    assert priority_map[1] == ["config_manager"]
    assert priority_map[2] == ["proxy"]


def test_collect_module_ready_fallback_priority():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    # No modules reply
    sub_sock.poll.return_value = False

    module_names = ["unresponsive_mod"]
    priority_map = _collect_module_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        module_names=module_names,
        external_handled_module="bus_broker",
        window=0.01,
    )

    # Unresponsive module defaults to priority 1
    assert 1 in priority_map
    assert "unresponsive_mod" in priority_map[1]


def test_wait_for_level_ready_success():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    sub_sock.poll.return_value = True
    sub_sock.recv_multipart.side_effect = [
        [b"system.ready", json.dumps({"name": "tcp_server", "priority": 3}).encode("utf-8")],
        [b"system.ready", json.dumps({"name": "channel_manager", "priority": 3}).encode("utf-8")],
    ]

    expected = ["tcp_server", "channel_manager"]
    _wait_for_level_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        priority=3,
        expected=expected,
        timeout_per_module=0.5,
    )

    # Verify system.start was sent for priority 3
    pub_sock.send_multipart.assert_called_once()
    sent_frames = pub_sock.send_multipart.call_args[0][0]
    assert sent_frames[0] == b"system.start"
    payload = json.loads(sent_frames[1].decode("utf-8"))
    assert payload["priority"] == 3


def test_wait_for_level_ready_timeout():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    sub_sock.poll.return_value = False

    expected = ["slow_module"]
    # Should not raise exception on timeout
    _wait_for_level_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        priority=4,
        expected=expected,
        timeout_per_module=0.01,
    )

    pub_sock.send_multipart.assert_called_once()


def test_terminate_all_processes_and_threads():
    # 1. Process already exited
    proc_exited = MagicMock()
    proc_exited.poll.return_value = 0

    # 2. Process responding gracefully to terminate()
    proc_graceful = MagicMock()
    proc_graceful.poll.side_effect = [None, None, 0, 0, 0]

    # 3. Process that hangs and requires kill()
    proc_stuck = MagicMock()
    proc_stuck.poll.side_effect = [None, None, None, None, None]
    proc_stuck.wait.side_effect = [None, subprocess.TimeoutExpired(cmd="stuck", timeout=3.0)]

    # 4. Thread already exited
    thread_exited = MagicMock()
    thread_exited.is_alive.return_value = False

    # 5. Active thread joined gracefully
    thread_active = MagicMock()
    thread_active.is_alive.side_effect = [True, False]

    handles = [
        ModuleHandle("exited_proc", "multiprocessing", proc=proc_exited),
        ModuleHandle("graceful_proc", "multiprocessing", proc=proc_graceful),
        ModuleHandle("stuck_proc", "multiprocessing", proc=proc_stuck),
        ModuleHandle("exited_thread", "multithreading", thread=thread_exited),
        ModuleHandle("active_thread", "multithreading", thread=thread_active),
    ]

    with patch("time.monotonic", side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]):
        _terminate_all(handles)

    # Verify graceful process received terminate
    proc_graceful.terminate.assert_called_once()

    # Verify stuck process was terminated then killed
    proc_stuck.terminate.assert_called_once()
    proc_stuck.kill.assert_called_once()

    # Verify active thread was joined
    thread_active.join.assert_called_once()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/orchestrator/test_orchestrator_boot.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/orchestrator/test_orchestrator_boot.py
git commit -m "test(unit): add orchestrator priority boot sequence and termination unit tests"
```

---

### Task 3: Phase 6 Verification & Whole-Branch Review

**Files:**
- None (verification only)

- [ ] **Step 1: Run full Phase 6 test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/orchestrator/ -v`
Expected: 10 passed in < 1.0s.

- [ ] **Step 2: Run entire unit test suite across all 6 phases**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/ -v`
Expected: 150 passed in < 2.0s.

- [ ] **Step 3: Run full repository pytest suite to prove zero regressions**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest -q`
Expected: 240 passed.

- [ ] **Step 4: Execute orchestrator smoke test**

Run: `timeout 7 micromamba run -n NemoHeadUnit-Wireless python backend/main.py`
Expected: Clean startup across Waves 0–5 and clean exit with code 124 on timeout.

- [ ] **Step 5: Run graphify update**

Run: `/home/nemo/miniconda3/bin/graphify update .`

- [ ] **Step 6: Commit plan and spec documentation**

```bash
git add docs/superpowers/specs/2026-09-07-unit-tests-phase6-orchestrator-design.md docs/superpowers/plans/2026-09-07-unit-tests-phase6-orchestrator.md
git commit -m "docs(tests): add Phase 6 orchestrator unit test spec and plan"
```

- [ ] **Step 7: Run whole-branch review subagent**
