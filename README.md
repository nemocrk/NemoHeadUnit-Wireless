# NemoHeadUnit-Wireless

Modular, cross-platform web-based head unit architecture for Wireless Android Auto (Linux & Windows).

## Overview

`NemoHeadUnit-Wireless` is a refactored, microservices-based core for Wireless Android Auto. It replaces legacy monolithic PyQt UI scripts with process-isolated backend modules communicating over ZeroMQ (ZMQ) Pub/Sub and managed by a Priority Boot Orchestrator. HTTP and WebSocket routes are unified under a central Gateway Proxy (`proxy` module).

---

## Environment & Dependencies

This project relies on **micromamba** (or standard `conda`/`mamba`) for reproducible cross-platform Python environment management.

### Micromamba Environment Setup

```bash
# Clone or navigate to repo root
cd NemoHeadUnit-Wireless

# Create the virtual environment from environment.yml
micromamba create -f environment.yml -y

# Activate the environment
micromamba activate NemoHeadUnit-Wireless

# (Optional) Update dependencies if environment.yml changes:
micromamba env update -n NemoHeadUnit-Wireless -f environment.yml --prune
```

*Note: On Windows systems, you can also use `environment.windows.yml` located in the root repository if native Windows build toolchains are required.*

---

## Architecture & Priority Boot Waves

Backend modules inherit from `BaseBackendModule` and are launched in strictly ordered priority boot waves by `backend/main.py` (via root `main.py` entry point):

```
Priority 0: bus_broker
       │
       ▼
Priority 1: config_manager
       │
       ▼
Priority 2: proxy
       │
       ▼
Priority 3+: tcp_server, connectivity_manager
```

* **Priority 0 (`bus_broker`)**: Autonomous IPC message router operating on local defaults without external configuration dependencies. Manages heartbeat registry (`system.heartbeat`).
* **Priority 1 (`config_manager`)**: Central configuration engine storing YAML settings in OS AppData, validating strongly-typed module schemas, and exposing `/api/config`.
* **Priority 2 (`proxy`)**: Gateway Proxy webserver binding to the primary public port (`8000`) and dynamically routing `/api/<module_prefix>` to internal loopback microservices.
* **Priority 3+ (`tcp_server`, `connectivity_manager`, `channel_manager`, `media_server`)**: Functional domain microservices exposing hardware controls, sockets, channel logic, and media transports.
* **Priority 5 (`qt6_gui`)**: Native Qt6 Frontend Module using Shared Memory (SHM) zero-copy video/audio rendering and 16kHz microphone capture.

---

## Directory Structure

```
NemoHeadUnit-Wireless/
├── environment.yml           # Micromamba environment specification (NemoHeadUnit-Wireless)
├── environment.windows.yml   # Windows environment specification
├── README.md                 # Architecture & usage documentation
├── main.py                   # Root application entry point (delegates to backend/main.py)
├── backend/
│   ├── main.py               # Backend orchestrator (injects PYTHONPATH, boots module subprocesses in waves)
│   ├── shared/               # Core shared backend libraries
│   │   ├── base_module.py    # BaseBackendModule abstract class & run_module() entry launcher
│   │   ├── base_channel_module.py # BaseChannelModule abstract class for AA channel microservices
│   │   ├── nal_utils.py      # Binary H.264 NAL parsing & WebCodecs frame packing
│   │   ├── proto_utils.py    # AA frame serialization, SDR parsing & timestamp extraction
│   │   ├── config_client.py  # ConfigClient for ZMQ config fetching, schema transmission & hot-reloading
│   │   ├── config_schema.py  # Strongly-typed schema descriptors (field_string, field_int, field_bool, field_enum)
│   │   ├── ipc_utils.py      # Cross-platform ZMQ URI resolver (TCP loopback on Windows / POSIX IPC on Linux)
│   │   ├── logger.py         # Loguru console logging & WebSocket stream sink (ws://0.0.0.0:8766)
│   │   ├── bus_client.py     # Per-module ZMQ Pub/Sub IPC messaging client wrapper
│   │   ├── proto/            # Protobuf compiled Python classes (protos/oaa)
│   │   └── hardware/         # Cross-platform Hardware Adapter Layer (HAL)
│   └── modules/              # Process-isolated backend modules (ALL extending BaseBackendModule)
│       ├── bus_broker/       # Priority 0 core IPC router module (autonomous, manages sockets & system.heartbeat)
│       ├── config_manager/   # Priority 1 central config service (stores YAML in OS AppData, schema validation, REST API)
│       ├── proxy/            # Priority 2 Gateway Proxy module (exposed webserver on public port 8000)
│       ├── tcp_server/       # Priority 3 Wireless Android Auto TCP connection listener (port 5288) & SHM writer
│       ├── connectivity_manager/ # Priority 3 Bluetooth discovery/pairing & WiFi AP manager
│       ├── channel_manager/  # Priority 3 Consolidated channels (Control, Video, Audio PCM/AAC, Mic, Input, Sensor)
│       ├── media_server/     # Priority 4 Video transport decoder & SHM frame broadcaster
│       ├── qt6_gui/          # Priority 5 Native Qt6 Frontend Module (QOpenGLWidget, SHM, QAudioSink, QAudioSource)
│       └── _template/        # Template extending BaseBackendModule using run_module(SampleModule)
├── frontend/                 # Modern HTML5/CSS3/JS Web UI shell & client apps
├── scripts/                  # Developer tooling, deploy scripts, kiosk launchers
│   ├── launch_kiosk.sh       # Browser Kiosk Mode launcher script (Linux)
│   ├── launch_qt_kiosk.sh    # Native Qt6 Kiosk Mode launcher script (Linux)
│   ├── launch_qt_kiosk.bat   # Native Qt6 Kiosk Mode launcher script (Windows)


│   ├── bus_monitor.py        # Real-time ZMQ bus traffic monitor
│   ├── launch_kiosk.sh       # Linux kiosk launcher
│   └── launch_kiosk.bat      # Windows kiosk launcher
├── packaging/                # Debian packaging (micromamba-based), systemd service units, hardware fixes
├── services/                 # Platform-specific system background services
│   └── linux/                # Linux D-Bus APManager daemon service
│       └── ap_manager_service/
├── legacy_2026_07/           # Date-stamped legacy code archive
└── tests/                    # Unit test suite
```

---

## Hardware Abstraction Layer (HAL)

To guarantee universal cross-platform execution across Linux and Windows without hardcoded OS assumptions:
* **Bluetooth**: `connectivity_manager` attempts Linux BlueZ D-Bus initialization via `BlueZBluetoothAdapter`. If system D-Bus is unavailable, it gracefully falls back to `WindowsBluetoothAdapter` (supporting Windows Winsock `WSASetServiceW` SDP registration for Android Auto UUID `0000fcef-0000-1000-8000-00805f9b34fb` and mock loopback RFCOMM sockets for VM testing).
* **WiFi Access Point**: `connectivity_manager` attempts Linux APManager D-Bus initialization (`org.nemo.APManager`). If unavailable on Windows, it falls back to `WindowsWifiApAdapter` (direct WinRT `winrt.windows.networking` API or mock driver).

---

## System Background Services Setup

The Access Point Manager (`APManager`) operates as a system-level background daemon to manage WiFi hardware, softAP configuration, and networking interfaces without requiring root privileges for the main head unit backend process.

### Linux D-Bus APManager Daemon (`services/linux/ap_manager_service/`)

On Linux, the service exposes `org.nemo.APManager` on the system D-Bus and uses PolicyKit rules for unprivileged client access.

```bash
# 1. Install service as root (creates Unix group 'ap_manager', installs D-Bus policy, polkit rules, and systemd unit)
sudo bash services/linux/ap_manager_service/install.sh

# 2. Add your user to the 'ap_manager' Unix group
sudo usermod -aG ap_manager $USER

# 3. Log out and log back in for group membership to take effect

# Useful management commands:
sudo systemctl status org.nemo.APManager.service   # Check service status
journalctl -u org.nemo.APManager.service -f        # Stream live daemon logs
busctl introspect org.nemo.APManager /org/nemo/APManager # Inspect D-Bus methods
```

---

## Configuration Directory Standard

Persistent YAML configuration files are stored in the cross-platform OS standard user AppData directory (or resolved via `NEMO_CONFIG_DIR`):
* **Linux**: `~/.config/NemoHeadUnit-Wireless/`
* **Windows**: `%APPDATA%\NemoHeadUnit-Wireless\`

---

## Running the Application

### Launching Backend Subprocess Orchestrator

**On Linux (Bash):**
```bash
micromamba run -n NemoHeadUnit-Wireless python main.py
```

**On Windows (PowerShell):**
```powershell
micromamba run -n NemoHeadUnit-Wireless python main.py
```

---

### Primary Endpoints (via Gateway Proxy at http://127.0.0.1:8000):
* **`GET /api/config/`**: View system-wide configuration parameters and schema descriptors.
* **`GET /api/connectivity/status`**: Check Bluetooth discovery status, paired devices, and active WiFi AP state.
* **`POST /api/connectivity/discover`**: Trigger Bluetooth discovery scan.
* **`POST /api/connectivity/wifi/start`**: Programmatically initiate softAP hotspot.
* **`GET /api/tcp/status`**: Monitor active Wireless Android Auto TCP listener state.

---

## Packaging & Distribution

### 1. Debian Package Creation (`.deb`)

You can package the application into a self-contained Debian package targeting Linux distributions (Ubuntu/Debian) using `fpm`.

#### Prerequisites (Build Host)
* Ruby & FPM: `gem install fpm`
* `dpkg-deb` toolchain (`apt install dpkg`)

#### Build Command
```bash
# Build for amd64 architecture (default output in dist/)
bash packaging/build_deb.sh

# Cross-build metadata for arm64 target architecture
bash packaging/build_deb.sh --arch arm64 --output-dir dist
```

#### What the `.deb` Package Handles Automatically
* **Staging Layout**: Installs application files to `/opt/nemo-headunit/` (`main.py`, `backend/`, `frontend/`, `services/`, `hardware_fixes/`).
* **Environment Provisioning**: The `postinst` script automatically installs `micromamba` and creates the `NemoHeadUnit-Wireless` environment natively on the target hardware to prevent GLIBC/ABI version mismatches.
* **D-Bus & Systemd Integration**: Installs `org.nemo.APManager.service` for WiFi AP control and system policy rules.
* **Launcher Wrapper**: Installs `/usr/bin/nemo-headunit` and desktop entry shortcut.

#### Installing on Target Hardware
```bash
sudo dpkg -i dist/nemo-headunit_<version>_<arch>.deb
sudo apt-get install -f # Install system dependencies if missing
```

---

### 2. Automated Remote SSH Deployment

#### Automated Package Deployment (`scripts/deploy_remote_deb.sh`)
Builds the `.deb` package locally, transfers it over SSH to the target device, installs it via APT (automatically triggering platform hardware fixes), and launches the app in the background (`nohup`) while streaming live logs to your terminal:

```bash
# Build .deb locally, transfer, install via APT, and run on remote machine
bash scripts/deploy_remote_deb.sh nemo 192.168.1.50
```

#### Source Sync Deployment (`scripts/deploy_remote_micromamba.sh`)
For rapid dev/source syncing without building a `.deb`:
```bash
bash scripts/deploy_remote_micromamba.sh --sync-env nemo 192.168.1.50
```

---

### 3. Kiosk Launcher Tools

For production display deployment in head unit touchscreens:

* **Linux Kiosk Launcher (`scripts/launch_kiosk.sh`)**:
  ```bash
  # Launch Falkon kiosk browser (QtWebEngine with low memory footprint)
  bash scripts/launch_kiosk.sh

  # Launch with Chrome DevTools / remote debugging enabled
  bash scripts/launch_kiosk.sh --dev

  # Explicitly launch Falkon or Chromium
  bash scripts/launch_kiosk.sh --browser falkon
  ```
* **Windows Kiosk Launcher**:
  ```cmd
  scripts\launch_kiosk.bat http://localhost:8000
  ```
