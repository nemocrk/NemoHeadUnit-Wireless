# Phase 1: Shared Core Foundations Unit Tests Specification

**Date:** 2026-09-07  
**Status:** Approved  
**Scope:** `backend/shared/` core library modules (`ipc_utils.py`, `config_schema.py`, `config_client.py`, `touch_mapper.py`, `proto_utils.py`, `base_module.py`, `base_channel_module.py`).

---

## 1. Objectives & Quality Gates
- **High-Coverage Isolation**: Pure unit tests verifying contract guarantees without requiring real ZMQ daemons, physical audio/Bluetooth hardware, or external network connections.
- **Fast Execution**: Entire Phase 1 suite executes under 2 seconds.
- **Cross-Platform Proof**: Rigorous assertions covering Linux IPC (`ipc:///tmp/nemobus_v2.*`) and Windows TCP (`tcp://127.0.0.1:15000/15001`) addressing paths and data serialization.
- **Strict Protobuf Serialization**: Validate `proto_utils.py` encoding/decoding, timestamp prefix packing, SDR channel schema extraction, and AA frame wrapping.
- **Regression Detection**: Catch signature mismatches (e.g. `await self.publish()` vs sync `publish()`), boundary violations, and serialization corner-cases.

---

## 2. Directory Layout & Organization

All Phase 1 tests reside under `tests/unit/shared/` to mirror the repository layout:

```
tests/
├── conftest.py                             # Shared test fixtures (mock bus, fake clock)
└── unit/
    └── shared/
        ├── __init__.py
        ├── test_ipc_utils.py               # IPC URI generation & OS branching
        ├── test_config_schema.py           # Schema descriptors, validation, serialization
        ├── test_config_client.py           # ConfigClient local fallback & remote update handling
        ├── test_touch_mapper.py            # Aspect ratio, margins, letterbox coordinate mapping
        ├── test_proto_utils.py             # Protobuf encode/decode, timestamps, frame wrapping
        ├── test_base_module.py             # BaseBackendModule lifecycle, route registration, RPC
        └── test_base_channel_module.py     # BaseChannelModule frame dispatch & channel open/close
```

---

## 3. Subsystem Specifications

### 3.1 `tests/unit/shared/test_ipc_utils.py`
- Tests `get_bus_address(module_name, kind)`:
  - When `IS_WINDOWS=False`:
    - `kind="sub"` returns `"ipc:///tmp/nemobus_v2.pub"`
    - `kind="pub"` returns `"ipc:///tmp/nemobus_v2.sub"`
  - When `IS_WINDOWS=True` (simulated via monkeypatch):
    - `kind="sub"` returns `"tcp://127.0.0.1:15001"`
    - `kind="pub"` returns `"tcp://127.0.0.1:15000"`
  - Verifies default parameter values (`module_name="system"`, `kind="pub"`).

### 3.2 `tests/unit/shared/test_config_schema.py`
- Tests `ConfigFieldSchema`, `ConfigFieldMessage`, `ConfigFieldList`, `ConfigFieldOneof`:
  - `to_dict()` and `from_dict()` round-trip serialization.
  - `field_string`, `field_int`, `field_float`, `field_enum`, `field_bool` helper factories.
  - `validate_value()`:
    - Booleans: accepts `True`, `False`, strings `"true"`, `"1"`, `"yes"`, `"off"`, rejects invalid strings.
    - Integers: enforces min and max constraints, coerces numeric strings, raises `ValueError` on out-of-bounds or non-numeric.
    - Floats: enforces min and max constraints, coerces floats/ints.
    - Enums: validates choice presence, rejects unlisted options.
  - Oneof: raises `ValueError` if `active_branch` not in `branches`.

### 3.3 `tests/unit/shared/test_config_client.py`
- Tests `ConfigClient`:
  - Initialization: stores defaults, `config_data` matches defaults, `has_remote_config` is `False`.
  - `set_default_config()` updates defaults when `has_remote_config` is `False`, preserves remote when `True`.
  - `fetch_config()`: publishes `"config.get"` with module name, defaults, and schema to bus client.
  - `handle_config_response()`:
    - Ignores payloads intended for different modules (`payload["module"] != self.module_name`).
    - Merges remote configuration over default configuration.
    - Sets `has_remote_config = True`.
    - Triggers registered `on_update` callbacks.
    - Catches and logs callback exceptions without raising or crashing.

### 3.4 `tests/unit/shared/test_touch_mapper.py`
- Tests `TouchCoordinateMapper.map_coordinate`:
  - Direct 1:1 mapping when display resolution equals surface size.
  - `stretch_to_fill=True` scaling across different aspect ratios (e.g. 1920x1080 widget to 1280x720 AA).
  - Margin offsets: deducts `margin_width` and `margin_height`.
  - `stretch_to_fill=False` letterboxing (pillarbox and letterbox centering calculations).
  - Boundary clamping: coordinates never exceed `[0, negotiated_width]` and `[0, negotiated_height]`.

### 3.5 `tests/unit/shared/test_proto_utils.py`
- Tests `proto_utils.py` functions:
  - `encode_aa_frame` & `decode_aa_frame`:
    - Packs 2-byte big-endian message ID and payload into hex dictionary.
    - Unpacks raw bytes back into `(message_id, body)`.
    - Handles short/truncated frames (< 2 bytes) by returning `None`.
  - `parse_media_with_timestamp` & `build_media_with_timestamp`:
    - Symmetric packing of 8-byte big-endian microsecond timestamp and payload.
    - Round-trip exact identity verification.
    - Truncated payload (< 8 bytes) handling.
  - `channels_from_sdr_bytes` & `channel_config_from_sdr`:
    - Decoding synthetic SDR hex payloads into structured channel dictionaries.

### 3.6 `tests/unit/shared/test_base_module.py`
- Tests `BaseBackendModule`:
  - Lifecycle: `start()` calling `setup()`, `run()`, `teardown()`.
  - Route registration: `add_http_route` and `add_ws_route` with and without `path_prefix`.
  - Internal web server binding to dynamic loopback port (`127.0.0.1:0`).
  - Readiness announcements (`system.module_ready`, `system.ready`, `proxy.register_route`).
  - RPC calling via `call_module()` using mocked `module_registry` and `aiohttp`.
  - Config update propagation to `on_config_updated()`.

### 3.7 `tests/unit/shared/test_base_channel_module.py`
- Tests `BaseChannelModule`:
  - Subscribes to `aa.frame.ch<channel_id>`, `aa.channel.open`, `aa.channel.close`.
  - Frame dispatching: unpacks incoming frame, strips 2-byte message ID, and invokes `on_frame()`.
  - Channel open/close state transitions (`self.is_channel_open`, `self.channel_descriptor`).
  - Bug fix / verification: `send_frame()` publishes to `aa.frame.send` correctly without awaiting synchronous `publish()` (or aligning `publish` async signature).
