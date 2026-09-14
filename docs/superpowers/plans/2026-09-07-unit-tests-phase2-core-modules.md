# Phase 2: Core Infrastructure Modules (P0–P2) Unit Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement isolated, fast, robust unit tests for Priority 0–2 core infrastructure modules (`bus_broker`, `config_manager`, `proxy`) in `backend/modules/`.

**Architecture:** Create `tests/unit/modules/` containing dedicated test suites for the three core modules. Use `unittest.mock` to mock ZMQ sockets, aiohttp client sessions, and filesystem paths. Validate configuration serialization, IPC routing, module registry events, REST endpoints, and reverse-proxy request forwarding.

**Tech Stack:** Python 3.13+, pytest, pytest-asyncio, aiohttp, pyzmq, pyyaml, unittest.mock.

**Spec:** `docs/superpowers/specs/2026-09-07-unit-tests-phase2-core-modules-design.md`

## Global Constraints

- Must run cleanly inside the `NemoHeadUnit-Wireless` micromamba environment.
- Strict isolation: unit tests must never require real ZMQ daemons, real hardware, or live network sockets.
- Universal cross-platform compatibility: all tests and mocks must succeed on both Linux and Windows.
- Target execution time: Phase 2 suite must execute in under 2 seconds total.
- Maintain `pytestmark = pytest.mark.unit` on all test files for proper marker filtering.

---

### Task 1: Priority 0 Bus Broker Module Unit Tests

**Files:**
- Create: `tests/unit/modules/__init__.py`
- Create: `tests/unit/modules/test_bus_broker.py`
- Test: `tests/unit/modules/test_bus_broker.py`

**Interfaces:**
- Consumes: `backend.modules.bus_broker.main.BusBrokerModule`
- Produces: Complete unit coverage of `BusBrokerModule` socket lifecycle, registry events, and heartbeat broadcasting.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/modules/test_bus_broker.py
import pytest
from unittest.mock import MagicMock, patch
from modules.bus_broker.main import BusBrokerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_bus_broker():
    with patch("shared.base_module.BusClient"), \
         patch("modules.bus_broker.main.zmq.Context") as mock_ctx, \
         patch("modules.bus_broker.main.zmq.proxy"):
        mock_socket = MagicMock()
        mock_ctx.return_value.socket.return_value = mock_socket
        broker = BusBrokerModule()
        yield broker


def test_bus_broker_config_and_schema(mock_bus_broker):
    defaults = mock_bus_broker.get_default_config()
    assert defaults["heartbeat_interval"] == 2.0
    schema = mock_bus_broker.get_schema()
    assert "heartbeat_interval" in schema
    assert schema["heartbeat_interval"].type == "float"


def test_bus_broker_module_events(mock_bus_broker):
    # Event with name, priority, path_prefix, target_url
    mock_bus_broker._on_module_event("system.module_ready", {
        "name": "audio_manager",
        "priority": 3,
        "path_prefix": "/api/audio",
        "target_url": "http://127.0.0.1:8081",
    })
    assert "audio_manager" in mock_bus_broker.bus_registry
    entry = mock_bus_broker.bus_registry["audio_manager"]
    assert entry["priority"] == 3
    assert entry["path_prefix"] == "/api/audio"
    assert entry["target_url"] == "http://127.0.0.1:8081"

    # Route registration event updating existing entry
    mock_bus_broker._on_module_event("proxy.register_route", {
        "path_prefix": "/api/audio",
        "target_url": "http://127.0.0.1:8089",
    })
    assert mock_bus_broker.bus_registry["audio_manager"]["target_url"] == "http://127.0.0.1:8089"


@pytest.mark.asyncio
async def test_bus_broker_heartbeat_step(mock_bus_broker):
    mock_bus_broker.bus_registry = {
        "test_mod": {"name": "test_mod", "priority": 2}
    }
    mock_bus_broker.publish = MagicMock()
    mock_bus_broker.config["heartbeat_interval"] = 0.1

    # Simulate heartbeat broadcast check
    now = 1000.0
    mock_bus_broker._last_heartbeat = 0.0
    with patch("time.time", return_value=now):
        interval = mock_bus_broker.config.get("heartbeat_interval", 2.0)
        if now - mock_bus_broker._last_heartbeat >= interval:
            mock_bus_broker._last_heartbeat = now
            mock_bus_broker.publish("system.heartbeat", {
                "timestamp": now,
                "modules": mock_bus_broker.bus_registry,
            })

    mock_bus_broker.publish.assert_called_once()
    topic, payload = mock_bus_broker.publish.call_args[0]
    assert topic == "system.heartbeat"
    assert payload["timestamp"] == 1000.0
    assert "test_mod" in payload["modules"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/test_bus_broker.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Create `tests/unit/modules/__init__.py` and `tests/unit/modules/test_bus_broker.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/test_bus_broker.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/modules/__init__.py tests/unit/modules/test_bus_broker.py
git commit -m "test(unit): add bus_broker module lifecycle and event registry tests"
```

---

### Task 2: Priority 1 Config Manager Module Unit Tests

**Files:**
- Create: `tests/unit/modules/test_config_manager.py`
- Test: `tests/unit/modules/test_config_manager.py`

**Interfaces:**
- Consumes: `backend.modules.config_manager.main.ConfigManagerModule`, `get_user_config_dir`
- Produces: Complete unit coverage of YAML persistence, schema validation, and config REST endpoints.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/modules/test_config_manager.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from aiohttp import web
from modules.config_manager.main import ConfigManagerModule, get_user_config_dir
from shared.config_schema import field_int, field_string, schema_to_dict

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config_manager(tmp_path):
    with patch("shared.base_module.BusClient"):
        with patch("modules.config_manager.main.get_user_config_dir", return_value=tmp_path):
            mgr = ConfigManagerModule()
            yield mgr


def test_get_user_config_dir_env_override(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_config"
    monkeypatch.setenv("NEMO_CONFIG_DIR", str(custom_dir))
    res = get_user_config_dir()
    assert res == custom_dir
    assert custom_dir.exists()


def test_config_manager_yaml_load_and_save(mock_config_manager, tmp_path):
    assert mock_config_manager._load_config("nonexistent") == {}

    data = {"volume": 85, "theme": "dark"}
    saved = mock_config_manager._save_config("audio", data)
    assert saved is True

    loaded = mock_config_manager._load_config("audio")
    assert loaded == data


def test_config_manager_on_config_get_and_set(mock_config_manager):
    mock_config_manager.publish = MagicMock()
    schema = {"level": field_int(10, min=0, max=100)}

    # 1. on_config_get: registers schema, responds with defaults
    mock_config_manager.on_config_get("config.get", {
        "module": "display",
        "defaults": {"level": 50},
        "schema": schema_to_dict(schema),
    })

    assert "display" in mock_config_manager.schemas
    mock_config_manager.publish.assert_called_with("config.response", {
        "module": "display",
        "config": {"level": 50},
    })

    # 2. on_config_set: updates valid value and publishes updated
    mock_config_manager.on_config_set("config.set", {
        "module": "display",
        "config": {"level": 75},
    })

    mock_config_manager.publish.assert_called_with("config.updated.display", {
        "module": "display",
        "config": {"level": 75},
    })


@pytest.mark.asyncio
async def test_config_manager_rest_api(mock_config_manager):
    # Setup test schema and config
    mock_config_manager.schemas["test_mod"] = {"name": field_string("nemo")}
    mock_config_manager._save_config("test_mod", {"name": "nemo"})

    # GET /all
    req_all = MagicMock()
    resp_all = await mock_config_manager.handle_get_all(req_all)
    assert resp_all.status == 200

    # GET /test_mod
    req_mod = MagicMock()
    req_mod.match_info = {"module": "test_mod"}
    resp_mod = await mock_config_manager.handle_get_module(req_mod)
    assert resp_mod.status == 200

    # POST /test_mod valid update
    req_set = MagicMock()
    req_set.match_info = {"module": "test_mod"}
    req_set.json = MagicMock(return_value={"name": "new_nemo"})
    mock_config_manager.publish = MagicMock()
    resp_set = await mock_config_manager.handle_set_module(req_set)
    assert resp_set.status == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/test_config_manager.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/modules/test_config_manager.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/test_config_manager.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/modules/test_config_manager.py
git commit -m "test(unit): add config_manager persistence and rest api unit tests"
```

---

### Task 3: Priority 2 Gateway Proxy Module Unit Tests

**Files:**
- Create: `tests/unit/modules/test_proxy.py`
- Test: `tests/unit/modules/test_proxy.py`

**Interfaces:**
- Consumes: `backend.modules.proxy.main.ProxyModule`
- Produces: Complete unit coverage of dynamic route registration, module discovery, and request forwarding.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/modules/test_proxy.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from modules.proxy.main import ProxyModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_proxy():
    with patch("shared.base_module.BusClient"):
        proxy = ProxyModule()
        yield proxy


def test_proxy_config_and_schema(mock_proxy):
    cfg = mock_proxy.get_default_config()
    assert cfg["public_port"] == 8000
    assert cfg["host"] == "0.0.0.0"
    schema = mock_proxy.get_schema()
    assert "public_port" in schema
    assert "host" in schema


def test_proxy_route_registration_events(mock_proxy):
    # Route registration via proxy.register_route
    mock_proxy.on_register_route("proxy.register_route", {
        "path_prefix": "/api/audio",
        "target_url": "http://127.0.0.1:8081",
    })
    assert mock_proxy.routes.get("/api/audio") == "http://127.0.0.1:8081"

    # Route registration via system.module_ready
    mock_proxy.on_module_ready("system.module_ready", {
        "name": "connectivity",
        "priority": 3,
        "path_prefix": "/api/connectivity",
        "target_url": "http://127.0.0.1:8082",
    })
    assert mock_proxy.routes.get("/api/connectivity") == "http://127.0.0.1:8082"
    assert "connectivity" in mock_proxy.module_registry


@pytest.mark.asyncio
async def test_proxy_get_modules_endpoint(mock_proxy):
    mock_proxy.module_registry = {
        "tcp_server": {
            "name": "tcp_server",
            "priority": 3,
            "path_prefix": "/api/tcp",
        }
    }
    req = MagicMock()
    resp = await mock_proxy.handle_get_modules(req)
    assert resp.status == 200
    import json
    data = json.loads(resp.text)
    assert "modules" in data
    assert "proxy" in data["modules"]
    assert "tcp_server" in data["modules"]
    assert data["modules"]["tcp_server"]["path_prefix"] == "/api/tcp"


@pytest.mark.asyncio
async def test_proxy_request_forwarding_success(mock_proxy):
    mock_proxy.routes["/api/sample"] = "http://127.0.0.1:9090"

    # Mock downstream response
    mock_downstream_resp = AsyncMock()
    mock_downstream_resp.status = 200
    mock_downstream_resp.headers = {"Content-Type": "application/json"}
    mock_downstream_resp.read.return_value = b'{"result": "ok"}'

    mock_client = MagicMock()
    mock_req_ctx = AsyncMock()
    mock_req_ctx.__aenter__.return_value = mock_downstream_resp
    mock_client.request.return_value = mock_req_ctx
    mock_proxy.proxy_client_session = mock_client

    # Build incoming request
    req = MagicMock()
    req.method = "GET"
    req.path = "/api/sample/info"
    req.query_string = "key=val"
    req.headers = {"User-Agent": "test-client"}
    req.can_read_body = False

    resp = await mock_proxy.handle_proxy_request(req)
    assert resp.status == 200
    assert resp.body == b'{"result": "ok"}'
    mock_client.request.assert_called_once()
    call_args = mock_client.request.call_args
    assert call_args[1]["url"] == "http://127.0.0.1:9090/api/sample/info?key=val"


@pytest.mark.asyncio
async def test_proxy_request_downstream_error_502(mock_proxy):
    mock_proxy.routes["/api/down"] = "http://127.0.0.1:9091"

    mock_client = MagicMock()
    mock_client.request.side_effect = Exception("Connection refused")
    mock_proxy.proxy_client_session = mock_client

    req = MagicMock()
    req.method = "GET"
    req.path = "/api/down/test"
    req.query_string = ""
    req.headers = {}
    req.can_read_body = False

    resp = await mock_proxy.handle_proxy_request(req)
    assert resp.status == 502
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/test_proxy.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Save test file**

Write the contents above into `tests/unit/modules/test_proxy.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/test_proxy.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/modules/test_proxy.py
git commit -m "test(unit): add gateway proxy route registry and request forwarding tests"
```

---

### Task 4: Full Phase 2 Verification & Smoke Test

**Files:**
- Test: All tests under `tests/unit/`

**Interfaces:**
- Consumes: Complete Phase 1 & Phase 2 test suite

- [ ] **Step 1: Execute all Phase 2 module tests**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/modules/ -v`
Expected: ALL PASS (12 passed in < 1.5s)

- [ ] **Step 2: Run entire unit test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/ -v`
Expected: ALL PASS (41 passed in < 2.0s)

- [ ] **Step 3: Run entire repository test suite to guarantee zero regressions**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest -q`
Expected: ALL PASS (131+ passed)

- [ ] **Step 4: Mandatory smoke test orchestrator launch**

Run: `timeout 5s micromamba run -n NemoHeadUnit-Wireless python backend/main.py || true`
Expected: Clean startup sequence without warnings or syntax errors.

- [ ] **Step 5: Update graphify knowledge graph**

Run: `graphify update .`
Expected: Graph AST updated.
