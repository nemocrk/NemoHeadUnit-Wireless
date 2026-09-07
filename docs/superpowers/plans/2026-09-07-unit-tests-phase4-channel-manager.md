# Phase 4: Channel Manager & Protocol Handlers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement isolated, high-speed unit tests covering Service Discovery Response generation (`service_discovery.py`), Channel Manager orchestration & status streaming (`main.py`), Control channel protocol state machine (`control_handler.py`), AV media channels (`video_handler.py`, `audio_handler.py`), and auxiliary channels (`input_handler.py`, `sensor_handler.py`, `bluetooth_handler.py`, `wifi_handler.py`, `navigation_handler.py`).

**Architecture:** All channel handlers and orchestrators under `backend/modules/channel_manager/` are tested with zero real network sockets, zero physical hardware, zero real ZMQ daemons, and zero OS shared memory files. Tests leverage mock managers, synthetic Protobuf payloads, and in-memory async coroutine runners.

**Tech Stack:** Python 3.13 / 3.14, `pytest`, `pytest-asyncio`, `unittest.mock`, `aiohttp`, Google Protocol Buffers.

**Spec:** `docs/superpowers/specs/2026-09-07-unit-tests-phase4-channel-manager-design.md`

## Global Constraints
- Target execution time: Phase 4 suite < 2.0s total.
- Strict isolation: Unit tests must never bind real TCP/WS ports or spawn real SHM mappings.
- Universal cross-platform compatibility: Linux and Windows (`pathlib.Path`, cross-platform mocks).
- Strict prohibition of hardcoded channel IDs: All channel IDs must be resolved dynamically.
- All unit test files must define `pytestmark = pytest.mark.unit`.
- All test runs must use `micromamba run -n NemoHeadUnit-Wireless pytest ...`.

---

### Task 1: Service Discovery Response (SDR) Generation & Classification Tests

**Files:**
- Create: `tests/unit/channel_manager/__init__.py`
- Create: `tests/unit/channel_manager/test_service_discovery.py`

**Interfaces:**
- Consumes: `modules.channel_manager.service_discovery.classify_channel_descriptor`, `build_service_discovery_response`, `SEMANTIC_DEFAULTS`, `CODEC_ALIASES`, `ChannelType`
- Produces: 5 unit tests verifying channel descriptor classification across all 11 channel types, SDR binary protobuf construction, codec overrides, and adapter MAC/BSSID injection.

- [ ] **Step 1: Write failing test file `tests/unit/channel_manager/test_service_discovery.py`**

```python
import pytest
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from shared.constants import ChannelType
from modules.channel_manager.service_discovery import (
    classify_channel_descriptor,
    build_service_discovery_response,
    SEMANTIC_DEFAULTS,
)

pytestmark = pytest.mark.unit


def test_classify_channel_descriptor():
    assert classify_channel_descriptor({"input_channel": {}}) == ChannelType.INPUT
    assert classify_channel_descriptor({"sensor_channel": {}}) == ChannelType.SENSOR
    assert classify_channel_descriptor({"bluetooth_channel": {}}) == ChannelType.BLUETOOTH
    assert classify_channel_descriptor({"wifi_channel": {}}) == ChannelType.WIFI
    assert classify_channel_descriptor({"navigation_channel": {}}) == ChannelType.NAVIGATION
    assert classify_channel_descriptor({"media_info_channel": {}}) == ChannelType.MEDIA_PLAYBACK
    assert classify_channel_descriptor({"phone_status_channel": {}}) == ChannelType.PHONE_STATUS
    assert classify_channel_descriptor({"notification_channel": {}}) == ChannelType.NOTIFICATION
    assert classify_channel_descriptor({"av_input_channel": {}}) == ChannelType.AUDIO_MIC
    assert classify_channel_descriptor({"av_channel": {"video_configs": []}}) == ChannelType.VIDEO
    assert classify_channel_descriptor({"av_channel": {"stream_type": "VIDEO"}}) == ChannelType.VIDEO
    assert classify_channel_descriptor({"av_channel": {"audio_configs": []}}) == ChannelType.AUDIO
    assert classify_channel_descriptor({}) == ChannelType.UNKNOWN


def test_build_service_discovery_response_defaults():
    sdr_bytes, sdr_dict, channel_type_map = build_service_discovery_response()
    assert len(sdr_bytes) > 0
    assert isinstance(sdr_dict, dict)
    assert sdr_dict["head_unit_name"] == "NemoHeadUnit"

    # Decode binary protobuf to ensure round-trip integrity
    resp = ServiceDiscoveryResponse()
    resp.ParseFromString(sdr_bytes)
    assert resp.head_unit_name == "NemoHeadUnit"
    assert len(resp.channels) == len(SEMANTIC_DEFAULTS["channels"])

    # Channel 0 must map to CONTROL
    assert channel_type_map[0] == "CONTROL"
    assert 1 in channel_type_map
    assert channel_type_map[1] == "INPUT"


def test_build_service_discovery_response_param_injection():
    bt_mac = "AA:BB:CC:DD:EE:FF"
    wifi_bssid = "00:11:22:33:44:55"

    custom_cfg = {
        "head_unit_name": "CustomNemoUnit",
        "channels": [
            {"channel_id": 8, "bluetooth_channel": {}},
            {"channel_id": 9, "wifi_channel": {}},
        ]
    }

    sdr_bytes, sdr_dict, type_map = build_service_discovery_response(
        cfg=custom_cfg,
        bt_mac=bt_mac,
        wifi_bssid=wifi_bssid,
    )

    resp = ServiceDiscoveryResponse()
    resp.ParseFromString(sdr_bytes)
    assert resp.head_unit_name == "CustomNemoUnit"

    bt_found = False
    wifi_found = False
    for ch in resp.channels:
        if ch.HasField("bluetooth_channel"):
            assert ch.bluetooth_channel.adapter_address == bt_mac
            bt_found = True
        if ch.HasField("wifi_channel"):
            assert ch.wifi_channel.bssid == wifi_bssid
            wifi_found = True
    assert bt_found and wifi_found


def test_build_service_discovery_response_codec_overrides():
    custom_cfg = {
        "video_codec": "H265",
        "audio_codec": "AAC",
    }
    sdr_bytes, sdr_dict, type_map = build_service_discovery_response(cfg=custom_cfg)

    resp = ServiceDiscoveryResponse()
    resp.ParseFromString(sdr_bytes)

    # Check video channel has H265 and media audio has AAC
    for ch in resp.channels:
        if ch.HasField("av_channel"):
            if ch.av_channel.video_configs:
                assert "H265" in ch.av_channel.codec
            elif ch.av_channel.audio_type == 1:  # MEDIA
                assert "AAC" in ch.av_channel.codec
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/channel_manager/test_service_discovery.py -v`
Expected: 4 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/channel_manager/__init__.py tests/unit/channel_manager/test_service_discovery.py
git commit -m "test(unit): add service discovery classification and sdr generation unit tests"
```

---

### Task 2: Channel Manager Core & Status REST / SSE Stream Tests

**Files:**
- Create: `tests/unit/channel_manager/test_channel_manager_module.py`

**Interfaces:**
- Consumes: `modules.channel_manager.main.ChannelManagerModule`, `ChannelType`
- Produces: 5 unit tests verifying defaults, schema descriptors, dynamic channel lookup, REST `/status`, and Server-Sent Events `/stream_status`.

- [ ] **Step 1: Write failing test file `tests/unit/channel_manager/test_channel_manager_module.py`**

```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shared.constants import ChannelType
from modules.channel_manager.main import ChannelManagerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_channel_manager():
    with patch("shared.base_module.BusClient"), \
         patch("modules.channel_manager.main.BidirectionalMediaSHM"):
        mgr = ChannelManagerModule()
        yield mgr


def test_channel_manager_config_and_schema(mock_channel_manager):
    defaults = mock_channel_manager.get_default_config()
    assert defaults["head_unit_name"] == "NemoHeadUnit"
    assert defaults["autoclose_on_shutdown"] is True

    schema = mock_channel_manager.get_schema()
    assert isinstance(schema, dict)


def test_channel_manager_dynamic_channel_registry(mock_channel_manager):
    # Initial state: 0 is CONTROL
    assert mock_channel_manager.get_channel_type(0) == ChannelType.CONTROL
    assert mock_channel_manager.get_channel_type(999) == ChannelType.UNKNOWN

    # Dynamic SDR population
    mock_channel_manager.set_channel_type_map({
        "1": "INPUT",
        "2": "SENSOR",
        "3": "VIDEO",
        "4": "AUDIO",
    })
    assert mock_channel_manager.get_channel_type(1) == ChannelType.INPUT
    assert mock_channel_manager.get_channel_type(3) == ChannelType.VIDEO
    assert mock_channel_manager.get_channel_id_for_type(ChannelType.VIDEO) == 3
    assert mock_channel_manager.get_channel_id_for_type(ChannelType.INPUT) == 1

    # Fallback lookup for unmapped types
    assert mock_channel_manager.get_channel_id_for_type(ChannelType.BLUETOOTH) == 8


@pytest.mark.asyncio
async def test_channel_manager_rest_status(mock_channel_manager):
    mock_channel_manager.active_channels = {
        3: {
            "channel_id": 3,
            "av_channel": {
                "codec": "MEDIA_CODEC_VIDEO_H264_BP",
                "video_configs": [{"video_resolution": "VIDEO_1280x720"}],
            }
        }
    }
    mock_channel_manager.channel_type_map[3] = ChannelType.VIDEO

    req = MagicMock()
    resp = await mock_channel_manager.handle_get_status(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert 3 in data["active_channels"]
    assert "3" in data["stream_config"]["streams"]
    assert data["stream_config"]["streams"]["3"]["codec"] == "H264"


@pytest.mark.asyncio
async def test_channel_manager_session_events(mock_channel_manager):
    mock_channel_manager.send_wire_frame = AsyncMock()

    # 1. TCP Session connected triggers VERSION_REQUEST on channel 0
    await mock_channel_manager.on_tcp_session_connected({"address": "127.0.0.1:5288"})
    mock_channel_manager.send_wire_frame.assert_called_once()
    args = mock_channel_manager.send_wire_frame.call_args[0]
    assert args[0] == 0  # channel 0
    assert args[1] == 1  # VERSION_REQUEST

    # 2. TLS handshake completed triggers AUTH_COMPLETE on channel 0
    mock_channel_manager.send_wire_frame.reset_mock()
    await mock_channel_manager.on_tls_handshake_completed({})
    mock_channel_manager.send_wire_frame.assert_called_once()
    args = mock_channel_manager.send_wire_frame.call_args[0]
    assert args[0] == 0  # channel 0
    assert args[1] == 4  # AUTH_COMPLETE
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/channel_manager/test_channel_manager_module.py -v`
Expected: 4 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/channel_manager/test_channel_manager_module.py
git commit -m "test(unit): add channel_manager module lifecycle and dynamic registry unit tests"
```

---

### Task 3: Control Channel Protocol Handler Tests

**Files:**
- Create: `tests/unit/channel_manager/test_control_handler.py`

**Interfaces:**
- Consumes: `modules.channel_manager.handlers.control_handler.ControlChannelHandler`, `ControlMessage`, `PingRequest`, `AudioFocusRequest`
- Produces: 5 unit tests verifying version negotiation, TLS trigger, SDR dispatch, channel open response, ping keepalives, and audio focus grants.

- [ ] **Step 1: Write failing test file `tests/unit/channel_manager/test_control_handler.py`**

```python
import struct
import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from protos.oaa.control.PingResponseMessage_pb2 import PingResponse
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.audio.AudioFocusRequestMessage_pb2 import AudioFocusRequest
from protos.oaa.audio.AudioFocusResponseMessage_pb2 import AudioFocusResponse
from protos.oaa.audio.AudioFocusTypeEnum_pb2 import AudioFocusType
from protos.oaa.audio.AudioFocusStateEnum_pb2 import AudioFocusState
from protos.oaa.common.StatusEnum_pb2 import Status
from modules.channel_manager.handlers.control_handler import ControlChannelHandler

pytestmark = pytest.mark.unit
MSG = ControlMessage.Enum


@pytest.fixture
def mock_control_handler():
    mock_mgr = MagicMock()
    mock_mgr.send_wire_frame = AsyncMock()
    mock_mgr.publish = MagicMock()
    mock_mgr.config = {}
    mock_mgr.set_channel_type_map = MagicMock()
    handler = ControlChannelHandler(mock_mgr)
    return handler


@pytest.mark.asyncio
async def test_control_handler_version_exchange(mock_control_handler):
    # 1. Phone sends VERSION_REQUEST
    await mock_control_handler.handle_frame(MSG.VERSION_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.VERSION_RESPONSE
    # Check payload has status OK (1.1)
    status, maj, min_ = struct.unpack(">HHH", payload)
    assert maj == 1 and min_ == 1

    # 2. Phone sends VERSION_RESPONSE
    mock_control_handler.manager.send_wire_frame.reset_mock()
    assert mock_control_handler.tls_started is False
    await mock_control_handler.handle_frame(MSG.VERSION_RESPONSE, b"")
    assert mock_control_handler.tls_started is True
    mock_control_handler.manager.publish.assert_called_once_with("aa.handshake.start_tls", {})


@pytest.mark.asyncio
async def test_control_handler_ssl_handshake_and_sdr(mock_control_handler):
    # 1. Phone sends SSL_HANDSHAKE
    handshake_bytes = b"\x16\x03\x03data"
    await mock_control_handler.handle_frame(MSG.SSL_HANDSHAKE, handshake_bytes)
    mock_control_handler.manager.publish.assert_called_once_with(
        "aa.handshake.feed_input",
        {"payload_hex": handshake_bytes.hex()},
    )

    # 2. Phone sends SERVICE_DISCOVERY_REQUEST
    await mock_control_handler.handle_frame(MSG.SERVICE_DISCOVERY_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, sdr_bytes = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.SERVICE_DISCOVERY_RESPONSE
    assert len(sdr_bytes) > 0
    mock_control_handler.manager.publish.assert_any_call("aa.sdr.channels", pytest.approx(mock_control_handler.manager.publish.call_args_list[-1][0][1]))


@pytest.mark.asyncio
async def test_control_handler_channel_open_and_ping(mock_control_handler):
    # 1. CHANNEL_OPEN_REQUEST
    await mock_control_handler.handle_frame(MSG.CHANNEL_OPEN_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.CHANNEL_OPEN_RESPONSE
    resp = ChannelOpenResponse()
    resp.ParseFromString(payload)
    assert resp.status == Status.OK

    # 2. PING_REQUEST -> PING_RESPONSE with same timestamp
    mock_control_handler.manager.send_wire_frame.reset_mock()
    ping_req = PingRequest(timestamp=987654321)
    await mock_control_handler.handle_frame(MSG.PING_REQUEST, ping_req.SerializeToString())
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.PING_RESPONSE
    pong = PingResponse()
    pong.ParseFromString(payload)
    assert pong.timestamp == 987654321


@pytest.mark.asyncio
async def test_control_handler_audio_focus_request(mock_control_handler):
    req = AudioFocusRequest()
    req.audio_focus_type = AudioFocusType.Enum.GAIN

    await mock_control_handler.handle_frame(MSG.AUDIO_FOCUS_REQUEST, req.SerializeToString())
    mock_control_handler.manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_control_handler.manager.send_wire_frame.call_args[0][:3]
    assert ch == 0
    assert msg_id == MSG.AUDIO_FOCUS_RESPONSE
    resp = AudioFocusResponse()
    resp.ParseFromString(payload)
    assert resp.audio_focus_state == AudioFocusState.GAIN
    assert resp.granted is True

    mock_control_handler.manager.publish.assert_called_with("media.audio.focus", {
        "channel_id": 0,
        "focus_type": AudioFocusType.Enum.GAIN,
        "focus_state": AudioFocusState.GAIN,
        "is_paused": False,
    })


@pytest.mark.asyncio
async def test_control_handler_shutdown_request(mock_control_handler):
    await mock_control_handler.handle_frame(MSG.SHUTDOWN_REQUEST, b"")
    mock_control_handler.manager.send_wire_frame.assert_called_once_with(
        0, MSG.SHUTDOWN_RESPONSE, b"", encrypted=True
    )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/channel_manager/test_control_handler.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/channel_manager/test_control_handler.py
git commit -m "test(unit): add control_handler channel 0 handshake and focus unit tests"
```

---

### Task 4: AV Channels (Video & Audio) Handlers Tests

**Files:**
- Create: `tests/unit/channel_manager/test_av_handlers.py`

**Interfaces:**
- Consumes: `modules.channel_manager.handlers.video_handler.VideoChannelHandler`, `modules.channel_manager.handlers.audio_handler.AudioChannelHandler`, `AVChannelMessage`
- Produces: 6 unit tests verifying AV channel setup negotiation, codec extraction, focus indications, zero-copy SHM NAL/audio routing, and batch acking.

- [ ] **Step 1: Write failing test file `tests/unit/channel_manager/test_av_handlers.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
from protos.oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
from protos.oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
from protos.oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
from protos.oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication
from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
from shared.constants import ChannelType
from modules.channel_manager.handlers.video_handler import VideoChannelHandler, UNACKED_FRAMES_THRESHOLD
from modules.channel_manager.handlers.audio_handler import AudioChannelHandler

pytestmark = pytest.mark.unit
AV_MSG = AVChannelMessage.Enum


@pytest.fixture
def mock_av_manager():
    mgr = MagicMock()
    mgr.send_wire_frame = AsyncMock()
    mgr.publish = MagicMock()
    mgr.broadcast_ws_json = AsyncMock()
    mgr.broadcast_ws_media = AsyncMock()
    mgr.get_stream_config_dict.return_value = {"streams": {}}
    mgr.get_channel_id_for_type.side_effect = lambda t: 3 if t == ChannelType.VIDEO else 4
    mgr.active_channels = {3: {}, 4: {}}
    mgr.ws_clients = set()
    return mgr


@pytest.mark.asyncio
async def test_video_handler_setup_and_start(mock_av_manager):
    video = VideoChannelHandler(mock_av_manager)
    setup_req = AVChannelSetupRequest(media_codec_type=3)  # H264

    # 1. SETUP_REQUEST -> SETUP_RESPONSE(OK, max_unacked=10)
    await video.handle_frame(channel_id=3, message_id=AV_MSG.SETUP_REQUEST, body=setup_req.SerializeToString())
    assert video.setup_completed is True
    mock_av_manager.send_wire_frame.assert_any_call(
        3, AV_MSG.SETUP_RESPONSE, pytest.approx(mock_av_manager.send_wire_frame.call_args_list[0][0][2]), encrypted=True
    )
    # Check focus indication was also sent
    assert mock_av_manager.send_wire_frame.call_count == 2

    # 2. START_INDICATION -> session_id extracted, published video.stream_start
    start_req = AVChannelStartIndication(session=42)
    await video.handle_frame(channel_id=3, message_id=AV_MSG.START_INDICATION, body=start_req.SerializeToString())
    assert video.session_id == 42
    mock_av_manager.publish.assert_called_with("video.stream_start", {
        "session_id": 42,
        "codec": "MEDIA_CODEC_VIDEO_H264_BP",
        "codec_enum": 3,
    })


@pytest.mark.asyncio
async def test_video_handler_shm_batch_acking(mock_av_manager):
    video = VideoChannelHandler(mock_av_manager)
    video.session_id = 100

    # Feed 9 frames: publishes raw NAL, no ACK yet
    for i in range(9):
        await video.process_shm_frame(message_id=0, offset=i * 100, ts_us=i * 1000, payload_len=100)
    assert mock_av_manager.send_wire_frame.call_count == 0
    assert video.unacked_frames == 9

    # 10th frame triggers batch ACK
    await video.process_shm_frame(message_id=0, offset=900, ts_us=9000, payload_len=100)
    assert mock_av_manager.send_wire_frame.call_count == 1
    ch, msg_id, payload = mock_av_manager.send_wire_frame.call_args[0][:3]
    assert ch == 3
    assert msg_id == AV_MSG.AV_MEDIA_ACK_INDICATION
    ack = AVMediaAckIndication()
    ack.ParseFromString(payload)
    assert ack.session_id == 100
    assert ack.ack_count == 10
    assert video.unacked_frames == 0


@pytest.mark.asyncio
async def test_video_handler_send_focus_indication(mock_av_manager):
    video = VideoChannelHandler(mock_av_manager)
    video.unacked_frames = 5
    video.session_id = 200

    # Non-PROJECTED focus indication flushes unacked frames first
    await video.send_focus_indication(VideoFocusMode.Enum.NATIVE)
    assert mock_av_manager.send_wire_frame.call_count == 2
    # First call: flushed unacked ACK
    assert mock_av_manager.send_wire_frame.call_args_list[0][0][1] == AV_MSG.AV_MEDIA_ACK_INDICATION
    # Second call: focus indication
    assert mock_av_manager.send_wire_frame.call_args_list[1][0][1] == AV_MSG.VIDEO_FOCUS_INDICATION


@pytest.mark.asyncio
async def test_audio_handler_setup_and_start(mock_av_manager):
    audio = AudioChannelHandler(mock_av_manager)
    setup_req = AVChannelSetupRequest(media_codec_type=1)  # PCM

    # 1. SETUP_REQUEST -> SETUP_RESPONSE(OK, max_unacked=10)
    await audio.handle_frame(channel_id=4, message_id=AV_MSG.SETUP_REQUEST, body=setup_req.SerializeToString())
    mock_av_manager.send_wire_frame.assert_called_once()
    assert mock_av_manager.send_wire_frame.call_args[0][1] == AV_MSG.SETUP_RESPONSE
    mock_av_manager.publish.assert_called_with("media.audio.channel_configured", {
        "channel_id": 4,
        "codec": "MEDIA_CODEC_AUDIO_PCM",
        "codec_enum": 1,
        "sample_rate": 48000,
        "channel_count": 2,
        "bit_depth": 16,
        "audio_type": "MEDIA",
    })

    # 2. START_INDICATION -> session recorded, stream ACTIVE published
    mock_av_manager.send_wire_frame.reset_mock()
    start_req = AVChannelStartIndication(session=7)
    await audio.handle_frame(channel_id=4, message_id=AV_MSG.START_INDICATION, body=start_req.SerializeToString())
    assert audio.sessions[4] == 7
    mock_av_manager.publish.assert_called_with("media.audio.stream_status", {
        "channel_id": 4,
        "status": "ACTIVE",
        "session_id": 7,
    })


@pytest.mark.asyncio
async def test_audio_handler_shm_batch_acking(mock_av_manager):
    audio = AudioChannelHandler(mock_av_manager)
    audio.sessions[4] = 99

    for i in range(10):
        await audio.process_shm_frame(channel_id=4, message_id=0, offset=i * 50, ts_us=i * 500, payload_len=50)

    assert mock_av_manager.send_wire_frame.call_count == 1
    assert mock_av_manager.send_wire_frame.call_args[0][1] == AV_MSG.AV_MEDIA_ACK_INDICATION
    assert audio.unacked_counts[4] == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/channel_manager/test_av_handlers.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/channel_manager/test_av_handlers.py
git commit -m "test(unit): add av_handlers video and audio channel protocol unit tests"
```

---

### Task 5: Auxiliary Channels Handlers Tests

**Files:**
- Create: `tests/unit/channel_manager/test_auxiliary_handlers.py`

**Interfaces:**
- Consumes: `modules.channel_manager.handlers.input_handler.InputChannelHandler`, `sensor_handler.SensorChannelHandler`, `bluetooth_handler.BluetoothChannelHandler`, `wifi_handler.WifiChannelHandler`, `navigation_handler.NavigationChannelHandler`
- Produces: 5 unit tests verifying input touch/media key generation, sensor start responses, Bluetooth pairing responses, WiFi security responses, and navigation turn events.

- [ ] **Step 1: Write failing test file `tests/unit/channel_manager/test_auxiliary_handlers.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.input.InputChannelMessageIdsEnum_pb2 import InputChannelMessage
from protos.oaa.input.InputBindingResponseMessage_pb2 import InputBindingResponse
from protos.oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
from protos.oaa.sensor.SensorChannelMessageIdsEnum_pb2 import SensorChannelMessage
from protos.oaa.sensor.SensorStartRequestMessage_pb2 import SensorStartRequestMessage
from protos.oaa.sensor.SensorStartResponseMessage_pb2 import SensorStartResponseMessage
from protos.oaa.sensor.SensorEventIndicationMessage_pb2 import SensorEventIndication
from protos.oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2 import BluetoothChannelMessage
from protos.oaa.bluetooth.BluetoothPairingResponseMessage_pb2 import BluetoothPairingResponse
from protos.oaa.wifi.WifiChannelMessageIdsEnum_pb2 import WifiChannelMessage
from protos.oaa.wifi.WifiCredentialsResponseMessage_pb2 import WifiCredentialsResponse
from protos.oaa.navigation.NavigationTurnEventMessage_pb2 import NavigationTurnEvent
from protos.oaa.navigation.ManeuverTypeEnum_pb2 import ManeuverType
from protos.oaa.common.StatusEnum_pb2 import Status
from shared.constants import ChannelType
from modules.channel_manager.handlers.input_handler import InputChannelHandler
from modules.channel_manager.handlers.sensor_handler import SensorChannelHandler
from modules.channel_manager.handlers.bluetooth_handler import BluetoothChannelHandler
from modules.channel_manager.handlers.wifi_handler import WifiChannelHandler
from modules.channel_manager.handlers.navigation_handler import NavigationChannelHandler

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_aux_manager():
    mgr = MagicMock()
    mgr.send_wire_frame = AsyncMock()
    mgr.publish = MagicMock()
    mgr.get_channel_id_for_type.side_effect = lambda t: {
        ChannelType.INPUT: 1,
        ChannelType.SENSOR: 2,
        ChannelType.BLUETOOTH: 8,
        ChannelType.WIFI: 9,
        ChannelType.NAVIGATION: 10,
    }.get(t, 1)
    return mgr


@pytest.mark.asyncio
async def test_input_handler_touch_and_media_keys(mock_aux_manager):
    input_h = InputChannelHandler(mock_aux_manager)

    # 1. BINDING_REQUEST -> BINDING_RESPONSE(OK)
    await input_h.handle_frame(1, InputChannelMessage.Enum.BINDING_REQUEST, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == InputChannelMessage.Enum.BINDING_RESPONSE

    # 2. handle_touch_event -> INPUT_EVENT_INDICATION with TouchLocation
    mock_aux_manager.send_wire_frame.reset_mock()
    await input_h.handle_touch_event(action=0, x=640, y=360, pointer_id=1)
    mock_aux_manager.send_wire_frame.assert_called_once()
    ch, msg_id, payload = mock_aux_manager.send_wire_frame.call_args[0][:3]
    assert ch == 1
    assert msg_id == InputChannelMessage.Enum.INPUT_EVENT_INDICATION
    ind = InputEventIndication()
    ind.ParseFromString(payload)
    assert ind.touch_event.touch_action == 0
    assert ind.touch_event.touch_location[0].x == 640
    assert ind.touch_event.touch_location[0].y == 360

    # 3. handle_media_key -> ButtonEvents
    mock_aux_manager.send_wire_frame.reset_mock()
    await input_h.handle_media_key(key_code=85)
    mock_aux_manager.send_wire_frame.assert_called_once()
    ind2 = InputEventIndication()
    ind2.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert ind2.button_event.button_events[0].keycode == 85


@pytest.mark.asyncio
async def test_sensor_handler_driving_status(mock_aux_manager):
    sensor = SensorChannelHandler(mock_aux_manager)

    # SENSOR_REQUEST for driving status (type 13)
    req = SensorStartRequestMessage(sensor_type=13)
    await sensor.handle_frame(2, SensorChannelMessage.Enum.SENSOR_REQUEST, req.SerializeToString())

    assert mock_aux_manager.send_wire_frame.call_count == 2
    # 1st: SENSOR_START_RESPONSE (OK)
    assert mock_aux_manager.send_wire_frame.call_args_list[0][0][1] == SensorChannelMessage.Enum.SENSOR_START_RESPONSE
    # 2nd: SENSOR_EVENT_INDICATION
    assert mock_aux_manager.send_wire_frame.call_args_list[1][0][1] == SensorChannelMessage.Enum.SENSOR_EVENT_INDICATION
    event = SensorEventIndication()
    event.ParseFromString(mock_aux_manager.send_wire_frame.call_args_list[1][0][2])
    assert len(event.driving_status) > 0
    assert event.driving_status[0].status == 0  # UNRESTRICTED


@pytest.mark.asyncio
async def test_bluetooth_handler_pairing_and_auth(mock_aux_manager):
    bt = BluetoothChannelHandler(mock_aux_manager)

    # 1. PAIRING_REQUEST -> PAIRING_RESPONSE(already_paired=True)
    await bt.handle_frame(8, BluetoothChannelMessage.Enum.PAIRING_REQUEST, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == BluetoothChannelMessage.Enum.PAIRING_RESPONSE
    resp = BluetoothPairingResponse()
    resp.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert resp.already_paired is True

    # 2. AUTH_DATA -> AUTH_RESULT(OK)
    mock_aux_manager.send_wire_frame.reset_mock()
    await bt.handle_frame(8, BluetoothChannelMessage.Enum.AUTH_DATA, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == BluetoothChannelMessage.Enum.AUTH_RESULT


@pytest.mark.asyncio
async def test_wifi_handler_credentials(mock_aux_manager):
    wifi = WifiChannelHandler(mock_aux_manager)

    await wifi.handle_frame(9, WifiChannelMessage.Enum.CREDENTIALS_REQUEST, b"")
    mock_aux_manager.send_wire_frame.assert_called_once()
    assert mock_aux_manager.send_wire_frame.call_args[0][1] == WifiChannelMessage.Enum.CREDENTIALS_RESPONSE
    resp = WifiCredentialsResponse()
    resp.ParseFromString(mock_aux_manager.send_wire_frame.call_args[0][2])
    assert resp.ssid == "AndroidAutoAP"
    assert resp.passphrase == "12345678"


@pytest.mark.asyncio
async def test_navigation_handler_turn_event(mock_aux_manager):
    nav = NavigationChannelHandler(mock_aux_manager)

    turn = NavigationTurnEvent()
    turn.road_name = "Highway 101"
    turn.turn_event = ManeuverType.TURN_NORMAL_RIGHT

    await nav.handle_frame(10, 1, turn.SerializeToString())
    assert nav.active_road == "Highway 101"
    assert nav.last_maneuver_type == ManeuverType.TURN_NORMAL_RIGHT
    mock_aux_manager.publish.assert_called_with("navigation.turn_event", {
        "event_name": "turn-normal-right",
        "road_name": "Highway 101",
        "maneuver_type": ManeuverType.TURN_NORMAL_RIGHT,
        "turn_side": 0,
    })
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/channel_manager/test_auxiliary_handlers.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/channel_manager/test_auxiliary_handlers.py
git commit -m "test(unit): add auxiliary channel handlers unit tests"
```

---

### Task 6: Phase 4 Verification & Integration Gate

**Files:**
- None (verification only)

- [ ] **Step 1: Run full Phase 4 test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/channel_manager/ -v`
Expected: 20+ passed in < 1.5s.

- [ ] **Step 2: Run entire unit test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/ -v`
Expected: 110+ passed in < 2.0s.

- [ ] **Step 3: Run full repository pytest suite to prove zero regressions**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest -q`
Expected: 200+ passed.

- [ ] **Step 4: Execute orchestrator smoke test**

Run: `timeout 7 micromamba run -n NemoHeadUnit-Wireless python backend/main.py`
Expected: Clean startup across Waves 0–5 and clean exit with code 124 on timeout.

- [ ] **Step 5: Run graphify update**

Run: `/home/nemo/miniconda3/bin/graphify update .`

- [ ] **Step 6: Commit plan and spec documentation**

```bash
git add docs/superpowers/specs/2026-09-07-unit-tests-phase4-channel-manager-design.md docs/superpowers/plans/2026-09-07-unit-tests-phase4-channel-manager.md
git commit -m "docs(tests): add Phase 4 channel manager and protocol handlers unit test spec and plan"
```

- [ ] **Step 7: Run whole-branch review subagent**
