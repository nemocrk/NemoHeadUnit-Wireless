# Phase 5: Connectivity Manager & Hardware Adapters Unit Test Suite Design

## 1. Context & Objectives
Phase 5 of the comprehensive unit test suite focuses on the wireless connectivity stack and hardware abstraction layers:
- **RFCOMM Packet Framing & Codec (`backend/modules/connectivity_manager/packet.py`)**: Wire-level encoding, decoding, header boundary checking, and socket streaming.
- **RFCOMM Wireless Handshake (`backend/modules/connectivity_manager/handshake.py`)**: Android Auto wireless handshake state machine (`WifiStartRequest`, `WifiInfoRequest`, `WifiInfoResponse`, `WifiConnectionStatus`, credentials distribution).
- **Hardware Adapters & Factories (`backend/shared/hardware/`)**: Abstract interfaces (`BaseAudioAdapter`, `BaseBluetoothAdapter`, `BaseWifiApAdapter`), fallback mock implementations (`MockAudioAdapter`), and OS factory selectors (`get_audio_adapter`, `get_bluetooth_adapter`, `get_wifi_adapter`).
- **Connectivity Manager Orchestration & REST Endpoints (`backend/modules/connectivity_manager/main.py`)**: Core module lifecycle, REST endpoints (`/status`, `/paired`, `/discovered`, `/discover`, `/pair`, `/connect`, `/disconnect`, `/wifi/start`, `/wifi/stop`), device filtering, autoconnect priority queue, RFCOMM dispatch, and Bluetooth telephony/telemetry callbacks.

---

## 2. Global Constraints & Architectural Invariants
1. **Target Execution Speed**:
   The entire Phase 5 unit test suite must execute in **< 2.0 seconds** total.
2. **Strict Isolation**:
   No real Bluetooth HCI sockets, no real BlueZ DBus daemons, no real RFCOMM ports, no real hostapd/APManager interfaces, and no real audio hardware or ZMQ bus brokers. All external sockets and hardware calls must be mocked (`unittest.mock.MagicMock`, `AsyncMock`).
3. **Universal Cross-Platform Compliance**:
   All tests and mocks must run identically on Linux and Windows platforms without relying on POSIX-only signals or OS-specific paths.
4. **Protobuf Serialization Mandate**:
   Payloads passed across RFCOMM (`WifiStartRequest`, `WifiSecurityResponse`, `WifiConnectStatus`) must use generated Protobuf classes and enums.
5. **Dynamic Channel & Device Mapping**:
   Tests must respect dynamic device addresses, avoid hardcoded MACs, and verify proper state propagation.
6. **Strict Marker Requirement**:
   Every test file must declare `pytestmark = pytest.mark.unit`.

---

## 3. Component Architecture & Test Breakdown

### Component 1: RFCOMM Wire Framing & Packet Codec (`packet.py`)
- Target: `backend/modules/connectivity_manager/packet.py`
- Test File: `tests/unit/connectivity_manager/test_packet.py`
- Scope:
  - `encode(msg_id, payload)`: Big-endian packing of `(len, msg_id)`, empty payloads, arbitrary byte payloads.
  - `decode(data)`: Header size checks (< 4 bytes), payload truncation detection, successful Packet extraction.
  - `_recv_exact(sock, n)`: Multi-chunk streaming over socket, EOF handling when socket closes unexpectedly.
  - `recv_packet(sock)` and `send_packet(sock, msg_id, payload)`: Complete packet transmission, socket error trapping.

### Component 2: RFCOMM Handshake State Machine (`handshake.py`)
- Target: `backend/modules/connectivity_manager/handshake.py`
- Test File: `tests/unit/connectivity_manager/test_handshake.py`
- Scope:
  - `RfcommHandshake.run()` Happy Path: Proactive `WifiStartRequest` (msg 1), optional `WifiStartResponse` ack (msg 6), `WifiInfoRequest` (msg 2) triggering `WifiInfoResponse` (msg 3) with WPA2 credentials, and `WifiConnectionStatus` (msg 7) returning `HandshakeResult(success=True, phone_ip=...)`.
  - Error Handling: Socket timeout, connection reset, failed start request, failed info response, unexpected message loop exhaustion (> 20 messages).
  - Security & AP Configuration Normalization: Validation and fallback for security mode and access point type.

### Component 3: Hardware Adapters & Factories (`shared/hardware/`)
- Target: `backend/shared/hardware/` (`base_audio.py`, `mock_audio.py`, `base_bluetooth.py`, `base_wifi_ap.py`)
- Test File: `tests/unit/hardware/test_hardware_adapters.py`
- Scope:
  - `MockAudioAdapter`: Volume clamping (0-100), `volume_up`, `volume_down`, `toggle_mute`, sink/source listing and selection, HFP bidirectional loopback simulation.
  - Factory Platform Resolution: `get_audio_adapter()` selecting Windows, Linux, or Mock adapter; `get_bluetooth_adapter()` and `get_wifi_adapter()` graceful fallback on platform or driver load exceptions.

### Component 4: Connectivity Manager Module Lifecycle & REST APIs (`main.py`)
- Target: `backend/modules/connectivity_manager/main.py`
- Test File: `tests/unit/connectivity_manager/test_connectivity_manager_module.py`
- Scope:
  - Config & Schema validation: `get_default_config()` and `get_schema()`.
  - REST Endpoints:
    - `GET /status`: reports adapter state, discoverability, active device, and WiFi status.
    - `GET /paired`: lists paired devices from adapter.
    - `GET /discovered` & `POST /discover`: triggers active Bluetooth discovery.
    - `POST /pair`, `POST /pair/confirm`, `POST /pair/reject`: PIN pairing workflows.
    - `POST /connect` & `POST /disconnect`: profile connection management.
    - `POST /wifi/start` & `POST /wifi/stop`: manual AP control.
    - `POST /devices/ignore` & `POST /devices/unignore`: blacklist device management.

### Component 5: Autoconnect Loop & Event Handlers (`main.py`)
- Target: `backend/modules/connectivity_manager/main.py`
- Test File: `tests/unit/connectivity_manager/test_connectivity_events.py`
- Scope:
  - `_autoconnect_loop`: Priority ordering (known AA devices first, ignored devices skipped, connected devices skipped), round-robin cursor, exponential backoff, and immediate wake-up via `on_try_autoconnect`.
  - RFCOMM Connection Callback (`_on_rfcomm_connection` & `_start_ap_and_handshake`): Accepted socket handling, AP launching, credentials preparation, thread execution, failure handling.
  - Telemetry & Telephony Callbacks: `_on_hfp_state_changed` triggering `phone.status` and audio loopback; `_on_bluetooth_telemetry_changed` formatting battery and signal indicators.

---

## 4. Verification & Gate Strategy
- Direct execution of individual test files.
- Aggregate execution of `tests/unit/connectivity_manager/` and `tests/unit/hardware/`.
- Full repository unit test suite verification.
- Backend orchestrator smoke test.
- Code graph AST update via `graphify`.
