# Phase 1: Shared Core Foundations Unit Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement isolated, fast, robust unit tests for all core shared foundation modules in `backend/shared/` without requiring external hardware or daemons.

**Architecture:** Create a mirrored `tests/unit/shared/` test suite with shared fixtures in `tests/conftest.py`. Test cases cover pure functions, schema validation, configuration fallback/events, touch projection, frame encoding/decoding, and base class lifecycles using pytest and unittest.mock.

**Tech Stack:** Python 3.13+, pytest, pytest-asyncio, aiohttp, pyzmq, unittest.mock, protobuf.

**Spec:** `docs/superpowers/specs/2026-09-07-unit-tests-phase1-shared-design.md`

## Global Constraints

- Must run cleanly inside the `NemoHeadUnit-Wireless` micromamba environment.
- Strict isolation: unit tests must never require real ZMQ daemons, real hardware (Bluetooth/Audio/GStreamer), or live network sockets.
- Universal cross-platform compatibility: all tests and mocks must succeed on both Linux and Windows.
- Target execution time: Phase 1 suite must execute in under 2 seconds total.
- No hardcoded channel IDs or raw magic byte arrays where Protobuf definitions or `proto_utils` helpers exist.

---

### Task 1: Test Shared Fixtures & IPC Address Resolution

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/shared/__init__.py`
- Create: `tests/unit/shared/test_ipc_utils.py`
- Test: `tests/unit/shared/test_ipc_utils.py`

**Interfaces:**
- Consumes: `backend.shared.ipc_utils.get_bus_address(module_name: str, kind: str) -> str`
- Produces: `mock_bus` fixture in `tests/conftest.py` for subsequent tasks

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/shared/test_ipc_utils.py
import pytest
from shared.ipc_utils import get_bus_address, PUB_PORT, SUB_PORT


def test_ipc_utils_linux_endpoints(monkeypatch):
    monkeypatch.setattr("shared.ipc_utils.IS_WINDOWS", False)
    assert get_bus_address(kind="pub") == "ipc:///tmp/nemobus_v2.sub"
    assert get_bus_address(kind="sub") == "ipc:///tmp/nemobus_v2.pub"


def test_ipc_utils_windows_endpoints(monkeypatch):
    monkeypatch.setattr("shared.ipc_utils.IS_WINDOWS", True)
    assert get_bus_address(kind="pub") == f"tcp://127.0.0.1:{PUB_PORT}"
    assert get_bus_address(kind="sub") == f"tcp://127.0.0.1:{SUB_PORT}"


def test_ipc_utils_default_arguments(monkeypatch):
    monkeypatch.setattr("shared.ipc_utils.IS_WINDOWS", False)
    assert get_bus_address() == "ipc:///tmp/nemobus_v2.sub"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_ipc_utils.py -v`
Expected: FAIL (file not found or package structure error)

- [ ] **Step 3: Create package structure and test file**

Create `tests/unit/__init__.py`, `tests/unit/shared/__init__.py`, `tests/conftest.py` with `mock_bus` fixture:

```python
# tests/conftest.py
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.subscriptions = {}

    def _subscribe(topic, cb):
        bus.subscriptions[topic] = cb

    bus.subscribe.side_effect = _subscribe
    return bus
```

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_ipc_utils.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/unit/__init__.py tests/unit/shared/__init__.py tests/unit/shared/test_ipc_utils.py
git commit -m "test(unit): add shared fixtures and ipc_utils unit tests"
```

---

### Task 2: Config Schema Descriptors & Validation Tests

**Files:**
- Create: `tests/unit/shared/test_config_schema.py`
- Test: `tests/unit/shared/test_config_schema.py`

**Interfaces:**
- Consumes: `backend.shared.config_schema`: `ConfigFieldSchema`, `ConfigFieldMessage`, `ConfigFieldList`, `ConfigFieldOneof`, `validate_value`, `schema_to_dict`, `schema_from_dict`, `field_string`, `field_int`, `field_float`, `field_enum`, `field_bool`.
- Produces: Complete coverage of configuration serialization and type coercion.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/shared/test_config_schema.py
import pytest
from shared.config_schema import (
    ConfigFieldSchema,
    ConfigFieldMessage,
    ConfigFieldList,
    ConfigFieldOneof,
    field_string,
    field_int,
    field_float,
    field_enum,
    field_bool,
    schema_to_dict,
    schema_from_dict,
    validate_value,
)


def test_schema_field_factories():
    s = field_string("default_val")
    assert s.type == "string" and s.default == "default_val"

    i = field_int(10, min=0, max=100)
    assert i.type == "int" and i.min == 0 and i.max == 100

    f = field_float(1.5, min=0.0, max=5.0)
    assert f.type == "float" and f.default == 1.5

    b = field_bool(True)
    assert b.type == "bool" and b.default is True

    e = field_enum("a", ["a", "b", "c"])
    assert e.type == "enum" and e.choices == ["a", "b", "c"]

    with pytest.raises(ValueError):
        field_enum("invalid", ["a", "b"])


def test_schema_serialization_roundtrip():
    schema = {
        "text": field_string("hello"),
        "nested": ConfigFieldMessage(fields={"count": field_int(5)}),
        "items": ConfigFieldList(item_schema=field_string("item")),
        "choice": ConfigFieldOneof(
            branches={"b1": field_string("val1"), "b2": field_int(2)},
            active_branch="b1",
        ),
    }
    serialized = schema_to_dict(schema)
    restored = schema_from_dict(serialized)
    assert restored["text"].default == "hello"
    assert restored["nested"].fields["count"].default == 5
    assert restored["choice"].active_branch == "b1"


def test_validate_value_bool():
    schema = field_bool(False)
    assert validate_value(schema, True) is True
    assert validate_value(schema, "yes") is True
    assert validate_value(schema, "0") is False
    assert validate_value(schema, "off") is False
    with pytest.raises(ValueError):
        validate_value(schema, "not_a_bool")


def test_validate_value_int_bounds():
    schema = field_int(10, min=5, max=15)
    assert validate_value(schema, "10") == 10
    with pytest.raises(ValueError):
        validate_value(schema, 4)
    with pytest.raises(ValueError):
        validate_value(schema, 16)
    with pytest.raises(ValueError):
        validate_value(schema, "abc")


def test_validate_value_enum():
    schema = field_enum("foo", ["foo", "bar"])
    assert validate_value(schema, "bar") == "bar"
    with pytest.raises(ValueError):
        validate_value(schema, "baz")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_config_schema.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/shared/test_config_schema.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_config_schema.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/shared/test_config_schema.py
git commit -m "test(unit): add config_schema validation and serialization tests"
```

---

### Task 3: Config Client Fallback & Event Handling Tests

**Files:**
- Create: `tests/unit/shared/test_config_client.py`
- Test: `tests/unit/shared/test_config_client.py`

**Interfaces:**
- Consumes: `backend.shared.config_client.ConfigClient`, `mock_bus` fixture
- Produces: Validated config negotiation behavior for modules

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/shared/test_config_client.py
from shared.config_client import ConfigClient
from shared.config_schema import field_string


def test_config_client_defaults_fallback(mock_bus):
    defaults = {"host": "127.0.0.1", "port": 8080}
    client = ConfigClient("test_mod", mock_bus, default_config=defaults)

    assert client.config_data == defaults
    assert client.has_remote_config is False

    res = client.fetch_config()
    assert res == defaults
    mock_bus.publish.assert_called_once()
    topic, payload = mock_bus.publish.call_args[0]
    assert topic == "config.get"
    assert payload["module"] == "test_mod"
    assert payload["defaults"] == defaults


def test_config_client_update_and_callback(mock_bus):
    defaults = {"volume": 50, "muted": False}
    client = ConfigClient("audio_mod", mock_bus, default_config=defaults)

    received_updates = []
    client.on_update(lambda cfg: received_updates.append(dict(cfg)))

    # Response for another module should be ignored
    client.handle_config_response("config.response", {"module": "other_mod", "config": {"volume": 90}})
    assert client.has_remote_config is False
    assert len(received_updates) == 0

    # Response for this module
    client.handle_config_response("config.response", {"module": "audio_mod", "config": {"volume": 75}})
    assert client.has_remote_config is True
    assert client.config_data == {"volume": 75, "muted": False}
    assert len(received_updates) == 1
    assert received_updates[0]["volume"] == 75


def test_config_client_callback_exception_safety(mock_bus):
    client = ConfigClient("safe_mod", mock_bus, default_config={"x": 1})

    def bad_cb(cfg):
        raise RuntimeError("Callback explosion")

    client.on_update(bad_cb)
    # Should not raise exception
    client.handle_config_response("config.response", {"module": "safe_mod", "config": {"x": 2}})
    assert client.config_data["x"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_config_client.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/shared/test_config_client.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_config_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/shared/test_config_client.py
git commit -m "test(unit): add config_client fallback and subscription tests"
```

---

### Task 4: Touch Coordinate Mapper Aspect Ratio & Margins Tests

**Files:**
- Create: `tests/unit/shared/test_touch_mapper.py`
- Test: `tests/unit/shared/test_touch_mapper.py`

**Interfaces:**
- Consumes: `backend.shared.touch_mapper.TouchCoordinateMapper`, `TouchPoint`
- Produces: Precise mathematical verification of touch projection and bounds clamping

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/shared/test_touch_mapper.py
from shared.touch_mapper import TouchCoordinateMapper, TouchPoint


def test_touch_mapper_direct_one_to_one():
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=100.0,
        raw_y=200.0,
        surface_width=1280.0,
        surface_height=720.0,
        negotiated_width=1280,
        negotiated_height=720,
        stretch_to_fill=True,
    )
    assert pt == TouchPoint(x=100, y=200)


def test_touch_mapper_stretch_scaling():
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=960.0,
        raw_y=540.0,
        surface_width=1920.0,
        surface_height=1080.0,
        negotiated_width=1280,
        negotiated_height=720,
        stretch_to_fill=True,
    )
    assert pt == TouchPoint(x=640, y=360)


def test_touch_mapper_margins_applied():
    # 1280x720 negotiated with 80px width margin and 40px height margin -> 1200x680 active ui
    pt = TouchCoordinateMapper.map_coordinate(
        raw_x=1920.0,
        raw_y=1080.0,
        surface_width=1920.0,
        surface_height=1080.0,
        negotiated_width=1280,
        negotiated_height=720,
        margin_width=80,
        margin_height=40,
        stretch_to_fill=True,
    )
    assert pt == TouchPoint(x=1200, y=680)


def test_touch_mapper_clamping():
    # Coordinates outside physical display bounds
    pt_negative = TouchCoordinateMapper.map_coordinate(
        raw_x=-50.0,
        raw_y=-100.0,
        surface_width=1000.0,
        surface_height=500.0,
        negotiated_width=800,
        negotiated_height=480,
    )
    assert pt_negative == TouchPoint(x=0, y=0)

    pt_overflow = TouchCoordinateMapper.map_coordinate(
        raw_x=2000.0,
        raw_y=2000.0,
        surface_width=1000.0,
        surface_height=500.0,
        negotiated_width=800,
        negotiated_height=480,
    )
    assert pt_overflow == TouchPoint(x=800, y=480)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_touch_mapper.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/shared/test_touch_mapper.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_touch_mapper.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/shared/test_touch_mapper.py
git commit -m "test(unit): add touch_mapper coordinate transformation tests"
```

---

### Task 5: Protobuf Framing & Media Timestamp Utilities Tests

**Files:**
- Create: `tests/unit/shared/test_proto_utils.py`
- Test: `tests/unit/shared/test_proto_utils.py`

**Interfaces:**
- Consumes: `backend.shared.proto_utils`: `encode_aa_frame`, `decode_aa_frame`, `build_media_with_timestamp`, `parse_media_with_timestamp`.
- Produces: Wire framing and media timestamp packet verification.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/shared/test_proto_utils.py
import struct
from shared.proto_utils import (
    encode_aa_frame,
    decode_aa_frame,
    build_media_with_timestamp,
    parse_media_with_timestamp,
)


def test_aa_frame_encode_decode_roundtrip():
    channel_id = 3
    message_id = 0x8001
    payload = b"test_payload_bytes_12345"

    frame = encode_aa_frame(channel_id=channel_id, message_id=message_id, proto_body=payload)
    assert frame["channel_id"] == channel_id
    assert frame["flags"] == 0x0B

    raw_wire_bytes = bytes.fromhex(frame["payload_hex"])
    decoded = decode_aa_frame(raw_wire_bytes)
    assert decoded is not None
    dec_msg_id, dec_body = decoded
    assert dec_msg_id == message_id
    assert dec_body == payload


def test_aa_frame_decode_malformed_short_bytes():
    assert decode_aa_frame(b"") is None
    assert decode_aa_frame(b"\x01") is None


def test_media_with_timestamp_roundtrip():
    timestamp_us = 1718000000123456
    media_data = b"\x00\x00\x01\x65\x88\x84\x00\x10\xff\xee"

    packed = build_media_with_timestamp(timestamp_us, media_data)
    # Check 8-byte BE timestamp prefix
    ts_prefix = struct.unpack(">Q", packed[:8])[0]
    assert ts_prefix == timestamp_us
    assert packed[8:] == media_data

    dec_ts, dec_data = parse_media_with_timestamp(packed)
    assert dec_ts == timestamp_us
    assert dec_data == media_data


def test_media_with_timestamp_truncated():
    dec_ts, dec_data = parse_media_with_timestamp(b"\x01\x02\x03")
    assert dec_ts == 0
    assert dec_data == b"\x01\x02\x03"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_proto_utils.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/shared/test_proto_utils.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_proto_utils.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/shared/test_proto_utils.py
git commit -m "test(unit): add proto_utils framing and media timestamp tests"
```

---

### Task 6: BaseBackendModule Lifecycle & Route Registration Tests

**Files:**
- Create: `tests/unit/shared/test_base_module.py`
- Test: `tests/unit/shared/test_base_module.py`

**Interfaces:**
- Consumes: `backend.shared.base_module.BaseBackendModule`
- Produces: Verified base module lifecycle and route mapping

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/shared/test_base_module.py
import pytest
from aiohttp import web
from unittest.mock import AsyncMock, MagicMock, patch
from shared.base_module import BaseBackendModule


class DummyModule(BaseBackendModule):
    def __init__(self):
        super().__init__(name="dummy", priority=2, path_prefix="/api/dummy")
        self.setup_called = False
        self.teardown_called = False

    def get_default_config(self):
        return {"param": 100}

    def get_schema(self):
        return {}

    async def setup(self):
        self.setup_called = True

    async def run(self):
        pass

    async def teardown(self):
        self.teardown_called = True


def test_base_module_initialization():
    mod = DummyModule()
    assert mod.name == "dummy"
    assert mod.priority == 2
    assert mod.path_prefix == "/api/dummy"
    assert mod.config == {"param": 100}


def test_base_module_add_http_and_ws_routes():
    mod = DummyModule()

    async def sample_handler(req):
        return web.Response(text="ok")

    mod.add_http_route("GET", "/status", sample_handler)
    mod.add_ws_route("/stream", sample_handler)

    routes = [r.resource.canonical for r in mod.web_app.router.routes()]
    assert "/api/dummy/status" in routes
    assert "/api/dummy/stream" in routes


@pytest.mark.asyncio
async def test_base_module_call_module_rpc():
    mod = DummyModule()
    # Mock system registry
    mod.module_registry = {
        "target_mod": {"target_url": "http://127.0.0.1:9999"}
    }

    mock_resp = AsyncMock()
    mock_resp.json.return_value = {"success": True}

    mock_session = MagicMock()
    mock_req_ctx = AsyncMock()
    mock_req_ctx.__aenter__.return_value = mock_resp
    mock_session.request.return_value = mock_req_ctx
    mod.client_session = mock_session

    res = await mod.call_module("target_mod", "GET", "/status")
    assert res == {"success": True}
    mock_session.request.assert_called_with(
        method="GET",
        url="http://127.0.0.1:9999/status",
        json=None,
        timeout=pytest.approx(3.0),
    )


@pytest.mark.asyncio
async def test_base_module_call_module_missing_registry():
    mod = DummyModule()
    with pytest.raises(RuntimeError, match="Target module 'unknown' is not currently available"):
        await mod.call_module("unknown", "GET", "/status")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_base_module.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/shared/test_base_module.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_base_module.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/shared/test_base_module.py
git commit -m "test(unit): add base_backend_module lifecycle and rpc unit tests"
```

---

### Task 7: BaseChannelModule Event Subscriptions & Frame Dispatch Tests

**Files:**
- Modify: `backend/shared/base_channel_module.py:82-93` (fix `await self.publish` bug)
- Create: `tests/unit/shared/test_base_channel_module.py`
- Test: `tests/unit/shared/test_base_channel_module.py`

**Interfaces:**
- Consumes: `backend.shared.base_channel_module.BaseChannelModule`
- Produces: Correct channel open/close and incoming/outgoing wire frame handling

- [ ] **Step 1: Fix `await self.publish` bug in `backend/shared/base_channel_module.py`**

In `backend/shared/base_channel_module.py`, line 92: change `await self.publish(...)` to `self.publish(...)`.

```python
    async def send_frame(self, message_id: int, proto_body: bytes, control: bool = False) -> None:
        """
        Helper method to publish an outgoing frame to 'aa.frame.send'.
        """
        frame_dict = encode_aa_frame(
            channel_id=self.channel_id,
            message_id=message_id,
            proto_body=proto_body,
            control=control,
        )
        self.publish("aa.frame.send", frame_dict)
```

- [ ] **Step 2: Write test for `BaseChannelModule`**

```python
# tests/unit/shared/test_base_channel_module.py
import pytest
from unittest.mock import MagicMock
from shared.base_channel_module import BaseChannelModule
from shared.proto_utils import encode_aa_frame


class SampleChannelModule(BaseChannelModule):
    def __init__(self, ch_id=5):
        super().__init__(name="sample_ch", channel_id=ch_id, priority=3)
        self.received_frames = []

    async def run(self):
        pass

    async def teardown(self):
        pass

    async def on_frame(self, message_id: int, encrypted: bool, payload: bytes):
        self.received_frames.append((message_id, encrypted, payload))


@pytest.mark.asyncio
async def test_channel_module_lifecycle():
    mod = SampleChannelModule(ch_id=4)
    assert mod.channel_id == 4
    assert mod.is_channel_open is False

    await mod._handle_channel_open({"channel_id": 4, "descriptor": {"name": "video"}})
    assert mod.is_channel_open is True
    assert mod.channel_descriptor == {"name": "video"}

    await mod._handle_channel_close({"channel_id": 4})
    assert mod.is_channel_open is False


@pytest.mark.asyncio
async def test_channel_module_frame_dispatch():
    mod = SampleChannelModule(ch_id=7)
    frame = encode_aa_frame(channel_id=7, message_id=0x1234, proto_body=b"audio_bytes")

    await mod._handle_bus_frame({
        "payload_hex": frame["payload_hex"],
        "encrypted": True,
    })

    assert len(mod.received_frames) == 1
    msg_id, enc, data = mod.received_frames[0]
    assert msg_id == 0x1234
    assert enc is True
    assert data == b"audio_bytes"


@pytest.mark.asyncio
async def test_channel_module_send_frame():
    mod = SampleChannelModule(ch_id=2)
    mod.publish = MagicMock()

    await mod.send_frame(message_id=0x0008, proto_body=b"\x08\x00", control=True)

    mod.publish.assert_called_once()
    topic, frame_data = mod.publish.call_args[0]
    assert topic == "aa.frame.send"
    assert frame_data["channel_id"] == 2
```

- [ ] **Step 3: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/test_base_channel_module.py -v`
Expected: PASS (3 passed)

- [ ] **Step 4: Commit**

```bash
git add backend/shared/base_channel_module.py tests/unit/shared/test_base_channel_module.py
git commit -m "fix(shared): correct send_frame sync publish and add base_channel_module unit tests"
```

---

### Task 8: Full Phase 1 Verification & Smoke Test

**Files:**
- Test: All tests under `tests/unit/shared/`

**Interfaces:**
- Consumes: Complete Phase 1 test suite

- [ ] **Step 1: Execute all Phase 1 tests**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/shared/ -v`
Expected: ALL PASS (26+ passed in < 2.0s)

- [ ] **Step 2: Run entire test suite to guarantee zero regression**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest -q`
Expected: ALL PASS (90 existing + 26 new = 116+ passed)

- [ ] **Step 3: Mandatory smoke test orchestrator launch**

Run: `timeout 5s micromamba run -n NemoHeadUnit-Wireless python backend/main.py || true`
Expected: Clean startup sequence without warnings or syntax errors.

- [ ] **Step 4: Update graphify knowledge graph**

Run: `graphify update .`
Expected: Graph AST updated.
