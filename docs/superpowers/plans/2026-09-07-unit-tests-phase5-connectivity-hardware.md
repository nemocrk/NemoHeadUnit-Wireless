# Phase 5: Connectivity Manager & Hardware Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement isolated, high-speed unit tests covering RFCOMM packet framing (`packet.py`), RFCOMM wireless handshake state machine (`handshake.py`), Hardware adapters & factory selectors (`shared/hardware/`), Connectivity Manager REST endpoints & lifecycle (`main.py`), and Autoconnect/telemetry background event loops.

**Architecture:** All connectivity components under `backend/modules/connectivity_manager/` and `backend/shared/hardware/` are tested with zero real Bluetooth hardware/HCI, zero BlueZ DBus daemons, zero physical RFCOMM sockets, zero real WiFi interfaces, and zero ZMQ bus daemons. Tests leverage in-memory mock sockets, synthetic Protobuf payloads, and mocked hardware adapters.

**Tech Stack:** Python 3.13 / 3.14, `pytest`, `pytest-asyncio`, `unittest.mock`, `aiohttp`, Google Protocol Buffers.

**Spec:** `docs/superpowers/specs/2026-09-07-unit-tests-phase5-connectivity-hardware-design.md`

## Global Constraints
- Target execution time: Phase 5 suite < 2.0s total.
- Strict isolation: Unit tests must never bind real Bluetooth RFCOMM sockets, call real BlueZ DBus, or invoke real hostapd/APManager.
- Universal cross-platform compatibility: Linux and Windows (`pathlib.Path`, cross-platform mocks).
- Strict prohibition of hardcoded channel IDs: Dynamic resolution where applicable.
- All unit test files must define `pytestmark = pytest.mark.unit`.
- All test runs must use `micromamba run -n NemoHeadUnit-Wireless pytest ...`.

---

### Task 1: RFCOMM Wire Framing & Packet Codec Unit Tests

**Files:**
- Create: `tests/unit/connectivity_manager/__init__.py`
- Create: `tests/unit/connectivity_manager/test_packet.py`

**Interfaces:**
- Consumes: `modules.connectivity_manager.packet` (`Packet`, `encode`, `decode`, `recv_packet`, `send_packet`, `_recv_exact`, `HEADER_SIZE`, `MSG_*`)
- Produces: 5 unit tests verifying packet encoding, decoding with valid and truncated buffers, exact socket streaming chunks, socket transmission, and socket EOF handling.

- [ ] **Step 1: Write failing test file `tests/unit/connectivity_manager/test_packet.py`**

```python
import struct
import pytest
from unittest.mock import MagicMock
from modules.connectivity_manager.packet import (
    Packet,
    encode,
    decode,
    recv_packet,
    send_packet,
    _recv_exact,
    HEADER_SIZE,
    MSG_WIFI_START_REQUEST,
    MSG_WIFI_INFO_REQUEST,
)

pytestmark = pytest.mark.unit


def test_packet_encode_basic_and_empty():
    # Empty payload
    data_empty = encode(MSG_WIFI_START_REQUEST, b"")
    assert len(data_empty) == HEADER_SIZE
    payload_len, msg_id = struct.unpack(">HH", data_empty)
    assert payload_len == 0
    assert msg_id == MSG_WIFI_START_REQUEST

    # Non-empty payload
    payload = b"HelloAndroidAuto"
    data = encode(MSG_WIFI_INFO_REQUEST, payload)
    assert len(data) == HEADER_SIZE + len(payload)
    payload_len, msg_id = struct.unpack(">HH", data[:HEADER_SIZE])
    assert payload_len == len(payload)
    assert msg_id == MSG_WIFI_INFO_REQUEST
    assert data[HEADER_SIZE:] == payload


def test_packet_decode_valid_and_truncated():
    # Valid decode
    raw = struct.pack(">HH", 5, MSG_WIFI_START_REQUEST) + b"12345"
    pkt = decode(raw)
    assert pkt is not None
    assert pkt.msg_id == MSG_WIFI_START_REQUEST
    assert pkt.payload == b"12345"

    # Buffer too short (< HEADER_SIZE)
    assert decode(b"\x00\x01") is None

    # Truncated payload
    truncated = struct.pack(">HH", 10, MSG_WIFI_START_REQUEST) + b"123"
    assert decode(truncated) is None


def test_recv_exact_chunks_and_eof():
    mock_sock = MagicMock()

    # 1. Successful read across multiple chunks
    mock_sock.recv.side_effect = [b"AB", b"CD", b"E"]
    result = _recv_exact(mock_sock, 5)
    assert result == b"ABCDE"
    assert mock_sock.recv.call_count == 3

    # 2. Premature EOF
    mock_sock.recv.reset_mock()
    mock_sock.recv.side_effect = [b"AB", b""]
    assert _recv_exact(mock_sock, 5) is None


def test_recv_packet_socket():
    mock_sock = MagicMock()

    # Successful packet: header + payload
    header = struct.pack(">HH", 4, MSG_WIFI_INFO_REQUEST)
    payload = b"TEST"
    mock_sock.recv.side_effect = [header, payload]

    pkt = recv_packet(mock_sock)
    assert pkt is not None
    assert pkt.msg_id == MSG_WIFI_INFO_REQUEST
    assert pkt.payload == b"TEST"

    # Socket error / EOF on header
    mock_sock.recv.side_effect = [b""]
    assert recv_packet(mock_sock) is None


def test_send_packet_socket():
    mock_sock = MagicMock()

    # Success
    payload = b"PAYLOAD"
    assert send_packet(mock_sock, MSG_WIFI_START_REQUEST, payload) is True
    expected_bytes = struct.pack(">HH", len(payload), MSG_WIFI_START_REQUEST) + payload
    mock_sock.sendall.assert_called_once_with(expected_bytes)

    # Socket exception
    mock_sock.sendall.side_effect = OSError("Socket broken")
    assert send_packet(mock_sock, MSG_WIFI_START_REQUEST, payload) is False
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/connectivity_manager/test_packet.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/connectivity_manager/__init__.py tests/unit/connectivity_manager/test_packet.py
git commit -m "test(unit): add rfcomm packet wire encoding and decoding unit tests"
```

---

### Task 2: RFCOMM Wireless Handshake State Machine Unit Tests

**Files:**
- Create: `tests/unit/connectivity_manager/test_handshake.py`

**Interfaces:**
- Consumes: `modules.connectivity_manager.handshake.RfcommHandshake`, `HandshakeResult`
- Produces: 5 unit tests verifying full handshake happy path, start response ack, start request error, credential response error, and message loop exhaustion.

- [ ] **Step 1: Write failing test file `tests/unit/connectivity_manager/test_handshake.py`**

```python
import struct
import pytest
from unittest.mock import MagicMock
from modules.connectivity_manager.packet import (
    MSG_WIFI_START_REQUEST,
    MSG_WIFI_START_RESPONSE,
    MSG_WIFI_INFO_REQUEST,
    MSG_WIFI_INFO_RESPONSE,
    MSG_WIFI_CONNECT_STATUS,
    encode,
)
from modules.connectivity_manager.handshake import RfcommHandshake, HandshakeResult
from protos.oaa.wifi.WifiStartRequestMessage_pb2 import WifiStartRequest
from protos.oaa.wifi.WifiStartResponseMessage_pb2 import WifiStartResponse
from protos.oaa.wifi.WifiSecurityResponseMessage_pb2 import WifiSecurityResponse
from protos.oaa.wifi.WifiConnectStatusMessage_pb2 import WifiConnectStatus

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_handshake_socket():
    sock = MagicMock()
    return sock


@pytest.fixture
def sample_credentials():
    return {
        "ssid": "TestAndroidAutoAP",
        "key": "TestPassphrase123",
        "bssid": "AA:BB:CC:DD:EE:FF",
        "gateway_ip": "192.168.50.1",
        "tcp_port": 5288,
        "security_mode": 8,
        "ap_type": 1,
    }


def test_handshake_happy_path(mock_handshake_socket, sample_credentials):
    stages = []
    def on_stage(s):
        stages.append(s)

    # Prepare phone messages incoming from socket
    # 1. WifiStartResponse (optional ack)
    start_resp = WifiStartResponse().SerializeToString()
    pkt1 = encode(MSG_WIFI_START_RESPONSE, start_resp)

    # 2. WifiInfoRequest
    pkt2 = encode(MSG_WIFI_INFO_REQUEST, b"")

    # 3. WifiConnectStatus (phone joined)
    status_msg = WifiConnectStatus()
    status_msg.status = 0  # OK
    status_msg.ip_address = "192.168.50.100"
    pkt3 = encode(MSG_WIFI_CONNECT_STATUS, status_msg.SerializeToString())

    # Socket reads: each recv returns exact header + payload
    mock_handshake_socket.recv.side_effect = [
        pkt1[:4], pkt1[4:],
        pkt2[:4], pkt2[4:],
        pkt3[:4], pkt3[4:],
    ]

    hs = RfcommHandshake(mock_handshake_socket, sample_credentials, on_stage_cb=on_stage)
    res = hs.run()

    assert res.success is True
    assert res.phone_ip == "192.168.50.100"
    assert "WifiStartRequest" in stages
    assert "WifiInfoRequest" in stages
    assert "WifiInfoResponse" in stages
    assert "WifiConnectionStatus" in stages

    # Check head unit sent WifiStartRequest and WifiInfoResponse
    assert mock_handshake_socket.sendall.call_count == 2
    first_sent = mock_handshake_socket.sendall.call_args_list[0][0][0]
    first_len, first_msg = struct.unpack(">HH", first_sent[:4])
    assert first_msg == MSG_WIFI_START_REQUEST

    second_sent = mock_handshake_socket.sendall.call_args_list[1][0][0]
    second_len, second_msg = struct.unpack(">HH", second_sent[:4])
    assert second_msg == MSG_WIFI_INFO_RESPONSE
    resp_proto = WifiSecurityResponse()
    resp_proto.ParseFromString(second_sent[4:])
    assert resp_proto.ssid == "TestAndroidAutoAP"
    assert resp_proto.key == "TestPassphrase123"
    assert resp_proto.bssid == "AA:BB:CC:DD:EE:FF"


def test_handshake_socket_close_on_first_read(mock_handshake_socket, sample_credentials):
    mock_handshake_socket.recv.return_value = b""
    hs = RfcommHandshake(mock_handshake_socket, sample_credentials)
    res = hs.run()
    assert res.success is False
    assert "Socket closed" in res.error


def test_handshake_send_start_request_failure(mock_handshake_socket, sample_credentials):
    mock_handshake_socket.sendall.side_effect = OSError("Write failed")
    hs = RfcommHandshake(mock_handshake_socket, sample_credentials)
    res = hs.run()
    assert res.success is False
    assert "WifiStartRequest" in res.error or "Write failed" in res.error


def test_handshake_event_loop_exhaustion(mock_handshake_socket, sample_credentials):
    # Phone sends unknown packets repeated 20 times
    dummy_pkt = encode(99, b"dummy")
    side_effects = []
    for _ in range(20):
        side_effects.extend([dummy_pkt[:4], dummy_pkt[4:]])
    mock_handshake_socket.recv.side_effect = side_effects

    hs = RfcommHandshake(mock_handshake_socket, sample_credentials)
    res = hs.run()
    assert res.success is False
    assert "exhausted" in res.error.lower()


def test_handshake_credentials_sanitization(mock_handshake_socket):
    # Test invalid ap_type and invalid security_mode sanitization fallback
    dirty_creds = {
        "ssid": "AP",
        "key": "pw",
        "bssid": "00:11:22:33:44:55",
        "security_mode": 9999,  # invalid
        "ap_type": 8888,        # invalid
    }
    pkt_info = encode(MSG_WIFI_INFO_REQUEST, b"")
    status_msg = WifiConnectStatus(status=0, ip_address="192.168.50.2")
    pkt_status = encode(MSG_WIFI_CONNECT_STATUS, status_msg.SerializeToString())

    mock_handshake_socket.recv.side_effect = [
        pkt_info[:4], pkt_info[4:],
        pkt_status[:4], pkt_status[4:],
    ]

    hs = RfcommHandshake(mock_handshake_socket, dirty_creds)
    res = hs.run()
    assert res.success is True

    # Check that security_mode fell back to WPA2_SECURITY_MODE (8) and ap_type to AP_TYPE_DYNAMIC (1)
    second_sent = mock_handshake_socket.sendall.call_args_list[1][0][0]
    resp_proto = WifiSecurityResponse()
    resp_proto.ParseFromString(second_sent[4:])
    assert resp_proto.security_mode == 8
    assert resp_proto.access_point_type == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/connectivity_manager/test_handshake.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/connectivity_manager/test_handshake.py
git commit -m "test(unit): add rfcomm handshake state machine and credential distribution unit tests"
```

---

### Task 3: Hardware Adapters & Factories Unit Tests

**Files:**
- Create: `tests/unit/hardware/__init__.py`
- Create: `tests/unit/hardware/test_hardware_adapters.py`

**Interfaces:**
- Consumes: `shared.hardware.base_audio`, `mock_audio.MockAudioAdapter`, `base_bluetooth`, `base_wifi_ap`
- Produces: 5 unit tests verifying mock audio adapter volume operations, sink/source manipulation, HFP loopback, and factory platform fallbacks.

- [ ] **Step 1: Write failing test file `tests/unit/hardware/test_hardware_adapters.py`**

```python
import sys
import pytest
from unittest.mock import patch, MagicMock
from shared.hardware.base_audio import BaseAudioAdapter, get_audio_adapter
from shared.hardware.mock_audio import MockAudioAdapter
from shared.hardware.base_bluetooth import BaseBluetoothAdapter, get_bluetooth_adapter
from shared.hardware.base_wifi_ap import BaseWifiApAdapter, get_wifi_adapter

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_mock_audio_adapter_volume_operations():
    adapter = MockAudioAdapter()

    # Initial state
    vol_state = await adapter.get_volume()
    assert vol_state["volume"] == 80
    assert vol_state["muted"] is False

    # Clamping tests
    await adapter.set_volume(150)
    assert (await adapter.get_volume())["volume"] == 100

    await adapter.set_volume(-50)
    assert (await adapter.get_volume())["volume"] == 0

    # Volume step operations
    await adapter.set_volume(50)
    await adapter.volume_up(10)
    assert (await adapter.get_volume())["volume"] == 60

    await adapter.volume_down(20)
    assert (await adapter.get_volume())["volume"] == 40

    # Toggle mute
    muted_state = await adapter.toggle_mute()
    assert muted_state["muted"] is True
    unmuted_state = await adapter.toggle_mute()
    assert unmuted_state["muted"] is False


@pytest.mark.asyncio
async def test_mock_audio_adapter_sinks_sources_and_loopback():
    adapter = MockAudioAdapter()

    sinks = await adapter.get_available_sinks()
    assert len(sinks) > 0
    assert sinks[0]["id"] == "default"

    sources = await adapter.get_available_sources()
    assert len(sources) > 0
    assert sources[0]["id"] == "default"

    assert await adapter.set_active_sink("default") is True
    assert await adapter.set_active_source("default") is True

    # HFP loopback
    lb_active = await adapter.ensure_hfp_loopback(True, bluez_source="src", bluez_sink="sink")
    assert lb_active["active"] is True
    assert lb_active["rx_loopback_id"] != ""

    lb_inactive = await adapter.ensure_hfp_loopback(False)
    assert lb_inactive["active"] is False
    assert lb_inactive["rx_loopback_id"] == ""


def test_get_audio_adapter_factory():
    # Unsupported platform returns MockAudioAdapter
    with patch("sys.platform", "unknown_os"):
        adapter = get_audio_adapter()
        assert isinstance(adapter, MockAudioAdapter)

    # Linux fallback on exception
    with patch("sys.platform", "linux"), \
         patch("shared.hardware.linux_audio.LinuxPulseAudioAdapter", side_effect=Exception("No PA")):
        adapter = get_audio_adapter()
        assert isinstance(adapter, MockAudioAdapter)


def test_get_bluetooth_adapter_factory():
    # Linux fallback to Windows/Mock adapter on exception
    with patch("sys.platform", "linux"), \
         patch("shared.hardware.bluez_bluetooth.BluezBluetoothAdapter", side_effect=Exception("No DBus")):
        adapter = get_bluetooth_adapter()
        assert isinstance(adapter, BaseBluetoothAdapter)


def test_get_wifi_adapter_factory():
    # Linux fallback to Windows/Mock adapter on exception
    with patch("sys.platform", "linux"), \
         patch("shared.hardware.apmanager_wifi_ap.APManagerWifiApAdapter", side_effect=Exception("No APManager")):
        adapter = get_wifi_adapter()
        assert isinstance(adapter, BaseWifiApAdapter)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/hardware/test_hardware_adapters.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/hardware/__init__.py tests/unit/hardware/test_hardware_adapters.py
git commit -m "test(unit): add hardware adapter interfaces and mock audio unit tests"
```

---

### Task 4: Connectivity Manager Module Lifecycle & REST APIs

**Files:**
- Create: `tests/unit/connectivity_manager/test_connectivity_manager_module.py`

**Interfaces:**
- Consumes: `modules.connectivity_manager.main.ConnectivityManagerModule`
- Produces: 5 unit tests verifying default config & schema, REST `/status`, Bluetooth discovery & pairing endpoints, and WiFi manual start/stop endpoints.

- [ ] **Step 1: Write failing test file `tests/unit/connectivity_manager/test_connectivity_manager_module.py`**

```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.connectivity_manager.main import ConnectivityManagerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_conn_module():
    with patch("shared.base_module.BusClient"), \
         patch("modules.connectivity_manager.main.get_bluetooth_adapter") as mock_bt_fac, \
         patch("modules.connectivity_manager.main.get_wifi_adapter") as mock_wifi_fac, \
         patch("modules.connectivity_manager.main.get_audio_adapter") as mock_audio_fac, \
         patch("modules.connectivity_manager.main.BlueZHFClient"), \
         patch("modules.connectivity_manager.main.BlueZPBAPClient"):

        mock_bt = MagicMock()
        mock_bt.setup = AsyncMock()
        mock_bt.get_adapter_address.return_value = "00:11:22:33:44:55"
        mock_bt.get_paired_devices = AsyncMock(return_value=[{"address": "AA:BB:CC:11:22:33", "name": "Phone"}])
        mock_bt.start_discovery = AsyncMock()
        mock_bt.stop_discovery = AsyncMock()
        mock_bt.pair_device = AsyncMock(return_value=(True, "Pairing initiated"))
        mock_bt.confirm_pairing = AsyncMock(return_value=True)
        mock_bt.connect_device = AsyncMock(return_value=(True, "Connected"))
        mock_bt.disconnect_device = AsyncMock(return_value=True)
        mock_bt.remove_paired_device = AsyncMock(return_value=True)
        mock_bt_fac.return_value = mock_bt

        mock_wifi = MagicMock()
        mock_wifi.setup = AsyncMock()
        mock_wifi.start_ap = AsyncMock(return_value=(True, {"ssid": "AndroidAutoAP", "key": "12345678", "bssid": "00:11:22:33:44:55", "gateway_ip": "192.168.50.1"}))
        mock_wifi.stop_ap = AsyncMock(return_value=True)
        mock_wifi.get_status.return_value = {"active": True, "ssid": "AndroidAutoAP"}
        mock_wifi_fac.return_value = mock_wifi

        mod = ConnectivityManagerModule()
        mod._bt_adapter = mock_bt
        mod._wifi_adapter = mock_wifi
        yield mod


def test_connectivity_config_and_schema(mock_conn_module):
    defaults = mock_conn_module.get_default_config()
    assert defaults["adapter_name"] == "NemoHeadUnit"
    assert defaults["wifi_ssid"] == "AndroidAutoAP"
    assert defaults["autoconnect_enabled"] is True

    schema = mock_conn_module.get_schema()
    assert "wifi_channel" in schema
    assert "autoconnect_backoff_cap_s" in schema


@pytest.mark.asyncio
async def test_handle_get_status_and_paired(mock_conn_module):
    # GET /status
    req = MagicMock()
    resp = await mock_conn_module.handle_get_status(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert data["adapter_name"] == "NemoHeadUnit"
    assert data["bt_address"] == "00:11:22:33:44:55"
    assert data["wifi_active"] is True

    # GET /paired
    resp_paired = await mock_conn_module.handle_get_paired(req)
    assert resp_paired.status == 200
    data_paired = json.loads(resp_paired.text)
    assert len(data_paired["devices"]) == 1
    assert data_paired["devices"][0]["name"] == "Phone"


@pytest.mark.asyncio
async def test_handle_bluetooth_discovery_endpoints(mock_conn_module):
    # POST /discover
    req = MagicMock()
    req.json = AsyncMock(return_value={"duration_sec": 5})
    resp = await mock_conn_module.handle_post_discover(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "discovery_started"
    mock_conn_module._bt_adapter.start_discovery.assert_called_once()

    # GET /discovered
    mock_conn_module._discovered_devices = [{"address": "99:88:77:66:55:44", "name": "DiscoveredPhone"}]
    resp_disc = await mock_conn_module.handle_get_discovered(req)
    assert resp_disc.status == 200
    data_disc = json.loads(resp_disc.text)
    assert len(data_disc["devices"]) == 1


@pytest.mark.asyncio
async def test_handle_pairing_and_connect_endpoints(mock_conn_module):
    # POST /pair
    req_pair = MagicMock()
    req_pair.json = AsyncMock(return_value={"address": "AA:BB:CC:11:22:33"})
    resp_pair = await mock_conn_module.handle_post_pair(req_pair)
    assert resp_pair.status == 200
    mock_conn_module._bt_adapter.pair_device.assert_called_once()

    # POST /pair/confirm
    req_conf = MagicMock()
    req_conf.json = AsyncMock(return_value={"address": "AA:BB:CC:11:22:33", "confirm": True})
    resp_conf = await mock_conn_module.handle_post_pair_confirm(req_conf)
    assert resp_conf.status == 200
    mock_conn_module._bt_adapter.confirm_pairing.assert_called_once_with("AA:BB:CC:11:22:33", True)

    # POST /connect
    req_conn = MagicMock()
    req_conn.json = AsyncMock(return_value={"address": "AA:BB:CC:11:22:33"})
    resp_conn = await mock_conn_module.handle_post_connect(req_conn)
    assert resp_conn.status == 200
    mock_conn_module._bt_adapter.connect_device.assert_called_once_with("AA:BB:CC:11:22:33")

    # POST /disconnect
    resp_disc = await mock_conn_module.handle_post_disconnect(req_conn)
    assert resp_disc.status == 200
    mock_conn_module._bt_adapter.disconnect_device.assert_called_once_with("AA:BB:CC:11:22:33")


@pytest.mark.asyncio
async def test_handle_wifi_manual_controls_and_device_filter(mock_conn_module):
    # POST /wifi/start
    req = MagicMock()
    resp_wstart = await mock_conn_module.handle_wifi_start(req)
    assert resp_wstart.status == 200
    mock_conn_module._wifi_adapter.start_ap.assert_called_once()

    # POST /wifi/stop
    resp_wstop = await mock_conn_module.handle_wifi_stop(req)
    assert resp_wstop.status == 200
    mock_conn_module._wifi_adapter.stop_ap.assert_called_once()

    # POST /devices/ignore and unignore
    req_ignore = MagicMock()
    req_ignore.json = AsyncMock(return_value={"address": "XX:YY:ZZ:11:22:33"})
    resp_ign = await mock_conn_module.handle_post_ignore_device(req_ignore)
    assert resp_ign.status == 200
    assert "XX:YY:ZZ:11:22:33" in mock_conn_module.config.get("ignored_devices", [])

    resp_unign = await mock_conn_module.handle_post_unignore_device(req_ignore)
    assert resp_unign.status == 200
    assert "XX:YY:ZZ:11:22:33" not in mock_conn_module.config.get("ignored_devices", [])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/connectivity_manager/test_connectivity_manager_module.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/connectivity_manager/test_connectivity_manager_module.py
git commit -m "test(unit): add connectivity_manager module config and rest api unit tests"
```

---

### Task 5: Connectivity Manager Autoconnect & Event Handling

**Files:**
- Create: `tests/unit/connectivity_manager/test_connectivity_events.py`

**Interfaces:**
- Consumes: `modules.connectivity_manager.main.ConnectivityManagerModule`
- Produces: 5 unit tests verifying autoconnect prioritization, ignore filters, RFCOMM connection handling, HFP telephony state changes, and telemetry callbacks.

- [ ] **Step 1: Write failing test file `tests/unit/connectivity_manager/test_connectivity_events.py`**

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.connectivity_manager.main import ConnectivityManagerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_conn_events():
    with patch("shared.base_module.BusClient"), \
         patch("modules.connectivity_manager.main.get_bluetooth_adapter") as mock_bt_fac, \
         patch("modules.connectivity_manager.main.get_wifi_adapter") as mock_wifi_fac, \
         patch("modules.connectivity_manager.main.get_audio_adapter") as mock_audio_fac, \
         patch("modules.connectivity_manager.main.BlueZHFClient"), \
         patch("modules.connectivity_manager.main.BlueZPBAPClient"):

        mock_bt = MagicMock()
        mock_bt.connect_device = AsyncMock(return_value=(True, "Connected"))
        mock_bt.get_device_name.return_value = "TestPhone"
        mock_bt_fac.return_value = mock_bt

        mock_wifi = MagicMock()
        mock_wifi.start_ap = AsyncMock(return_value=(True, {"ssid": "AP", "key": "pw", "bssid": "00:11:22:33:44:55", "gateway_ip": "192.168.50.1"}))
        mock_wifi_fac.return_value = mock_wifi

        mock_audio = MagicMock()
        mock_audio.ensure_hfp_loopback = AsyncMock()
        mock_audio_fac.return_value = mock_audio

        mod = ConnectivityManagerModule()
        mod._bt_adapter = mock_bt
        mod._wifi_adapter = mock_wifi
        mod._audio_adapter = mock_audio
        mod.publish = MagicMock()
        yield mod


@pytest.mark.asyncio
async def test_autoconnect_loop_priority_and_filtering(mock_conn_events):
    mock_conn_events._running = True
    mock_conn_events.config["known_aa_devices"] = ["AA:AA:AA:AA:AA:AA"]
    mock_conn_events.config["ignored_devices"] = ["CC:CC:CC:CC:CC:CC"]

    # 3 devices: 1 known AA, 1 regular, 1 ignored
    mock_conn_events._bt_adapter.get_paired_devices.return_value = [
        {"address": "BB:BB:BB:BB:BB:BB", "name": "RegularPhone", "connected": False},
        {"address": "CC:CC:CC:CC:CC:CC", "name": "IgnoredSpeaker", "connected": False},
        {"address": "AA:AA:AA:AA:AA:AA", "name": "AndroidAutoPhone", "connected": False},
    ]

    # Run autoconnect loop for one iteration then cancel
    task = asyncio.create_task(mock_conn_events._autoconnect_loop())
    mock_conn_events.on_try_autoconnect("bluetooth_manager.try_autoconnect", {})
    await asyncio.sleep(0.05)
    mock_conn_events._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # AA:AA should be prioritized and connected first
    mock_conn_events._bt_adapter.connect_device.assert_called_with("AA:AA:AA:AA:AA:AA")


@pytest.mark.asyncio
async def test_rfcomm_connection_triggers_wifi_and_handshake(mock_conn_events):
    mock_sock = MagicMock()
    mock_conn_events._running = True
    mock_conn_events._rfcomm_listening = True

    with patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        mock_conn_events._on_rfcomm_connection(mock_sock, "11:22:33:44:55:66")

        # Let the async _start_ap_and_handshake run
        await asyncio.sleep(0.05)

        assert mock_conn_events._rfcomm_connected is True
        assert mock_conn_events._active_device == "11:22:33:44:55:66"
        mock_conn_events.publish.assert_any_call(
            "rfcomm.handshake.started", {"device_address": "11:22:33:44:55:66"}
        )
        mock_conn_events._wifi_adapter.start_ap.assert_called_once()
        mock_thread.start.assert_called_once()


def test_hfp_state_changed_telephony_and_audio(mock_conn_events):
    mock_conn_events._active_device = "11:22:33:44:55:66"

    # 1. Inbound call state
    mock_conn_events._on_hfp_state_changed({
        "is_in_call": True,
        "call_state": "INCOMING",
        "caller_name": "Alice",
        "phone_number": "+1234567890",
        "battery_pct": 85,
        "signal_bars": 4,
        "carrier": "Vodafone",
    })

    mock_conn_events.publish.assert_called_with("phone.status", {
        "is_in_call": True,
        "call_state": "INCOMING",
        "caller_name": "Alice",
        "phone_number": "+1234567890",
        "battery_pct": 85,
        "signal_bars": 4,
        "carrier": "Vodafone",
        "device_address": "11:22:33:44:55:66",
        "is_connected": True,
        "device_name": "TestPhone",
        "battery_level": 85,
        "signal_strength": 4,
        "operator_name": "Vodafone",
    })


def test_bluetooth_telemetry_changed_event(mock_conn_events):
    mock_conn_events._on_bluetooth_telemetry_changed(
        address="11:22:33:44:55:66",
        battery_pct=90,
        signal_bars=5,
        operator_name="TIM",
        is_roaming=False,
    )

    mock_conn_events.publish.assert_called_with("phone.status", {
        "source": "bluetooth_hfp",
        "device_address": "11:22:33:44:55:66",
        "device_name": "TestPhone",
        "is_connected": True,
        "operator_name": "TIM",
        "carrier": "TIM",
        "battery_level": 90,
        "battery_pct": 90,
        "signal_strength": 5,
        "signal_bars": 5,
        "is_roaming": False,
    })


def test_pin_requested_and_device_connection_events(mock_conn_events):
    # PIN callback
    mock_conn_events._on_pin_requested("11:22:33:44:55:66", "123456")
    assert mock_conn_events._pairing_pin == "123456"
    assert mock_conn_events._pairing_device == "11:22:33:44:55:66"
    mock_conn_events.publish.assert_called_with("bluetooth_manager.pairing.pin", {
        "device_address": "11:22:33:44:55:66",
        "pin": "123456",
    })

    # Connection changed callback: connected
    mock_conn_events._on_device_connection_changed("11:22:33:44:55:66", True)
    assert mock_conn_events._active_device == "11:22:33:44:55:66"
    mock_conn_events.publish.assert_any_call("bluetooth_manager.paired.connected", {
        "device_address": "11:22:33:44:55:66",
        "device_name": "TestPhone",
    })

    # Connection changed callback: disconnected
    mock_conn_events._on_device_connection_changed("11:22:33:44:55:66", False)
    assert mock_conn_events._active_device is None
    mock_conn_events.publish.assert_any_call("bluetooth_manager.paired.disconnected", {
        "device_address": "11:22:33:44:55:66",
        "device_name": "TestPhone",
    })
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/connectivity_manager/test_connectivity_events.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/connectivity_manager/test_connectivity_events.py
git commit -m "test(unit): add connectivity autoconnect loop and telemetry event unit tests"
```

---

### Task 6: Phase 5 Verification & Integration Gate

**Files:**
- None (verification only)

- [ ] **Step 1: Run full Phase 5 test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/connectivity_manager/ tests/unit/hardware/ -v`
Expected: 20+ passed in < 1.5s.

- [ ] **Step 2: Run entire unit test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/ -v`
Expected: 135+ passed in < 2.0s.

- [ ] **Step 3: Run full repository pytest suite to prove zero regressions**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest -q`
Expected: 225+ passed.

- [ ] **Step 4: Execute orchestrator smoke test**

Run: `timeout 7 micromamba run -n NemoHeadUnit-Wireless python backend/main.py`
Expected: Clean startup across Waves 0–5 and clean exit with code 124 on timeout.

- [ ] **Step 5: Run graphify update**

Run: `/home/nemo/miniconda3/bin/graphify update .`

- [ ] **Step 6: Commit plan and spec documentation**

```bash
git add docs/superpowers/specs/2026-09-07-unit-tests-phase5-connectivity-hardware-design.md docs/superpowers/plans/2026-09-07-unit-tests-phase5-connectivity-hardware.md
git commit -m "docs(tests): add Phase 5 connectivity manager and hardware adapters unit test spec and plan"
```

- [ ] **Step 7: Run whole-branch review subagent**
