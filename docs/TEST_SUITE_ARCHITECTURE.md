# Test Suite Architecture — NemoHeadUnit-Wireless

This document defines the testing architecture, structure, and execution workflows for NemoHeadUnit-Wireless.

---

## 1. Core Principles

- **Root Alignment**: The test suite directory structure matches the repository layout (`tests/unit/modules/...` mirrors `modules/...`).
- **Strict Isolation**: Unit tests must not import or depend on sibling modules. Only the module under test and its mocks are loaded.
- **Parametric Hardware Mocks**: Any test case interacting with physical host hardware (e.g., sound cards, Bluetooth controllers) runs in dual configurations: one with mocks and one with physical hardware (skipped at runtime if the hardware is absent).
- **In-Process Broker**: Integration and E2E tests run a real in-process ZeroMQ XPUB/XSUB broker via the `in_process_broker` fixture to ensure precise message routing.
- **Fuzzing & Boundaries**: Property-based tests (`hypothesis`) generate boundary payloads, truncated headers, and corrupted streams to validate codec resilience.

---

## 2. Directory Layout

```
tests/
├── conftest.py                          # Global fixtures (fake_bus, qt_app, etc.)
├── pytest.ini                           # Test runner markers and configurations
│
├── unit/                                # Unit tests (isolated execution)
│   ├── shared/
│   │   ├── test_proto_utils.py
│   │   ├── test_logger.py
│   │   ├── test_bus_client.py
│   │   └── test_config_client.py
│   └── modules/
│       ├── channel_modules/
│       │   ├── test_base_channel_module.py
│       │   ├── audio/
│       │   │   └── test_audio.py
│       │   └── av_input/
│       │       └── test_av_input.py
│       ├── oaa_control_channel/
│       │   ├── test_handshake.py
│       │   ├── test_serializer.py
│       │   └── test_service_discovery.py
│       ├── channel_manager/
│       │   └── test_channel_manager.py
│       ├── tcp_server/
│       │   └── test_tcp_server.py
│       ├── audio_manager/
│       │   └── test_audio_manager.py
│       └── video_ui/
│           └── test_video_ui.py
│
├── integration/                          # Multi-module bus interaction tests
│   ├── test_bus_broker.py               # ZMQ routing under multi-client loads
│   ├── test_channel_lifecycle.py        # Module coordination & state changes
│   └── test_boot_shutdown.py            # Orderly orchestrator boot/shutdown
│
├── e2e/                                 # End-to-end integration sequences
│   ├── helpers/
│   │   ├── phone_mock.py                # Simulated Android Auto client (RFCOMM + TCP)
│   │   ├── frame_sequences.py           # Pre-packaged protocol wire frames
│   │   └── stack_launcher.py            # Threaded process stack orchestrator
│   ├── smoke/                           # Light E2E sanity checks (<30s)
│   │   └── test_bt_connect_to_handshake.py
│   └── full_session/                    # Comprehensive projection loops (>60s)
│       └── test_full_aa_session.py
│
├── performance/                         # Hardware profiling & benchmark runs
│   ├── test_bus_latency.py
│   ├── test_audio_latency.py
│   └── test_memory_rss.py
│
└── fuzz/                                # Property-based fuzzing (Hypothesis)
    ├── test_aa_wire_format.py
    └── test_bus_payload_malformed.py
```

---

## 3. Test Categories & Gates

### 3.1 Unit Tests (`@pytest.mark.unit`)
- **Focus**: Function-level contract validation.
- **Constraints**: Hardware interfaces (PulseAudio, BlueZ, GStreamer) must be fully stubbed.
- **Bus**: Uses the `fake_bus` fixture (in-process ZMQ routing on temporary IPC paths).
- **Target**: Execution speed $\le 1.0\text{ s}$ per test; minimum $80\%$ coverage boundary.

### 3.2 Integration Tests (`@pytest.mark.integration`)
- **Focus**: Inter-module event coordination.
- **Execution**: Modules are launched as background threads connected to an in-process ZMQ broker.
- **Target**: Validates shutdown sequences, configuration updates, and device-change announcements.

### 3.3 End-to-End Tests (`@pytest.mark.e2e`)
- **Smoke (`@pytest.mark.e2e_smoke`)**: Standard CI verification. Asserts that the stack successfully boots, processes Version Requests, and establishes the control channel within 30 seconds.
- **Full (`@pytest.mark.e2e_full`)**: Nightly/manual execution. Drives a mock Android Auto phone connection through pairing, AP connection, projection streaming, and graceful shutdown.

---

## 4. Key Fixtures (`tests/conftest.py`)

- `in_process_broker`: Starts a native ZeroMQ broker on unique IPC endpoints (`ipc:///tmp/nemotest-{uuid}.*`).
- `bus_client`: A standard `BusClient` connected to the active in-process test broker, exposing `wait_for` helper interfaces.
- `aa_frame_factory`: Generates wire-valid bytes (SDR, Channel Open, Media Indications) and malformed variants (truncated, overflowed) for channel tests.
- `qt_app`: Initializes a single session-scoped `QApplication` utilizing the `offscreen` platform context (`QT_QPA_PLATFORM=offscreen`), allowing UI widget tests to run on headless servers.

---

## 5. Execution Profiles (Makefile)

```makefile
# Run all fast unit tests with coverage enforcement
test-unit:
	pytest -m unit --cov=. --cov-report=term-missing --cov-fail-under=80

# Run multi-process integration tests
test-integration:
	pytest -m integration

# Run quick end-to-end smoke scenarios
test-e2e-smoke:
	pytest -m e2e_smoke

# Run performance benchmark tests
test-performance:
	pytest -m performance

# Run standard CI pipeline checks
test-ci:
	pytest -m "unit or integration or e2e_smoke" --cov=. --cov-fail-under=80
```
