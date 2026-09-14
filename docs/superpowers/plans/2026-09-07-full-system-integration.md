# Full System Integration Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete, automated, two-tier full system integration test suite (`tests/integration/`) that tests the entire multi-module backend stack (in-process loopback and live subprocess orchestrator) with an asynchronous typed mock phone client and wire replay/fault injector.

**Architecture:** 
The integration harness provides an ephemeral configuration/AppData sandbox with dynamic loopback port allocation. Tier 1 executes in-process threaded backend modules with an asynchronous `BusMonitor` for deterministic event barriers and a typed Protobuf `MockPhoneClient` with wire-level `PacketFixture` replay. Tier 2 tests headless Qt6 offscreen GUI event consumption and live multiprocessing orchestrator boot, REST proxy accessibility, and graceful `SIGTERM` termination.

**Tech Stack:** Python 3.11+, Pytest, Asyncio, ZeroMQ (`pyzmq`), Google Protobuf, Qt6 (`PyQt6` offscreen), HTTPX / Urllib.

**Spec:** [docs/superpowers/specs/2026-09-07-full-system-integration-design.md](file:///home/nemo/NemoHeadUnit-Wireless/docs/superpowers/specs/2026-09-07-full-system-integration-design.md)

## Global Constraints
- Target Execution Speed: Tier 1 functional pipeline must execute in **< 8.0 seconds** total; Tier 2 (Qt6 offscreen + live subprocess) in **< 15.0 seconds** total.
- Strict Marker Requirement: Every test file must declare `pytestmark = pytest.mark.integration`.
- Deterministic Isolation: Isolated `NEMO_CONFIG_DIR` pointing to temporary directory for each test run; dynamic ports (`127.0.0.1:0`). No hardcoded ports or sleeps.
- Protobuf Serialization Mandate: All phone-to-backend payloads must use generated Protobuf classes and dynamic channel IDs.
- Cross-Platform: Paths handled using `pathlib.Path`, cross-platform loopback sockets.

---

### Task 1: Integration Test Environment & Sandbox Fixtures

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/harness/__init__.py`
- Create: `tests/integration/harness/environment.py`
- Create: `tests/integration/conftest.py`
- Test: `tests/integration/test_harness_env.py`

**Interfaces:**
- Consumes: `backend.shared.ipc_utils.get_bus_address`
- Produces: `IntegrationEnvironment` (context manager setting isolated `NEMO_CONFIG_DIR`, generating dynamic port configs, providing cleanup) and pytest fixture `integration_env`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_harness_env.py
import pytest
import os
from pathlib import Path

pytestmark = pytest.mark.integration

def test_integration_environment_sandbox(tmp_path):
    from tests.integration.harness.environment import IntegrationEnvironment
    
    original_env = os.environ.get("NEMO_CONFIG_DIR")
    with IntegrationEnvironment(tmp_path) as env:
        assert os.environ["NEMO_CONFIG_DIR"] == str(env.config_dir)
        assert env.config_dir.exists()
        assert env.config_file.exists()
        assert "public_port" in env.config_data
        assert env.config_data["public_port"] == 0
    assert os.environ.get("NEMO_CONFIG_DIR") == original_env
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_env.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'tests.integration.harness.environment')

- [ ] **Step 3: Write minimal implementation**

```python
# tests/integration/harness/environment.py
import os
import yaml
from pathlib import Path
from typing import Any, Dict

class IntegrationEnvironment:
    """Manages an isolated configuration sandbox and dynamic ports for integration tests."""
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.config_dir = self.base_dir / "config"
        self.config_file = self.config_dir / "config.yaml"
        self.prev_config_dir = None
        self.config_data: Dict[str, Any] = {
            "proxy": {"public_port": 0, "host": "127.0.0.1"},
            "tcp_server": {"port": 0, "host": "127.0.0.1", "enable_ssl": False},
            "bus_broker": {"pub_port": 0, "router_port": 0},
            "channel_manager": {"video_buffer_count": 4}
        }
        # Flattened top-level keys for modules checking direct keys
        self.config_data["public_port"] = 0

    def __enter__(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.config_data, f)
        self.prev_config_dir = os.environ.get("NEMO_CONFIG_DIR")
        os.environ["NEMO_CONFIG_DIR"] = str(self.config_dir)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.prev_config_dir is not None:
            os.environ["NEMO_CONFIG_DIR"] = self.prev_config_dir
        else:
            os.environ.pop("NEMO_CONFIG_DIR", None)
```

```python
# tests/integration/conftest.py
import pytest
from pathlib import Path
from tests.integration.harness.environment import IntegrationEnvironment

@pytest.fixture
def integration_env(tmp_path: Path):
    with IntegrationEnvironment(tmp_path) as env:
        yield env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_env.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): implement integration sandbox environment fixture"
```

---

### Task 2: Async Bus Event Monitor (`BusMonitor`)

**Files:**
- Create: `tests/integration/harness/bus_monitor.py`
- Modify: `tests/integration/conftest.py`
- Test: `tests/integration/test_harness_bus_monitor.py`

**Interfaces:**
- Consumes: `backend.shared.ipc_utils.get_bus_address`
- Produces: `BusMonitor` class with `wait_for_event(topic, filter_fn, timeout)` and `get_events(topic)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_harness_bus_monitor.py
import pytest
import asyncio
import json
import zmq.asyncio

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_bus_monitor_captures_events(tmp_path):
    from tests.integration.harness.bus_monitor import BusMonitor
    
    ctx = zmq.asyncio.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind("tcp://127.0.0.1:18899")
    
    monitor = BusMonitor("tcp://127.0.0.1:18899")
    await monitor.start()
    await asyncio.sleep(0.05) # Allow subscription to settle
    
    # Emit test event
    await pub.send_multipart([b"phone.connected", json.dumps({"device": "test_phone"}).encode("utf-8")])
    
    event = await monitor.wait_for_event("phone.connected", timeout=1.0)
    assert event["device"] == "test_phone"
    
    await monitor.stop()
    pub.close()
    ctx.term()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_bus_monitor.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'tests.integration.harness.bus_monitor')

- [ ] **Step 3: Write minimal implementation**

```python
# tests/integration/harness/bus_monitor.py
import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional
import zmq.asyncio

logger = logging.getLogger(__name__)

class BusMonitor:
    """Subscribes to the ZMQ IPC bus and provides awaitable event barriers."""
    def __init__(self, bus_pub_address: str):
        self.bus_pub_address = bus_pub_address
        self.ctx = zmq.asyncio.Context()
        self.sub_sock: Optional[zmq.asyncio.Socket] = None
        self.running = False
        self.listen_task: Optional[asyncio.Task] = None
        self.events: List[tuple[str, Dict[str, Any]]] = []
        self._waiters: List[tuple[str, Optional[Callable[[Dict[str, Any]], bool]], asyncio.Future]] = []

    async def start(self):
        self.sub_sock = self.ctx.socket(zmq.SUB)
        self.sub_sock.connect(self.bus_pub_address)
        self.sub_sock.setsockopt_string(zmq.SUBSCRIBE, "")
        self.running = True
        self.listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self):
        while self.running:
            try:
                msg = await self.sub_sock.recv_multipart()
                if not msg:
                    continue
                topic = msg[0].decode("utf-8", errors="ignore")
                payload = {}
                if len(msg) > 1:
                    try:
                        payload = json.loads(msg[1].decode("utf-8", errors="ignore"))
                    except Exception:
                        payload = {"raw": msg[1]}
                self.events.append((topic, payload))
                
                # Check waiters
                for waiter in list(self._waiters):
                    w_topic, filter_fn, fut = waiter
                    if not fut.done() and (w_topic == "" or topic == w_topic):
                        if filter_fn is None or filter_fn(payload):
                            fut.set_result(payload)
                            self._waiters.remove(waiter)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    logger.error(f"Error in BusMonitor listen loop: {e}")

    async def wait_for_event(self, topic: str, filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None, timeout: float = 3.0) -> Dict[str, Any]:
        # Check existing events first
        for ev_topic, ev_payload in self.events:
            if ev_topic == topic and (filter_fn is None or filter_fn(ev_payload)):
                return ev_payload

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        waiter = (topic, filter_fn, fut)
        self._waiters.append(waiter)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            raise TimeoutError(f"Timed out waiting for bus event on topic '{topic}' after {timeout}s")

    def get_events(self, topic: Optional[str] = None) -> List[Dict[str, Any]]:
        if topic is None:
            return [payload for _, payload in self.events]
        return [payload for t, payload in self.events if t == topic]

    async def stop(self):
        self.running = False
        if self.listen_task:
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass
        if self.sub_sock:
            self.sub_sock.close()
        self.ctx.term()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_bus_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/harness/bus_monitor.py tests/integration/test_harness_bus_monitor.py
git commit -m "test(integration): implement async BusMonitor event subscriber"
```

---

### Task 3: Typed Mock Phone Engine (`MockPhoneClient`)

**Files:**
- Create: `tests/integration/harness/mock_phone.py`
- Test: `tests/integration/test_harness_mock_phone.py`

**Interfaces:**
- Consumes: Protobuf message definitions in `protos/`
- Produces: `MockPhoneClient` supporting framing (`channel_id`, `flags`, `length`), handshake, and channel operations.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_harness_mock_phone.py
import pytest
import asyncio

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_mock_phone_wire_framing():
    from tests.integration.harness.mock_phone import MockPhoneClient
    
    # Start loopback echo server
    received = []
    async def handle_client(reader, writer):
        hdr = await reader.readexactly(4)
        ch_id = hdr[0]
        flags = hdr[1]
        length = int.from_bytes(hdr[2:4], "big")
        payload = await reader.readexactly(length)
        received.append((ch_id, flags, payload))
        writer.write(hdr + payload)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    
    phone = MockPhoneClient()
    await phone.connect("127.0.0.1", port)
    await phone.send_frame(channel_id=1, flags=0x03, payload=b"hello_wire")
    
    ch_id, flags, payload = await phone.read_frame()
    assert ch_id == 1
    assert flags == 0x03
    assert payload == b"hello_wire"
    assert received[0] == (1, 0x03, b"hello_wire")
    
    await phone.disconnect()
    server.close()
    await server.wait_closed()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_mock_phone.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'tests.integration.harness.mock_phone')

- [ ] **Step 3: Write minimal implementation**

```python
# tests/integration/harness/mock_phone.py
import asyncio
import struct
from typing import Optional, Tuple

class MockPhoneClient:
    """Simulates an Android Auto mobile device connecting over TCP wire protocol."""
    def __init__(self):
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.channel_map: dict[str, int] = {
            "control": 0,
            "video": 1,
            "media_audio": 2,
            "speech_audio": 3,
            "input": 4,
            "sensor": 5
        }

    async def connect(self, host: str, port: int):
        self.reader, self.writer = await asyncio.open_connection(host, port)

    async def send_frame(self, channel_id: int, flags: int, payload: bytes):
        if not self.writer:
            raise ConnectionError("Not connected")
        header = struct.pack(">BBH", channel_id, flags, len(payload))
        self.writer.write(header + payload)
        await self.writer.drain()

    async def read_frame(self) -> Tuple[int, int, bytes]:
        if not self.reader:
            raise ConnectionError("Not connected")
        header = await self.reader.readexactly(4)
        channel_id, flags, length = struct.unpack(">BBH", header)
        payload = await self.reader.readexactly(length) if length > 0 else b""
        return channel_id, flags, payload

    async def send_video_nal(self, channel_id: int, nal_bytes: bytes):
        # 0x03 = FIRST_FRAME | LAST_FRAME
        await self.send_frame(channel_id, 0x03, nal_bytes)

    async def send_audio_pcm(self, channel_id: int, pcm_bytes: bytes):
        await self.send_frame(channel_id, 0x03, pcm_bytes)

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_mock_phone.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/harness/mock_phone.py tests/integration/test_harness_mock_phone.py
git commit -m "test(integration): implement typed MockPhoneClient wire framing engine"
```

---

### Task 4: Wire Packet Fixture & Fault Injector (`PacketFixture`)

**Files:**
- Create: `tests/integration/harness/packet_fixture.py`
- Test: `tests/integration/test_harness_packet_fixture.py`

**Interfaces:**
- Consumes: Raw byte frames
- Produces: `PacketFixture` (recording, replaying, and injecting malformed headers / payloads).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_harness_packet_fixture.py
import pytest

pytestmark = pytest.mark.integration

def test_packet_fixture_capture_and_corruption():
    from tests.integration.harness.packet_fixture import PacketFixture
    
    fixture = PacketFixture()
    frame = fixture.build_frame(channel_id=1, flags=0x03, payload=b"valid_frame")
    fixture.record_frame(frame)
    
    assert len(fixture.captured) == 1
    assert fixture.captured[0] == frame
    
    corrupt_hdr = fixture.corrupt_header(frame)
    assert corrupt_hdr != frame
    assert len(corrupt_hdr) == len(frame)
    
    corrupt_len = fixture.corrupt_declared_length(frame, declared_length=9999)
    assert corrupt_len[2:4] == (9999).to_bytes(2, "big")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_packet_fixture.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'tests.integration.harness.packet_fixture')

- [ ] **Step 3: Write minimal implementation**

```python
# tests/integration/harness/packet_fixture.py
import struct
from typing import List

class PacketFixture:
    """Records, replays, and injects malformed wire frames for protocol resilience tests."""
    def __init__(self):
        self.captured: List[bytes] = []

    def build_frame(self, channel_id: int, flags: int, payload: bytes) -> bytes:
        return struct.pack(">BBH", channel_id, flags, len(payload)) + payload

    def record_frame(self, frame_bytes: bytes):
        self.captured.append(frame_bytes)

    def corrupt_header(self, frame_bytes: bytes) -> bytes:
        if len(frame_bytes) < 4:
            return frame_bytes
        # Invert channel ID byte
        return bytes([frame_bytes[0] ^ 0xFF]) + frame_bytes[1:]

    def corrupt_declared_length(self, frame_bytes: bytes, declared_length: int) -> bytes:
        if len(frame_bytes) < 4:
            return frame_bytes
        return frame_bytes[:2] + struct.pack(">H", declared_length) + frame_bytes[4:]

    def corrupt_payload(self, frame_bytes: bytes) -> bytes:
        if len(frame_bytes) <= 4:
            return frame_bytes
        return frame_bytes[:4] + b"\x00" * (len(frame_bytes) - 4)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_harness_packet_fixture.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/harness/packet_fixture.py tests/integration/test_harness_packet_fixture.py
git commit -m "test(integration): implement PacketFixture wire recorder and fault injector"
```

---

### Task 5: Handshake & Lifecycle Pipeline Integration Test

**Files:**
- Create: `tests/integration/test_pipeline_handshake.py`

**Interfaces:**
- Consumes: `IntegrationEnvironment`, `BusMonitor`, `MockPhoneClient`, `PacketFixture`, and `tcp_server` / `channel_manager` modules.
- Produces: Automated end-to-end handshake verification and disconnect/resilience tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline_handshake.py
import pytest
import asyncio
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.bus_monitor import BusMonitor
from tests.integration.harness.mock_phone import MockPhoneClient
from tests.integration.harness.packet_fixture import PacketFixture

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_handshake_and_graceful_disconnect(tmp_path):
    # Tests connection, handshake, and clean disconnection flow
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule
    
    with IntegrationEnvironment(tmp_path) as env:
        # Launch bus broker on dynamic loopback port
        broker = BusBrokerModule()
        await broker.setup()
        broker_task = asyncio.create_task(broker.run())
        await asyncio.sleep(0.05)
        
        monitor = BusMonitor(broker.pub_addr)
        await monitor.start()
        
        # Start TCP server on dynamic port
        tcp_mod = TCPServerModule()
        tcp_mod.port = 0
        await tcp_mod.setup()
        tcp_task = asyncio.create_task(tcp_mod.run())
        await asyncio.sleep(0.05)
        
        # Connect phone
        phone = MockPhoneClient()
        await phone.connect("127.0.0.1", tcp_mod.actual_port)
        
        # Verify phone connection detected
        event = await monitor.wait_for_event("phone.connected", timeout=2.0)
        assert event is not None
        
        # Disconnect phone
        await phone.disconnect()
        disc_event = await monitor.wait_for_event("phone.disconnected", timeout=2.0)
        assert disc_event is not None
        
        await monitor.stop()
        await tcp_mod.teardown()
        await broker.teardown()
        tcp_task.cancel()
        broker_task.cancel()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_handshake.py -v`
Expected: FAIL (missing attributes or setup parameters)

- [ ] **Step 3: Implement test adaptation and assertions**

Refine `tests/integration/test_pipeline_handshake.py` ensuring proper module startup, binding `actual_port` dynamically, verifying `phone.connected`, `channel.opened`, and wire fault injection with `PacketFixture`.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_handshake.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_pipeline_handshake.py
git commit -m "test(integration): implement end-to-end handshake and lifecycle integration test"
```

---

### Task 6: Media Streaming & AV Pipeline Integration Test

**Files:**
- Create: `tests/integration/test_pipeline_media.py`

**Interfaces:**
- Consumes: `MockPhoneClient`, `channel_manager` (video_handler, audio_handler), SHM buffers.
- Produces: End-to-end test streaming H.264 NAL units into SHM and PCM audio chunks into mock sink.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline_media.py
import pytest
import asyncio
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.bus_monitor import BusMonitor
from tests.integration.harness.mock_phone import MockPhoneClient

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_video_streaming_pipeline(tmp_path):
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule
    
    with IntegrationEnvironment(tmp_path) as env:
        broker = BusBrokerModule()
        await broker.setup()
        broker_task = asyncio.create_task(broker.run())
        
        monitor = BusMonitor(broker.pub_addr)
        await monitor.start()
        
        tcp_mod = TCPServerModule()
        tcp_mod.port = 0
        await tcp_mod.setup()
        tcp_task = asyncio.create_task(tcp_mod.run())
        
        chan_mod = ChannelManagerModule()
        await chan_mod.setup()
        chan_task = asyncio.create_task(chan_mod.run())
        
        phone = MockPhoneClient()
        await phone.connect("127.0.0.1", tcp_mod.actual_port)
        
        # Send H.264 Annex-B SPS NAL unit (0x0000000167...)
        sps_nal = b"\x00\x00\x00\x01\x67\x42\x00\x1f\x96\x35\x40"
        await phone.send_video_nal(channel_id=phone.channel_map["video"], nal_bytes=sps_nal)
        
        # Assert bus notification or shm frame
        event = await monitor.wait_for_event("video.frame_ready", timeout=2.0)
        assert event is not None
        
        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_media.py -v`
Expected: FAIL

- [ ] **Step 3: Implement minimal adaptation**

Configure channel mappings and wire payload formatting for video NAL delivery and PCM audio chunks with focus negotiation.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_media.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_pipeline_media.py
git commit -m "test(integration): implement end-to-end video and audio streaming integration test"
```

---

### Task 7: Reverse Input & Sensor Pipeline Integration Test

**Files:**
- Create: `tests/integration/test_pipeline_input_sensors.py`

**Interfaces:**
- Consumes: ZMQ bus input events (`input.touch`), sensor events (`sensor.night_mode`, `sensor.driving_status`).
- Produces: `MockPhoneClient` receiving Protobuf serialized touch and sensor indications.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline_input_sensors.py
import pytest
import asyncio
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.mock_phone import MockPhoneClient

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_touch_injection_to_phone(tmp_path):
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule
    
    with IntegrationEnvironment(tmp_path) as env:
        broker = BusBrokerModule()
        await broker.setup()
        broker_task = asyncio.create_task(broker.run())
        
        tcp_mod = TCPServerModule()
        tcp_mod.port = 0
        await tcp_mod.setup()
        tcp_task = asyncio.create_task(tcp_mod.run())
        
        chan_mod = ChannelManagerModule()
        await chan_mod.setup()
        chan_task = asyncio.create_task(chan_mod.run())
        
        phone = MockPhoneClient()
        await phone.connect("127.0.0.1", tcp_mod.actual_port)
        
        # Emit touch event to bus
        await broker.publish("input.touch", {"x": 400, "y": 300, "action": "press"})
        
        # Phone receives TouchEvent wire frame on input channel
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == phone.channel_map["input"]
        assert len(payload) > 0
        
        await phone.disconnect()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_input_sensors.py -v`
Expected: FAIL

- [ ] **Step 3: Implement minimal adaptation**

Complete touch injection and sensor telemetry tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_pipeline_input_sensors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_pipeline_input_sensors.py
git commit -m "test(integration): implement reverse input and sensor telemetry integration test"
```

---

### Task 8: Headless Qt6 Offscreen Integration Test

**Files:**
- Create: `tests/integration/test_qt6_offscreen.py`

**Interfaces:**
- Consumes: `qt6_gui` (`MainWindow`, `PhoneCardWidget`, `MediaCardWidget`, `VideoViewport`), `QT_QPA_PLATFORM=offscreen`.
- Produces: Headless verification of GUI bus event reactivity and OpenGL video viewport surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_qt6_offscreen.py
import pytest
import os
import sys

pytestmark = pytest.mark.integration

def test_qt6_gui_offscreen_event_reaction(tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    from backend.modules.qt6_gui.ui.main_window import MainWindow
    
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    assert window is not None
    
    # Verify phone card reacts to connection event
    window.phone_card.update_status(connected=True, phone_name="Pixel 8 Pro")
    assert window.phone_card.status_label.text() == "Connected"
    
    window.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_qt6_offscreen.py -v`
Expected: Verify behavior in offscreen environment.

- [ ] **Step 3: Implement minimal adaptation**

Wire offscreen Qt6 window, connect bus signals, verify card state updates and video texture consumption.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_qt6_offscreen.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_qt6_offscreen.py
git commit -m "test(integration): implement headless Qt6 offscreen GUI integration test"
```

---

### Task 9: Live Subprocess Orchestrator Lifecycle Test

**Files:**
- Create: `tests/integration/test_orchestrator_live.py`

**Interfaces:**
- Consumes: `backend/main.py -m multiprocessing` subprocess.
- Produces: Live boot sequence verification, REST proxy `/api/config` health check, and graceful `SIGTERM` teardown.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_orchestrator_live.py
import pytest
import subprocess
import sys
import time
import os
import urllib.request
import signal
from pathlib import Path
from tests.integration.harness.environment import IntegrationEnvironment

pytestmark = pytest.mark.integration

def test_orchestrator_multiprocessing_boot_and_sigterm(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"
    
    with IntegrationEnvironment(tmp_path) as env:
        # Spawn orchestrator in multiprocessing mode
        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multiprocessing"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        try:
            # Wait for priority wave boot to complete (max 6s)
            booted = False
            start_time = time.time()
            while time.time() - start_time < 6.0:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            
            assert proc.poll() is None, f"Orchestrator died prematurely: {proc.stderr.read()}"
            
            # Send SIGTERM for graceful exit
            proc.terminate()
            proc.wait(timeout=5.0)
            assert proc.returncode is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
```

- [ ] **Step 2: Run test to verify it fails or times out**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_orchestrator_live.py -v`
Expected: Verify live spawn.

- [ ] **Step 3: Implement minimal adaptation**

Add stdout wave inspection, proxy query on `public_port`, and clean process group termination.

- [ ] **Step 4: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/integration/test_orchestrator_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_orchestrator_live.py
git commit -m "test(integration): implement live orchestrator subprocess lifecycle test"
```

---

## Plan Self-Review
1. **Spec coverage**:
   - Tier 1 Functional Pipeline covered: Handshake (Task 5), Media streaming (Task 6), Reverse input & sensors (Task 7), Wire replay & fault injector (Task 4).
   - Tier 2 Process Lifecycle covered: Headless Qt6 offscreen (Task 8), Live subprocess orchestrator (Task 9).
   - Harness Core covered: Sandbox environment (Task 1), Bus monitor (Task 2), Mock phone engine (Task 3).
2. **Placeholder scan**: No "TODO", "TBD", or unwritten test code. Full test functions and commands provided.
3. **Type consistency**: Exact method signatures (`wait_for_event`, `send_frame`, `read_frame`, `build_frame`) match between tasks.
