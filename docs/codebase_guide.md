# NemoHeadUnit-Wireless: Complete Codebase & Module Guide

This document provides a comprehensive guide to the **NemoHeadUnit-Wireless** codebase, explaining the role, architecture, and interaction mechanics of every module, shared library, and service.

---

## 1. Orchestration & Core Entry Points

### [`main.py`](file:///home/nemo/NemoHeadUnit-Wireless/main.py)
* **Role**: Root entry point wrapper.
* **Mechanism**: Injects repository root into `PYTHONPATH`, parses CLI arguments (`--mode`, `--port`, `--headless`, `--config-dir`), and delegates execution to `backend/main.py`.

### [`backend/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/main.py)
* **Role**: Primary system lifecycle orchestrator and process/thread supervisor.
* **Mechanism**:
  - Discovers active backend modules in `backend/modules/*/main.py`.
  - In Multiprocessing mode (`--mode multiprocessing`), spawns each module as a separate OS subprocess.
  - In Multithreading mode (`--mode multithreading`), launches each module inside an isolated `threading.Thread`.
  - Coordinates the 6-wave priority boot sequence ($P_0 \to P_5$), sending `system.start` and collecting `system.ready`.
  - Supervises process health, restarts crashed critical modules if configured, and executes clean graceful shutdown on `SIGINT`/`SIGTERM`/`system.stop`.

---

## 2. Backend Modules (`backend/modules/`)

### Wave 0: [`backend/modules/bus_broker/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/bus_broker)
* **Priority**: 0
* **Role**: Autonomous IPC message router.
* **Mechanism**: Runs ZeroMQ XPUB/XSUB proxy thread, resolves cross-platform endpoints via `ipc_utils.py`, tracks `system.heartbeat` across all modules, and provides dynamic loopback HTTP target resolution.

### Wave 1: [`backend/modules/config_manager/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/config_manager)
* **Priority**: 1
* **Role**: Centralized configuration and settings engine.
* **Mechanism**: Persists YAML configuration in standard OS AppData directories (`~/.config/NemoHeadUnit-Wireless` on Linux, `%APPDATA%\NemoHeadUnit-Wireless` on Windows). Validates module schemas received on startup and exposes a REST API at `/api/config`.

### Wave 2: [`backend/modules/proxy/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/proxy)
* **Priority**: 2
* **Role**: Public gateway webserver and reverse proxy.
* **Mechanism**: Binds to public port `8000`. Serves static files for `frontend/` and reverse-proxies `/api/<module_prefix>` and WebSocket connections dynamically to internal microservices.

### Wave 3: [`backend/modules/tcp_server/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/tcp_server)
* **Priority**: 3
* **Role**: Wireless Android Auto TCP transport socket listener.
* **Mechanism**: Binds to port `5288`. Manages TLS/SSL socket handshakes with mobile devices, enforces message sequence numbering, and forwards decrypted byte streams to `channel_manager`.

### Wave 3: [`backend/modules/connectivity_manager/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/connectivity_manager)
* **Priority**: 3
* **Role**: Unified Bluetooth discovery/pairing and WiFi SoftAP lifecycle manager.
* **Mechanism**: Utilizes the Hardware Abstraction Layer (`backend/shared/hardware/`). Registers Android Auto SDP UUID `0000fcef-0000-1000-8000-00805f9b34fb`, performs RFCOMM security exchange, transfers WiFi AP credentials to the mobile phone, and triggers WiFi hotspot activation.

### Wave 3: [`backend/modules/channel_manager/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/channel_manager)
* **Priority**: 3
* **Role**: Android Auto logical channel demultiplexer and protocol engine.
* **Mechanism**: Implements OpenAndroidAuto protocol channels:
  - **Control Channel**: Version negotiation, ping/pong heartbeats, SSL key renegotiation.
  - **Video Channel**: H.264 video stream extraction, forwarding NAL packets to `media_server`.
  - **Audio Channels**: Media audio (AAC/PCM), speech guidance, and system sound demuxing.
  - **Input Channel**: Injects touchscreen coordinates and keypresses from UI modules to the phone.
  - **Sensor Channel**: GPS, speed, and night mode sensor feeds.

### Wave 4: [`backend/modules/media_server/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/media_server)
* **Priority**: 4
* **Role**: High-throughput media decoder and zero-copy frame buffer manager.
* **Mechanism**: Parses incoming H.264 NAL units, writes decoded video frames directly into OS Shared Memory (`/dev/shm/nemo_video_frame` or Windows memory maps), and streams WebCodecs frames over WebSocket for browser clients.

### Wave 5: [`backend/modules/qt6_gui/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/qt6_gui)
* **Priority**: 5
* **Role**: Native Qt6 automotive touch user interface.
* **Mechanism**:
  - `ui/video_viewport_gl.py`: Hardware-accelerated `QOpenGLWidget` reading SHM video directly for 60 FPS rendering.
  - `ui/drawers/`: Animated sliding overlay cards for Settings, Bluetooth, Phone (PBAP contacts), Diagnostics, and Logs.
  - `media/audio_output_handler.py`: High-fidelity audio playback via `QAudioSink`.
  - `ui/touch_mapper.py`: Normalizes display touch events and injects them into Android Auto.

### Wave 5: [`backend/modules/diagnostic/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/modules/diagnostic)
* **Priority**: 5
* **Role**: Live telemetry, bus tracing, and system health monitor.
* **Mechanism**: Collects heartbeat metrics, calculates transport latencies, tracks frame drops, and exposes `/api/diagnostic/health`.

---

## 3. Shared Libraries (`backend/shared/`)

* [`base_module.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/base_module.py): Abstract base class `BaseBackendModule` providing lifecycle methods, automatic `ConfigClient` registration, and HTTP/WS route declaration.
* [`bus_client.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/bus_client.py): Unified pub/sub facade selecting between `ZmqBusClient` and `InMemoryBusClient`.
* [`ipc_utils.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/ipc_utils.py): Cross-platform socket addressing helper (POSIX domain sockets on Linux, TCP loopback on Windows).
* [`config_client.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/config_client.py): Dynamic settings synchronization client.
* [`config_schema.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/config_schema.py): Strongly-typed schema descriptor fields (`field_string`, `field_int`, `field_bool`, `field_enum`).
* [`logger.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/logger.py): Non-blocking Loguru logger with WebSocket log streaming on port 8766.
* [`nal_utils.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/nal_utils.py): Binary H.264 NAL parsing and Annex B frame segmentation.
* [`proto_utils.py`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/proto_utils.py): Android Auto frame serialization and timestamp extraction.
* [`hardware/`](file:///home/nemo/NemoHeadUnit-Wireless/backend/shared/hardware/): Hardware Abstraction Layer with Linux BlueZ/APManager implementations, Windows Winsock/WinRT drivers, and mock adapters.

---

## 4. Frontend Web Shell (`frontend/`)

* [`index.html`](file:///home/nemo/NemoHeadUnit-Wireless/frontend/index.html): Clean HTML5 shell providing full-screen video canvas and 2x2 home dashboard.
* [`css/style.css`](file:///home/nemo/NemoHeadUnit-Wireless/frontend/css/style.css) & [`css/theme.css`](file:///home/nemo/NemoHeadUnit-Wireless/frontend/css/theme.css): Modern dark automotive UI styling with glassmorphism and animations.
* [`js/video_renderer.js`](file:///home/nemo/NemoHeadUnit-Wireless/frontend/js/video_renderer.js): Browser-native WebCodecs `VideoDecoder` consuming WebSocket H.264 NAL frames.
* [`js/audio_player.js`](file:///home/nemo/NemoHeadUnit-Wireless/frontend/js/audio_player.js): Web Audio API streaming player with drift compensation.

---

## 5. System Services & Packaging

* [`packaging/`](file:///home/nemo/NemoHeadUnit-Wireless/packaging): Debian (`build_deb.sh`) and Arch Linux (`build_arch.sh`) packaging scripts, systemd unit `nemo-kiosk.service`, and desktop shortcuts.
* [`services/linux/ap_manager_service/`](file:///home/nemo/NemoHeadUnit-Wireless/services/linux/ap_manager_service): Linux D-Bus APManager daemon for managing WiFi SoftAP interfaces without root privileges.
* [`scripts/distribute.sh`](file:///home/nemo/NemoHeadUnit-Wireless/scripts/distribute.sh) & [`scripts/distribute.ps1`](file:///home/nemo/NemoHeadUnit-Wireless/scripts/distribute.ps1): Cross-platform automated deployment engines for local and remote SSH targets.
