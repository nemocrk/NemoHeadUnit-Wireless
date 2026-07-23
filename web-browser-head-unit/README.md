# Web Browser Head Unit

Modular, cross-platform web-based head unit architecture for Wireless Android Auto (Linux & Windows).

## Overview

`web-browser-head-unit` is the refactored, microservices-based core for NemoHeadUnit-Wireless. It replaces legacy monolithic PyQt UI scripts with process-isolated backend modules communicating over ZeroMQ (ZMQ) Pub/Sub and managed by a Priority Boot Orchestrator. HTTP and WebSocket routes are unified under a central Gateway Proxy (`proxy` module).

---

## Environment & Dependencies

This project relies on **micromamba** (or standard `conda`/`mamba`) for reproducible cross-platform Python environment management.

### Micromamba Environment Setup

```bash
# Navigate to web-browser-head-unit root
cd web-browser-head-unit

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

Backend modules inherit from `BaseBackendModule` and are launched in strictly ordered priority boot waves by `backend/main.py`:

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
* **Priority 3+ (`tcp_server`, `connectivity_manager`)**: Functional domain microservices exposing hardware controls, sockets, and channel logic.

---

## Directory Structure

```
web-browser-head-unit/
├── environment.yml           # Micromamba environment specification (NemoHeadUnit-Wireless)
├── README.md                 # Architecture & usage documentation
├── backend/
│   ├── main.py               # Backend orchestrator (injects PYTHONPATH, boots module subprocesses in waves)
│   ├── shared/               # Core shared backend libraries
│   │   ├── base_module.py    # BaseBackendModule abstract class & run_module() entry launcher
│   │   ├── base_channel_module.py # BaseChannelModule abstract class for AA channel microservices
│   │   ├── nal_utils.py      # Binary H.264 NAL parsing & WebCodecs frame packing (docs/new-pattern.md)
│   │   ├── proto_utils.py    # AA frame serialization, SDR parsing & timestamp extraction
│   │   ├── config_client.py  # ConfigClient for ZMQ config fetching, schema transmission & hot-reloading
│   │   ├── config_schema.py  # Strongly-typed schema descriptors (field_string, field_int, field_bool, field_enum)
│   │   ├── ipc_utils.py      # Cross-platform ZMQ URI resolver (TCP loopback on Windows / POSIX IPC on Linux)
│   │   ├── logger.py         # Loguru console logging & WebSocket stream sink (ws://0.0.0.0:8766)
│   │   ├── bus_client.py     # Per-module ZMQ Pub/Sub IPC messaging client wrapper
│   │   ├── proto/            # Protobuf compiled Python classes (protos/oaa)
│   │   └── hardware/         # Cross-platform Hardware Adapter Layer (HAL)
│   │       ├── base_adapter.py        # BaseBluetoothAdapter & BaseWifiApAdapter abstract interfaces
│   │       ├── bluez_bluetooth.py     # Linux BlueZ D-Bus Bluetooth driver
│   │       ├── windows_bluetooth.py   # Windows / Mock RFCOMM Bluetooth driver
│   │       ├── apmanager_wifi_ap.py   # Linux D-Bus APManager driver (org.nemo.APManager)
│   │       └── windows_wifi_ap.py     # Windows Mobile Hotspot & Mock WiFi AP driver
│   └── modules/              # Process-isolated backend modules (ALL extending BaseBackendModule)
│       ├── bus_broker/       # Priority 0 core IPC router module (autonomous, manages sockets & system.heartbeat)
│       ├── config_manager/   # Priority 1 central config service (stores YAML in OS AppData, schema validation, REST API)
│       ├── proxy/            # Priority 2 Gateway Proxy module (exposed webserver on public port 8000)
│       ├── tcp_server/       # Priority 3 Wireless Android Auto TCP connection listener (port 5288) & SHM writer
│       ├── connectivity_manager/ # Priority 3 Bluetooth discovery/pairing & WiFi AP manager
│       ├── channel_manager/  # Priority 3 Consolidated channels (Control, Video, Audio PCM/AAC, Mic, Input, Sensor)
│       └── _template/        # Template extending BaseBackendModule using run_module(SampleModule)
├── frontend/                 # Modern HTML5/CSS3/JS Web UI shell & client apps
│   ├── index.html            # Main UI shell & WebCodecs Canvas player
│   └── js/
│       └── webcodecs_player.js # WebCodecs VideoDecoder & DataView binary protocol parser
├── scripts/                  # Developer tooling, environment setups, run/debug scripts
│   ├── launch_kiosk.sh       # Linux kiosk launcher with Intel i965 VA-API acceleration
│   └── launch_kiosk.bat      # Windows kiosk launcher with MS Edge hardware acceleration
├── packaging/                # Debian packaging, systemd service units, hardware fix scripts
└── services/                 # Platform-specific system background services
    ├── linux/                # Linux D-Bus APManager daemon service
    │   └── ap_manager_service/
    └── windows/              # Windows LocalSystem UAC-bypass APManager background service
        └── ap_manager_service/
```

---

## Hardware Abstraction Layer (HAL)

To guarantee universal cross-platform execution across Linux and Windows without hardcoded OS assumptions:
* **Bluetooth**: `connectivity_manager` attempts Linux BlueZ D-Bus initialization via `BlueZBluetoothAdapter`. If system D-Bus is unavailable, it gracefully falls back to `WindowsBluetoothAdapter` (supporting Windows Winsock `WSASetServiceW` SDP registration for Android Auto UUID `0000fcef-0000-1000-8000-00805f9b34fb` and mock loopback RFCOMM sockets for VM testing).
* **WiFi Access Point**: `connectivity_manager` attempts Linux APManager D-Bus initialization (`org.nemo.APManager`). If unavailable, it falls back to `WindowsWifiApAdapter` (communicating with `nemo_ap_manager_win_service.py` background service on port `15288` or direct WinRT `winrt.windows.networking` API).

---

## System Background Services Setup

The Access Point Manager (`APManager`) operates as a system-level background daemon to manage WiFi hardware, softAP configuration, and networking interfaces without requiring root or UAC privileges for the main head unit backend process.

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

### Windows APManager Background Service (`services/windows/ap_manager_service/`)

On Windows, `connectivity_manager` communicates with `nemo_ap_manager_win_service.py` running on local TCP port `15288` to manage Windows Mobile Hotspot features bypassing UAC prompts.

```cmd
:: Run as Administrator to grant Mobile Hotspot control permissions:
python services\windows\ap_manager_service\nemo_ap_manager_win_service.py
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
micromamba run -n NemoHeadUnit-Wireless python3 web-browser-head-unit/backend/main.py
```

**On Windows (PowerShell):**
```powershell
micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py
```

---

### Primary Endpoints (via Gateway Proxy at http://127.0.0.1:8000):
* **`GET /api/config/`**: View system-wide configuration parameters and schema descriptors.
* **`GET /api/connectivity/status`**: Check Bluetooth discovery status, paired devices, and active WiFi AP state.
* **`POST /api/connectivity/discover`**: Trigger Bluetooth discovery scan.
* **`POST /api/connectivity/wifi/start`**: Programmatically initiate softAP hotspot.
* **`GET /api/tcp/status`**: Monitor active Wireless Android Auto TCP listener state.
