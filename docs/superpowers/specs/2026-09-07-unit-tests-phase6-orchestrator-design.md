# Phase 6: Process Orchestrator Unit Test Suite Design

## 1. Context & Objectives
Phase 6 covers the central backend process orchestrator (`backend/main.py`), which manages the lifecycle, discovery, execution mode, multi-step priority wave boot sequence, and clean shutdown of all backend modules.
Key responsibilities under test:
- **CLI & Environment Mode Resolution (`get_execution_mode`)**: Support for `-m`, `--mode`, mode aliases (`thread`, `multithreading`, `multiprocessing`), environment overrides (`NEMO_EXECUTION_MODE`, `NEMO_MODE`), and default fallback.
- **Module Discovery (`discover_modules`)**: Dynamic discovery of `modules/*/main.py`, excluding template directories (`_template`) and private prefixes (`_*`).
- **Module Spawning & Readiness Handshake (`_start_module`)**: Thread spawning in multithreading mode, process spawning with injected `PYTHONPATH` in multiprocessing mode, and deterministic readiness pump for `bus_broker`.
- **Priority Wave Boot Sequence (`_collect_module_ready`, `_wait_for_level_ready`)**:
  - Wave 0: Autonomous bus broker initialization.
  - Ready-to-start collection: ZMQ `system.readytostart` broadcast, collection of `system.module_ready` `{name, priority}` within window, fallback to priority 1 for non-responsive modules, early loop exit when all reply.
  - Priority wave sequencing: Sequential broadcast of `system.start` `{priority: P}`, waiting for `system.ready` from all level-P modules, timeout resilience.
- **Process & Thread Termination (`_terminate_all`)**: Graceful termination via `SIGTERM`, graceful exit waiting, escalation to `SIGKILL` on timeout, and thread joining.

---

## 2. Global Constraints & Architectural Invariants
1. **Target Execution Speed**:
   The entire Phase 6 unit test suite must execute in **< 1.5 seconds** total.
2. **Strict Isolation**:
   Unit tests must never launch real backend subprocesses, bind real ZMQ daemons, or terminate host processes. All ZMQ sockets (`zmq.Socket`) and subprocess calls (`subprocess.Popen`) must be strictly mocked using `unittest.mock`.
3. **Universal Cross-Platform Compliance**:
   All tests, path lookups (`pathlib.Path`), environment variables, and mocks must operate identically on Linux and Windows.
4. **Strict Marker Requirement**:
   Every test file must declare `pytestmark = pytest.mark.unit`.

---

## 3. Component Architecture & Test Breakdown

### Component 1: CLI Execution Mode, Module Discovery & Handle Management
- Target: `backend/main.py` (`get_execution_mode`, `discover_modules`, `_start_module`, `ModuleHandle`)
- Test File: `tests/unit/orchestrator/test_orchestrator_discovery.py`
- Scope:
  - `test_get_execution_mode`: CLI argument variations, environment variable fallbacks, case insensitivity, default resolution.
  - `test_discover_modules`: Discovers valid modules, excludes `_template`, sorts deterministically.
  - `test_start_module_thread_mode`: Multithreading mode creates and starts daemon `threading.Thread`.
  - `test_start_module_process_mode`: Multiprocessing mode invokes `subprocess.Popen` with enriched `PYTHONPATH` (`BASE_DIR`, `repo_root`, `proto_dir`).
  - `test_start_module_stdout_pump_ready_event`: Stdout line monitoring sets `ready_event` upon detecting readiness string.

### Component 2: Priority Boot Sequence & Lifecycle Termination
- Target: `backend/main.py` (`_collect_module_ready`, `_wait_for_level_ready`, `_terminate_all`)
- Test File: `tests/unit/orchestrator/test_orchestrator_boot.py`
- Scope:
  - `test_collect_module_ready_all_reply`: `pub_sock.send_multipart` emits `system.readytostart`, `sub_sock.recv_multipart` feeds replies, priority map correctly populated, early loop termination.
  - `test_collect_module_ready_fallback_priority`: Modules that fail to reply within window assigned default priority 1 with warning log.
  - `test_wait_for_level_ready_success`: `pub_sock.send_multipart` emits `system.start`, `sub_sock.recv_multipart` feeds matching `system.ready` packets, pending set cleared.
  - `test_wait_for_level_ready_timeout`: Missing `system.ready` times out gracefully without raising unhandled exceptions.
  - `test_terminate_all_processes_and_threads`: Handles finished processes, running processes responding to `terminate()`, stubborn processes requiring `kill()`, and active thread joins.

---

## 4. Verification & Gate Strategy
- Individual test execution via pytest.
- Aggregate execution of `tests/unit/orchestrator/` suite (< 1.5s).
- Full repository unit test suite verification (< 2.0s).
- Full repository suite run proving zero regressions.
- Backend orchestrator smoke test.
- Code graph AST update via `graphify`.
