# NemoHeadUnit-Wireless: Complete Codebase & File Guide

This document provides a exhaustive file-by-file breakdown of the **NemoHeadUnit-Wireless** codebase, explaining the role, implementation details, and interaction mechanics of every script, library, service, and module.

---

## 1. Orchestration & IPC Core

### [`main.py`](file:///home/nemo/NemoHeadUnit-Wireless/main.py)
* **Role**: Primary system entry point and lifecycle orchestrator.
* **How it works**:
  - Automatically discovers executable modules matching `modules/*/main.py` (excluding folders starting with `_`).
  - Spawns `bus_broker.py` as an independent subprocess.
  - Executes a multi-stage priority boot sequence ($P_0, P_1, \dots$): broadcasts `system.readytostart`, collects module priority responses, and emits `system.start` per level while awaiting `system.ready`.
  - Listens for `system.shutdown` or system signals (`SIGINT`, `SIGTERM`), broadcasts `system.stop`, waits for `channel_manager.stopped`, and force-kills non-responsive processes after a grace period.
  - Spawns a background thread responding to `system.get_modules` with live process health status.

### [`bus_broker.py`](file:///home/nemo/NemoHeadUnit-Wireless/bus_broker.py)
* **Role**: ZeroMQ IPC pub/sub message broker daemon.
* **How it works**:
  - Binds `XSUB` socket to `ipc:///tmp/nemobus_v2.sub` and `XPUB` socket to `ipc:///tmp/nemobus_v2.pub`.
  - Runs a ZeroMQ proxy loop forwarding published messages across all connected modules.

---

## 2. Shared Core Infrastructure (`shared/`)

### [`shared/bus_client.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/bus_client.py)
* **Role**: ZeroMQ bus client wrapper and P2P high-throughput IPC engine.
* **How it works**:
  - Wraps ZMQ `PUB` and `SUB` sockets with configurable High Water Marks (`BUS_HWM=5000`).
  - Implements separate P2P ZMQ IPC sockets (`ipc:///tmp/nemo_ui_frames.ipc` and `ipc:///tmp/nemo_logs.ipc`) to bypass the main bus for high-bandwidth video frame compositing and live log streams.
  - Automatically injects telemetry metadata (`_trace`) into JSON payloads on publish and strips it before passing to handlers.
  - Measures subscriber receive latency, detects packet drops, monitors callback execution duration, and flags sequence gaps or duplicate frames.

### [`shared/shm_helper.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/shm_helper.py)
* **Role**: POSIX Shared Memory double-buffering engine for offscreen UI rendering.
* **How it works**:
  - Implements `DoubleSharedBuffer` allocating fixed-size POSIX shared memory segments (`nemo_shm_{name}_buf_0`, `nemo_shm_{name}_buf_1`).
  - Uses raw `ctypes` pointers wrapped in PyQt `QImage` objects for true zero-copy UI compositing without buffer reallocation on window resize.
  - Employs lockless swap flags (`swap_buffer` $\leftrightarrow$ `swap_ack`) for frame synchronization.
  - Monkey-patches Python's `multiprocessing.resource_tracker` to prevent premature unlinking of shared memory segments when subprocesses terminate.

### [`shared/logger.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/logger.py) & [`shared/bus_trace.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/bus_trace.py)
* **Role**: Centralized logging and distributed performance tracing framework.
* **How it works**:
  - `logger.py` provides formatted standard output logging and attaches ZMQ `BusClient` handlers to emit structured `log.entry` messages to the bus.
  - `bus_trace.py` manages non-blocking telemetry data collection (`BusTracer`), aggregating network metrics and publishing periodic performance summaries to `system.bus_trace`.

### [`shared/config_schema.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/config_schema.py) & [`shared/config_client.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/config_client.py)
* **Role**: Dynamic configuration validation and runtime subscriber client.
* **How it works**:
  - `config_schema.py` defines strongly-typed configuration dataclasses (WiFi, Bluetooth, Audio, Video, UI settings) and exports JSON schema validation logic.
  - `config_client.py` allows modules to query `config_manager` via ZMQ, subscribe to configuration change events, and trigger dynamic runtime hot-reloads.

### [`shared/proto_utils.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/proto_utils.py) & [`shared/proto_explorer.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/proto_explorer.py)
* **Role**: Dynamic Protobuf wire decoder and schema inspector.
* **How it works**:
  - `proto_utils.py` parses binary Protobuf wire formats (Varints, Length-delimited, Fixed32/64) directly without requiring pre-compiled `.py` Protobuf schema files.
  - `proto_explorer.py` provides recursive JSON inspection tools for decoding unknown or dynamic Android Auto protocol payloads.

### [`shared/touch_widgets.py`](file:///home/nemo/NemoHeadUnit-Wireless/shared/touch_widgets.py)
* **Role**: Remote touch input event propagation widgets.
* **How it works**:
  - Captures user touch/mouse inputs on `ui_shell` window surfaces, translates normalized coordinates, and forwards synthetic Qt input events to offscreen widget surfaces over ZMQ/SHM.

---

## 3. AP Manager DBus Service (`services/ap_manager_service/`)

### [`services/ap_manager_service/ap_manager_service.py`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/ap_manager_service.py)
* **Role**: DBus system daemon managing Wi-Fi Access Point creation and dismantling.
* **How it works**:
  - Implements DBus service `org.nemo.APManager` on the Linux system bus using `dasbus` / `dbus-python`.
  - Executes low-level Linux networking commands (`hostapd`, `wpa_supplicant`, `nmcli`, `iw`, `ip`) to spawn 2.4GHz / 5GHz Wi-Fi APs for Android Auto.

### Daemon Security & Service Files:
* [`org.nemo.APManager.conf`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/org.nemo.APManager.conf): System DBus permission config allowing `root` ownership and unprivileged user method invocation.
* [`org.nemo.APManager.policy`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/org.nemo.APManager.policy): Polkit policy file declaring authorization privileges for network configuration.
* [`org.nemo.APManager.service`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/org.nemo.APManager.service) & [`org.nemo.APManager.dbus-service`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/org.nemo.APManager.dbus-service): Systemd and DBus service definition files.
* [`install.sh`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/install.sh): Installation script deploying policy and unit files to `/etc/dbus-1/system.d/` and systemd directories.
* [`tests/test_ap_manager_service.py`](file:///home/nemo/NemoHeadUnit-Wireless/services/ap_manager_service/tests/test_ap_manager_service.py): Automated mock unit tests for DBus service method execution.

---

## 4. Connectivity & Protocol Stack Modules (`modules/`)

### [`modules/bluetooth_manager/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/bluetooth_manager)
* **Files**: `main.py`, `bluez_adapter.py`, `discovery.py`, `pairing.py`, `paired_devices.py`
* **Role**: Manages Bluetooth adapter state and device pairing.
* **How it works**: Interacts with standard Linux BlueZ DBus interfaces (`org.bluez`) to control adapter power, initiate agent-assisted pairing, record paired phones, and advertise head unit Bluetooth profiles.

### [`modules/rfcomm_handshake/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/rfcomm_handshake)
* **Files**: `main.py`, `dbus_rfcomm.py`, `handshake.py`, `packet.py`
* **Role**: Executes Wireless Android Auto Bluetooth RFCOMM negotiation.
* **How it works**: Listens on Bluetooth RFCOMM channels, parses incoming Android Auto wireless handshake requests (`packet.py`), transmits local Wi-Fi AP credentials over RFCOMM, and triggers Wi-Fi AP startup.

### [`modules/hostapd_helper/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/hostapd_helper)
* **Files**: `main.py`
* **Role**: ZMQ-to-DBus bridge for Wi-Fi AP activation.
* **How it works**: Listens for ZMQ topic `wifi.ap.start` and calls DBus methods on `org.nemo.APManager` service to start or stop `hostapd`.

### [`modules/tcp_server/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/tcp_server)
* **Files**: `main.py`, `server.py`, `aa_cryptor.py`, `frame_codec.py`, `frame_relay.py`, `message_to_proto.py`
* **Role**: TCP transport and encryption engine for Android Auto link.
* **How it works**:
  - `server.py` opens a TCP socket server for phone connections.
  - `aa_cryptor.py` performs OpenSSL SSL/TLS handshake and encrypts/decrypts Android Auto wire payloads.
  - `frame_codec.py` packs and unpacks AA framing headers (channel ID, flags, payload length).
  - `frame_relay.py` relays demuxed channel frames to ZMQ bus topics (`aa.frame.<channel_id>`).

### [`modules/oaa_control_channel/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/oaa_control_channel)
* **Files**: `main.py`, `handshake.py`, `serializer.py`, `service_discovery.py`
* **Role**: Manages Android Auto Control Channel (Channel 0).
* **How it works**: Negotiates protocol versions, exchanges service discovery responses (declaring supported video resolutions, audio codecs, input capabilities), and manages session lifecycle messages.

### [`modules/channel_manager/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/channel_manager) & [`modules/channel_modules/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/channel_modules)
* **Role**: Manages individual Android Auto data channels.
* **How it works**:
  - `channel_manager/launcher.py` & `registry.py` spawn discrete process modules for each active channel.
  - `channel_modules/base_channel_module.py`: Abstract base class for channel processes.
  - Specialized channel modules: `video` (H.264 video decoding dispatch), `audio` (PulseAudio/ALSA media stream), `input` (touch/button telemetry back to phone), `sensor` (GPS/car sensors), `bluetooth`, `wifi`, `av_input`.

---

## 5. User Interface & Compositing Subsystem (`modules/`)

### [`modules/ui_shell/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/ui_shell/main.py)
* **Role**: Main window compositor and application container.
* **How it works**: Initializes PyQt6 main window UI shell, receives shared memory pointers from offscreen widget processes over ZMQ, and paints final composite frames using `DoubleSharedBuffer`.

### Offscreen UI Sub-Modules
* [`modules/navbar_ui/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/navbar_ui/main.py): Navigation bar UI widget (home, back, app launcher buttons).
* [`modules/config_ui/`](file:///home/nemo/NemoHeadUnit-Wireless/modules/config_ui) (`main.py`, `field_widgets.py`, `form_builder.py`, `list_editor.py`, `module_tab.py`): Configuration UI overlay for updating system parameters dynamically.
* [`modules/bluetooth_ui/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/bluetooth_ui/main.py): Bluetooth pairing and device management screen.
* [`modules/video_ui/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/video_ui/main.py): Video playback viewport surface.
* [`modules/floating_menu_ui/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/floating_menu_ui/main.py): Floating quick-settings widget.
* [`modules/log_viewer_ui/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/log_viewer_ui/main.py): Real-time bus log viewer widget.
* [`modules/audio_manager/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/audio_manager/main.py): Audio focus and volume manager module.
* [`modules/zmq_trace/main.py`](file:///home/nemo/NemoHeadUnit-Wireless/modules/zmq_trace/main.py): Interactive ZeroMQ performance monitoring widget.

---

## 6. Packaging & Hardware Integration (`packaging/`, `packaging_micromamba/`)

* [`packaging/build_deb.sh`](file:///home/nemo/NemoHeadUnit-Wireless/packaging/build_deb.sh) / [`packaging_micromamba/build_deb.sh`](file:///home/nemo/NemoHeadUnit-Wireless/packaging_micromamba/build_deb.sh): Builds Debian (`.deb`) installation packages for ARM64/x86_64 target systems.
* [`postinst`](file:///home/nemo/NemoHeadUnit-Wireless/packaging_micromamba/postinst) & [`prerm`](file:///home/nemo/NemoHeadUnit-Wireless/packaging_micromamba/prerm): Debian package installation hooks registering systemd services, udev rules, and permissions.
* [`nemo-headunit.sh`](file:///home/nemo/NemoHeadUnit-Wireless/packaging_micromamba/nemo-headunit.sh) & [`nemo-headunit.desktop`](file:///home/nemo/NemoHeadUnit-Wireless/packaging_micromamba/nemo-headunit.desktop): Desktop launcher and shell execution wrapper.
* [`hardware_fixes/`](file:///home/nemo/NemoHeadUnit-Wireless/packaging_micromamba/hardware_fixes): Hardware-specific quirk scripts (e.g., Omni10 hardware audio/video fixes).

---

## 7. Testing & Verification Suite (`tests/`)

* [`tests/unit/`](file:///home/nemo/NemoHeadUnit-Wireless/tests/unit): Comprehensive unit tests covering ZMQ client, bus broker, SHM helper, logger, proto dynamic decoder, and modules.
* [`tests/integration/`](file:///home/nemo/NemoHeadUnit-Wireless/tests/integration): Integration tests verifying priority boot/shutdown sequences, Bluetooth connection flows, audio focus, and video streaming pipelines under synthetic load.
* [`tests/fuzz/`](file:///home/nemo/NemoHeadUnit-Wireless/tests/fuzz): Property-based fuzzing tests (`test_aa_wire_format.py`, `test_proto_utils_roundtrip.py`, `test_bus_payload_malformed.py`) validating resilience against corrupt network frames.
