# Architecture Design: Phase 4 — Channel Manager & Protocol Handlers Unit Tests

## 1. Objective & Scope

Create comprehensive, isolated, high-speed unit tests for the Android Auto Channel Manager orchestrator and all specialized sub-channel protocol handlers (`backend/modules/channel_manager/`).

### In Scope
- `modules/channel_manager/service_discovery.py`: Service Discovery Response (SDR) protobuf construction, descriptor classification (`classify_channel_descriptor`), codec overriding (`CODEC_ALIASES`), and Bluetooth MAC / WiFi BSSID parameter injection.
- `modules/channel_manager/main.py`: `ChannelManagerModule` lifecycle, configuration schema, dynamic channel registry (`set_channel_type_map`, `get_channel_id_for_type`), REST API endpoints (`/status`, `/stream_status`), WebSocket stream management, and event bus routing.
- `modules/channel_manager/handlers/control_handler.py`: Channel 0 protocol state machine (version negotiation, TLS trigger, SDR dispatch, channel open responses, ping/pong keepalives, audio/navigation focus grants, shutdown requests).
- `modules/channel_manager/handlers/video_handler.py` & `audio_handler.py`: AV channels setup negotiation, media codec extraction, start/stop indications, focus state transitions, zero-copy SHM buffer dispatch, and batch `AVMediaAckIndication` windowing.
- `modules/channel_manager/handlers/` (Auxiliary): Touch and media key event injection (`input_handler`), vehicle telemetry sensor requests (`sensor_handler`), Bluetooth pairing and auth handshakes (`bluetooth_handler`), WiFi security credential exchanges (`wifi_handler`), and navigation turn event extraction (`navigation_handler`).

### Out of Scope
- Real physical display, video decoder hardware, or audio output sinks.
- Real Bluetooth HCI controllers or WiFi interfaces.

---

## 2. Component Architecture & Verification Requirements

### 2.1 Service Discovery & Descriptor Classification (`test_service_discovery.py`)
- **Channel Classification**:
  - Validates classification of every descriptor dictionary into its corresponding `ChannelType` enum (`INPUT`, `SENSOR`, `VIDEO`, `AUDIO`, `AUDIO_MIC`, `BLUETOOTH`, `WIFI`, `NAVIGATION`, `MEDIA_PLAYBACK`, `PHONE_STATUS`, `NOTIFICATION`, `UNKNOWN`).
  - Correctly distinguishes between `VIDEO` and `AUDIO` within `av_channel` based on the presence of `video_configs` or `stream_type == "VIDEO"`.
- **SDR Construction (`build_service_discovery_response`)**:
  - Merges caller configuration with `SEMANTIC_DEFAULTS`.
  - Injects dynamic `bt_mac` and `wifi_bssid` into corresponding channel descriptors.
  - Applies global codec overrides via `CODEC_ALIASES` (e.g. `"H265"` -> `"MEDIA_CODEC_VIDEO_H265"`, `"AAC"` -> `"MEDIA_CODEC_AUDIO_AAC_LC"`).
  - Returns valid binary protobuf bytes, parsed dictionary representation, and `channel_type_map` mapping channel IDs to channel type names.

### 2.2 Channel Manager Module Orchestration (`test_channel_manager_module.py`)
- **Config & Schema**:
  - Verifies default semantic configuration (`head_unit_name`, `channels` list, `autoclose_on_shutdown`).
  - Verifies schema validation descriptor returned by `get_schema()`.
- **Dynamic Channel Registry**:
  - `set_channel_type_map` stores dynamic channel ID mappings from SDR.
  - `get_channel_type(channel_id)` resolves mapped type or `ChannelType.CONTROL` for channel 0.
  - `get_channel_id_for_type(target_type)` dynamically resolves the correct channel ID without hardcoding, using fallback defaults when unmapped.
  - `on_config_updated` pre-populates `channel_type_map` and `active_channels`.
- **REST & SSE Endpoints**:
  - `GET /api/channels/status` returns operational metadata, active channels, and connected client count.
  - `GET /api/channels/stream_status` formats Server-Sent Events (SSE) stream packets based on `current_stage_index` and handler states.
- **Bus Event Dispatch**:
  - `on_tcp_session_connected` triggers `VERSION_REQUEST` transmission on channel 0.
  - `on_tls_handshake_completed` transmits `AUTH_COMPLETE` indication to phone on channel 0.
  - `on_frame_shm` dispatches video and audio frames to `video_handler` and `audio_handler`.
  - `on_frame_received` dispatches non-media frames to specialized channel handlers.

### 2.3 Control Channel Protocol Handler (`test_control_handler.py`)
- **Version Negotiation**:
  - `_handle_version_request` responds with `VERSION_RESPONSE` (Major=1, Minor=1).
  - `_handle_version_response` sets `tls_started = True` and publishes `aa.handshake.start_tls`.
- **Handshake & Discovery**:
  - `_handle_ssl_handshake` forwards payload to `aa.handshake.feed_input`.
  - `_handle_service_discovery_request` builds and sends `SERVICE_DISCOVERY_RESPONSE`, updates channel map, and publishes `aa.sdr.channels`.
- **Channel Management & Keepalives**:
  - `_handle_channel_open_request` responds with `ChannelOpenResponse(status=OK)`.
  - `_handle_ping_request` extracts timestamp and returns matching `PingResponse(timestamp=...)`.
- **Audio & Navigation Focus**:
  - `_handle_audio_focus_request` maps focus types (`GAIN`, `GAIN_TRANSIENT`, `RELEASE`) to granted response states and publishes `media.audio.focus`.
  - `_handle_navigation_focus_request` returns `NavigationFocusResponse(PROJECTED)`.
- **Session Control**:
  - `_handle_shutdown_request` returns `SHUTDOWN_RESPONSE`.

### 2.4 AV Channels Handlers (`test_av_handlers.py`)
- **Video Channel Handler**:
  - `_handle_channel_open_request` sends `ChannelOpenResponse(OK)`.
  - `_handle_setup_request` parses `AVChannelSetupRequest`, updates active codec in stream config, returns `AVChannelSetupResponse(OK, max_unacked=10)`, and triggers video focus.
  - `_handle_start_indication` records `session_id` and publishes `video.stream_start`.
  - `_handle_stop_indication` flushes unacked frames and publishes `video.stream_stop`.
  - `process_shm_frame` publishes zero-copy NAL pointer `media.video.raw_nal_shm`, increments frame count, and emits `AVMediaAckIndication` when `unacked_frames >= 10`.
  - `send_focus_indication` formats and sends `VideoFocusIndication(PROJECTED / NATIVE)`.
- **Audio Channel Handler**:
  - `_handle_channel_open_request` sends `ChannelOpenResponse(OK)`.
  - `_handle_setup_request` parses audio configuration, publishes `media.audio.channel_configured`, and returns `AVChannelSetupResponse(OK)`.
  - `_handle_start_indication` / `_handle_stop_indication` publish `media.audio.stream_status` (`ACTIVE` / `STOPPED`).
  - `_handle_focus_request` grants audio focus and emits `media.audio.focus`.
  - `process_shm_frame` publishes `media.audio.frame_shm`, packs media frames for connected WebSocket clients, and emits batch ACKs every 10 frames.

### 2.5 Auxiliary Channels Handlers (`test_auxiliary_handlers.py`)
- **Input Channel Handler**:
  - `_handle_binding_request` returns `InputBindingResponse(OK)`.
  - `handle_touch_event` encodes `InputEventIndication` containing `TouchEvent` and `TouchLocation` coordinates and sends to touch channel.
  - `handle_media_key` encodes `InputEventIndication` with `ButtonEvents` keycode and pressed state.
- **Sensor Channel Handler**:
  - `_handle_channel_open_request` sends `ChannelOpenResponse(OK)`.
  - `_handle_sensor_start_request` sends `SensorStartResponseMessage(OK)` and matching `SensorEventIndication` (driving status, night mode, or parking brake).
- **Bluetooth Channel Handler**:
  - `_handle_pairing_request` sends `BluetoothPairingResponse(already_paired=True)`.
  - `_handle_auth_data` sends `BluetoothAuthenticationResult(OK)`.
- **WiFi Channel Handler**:
  - `_handle_credentials_request` sends `WifiCredentialsResponse` with configured SSID, passphrase, and WPA2 mode.
- **Navigation Channel Handler**:
  - `handle_frame` on `NavigationTurnEvent` updates active maneuver, distance, and road name, emitting `navigation.turn_event`.

---

## 3. Test Isolation & Mocking Strategy

1. **Zero Real Network or Shared Memory Allocations**:
   - `BidirectionalMediaSHM` is mocked to prevent shared memory allocations.
   - Sockets and WebSocket responses use `unittest.mock.MagicMock` and `AsyncMock`.
2. **Deterministic Execution**:
   - Tests execute in-memory with total execution time < 2.0s for the Phase 4 suite.
3. **No Hardcoded Channel IDs**:
   - Tests adhere to the strict prohibition of hardcoded channel IDs, dynamically resolving them via `manager.get_channel_id_for_type(...)` or configuration maps.
