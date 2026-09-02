# Diagnostic Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-platform multimedia diagnostic subsystem (`modules/diagnostic`) with REST API, WebSocket streams, Qt6 GUI drawer, and Web UI to validate audio (PCM, AAC, Mic, Loopback, Device switching) and video (HW decoder probes, transport re-encode benchmarks) directly through `media_server`.

**Architecture:** A new `diagnostic` backend module (`BaseBackendModule`, priority 5, `/api/diagnostic`) coordinates test suites by delegating hardware transport execution to `media_server`'s internal engines (`/api/media/diagnostic/*`), broadcasting live metrics over WebSockets to Qt6 GUI and Web frontends.

**Tech Stack:** Python 3.10+, PyQt6 / QAudioSink / QAudioSource, GStreamer / FFmpeg, ZMQ, asyncio, aiohttp, HTML5/JS.

**Spec:** `docs/superpowers/specs/2026-09-01-diagnostic-module-design.md`

## Global Constraints
- Cross-platform compliance for Linux (V4L2/VAAPI/PipeWire/PulseAudio) and Windows (WASAPI/D3D11/NVDEC).
- Full compatibility with both Multiprocess and Multithread execution modes via `BaseBackendModule` and `self.call_module()`.
- Zero hardcoded channel IDs or POSIX-only IPC addresses (`get_bus_address()` mandate).
- Reuses `media_server` internal transport and SHM pipelines rather than duplicating them.

---

### Task 1: `media_server` Diagnostic Endpoints

**Files:**
- Create: `backend/modules/media_server/diagnostic_routes.py`
- Modify: `backend/modules/media_server/main.py`
- Test: `tests/test_media_server_diagnostic.py`

**Interfaces:**
- Consumes: `media_server.transports.get_transport_class`, `media_server.shm.write_audio_frame()`, `media_server.audio_adapter`
- Produces:
  - `GET /api/media/diagnostic/capabilities` -> `{"sinks": [...], "sources": [...], "video_decoders": [...], "transports": [...]}`
  - `POST /api/media/diagnostic/audio/inject` -> `{"status": "ok", "samples_written": int}`
  - `POST /api/media/diagnostic/audio/set_device` -> `{"status": "ok", "active_sink": str}`
  - `POST /api/media/diagnostic/video/benchmark` -> `{"status": "ok", "fps": float, "latency_ms": float, "decoder": str}`

- [ ] **Step 1: Write the failing unit tests for media_server diagnostic routes**
- [ ] **Step 2: Run pytest to verify tests fail**
- [ ] **Step 3: Implement diagnostic route handlers in `backend/modules/media_server/diagnostic_routes.py` and register in `backend/modules/media_server/main.py`**
- [ ] **Step 4: Run pytest to verify tests pass**
- [ ] **Step 5: Commit**

---

### Task 2: Core `diagnostic` Module (`backend/modules/diagnostic/`)

**Files:**
- Create: `backend/modules/diagnostic/__init__.py`
- Create: `backend/modules/diagnostic/synthetic_media.py`
- Create: `backend/modules/diagnostic/main.py`
- Test: `tests/test_diagnostic_module.py`

**Interfaces:**
- Consumes: `shared.base_module.BaseBackendModule`, `shared.base_module.run_module`, `media_server` REST endpoints
- Produces:
  - `GET /api/diagnostic/status` -> `{"running": bool, "active_test": str}`
  - `POST /api/diagnostic/run` -> `{"status": "started", "test_id": str}`
  - `POST /api/diagnostic/stop` -> `{"status": "stopped"}`
  - `WS /api/diagnostic/ws` -> Live JSON metric events (`audio_level`, `video_frame`, `test_completed`)

- [ ] **Step 1: Write the failing unit tests for diagnostic module & synthetic media generator**
- [ ] **Step 2: Run pytest to verify tests fail**
- [ ] **Step 3: Implement synthetic media generators (PCM sine, reference AAC, SMPTE color bar H.264 NALs) in `synthetic_media.py`**
- [ ] **Step 4: Implement `DiagnosticModule(BaseBackendModule)` in `backend/modules/diagnostic/main.py`**
- [ ] **Step 5: Run pytest to verify tests pass**
- [ ] **Step 6: Commit**

---

### Task 3: Qt6 GUI Diagnostics Drawer

**Files:**
- Create: `backend/modules/qt6_gui/ui/drawers/diagnostics_drawer.py`
- Modify: `backend/modules/qt6_gui/ui/main_window.py`
- Modify: `backend/modules/qt6_gui/ui/command_bar.py`
- Modify: `backend/modules/qt6_gui/ui/drawers/settings_drawer.py`

**Interfaces:**
- Consumes: `/api/diagnostic/*` REST & WebSocket stream or Qt bridge
- Produces: Interactive slide-out drawer with Audio test triggers, Live VU meter, Device selectors, Video HW benchmark triggers, and stats readout.

- [ ] **Step 1: Create `backend/modules/qt6_gui/ui/drawers/diagnostics_drawer.py` with audio controls, VU meter, device selectors, video controls, and live metrics.**
- [ ] **Step 2: Connect Diagnostics Drawer to `main_window.py`, `command_bar.py`, and link from `settings_drawer.py`.**
- [ ] **Step 3: Test Qt widget instantiation and layout via automated unit test.**
- [ ] **Step 4: Commit**

---

### Task 4: Web Frontend Diagnostic Panel

**Files:**
- Create: `frontend/js/diagnostic.js`
- Create: `frontend/css/diagnostic.css`
- Modify: `frontend/index.html`
- Modify: `frontend/js/app.js`

**Interfaces:**
- Consumes: `/api/diagnostic/*` REST & WebSocket endpoint
- Produces: Modal/Tab in Web frontend for running audio & video diagnostics and displaying real-time FPS/latency/VU charts.

- [ ] **Step 1: Implement `frontend/js/diagnostic.js` and `frontend/css/diagnostic.css` handling WebSocket connection, test triggers, and VU meter rendering.**
- [ ] **Step 2: Add Diagnostics button and modal container in `frontend/index.html`.**
- [ ] **Step 3: Verify frontend script syntax and DOM binding.**
- [ ] **Step 4: Commit**

---

### Task 5: End-to-End Verification & Smoke Test

**Files:**
- Modify: `backend/main.py` (ensure priority discovery order)
- Modify: `web-browser-head-unit/README.md` (if applicable)

- [ ] **Step 1: Execute micromamba smoke test verifying clean boot of all modules including `diagnostic` (Priority 5).**
- [ ] **Step 2: Run all unit and integration tests.**
- [ ] **Step 3: Verify clean runtime logs.**
- [ ] **Step 4: Commit**
