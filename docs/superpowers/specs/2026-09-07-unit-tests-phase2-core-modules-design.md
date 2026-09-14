# Phase 2: Core Infrastructure Modules (P0–P2) Unit Tests Specification

**Date:** 2026-09-07  
**Status:** Approved  
**Scope:** Priority 0–2 core backend modules (`backend/modules/bus_broker/`, `backend/modules/config_manager/`, `backend/modules/proxy/`).

---

## 1. Objectives & Quality Gates
- **High-Coverage Isolation**: Pure unit tests verifying contract guarantees without requiring live system daemons, real hardware, or open network interfaces.
- **Fast Execution**: Entire Phase 2 suite executes in under 2 seconds.
- **Cross-Platform Invariance**: Validate directory paths across POSIX (`~/.config/NemoHeadUnit-Wireless`) and Windows (`%APPDATA%/NemoHeadUnit-Wireless`), with `NEMO_CONFIG_DIR` override.
- **Strict Separation of Concerns**: Mock downstream microservices, aiohttp client sessions, and ZMQ sockets with `unittest.mock`.
- **Zero Regressions**: Existing 119 tests remain 100% green.

---

## 2. Directory Layout & Organization

All Phase 2 tests reside under `tests/unit/modules/`:

```
tests/
└── unit/
    └── modules/
        ├── __init__.py
        ├── test_bus_broker.py          # P0: BusBrokerModule, XSUB/XPUB routing, heartbeat
        ├── test_config_manager.py       # P1: ConfigManagerModule, YAML storage, schema validation, REST
        └── test_proxy.py                # P2: ProxyModule, dynamic route registration, reverse proxy
```

---

## 3. Subsystem Specifications

### 3.1 `tests/unit/modules/test_bus_broker.py`
- Tests `BusBrokerModule`:
  - Default configuration (`heartbeat_interval: 2.0`) and schema validation.
  - Sockets initialization: XSUB and XPUB socket binding to `get_bus_address()` URIs.
  - Event recording: `_on_module_event` captures `system.module_ready`, `system.ready`, and `proxy.register_route` into `bus_registry`.
  - Heartbeat broadcasting: executes `run()` loop step, verifying `system.heartbeat` publication containing `timestamp` and active `modules` dictionary.
  - Clean socket teardown on module stop.

### 3.2 `tests/unit/modules/test_config_manager.py`
- Tests `ConfigManagerModule`:
  - `get_user_config_dir`:
    - Reads `NEMO_CONFIG_DIR` when present in environment.
    - Resolves `%APPDATA%/NemoHeadUnit-Wireless` on Windows.
    - Resolves `~/.config/NemoHeadUnit-Wireless` on Linux.
  - YAML persistence (`_load_config`, `_save_config`):
    - Round-trip file read/write using temporary directories.
    - Corrupt file handling returns empty dictionary without raising.
  - ZMQ Bus handlers:
    - `on_config_get`: stores schema, merges file config over defaults, publishes `config.response`.
    - `on_config_set`: validates updates against schema, saves to disk, publishes `config.updated.{module}`.
  - REST API routes (aiohttp test client):
    - `GET /api/config/all`: lists all active schemas and configurations.
    - `GET /api/config/{module}`: returns module config or 404.
    - `POST /api/config/{module}`: updates keys with schema validation, returns 200 on success, 400 on invalid input.

### 3.3 `tests/unit/modules/test_proxy.py`
- Tests `ProxyModule`:
  - Default configuration (`public_port: 8000`, `host: "0.0.0.0"`) and schema.
  - Route registration:
    - `on_register_route`: maps `path_prefix` to internal `target_url` in `routes`.
    - `on_module_ready`: automatically registers route when `path_prefix` and `target_url` are present.
  - System discovery:
    - `GET /api/system/modules`: returns all registered modules, priorities, and WebSocket log stream paths.
  - Reverse proxy request forwarding:
    - `handle_proxy_request`: routes matching prefix to downstream module URL using `proxy_client_session`.
    - Returns 502 Bad Gateway when downstream service connection fails.
