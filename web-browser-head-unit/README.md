# Web Browser Head Unit

Modular web-based head unit architecture for Wireless Android Auto (Windows & Linux Cross-Platform).

## Environment & Dependencies

This project uses **micromamba** to manage dependencies via `environment.yml`.

### Setup Environment:
```bash
# Create the environment
micromamba create -f environment.yml -y

# Activate the environment
micromamba activate NemoHeadUnit-Wireless
```

## Directory Structure

```
web-browser-head-unit/
├── environment.yml           # Micromamba environment specification (NemoHeadUnit-Wireless)
├── backend/
│   ├── main.py               # Backend orchestrator (injects PYTHONPATH, boots module subprocesses in waves)
│   ├── shared/               # Core shared libraries
│   │   ├── base_module.py    # BaseBackendModule abstract class & run_module() entry launcher
│   │   ├── config_client.py  # ConfigClient for ZMQ config fetching, schema transmission & runtime hot-reloading
│   │   ├── config_schema.py  # Strongly-typed schema descriptors (field_string, field_int, field_bool, field_enum)
│   │   ├── ipc_utils.py      # Cross-platform Windows (TCP loopback) / Linux (POSIX IPC) ZMQ URI resolver
│   │   ├── logger.py         # Loguru console logging & WebSocket stream sink (conditional on client connection)
│   │   └── bus_client.py     # Per-module ZMQ Pub/Sub IPC messaging client wrapper
│   └── modules/              # Process-isolated backend modules (ALL extending BaseBackendModule)
│       ├── bus_broker/       # Priority 0 core IPC router module (autonomous, manages sockets & system.heartbeat)
│       ├── config_manager/   # Priority 1 central config service (stores YAML in OS AppData, schema validation, REST API)
│       ├── proxy/            # Priority 2 Gateway Proxy module (only exposed webserver on port 8000)
│       └── _template/        # Template extending BaseBackendModule using run_module(SampleModule)
├── frontend/                 # Modern HTML5/CSS3/JS Web UI shell & client apps
├── scripts/                  # Developer tooling, environment setups, run/debug scripts
└── packaging/                # Debian packaging, systemd service units, hardware fix scripts
```

## Configuration Directory Standard

Configuration files are stored in the OS standard user AppData directory (or overridden via `NEMO_CONFIG_DIR`):
- **Linux**: `~/.config/NemoHeadUnit-Wireless/`
- **Windows**: `%APPDATA%\NemoHeadUnit-Wireless\`
