# Full System Integration Test Suite Design

## 1. Context & Objectives
Having completed unit test suites for Phases 1 through 6 (Shared libraries, Core modules, TCP server, Channel Manager, Connectivity & Hardware Adapters, and the Orchestrator), the project requires an automated, robust **Full System Integration Test Suite** (`tests/integration/`).

Key objectives:
- **Tier 1 Functional Pipeline Tests (In-Process / Threaded Loopback)**:
  - Validate the complete multi-module pipeline without external physical devices: `MockPhoneClient` connecting over TCP, negotiating protocol versions and service discovery, opening channels, streaming video (H.264 NAL parsing -> shared memory buffers) and audio (PCM streaming & focus), and handling reverse touch input and telemetry sensors.
  - Wire replay and fault injection testing via `PacketFixture`: golden frame comparison and malformed/truncated packet resilience.
- **Tier 2 Process Lifecycle & Headless GUI**:
  - Headless Qt6 integration test running under `QT_QPA_PLATFORM=offscreen` to verify widget state updates, command bar reaction, and video viewport texture ingestion.
  - Live subprocess orchestrator test spawning `backend/main.py -m multiprocessing` to verify priority wave boot sequence, external REST proxy access (`/api/config`), and graceful `SIGTERM` teardown.

---

## 2. Global Constraints & Architectural Invariants
1. **Target Execution Speed**:
   - Tier 1 functional pipeline tests must execute in **< 8.0 seconds** total.
   - Tier 2 (Qt6 offscreen + live subprocess) must execute in **< 15.0 seconds** total.
2. **Deterministic Isolation**:
   - Every test runs in an ephemeral sandbox with isolated `NEMO_CONFIG_DIR` pointing to a temporary directory.
   - Dynamic port allocation (`127.0.0.1:0`) and ephemeral ZMQ IPC endpoints prevent collisions with background processes or parallel tests.
   - No arbitrary `time.sleep()`. All event synchronization uses the async `BusMonitor` barrier (`wait_for_event`).
3. **Protobuf & Protocol Serialization Mandate**:
   - All message payloads exchanged between `MockPhoneClient` and the backend must use generated Protobuf message classes (`VersionRequest`, `ServiceDiscoveryRequest`, `ChannelOpenRequest`, `AudioFocusResponse`, `TouchEvent`, etc.).
   - Channel IDs must be dynamically mapped rather than hardcoded.
4. **Universal Cross-Platform Compliance**:
   - Paths handled using `pathlib.Path`, cross-platform loopback addressing, and graceful process signaling compliant with Linux and Windows.
5. **Strict Marker Requirement**:
   - Every integration test file must declare `pytestmark = pytest.mark.integration`.

---

## 3. Directory & Component Architecture

```
tests/integration/
├── conftest.py                       # Fixtures: isolated sandbox env, ephemeral ports, ZMQ event loop
├── harness/
│   ├── __init__.py
│   ├── environment.py                # Ephemeral AppData sandbox & dynamic port manager
│   ├── bus_monitor.py                # Async ZMQ event subscriber with awaitable event barriers
│   ├── mock_phone.py                 # Async simulated phone client (Protobuf handshake & channels)
│   └── packet_fixture.py             # Wire-level frame recorder, replayer, and fault injector
├── test_pipeline_handshake.py        # Protocol handshake, channel open/close, graceful disconnect
├── test_pipeline_media.py            # Video NAL streaming -> SHM ring buffer; Audio PCM & focus
├── test_pipeline_input_sensors.py    # Touch injection -> phone; Driving status & night mode sensors
├── test_qt6_offscreen.py             # Headless Qt6 GUI integration (offscreen video & widget state)
└── test_orchestrator_live.py         # Subprocess boot (multiprocessing), REST /api/config, SIGTERM exit
```

---

## 4. Test Specifications

### Component 1: Test Harness Core (`harness/`)
- `environment.py`: Creates isolated directories for config and shared memory, sets `NEMO_CONFIG_DIR`, generates default YAML configs with dynamic ports (`public_port: 0`, `tcp_server.port: 0`), and restores original environment on cleanup.
- `bus_monitor.py`: Connects an async SUB socket to the test `bus_broker` XPUB socket, subscribes to all topics (`""`), buffers events in an `asyncio.Queue`, and provides:
  - `async def wait_for_event(topic: str, filter_fn: Callable = None, timeout: float = 3.0) -> dict`
  - `def get_events(topic: str) -> list[dict]`
- `mock_phone.py`: Asynchronous TCP client:
  - Implements Android Auto wire framing (`channel_id`, `flags`, `length`, payload).
  - Handles protocol handshake: `VersionRequest` -> `VersionResponse`, `ServiceDiscoveryRequest` -> `ServiceDiscoveryResponse`, `ChannelOpenRequest` -> `ChannelOpenResponse`.
  - Exposes streaming helpers: `send_video_frame(nal_bytes)`, `send_audio_chunk(pcm_bytes)`, and `wait_for_input_event()`.
- `packet_fixture.py`:
  - Captures raw wire frames for golden comparisons.
  - Injects corrupted wire frames (truncated length header, garbage payload, out-of-order sequence).

### Component 2: Tier 1 Functional Pipeline Tests
- `test_pipeline_handshake.py`:
  - `test_handshake_and_channel_open`: Full handshake sequence between `MockPhoneClient` and backend; verifies `phone.connected` and `channel.opened` bus events.
  - `test_graceful_disconnect`: Phone disconnects TCP socket; verifies backend emits `phone.disconnected` and cleans up channel state.
  - `test_wire_fault_resilience`: Injects malformed frames; asserts `tcp_server` drops connection without crashing or memory leaks.
- `test_pipeline_media.py`:
  - `test_video_streaming_to_shm`: Mock phone transmits H.264 NAL units (SPS, PPS, IDR frame); asserts `video_handler` writes frame metadata and updates shared memory.
  - `test_audio_streaming_and_focus`: Mock phone streams PCM packets and requests audio focus; asserts `audio_handler` invokes audio sink and returns `AudioFocusResponse`.
- `test_pipeline_input_sensors.py`:
  - `test_touch_event_injection`: Mock UI emits `input.touch` event to ZMQ; asserts `channel_manager` serializes `TouchEvent` to `MockPhoneClient`.
  - `test_sensor_telemetry`: Emits `sensor.driving_status` and `sensor.night_mode` on ZMQ; asserts `MockPhoneClient` receives corresponding protobuf indications.

### Component 3: Tier 2 Process Lifecycle & Headless GUI
- `test_qt6_offscreen.py`:
  - Launches `qt6_gui` with `QT_QPA_PLATFORM=offscreen`.
  - Asserts dashboard phone card updates to "Connected" on `phone.connected`.
  - Simulates video frame ready event; asserts OpenGL video viewport widget executes texture upload without crashing.
- `test_orchestrator_live.py`:
  - Spawns `backend/main.py -m multiprocessing` as a subprocess.
  - Validates startup sequence: Wave 0 (`bus_broker`), Wave 1 (`config_manager`), Wave 2 (`proxy`), Wave 3+ (`tcp_server`, `channel_manager`).
  - Queries `http://127.0.0.1:<proxy_port>/api/config` via HTTP GET.
  - Sends `SIGTERM` to orchestrator; verifies all child processes terminate within 5 seconds without requiring `SIGKILL`.

---

## 5. Verification Strategy
- Run Tier 1: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_*.py -v`
- Run Tier 2: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_qt6_offscreen.py tests/integration/test_orchestrator_live.py -v`
- Full regression run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/ -m "unit or integration"`
- Smoke test: `micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py`
- Code graph update via `graphify`.
