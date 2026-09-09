# Intra-Module Communication Abstraction Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide an abstraction layer for intra-module communication across ZeroMQ Pub/Sub, Loopback REST, WebSockets, SSE, and SHM that eliminates all internal loopback webservers and ZMQ IPC sockets when running in multithreading mode while preserving full multiprocessing compatibility.

**Architecture:** A dual-backend adapter pattern where `BusClient`, `BaseBackendModule`, and `media_shm` select between socket-based IPC (`ZmqBusClient`, `aiohttp.web.TCPSite`, OS `SharedMemory`) and in-memory equivalents (`InMemoryBusClient`, in-memory coroutine dispatch, in-memory `bytearray` ring buffers) based on `NEMO_EXECUTION_MODE`. `proxy` operates as the unified public gateway dispatching external HTTP/WS/SSE requests directly to in-memory module handlers in multithreading mode.

**Tech Stack:** Python 3.11+, asyncio, pyzmq, aiohttp, multiprocessing.shared_memory, pytest.

**Spec:** Decisions resolved via interactive design interview:
1. `proxy` acts as single public gateway on :8000; translates external HTTP/WS/SSE requests in-memory without loopback sockets in multithreading mode.
2. WebSockets & SSE stream through `asyncio.Queue` / async generators.
3. Dual-backend adapter: multiprocessing retains ZMQ + loopback; multithreading uses zero-socket in-memory bus + direct coroutine dispatch.
4. SHM uses in-memory `bytearray` ring buffers in multithreading mode and OS `/dev/shm` in multiprocessing mode.
5. `add_http_route` and `add_ws_route` signatures remain identical on `BaseBackendModule`.
6. `call_module` uses direct coroutine invocation in multithreading mode.
7. `BusClient` acts as facade delegating to `ZmqBusClient` or `InMemoryBusClient`.

## Global Constraints

- Cross-Platform Invariant: Universal compliance across Linux and Windows.
- Backward Compatibility: Existing module route definitions (`add_http_route`, `add_ws_route`, `publish`, `subscribe`, `call_module`) must remain 100% operational without breaking changes.
- Test Coverage: Both execution modes (`multiprocessing` and `multithreading`) must have deterministic automated unit and integration tests.

---

### Task 1: In-Memory Event Bus & BusClient Facade

**Files:**
- Create: `backend/shared/bus_inmemory.py`
- Modify: `backend/shared/bus_client.py`
- Test: `tests/unit/shared/test_bus_inmemory.py`

**Interfaces:**
- Consumes: `get_execution_mode()` from `backend/main.py` or `os.environ["NEMO_EXECUTION_MODE"]`
- Produces: `InMemoryBusHub`, `InMemoryBusClient`, and updated `BusClient` facade

- [ ] **Step 1: Write the failing unit tests for InMemoryBusHub and InMemoryBusClient**

```python
# tests/unit/shared/test_bus_inmemory.py
import pytest
from backend.shared.bus_inmemory import InMemoryBusHub, InMemoryBusClient

@pytest.mark.asyncio
async def test_in_memory_bus_pub_sub():
    hub = InMemoryBusHub()
    client1 = InMemoryBusClient("mod1", hub=hub)
    client2 = InMemoryBusClient("mod2", hub=hub)

    received = []
    def on_event(topic, payload):
        received.append((topic, payload))

    client2.subscribe("test.topic", on_event)
    client1.publish("test.topic", {"hello": "world"})
    
    assert len(received) == 1
    assert received[0] == ("test.topic", {"hello": "world"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/shared/test_bus_inmemory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.shared.bus_inmemory'`

- [ ] **Step 3: Implement InMemoryBusHub and InMemoryBusClient**

Create `backend/shared/bus_inmemory.py`:
- `InMemoryBusHub`: Singleton or shared hub holding subscriber callback dictionaries with topic wildcard matching (`*` support matching ZMQ prefixes).
- Thread-safe dispatch with lock or `loop.call_soon_threadsafe`.
- `InMemoryBusClient`: Implements `publish(topic, payload)`, `subscribe(topic, callback)`, `unsubscribe(topic, callback)`, `start()`, `stop()`.

- [ ] **Step 4: Update BusClient to act as facade**

Modify `backend/shared/bus_client.py`:
- If `os.environ.get("NEMO_EXECUTION_MODE") == "multithreading"`, delegate methods to an internal `InMemoryBusClient` instance using a shared hub.
- Otherwise, retain existing ZeroMQ socket implementation (`ZmqBusClient`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/shared/test_bus_inmemory.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/shared/bus_inmemory.py backend/shared/bus_client.py tests/unit/shared/test_bus_inmemory.py
git commit -m "feat(bus): implement in-memory bus hub and BusClient facade for multithreading"
```

---

### Task 2: In-Memory Shared Memory Buffer Provider

**Files:**
- Modify: `backend/shared/media_shm.py`
- Test: `tests/unit/shared/test_media_shm_modes.py`

**Interfaces:**
- Consumes: Execution mode configuration
- Produces: `RingBufferInterface`, `RingSharedMemoryBuffer` (OS-backed), `InMemoryRingBuffer` (bytearray-backed), `BidirectionalMediaSHM`

- [ ] **Step 1: Write failing test for InMemoryRingBuffer**

```python
# tests/unit/shared/test_media_shm_modes.py
import pytest
from backend.shared.media_shm import InMemoryRingBuffer

def test_in_memory_ring_buffer_write_read():
    buf = InMemoryRingBuffer(name="test_buf", size=1024 * 1024)
    data = b"\x00\x00\x00\x01\x67\x42\x00\x1f"
    offset = buf.write_frame(stream_type=1, timestamp_us=123456, payload=data)
    
    stream_type, ts_us, payload = buf.read_frame(offset, len(data))
    assert stream_type == 1
    assert ts_us == 123456
    assert payload == data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/shared/test_media_shm_modes.py -v`
Expected: FAIL with `ImportError: cannot import name 'InMemoryRingBuffer'`

- [ ] **Step 3: Implement InMemoryRingBuffer and polymorphic BidirectionalMediaSHM**

In `backend/shared/media_shm.py`:
- Define `InMemoryRingBuffer` backed by `bytearray(size)`.
- Implements identical header packing `[Magic: 2B] + [StreamType: 1B] + [Reserved: 1B] + [Length: 4B] + [TimestampUs: 4B/8B]` and circular offset wrapping.
- Update `BidirectionalMediaSHM`: checks execution mode; uses `InMemoryRingBuffer` when multithreading, `RingSharedMemoryBuffer` when multiprocessing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/shared/test_media_shm_modes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/shared/media_shm.py tests/unit/shared/test_media_shm_modes.py
git commit -m "feat(shm): provide in-memory bytearray ring buffer for multithreading mode"
```

---

### Task 3: In-Memory Router & Zero-Socket BaseBackendModule

**Files:**
- Modify: `backend/shared/base_module.py`
- Test: `tests/unit/shared/test_base_module_modes.py`

**Interfaces:**
- Consumes: `InMemoryBusClient`, `add_http_route`, `add_ws_route`
- Produces: Mode-aware `BaseBackendModule` with zero-socket webserver bypass and in-memory `call_module`

- [ ] **Step 1: Write failing test for BaseBackendModule in multithreading mode**

```python
# tests/unit/shared/test_base_module_modes.py
import pytest
import os
from backend.shared.base_module import BaseBackendModule

class DummyTestModule(BaseBackendModule):
    def __init__(self):
        super().__init__(name="dummy", priority=3, path_prefix="/api/dummy")

    async def setup(self):
        self.add_http_route("GET", "/ping", self.handle_ping)

    async def handle_ping(self, request):
        from aiohttp import web
        return web.json_response({"pong": True})

    async def run(self): pass
    async def teardown(self): pass

@pytest.mark.asyncio
async def test_module_no_socket_in_multithreading(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")
    mod = DummyTestModule()
    await mod.start()
    assert mod.port == 0
    assert mod.site is None
    await mod.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/shared/test_base_module_modes.py -v`
Expected: FAIL because `mod.start()` currently tries to bind `TCPSite(..., "127.0.0.1", 0)`.

- [ ] **Step 3: Refactor BaseBackendModule to adapt based on mode**

In `backend/shared/base_module.py`:
- In `_start_web_server()`:
  - If `execution_mode == "multithreading"`, skip `TCPSite` creation. Set `self.target_url = f"inmemory://{self.name}"`.
  - Store route table `self._inmemory_routes: dict[tuple[str, str], Callable]` for direct lookup.
  - Register module in a global in-process registry `SHARED_MODULE_REGISTRY[self.name] = self`.
- In `call_module(target_module, method, path, data)`:
  - If `execution_mode == "multithreading"`:
    - Lookup `target_inst = SHARED_MODULE_REGISTRY.get(target_module)`.
    - Find handler from `target_inst._inmemory_routes`.
    - Construct mock `aiohttp.web.Request` or invoke handler directly, returning JSON dict.
  - If `execution_mode == "multiprocessing"`, retain HTTP `ClientSession` loopback.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/shared/test_base_module_modes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/shared/base_module.py tests/unit/shared/test_base_module_modes.py
git commit -m "feat(module): adapt BaseBackendModule to skip loopback sockets in multithreading mode"
```

---

### Task 4: Proxy Gateway In-Memory Route Dispatch

**Files:**
- Modify: `backend/modules/proxy/main.py`
- Test: `tests/integration/test_proxy_inmemory_dispatch.py`

**Interfaces:**
- Consumes: `SHARED_MODULE_REGISTRY` and `target_url = "inmemory://..."`
- Produces: Transparent forwarding of external HTTP, WebSocket, and SSE requests to in-memory handlers without loopback network hops

- [ ] **Step 1: Write integration test for Proxy in-memory dispatch**

```python
# tests/integration/test_proxy_inmemory_dispatch.py
import pytest
from aiohttp import web
from backend.modules.proxy.main import ProxyModule

@pytest.mark.asyncio
async def test_proxy_dispatches_inmemory_route(monkeypatch, aiohttp_client):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")
    proxy = ProxyModule()
    # verify proxy routes in-memory target to registered handler
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_proxy_inmemory_dispatch.py -v`
Expected: FAIL

- [ ] **Step 3: Update ProxyModule route dispatcher**

In `backend/modules/proxy/main.py`:
- In `handle_proxy_request`:
  - Check if target URL starts with `inmemory://`.
  - If in-memory:
    - Route HTTP: invoke registered module handler directly passing the request.
    - Route WebSocket: pass WebSocket connection directly to module handler coroutine.
    - Route SSE: stream responses directly to client.
  - If not in-memory: forward via `proxy_client_session` (multiprocessing mode).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_proxy_inmemory_dispatch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/modules/proxy/main.py tests/integration/test_proxy_inmemory_dispatch.py
git commit -m "feat(proxy): dispatch external HTTP and WebSocket requests in-memory in multithreading mode"
```

---

### Task 5: Orchestrator Integration & Verification

**Files:**
- Modify: `backend/main.py`
- Test: `tests/integration/test_orchestrator_modes.py`

**Interfaces:**
- Consumes: All modules running under `--mode multithreading` and `--mode multiprocessing`
- Produces: Zero socket binding for internal modules in multithreading mode, clean orchestrator priority wave boot sequence

- [ ] **Step 1: Update main.py orchestrator for multithreading mode**

In `backend/main.py`:
- In multithreading mode:
  - Initialize `InMemoryBusHub`.
  - Skip launching `bus_broker` process/thread (or run it as pure in-memory heartbeat announcer).
  - Collect `system.module_ready` and priority waves over in-memory bus.

- [ ] **Step 2: Run smoke test in multithreading mode**

Command:
`micromamba run -n NemoHeadUnit-Wireless python backend/main.py --mode multithreading` (run with timeout/timer check)
Expected: All priority waves boot cleanly with 0 loopback ports allocated for submodules; only proxy listens on port 8000.

- [ ] **Step 3: Run smoke test in multiprocessing mode**

Command:
`micromamba run -n NemoHeadUnit-Wireless python backend/main.py --mode multiprocessing`
Expected: Boots cleanly with normal ZMQ sockets and loopback ports.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py tests/integration/test_orchestrator_modes.py
git commit -m "feat(orchestrator): integrate zero-socket in-memory communication under multithreading mode"
```
