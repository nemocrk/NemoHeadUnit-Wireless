# Production Readiness & Codebase Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate NemoHeadUnit-Wireless to professional software-house grade for production deployment: remove dead/legacy code, harmonize all architecture and UI documentation, rewrite README.md into an authoritative technical manual, establish automated GitHub Actions CI for push/PR across Linux and Windows, and standardize project metadata.

**Architecture:** The project employs a 6-wave priority boot orchestrator managing process-isolated microservices communicating over ZeroMQ XPUB/XSUB (or in-memory bus hub in multithreading mode) and shared memory (POSIX `/dev/shm` / Windows named mapping) for zero-copy H.264 video. We prune dead historical artifacts, synchronize schemas/metadata, and wire a multi-platform CI suite.

**Tech Stack:** Python 3.13, Micromamba/Conda, PyQt6, ZeroMQ (pyzmq), OpenSSL/cryptography, Protobuf 3+, GitHub Actions, Ruff, Pytest.

**Spec:** User directive for production deployment readiness.

## Global Constraints
- Strictly cross-platform compliant (Linux & Windows) via `pathlib.Path` and `ipc_utils.py`.
- Preserve all active module APIs (`bus_broker`, `config_manager`, `proxy`, `tcp_server`, `connectivity_manager`, `channel_manager`, `media_server`, `qt6_gui`, `diagnostic`).
- Do not break backward compatibility with existing persistent YAML configs in OS AppData.
- Use `ruff` for linting and formatting (standard library / high performance).

---

### Task 1: Prune Dead Code and Obsolete Files

**Files:**
- Delete: `legacy_2026_07/` (entire directory)
- Delete: `scratch/` (entire directory)
- Delete: `scratch_test_sink.py`
- Delete: `index.html` (stale root copy; `frontend/index.html` is the active frontend)
- Delete: `.codex` (empty 0-byte file)
- Delete: `docs/session_handoff-old.md`
- Modify: `distribute.sh` (convert to thin forwarding wrapper to `scripts/distribute.sh`)
- Modify: `distribute.ps1` (convert to thin forwarding wrapper to `scripts/distribute.ps1`)
- Modify: `scripts/get_githuburl.bat` (move from root if desired or keep clean)

**Interfaces:**
- Consumes: Existing files in root.
- Produces: Clean root repository layout without dead artifacts or duplicate scripts.

- [ ] **Step 1: Remove obsolete files and directories**
```bash
rm -rf legacy_2026_07/ scratch/ scratch_test_sink.py index.html .codex docs/session_handoff-old.md
```

- [x] **Step 2: Replace root distribute.sh and distribute.ps1 with thin wrappers**
```bash
# distribute.sh:
#!/usr/bin/env bash
exec bash "$(dirname "$0")/scripts/distribute.sh" "$@"
```
```powershell
# distribute.ps1:
& "$PSScriptRoot\scripts\distribute.ps1" @args
```

- [x] **Step 3: Verify no active code references the deleted files**
Search for references to `legacy_2026_07` and `scratch_test_sink.py` and confirm 0 occurrences.

---

### Task 2: Standardize pyproject.toml, .gitignore, and Tooling Configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `VERSION`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Current build metadata and test configurations.
- Produces: Unified versioning (2.0.0), `ruff` linter/formatter config, updated pyproject discovery, and clean `.gitignore`.

- [x] **Step 1: Update VERSION to match pyproject.toml**
Set `VERSION` to `2.0.0`.

- [x] **Step 2: Add ruff configuration and fix pyrefly paths in pyproject.toml**
Configure `[tool.ruff]` and `[tool.ruff.lint]` (select standard rules `E`, `F`, `W`, `I`, `UP`), fix search paths from `web-browser-head-unit` to `backend`.

- [x] **Step 3: Update .gitignore**
Ensure `.coverage`, `coverage.json`, `graphify-out/cache`, `dist/`, `build/`, `*.egg-info/`, `.pytest_cache/`, `logs/` are excluded.

---

### Task 3: Authoritative Documentation Overhaul (README.md & docs/)

**Files:**
- Modify: `README.md`
- Modify: `docs/SYSTEM_ARCHITECTURE.md`
- Modify: `docs/UI_ARCHITECTURE.md`
- Modify: `docs/TODO.md`
- Modify: `docs/codebase_guide.md`

**Interfaces:**
- Consumes: Current module implementations in `backend/modules/` and `frontend/`.
- Produces: Professional, accurate technical documentation describing the real system.

- [x] **Step 1: Rewrite README.md**
Comprehensive coverage:
1. System Architecture Diagram (Waves 0-5).
2. Wireless Android Auto Connection Flow (BT RFCOMM SDP -> WiFi AP handoff -> TCP 5288 SSL socket -> Protobuf Channels).
3. Dual Execution Modes (Multiprocessing with ZMQ vs Multithreading with InMemoryBusHub).
4. Media Pipelines: Zero-copy SHM video ring buffer, QOpenGLWidget viewport, WebCodecs browser viewport, AAC/PCM audio decoding with QAudioSink and PulseAudio fallback.
5. Hardware Abstraction Layer (HAL) for Linux & Windows.
6. Getting Started, Dev Environment, and CLI Options.
7. Production Deployment (Systemd unit, Kiosk mode, APManager service, Windows shortcut).
8. Development, Testing, and CI guide.

- [x] **Step 2: Harmonize docs/SYSTEM_ARCHITECTURE.md**
Update module diagram and table to reflect the current 10 modules (`bus_broker`, `config_manager`, `proxy`, `tcp_server`, `connectivity_manager`, `channel_manager`, `media_server`, `qt6_gui`, `diagnostic`, `_template`).

- [x] **Step 3: Harmonize docs/UI_ARCHITECTURE.md**
Update to document the single-process Qt6 GPU compositor architecture (`qt6_gui`) and Web Kiosk frontend (`frontend/`), removing obsolete X11 multi-window hacks.

- [x] **Step 4: Update docs/TODO.md and docs/codebase_guide.md**
Align task statuses and module paths.

---

### Task 4: GitHub Actions Enterprise CI Pipeline

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `.github/workflows/test-suite.yml`

**Interfaces:**
- Consumes: Pytest test suite, ruff configuration, micromamba environments.
- Produces: Automated push & pull_request CI checks on GitHub.

- [x] **Step 1: Create .github/workflows/ci.yml**
Define automated workflow triggered on `push` and `pull_request` (branches: `main`, `master`, `dev`):
- `lint`: Runs `ruff check` and `ruff format --check` on Python 3.13.
- `test-linux`: Sets up micromamba with `environment.yml`, installs system packages (libzmq, dbus), runs `pytest -m "unit or integration or e2e_smoke"` with coverage.
- `test-windows`: Matrix job running unit tests on `windows-latest` with Python 3.13 to verify cross-platform compliance.
- `package-check`: Validates package metadata and distribution scripts.

- [x] **Step 2: Verify .github/workflows/test-suite.yml compatibility**
Ensure `test-suite.yml` complements `ci.yml` without duplicate conflicting rules.

---

### Task 5: Verification & Knowledge Graph Synchronization

**Files:**
- Update knowledge graph via `graphify update .` or verify graph state.
- Run tests and schema verification.

- [x] **Step 1: Verify pyproject.toml and pytest syntax**
Validate that pytest and package configuration parse without error.

- [x] **Step 2: Verify repo hygiene**
Check git status and ensure no untracked junk files exist.

