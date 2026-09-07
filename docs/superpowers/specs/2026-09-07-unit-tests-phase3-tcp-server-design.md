# Architecture Design: Phase 3 — Android Auto Wire & Cryptography Unit Tests

## 1. Objective & Scope

Create comprehensive, isolated, high-speed unit tests for the Android Auto wire protocol, cryptographic state machine, framing relay, protobuf dispatcher, and TCP server orchestrator (`backend/modules/tcp_server/`).

### In Scope
- `modules/tcp_server/frame_codec.py`: Wire frame encoding (MessageType, EncryptionType, Bulk vs First/Middle/Last fragmentation) and `FrameAssembler` per-channel reassembly.
- `modules/tcp_server/aa_cryptor.py`: In-band TLS 1.2 client memory BIO cryptographic engine (`AACryptor`), TLS record header parsing, record splitting, and error state isolation.
- `modules/tcp_server/frame_relay.py`: Low-level TCP socket reader (`FrameRelay`), exact buffer reads, multi-frame header extraction, `send_raw` partial-write loops, and disconnection notifications.
- `modules/tcp_server/message_to_proto.py`: Bidirectional message ID to protobuf enum name lookup, proto name to class resolution, and `frame_data_to_dict` serialization with `MessageToDict`.
- `modules/tcp_server/main.py`: `TCPServerModule` configuration schema, REST API status endpoints, inbound frame decryption/reassembly/SHM dispatch, and outbound frame transmission.

### Out of Scope
- Real physical network sockets or live Android phones.
- Physical audio/video playback hardware.

---

## 2. Component Architecture & Verification Requirements

### 2.1 Frame Codec (`test_frame_codec.py`)
- **Header Structure**: `[channel_id: 1B][flags: 1B][length: 2B BE]`.
- **Message Type Policy**: `CONTROL` (0x04) used ONLY for `CHANNEL_OPEN_RESPONSE` (msgId 0x0008) on `channel_id != 0`. `SPECIFIC` (0x00) for all other frames.
- **Encryption Policy**: `ENCRYPTED` (0x08) when `ssl_active=True` and cryptor is active; `PLAIN` (0x00) otherwise. Downgrades gracefully if cryptor is missing or inactive.
- **Fragmentation**: Single frames <= 16384 bytes encoded as `BULK` (0x03). Payloads > 16384 bytes split into `FIRST` (0x01) with 4-byte `total_size` header, sequential `MIDDLE` (0x00) chunks, and final `LAST` (0x02) chunk.
- **Reassembly (`FrameAssembler`)**:
  - `BULK` frames returned immediately.
  - `FIRST` initiates channel buffer with declared total size.
  - `MIDDLE` appends to buffer. Orphan middle frames without pending first frame are dropped with warning.
  - `LAST` completes assembly and returns `(channel_id, flags, assembled_bytes, total_size)`.
  - `reset(channel_id)` selectively clears single or all channel assembly buffers.

### 2.2 In-Band Cryptor (`test_aa_cryptor.py`)
- **Initialization & Memory BIOs**: Configures `ssl.PROTOCOL_TLS_CLIENT`, TLS 1.2 min/max version, `check_hostname=False`, `verify_mode=CERT_NONE`, and loads embedded AA client certificate and RSA private key.
- **Handshake Sequence**:
  - `drive_handshake()` on fresh cryptor returns initial ClientHello TLS record (`0x16 0x03 0x03...`).
  - `write_handshake_input()` feeds bytes to input memory BIO.
  - `is_active()` returns True upon handshake completion.
- **Encryption & Decryption**:
  - Encrypting before handshake complete raises `RuntimeError`.
  - In active state, `encrypt()` produces ciphertext from memory BIO, and `decrypt()` recovers original plaintext.
  - `encrypt_records()` segments multi-record ciphertext streams by parsing individual 5-byte TLS record headers (`[type: 1B][ver: 2B][len: 2B BE]`).
  - `parse_tls_record_header()` accurately identifies record types (Handshake, ApplicationData, Alert, ChangeCipherSpec) and validates record lengths against total payload.

### 2.3 Frame Relay (`test_frame_relay.py`)
- **Socket Framing Read Loop**:
  - Correctly parses 4-byte header for BULK/MIDDLE/LAST frames.
  - Correctly parses 8-byte header for FIRST frames, extracting 4-byte total size field.
  - Reads exact payload length.
  - Invokes `on_frame_cb(channel_id, flags, payload, total_size)`.
- **EOF & Error Resilience**:
  - Clean socket EOF (`recv` returns `b""`) exits loop and invokes `on_closed_cb`.
  - Socket exceptions terminate loop safely without hanging.
  - `stop()` invokes `sock.shutdown(socket.SHUT_RDWR)` safely.
- **Transmission (`send_raw`)**:
  - Uses `memoryview` to handle partial socket writes until entire payload is sent.
  - Detects closed connection (`send` returns 0) and raises `BrokenPipeError`.

### 2.4 Message to Proto Mapping (`test_message_to_proto.py`)
- **Message ID Lookup**: Maps IDs across AV, Control, Bluetooth, Input, Sensor, and Wifi enum spaces. Returns `UnknownMessageId <id>` for unknown IDs.
- **Class Resolution**: Maps string names to generated Protobuf message classes (e.g. `CHANNEL_OPEN_REQUEST` -> `ChannelOpenRequest`, `SETUP_REQUEST` -> `AVChannelSetupRequest`). Raises `ValueError` for unknown names.
- **Frame Data Parsing (`frame_data_to_dict`)**:
  - Serializes hex payloads to structured dictionaries with `message_id`, `message_name`, and `payload_as_dict`.
  - Returns `raw_bytes` dictionary for bytes-only messages.
  - Gracefully falls back to raw hex on corrupt or invalid payloads without raising exceptions.

### 2.5 TCP Server Module Orchestration (`test_tcp_server_module.py`)
- **Config & Schema**: Validates default host (`0.0.0.0`), port (`5288`), autostart (`True`), and schema ranges.
- **REST Endpoints**:
  - `GET /api/tcp/status` returns operational telemetry (`server_running`, `client_address`, `tls_active`, `frames_received`, `frames_sent`).
  - `POST /api/tcp/restart` triggers session restart sequence.
- **Bus Event Routing**:
  - `on_frame_send`: Takes `{channel_id, message_id, payload_hex, encrypted}`, encodes via `frame_codec.encode()`, and writes to `FrameRelay`.
  - Inbound raw frame processing (`_on_raw_frame`): Handles TLS decryption, feeds `FrameAssembler`, inspects message ID, routes media frames to SHM buffer with `aa.frame.shm` publication, and routes control/input/sensor messages to `aa.frame.received` and `aa.frame.ch<N>`.
  - Handshake lifecycle events: `on_handshake_start_tls` and `on_handshake_feed_input` drive `AACryptor` and publish `tcp.server.tls_handshake_completed`.

---

## 3. Test Isolation & Mocking Strategy

1. **Zero Real Network Binding**: All TCP socket operations use `unittest.mock.MagicMock` or in-memory `socket.socketpair()`.
2. **Zero Real ZMQ Daemons**: `BusClient` is mocked via the autouse `mock_bus` fixture, preventing IPC socket creation.
3. **Deterministic Execution**: All tests execute in memory with execution time targeted under 2.0s for the entire Phase 3 suite.
4. **Cross-Platform Invariance**: Strict `pathlib.Path` compliance, no POSIX-only calls.
