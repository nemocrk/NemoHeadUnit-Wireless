# Phase 3: Android Auto Wire & Cryptography Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement isolated, high-speed unit tests covering Android Auto wire framing (`frame_codec.py`), in-band TLS cryptography (`aa_cryptor.py`), low-level TCP socket reading (`frame_relay.py`), protobuf message mapping (`message_to_proto.py`), and the TCP server orchestrator (`main.py`).

**Architecture:** Each component under `backend/modules/tcp_server/` is tested with zero real network sockets, zero real ZMQ daemons, and zero external hardware. Sockets are replaced with `MagicMock` or in-memory pairs, TLS memory BIOs are tested with pure memory operations, and Protobuf messages use standard generated schemas.

**Tech Stack:** Python 3.13 / 3.14, `pytest`, `pytest-asyncio`, `unittest.mock`, `ssl`, `struct`, Google Protocol Buffers.

**Spec:** `docs/superpowers/specs/2026-09-07-unit-tests-phase3-tcp-server-design.md`

## Global Constraints
- Target execution time: Phase 3 suite < 2.0s total.
- Strict isolation: Unit tests must never bind real TCP ports (`5288`) or spawn unmocked ZMQ background threads.
- Universal cross-platform compatibility: Linux and Windows (`pathlib.Path`, cross-platform sockets).
- All unit test files must define `pytestmark = pytest.mark.unit`.
- All test runs must use `micromamba run -n NemoHeadUnit-Wireless pytest ...`.

---

### Task 1: Frame Codec & Reassembly Unit Tests

**Files:**
- Create: `tests/unit/tcp_server/__init__.py`
- Create: `tests/unit/tcp_server/test_frame_codec.py`

**Interfaces:**
- Consumes: `modules.tcp_server.frame_codec.encode`, `FrameAssembler`, `_FT_BULK`, `_FT_FIRST`, `_FT_MIDDLE`, `_FT_LAST`, `_MT_SPECIFIC`, `_MT_CONTROL`, `_ET_PLAIN`, `_ET_ENCRYPTED`
- Produces: 7 unit tests verifying wire header packing, CONTROL message-type flag policy, multi-frame threshold fragmentation, encryption downgrading, and `FrameAssembler` per-channel state machine.

- [ ] **Step 1: Write failing test file `tests/unit/tcp_server/test_frame_codec.py`**

```python
import struct
import pytest
from unittest.mock import MagicMock
from modules.tcp_server.frame_codec import (
    encode,
    FrameAssembler,
    FRAME_SIZE_THRESHOLD,
    _FT_BULK,
    _FT_FIRST,
    _FT_MIDDLE,
    _FT_LAST,
    _MT_SPECIFIC,
    _MT_CONTROL,
    _ET_PLAIN,
    _ET_ENCRYPTED,
    _MSG_CHANNEL_OPEN_RESPONSE,
)

pytestmark = pytest.mark.unit


def test_frame_codec_encode_bulk_plain():
    channel_id = 0
    message_id = 0x0001
    body = b"hello_aa"

    frames = encode(channel_id, message_id, body, ssl_active=False)
    assert len(frames) == 1
    raw = frames[0]

    # Header: [ch: 1B][flags: 1B][len: 2B BE] + payload
    ch, flags, payload_len = struct.unpack_from(">BBH", raw, 0)
    assert ch == channel_id
    assert flags & 0x03 == _FT_BULK
    assert flags & 0x04 == _MT_SPECIFIC
    assert flags & 0x08 == _ET_PLAIN
    assert payload_len == 2 + len(body)
    msg_id = struct.unpack_from(">H", raw, 4)[0]
    assert msg_id == message_id
    assert raw[6:] == body


def test_frame_codec_encode_control_message_type():
    # Only CHANNEL_OPEN_RESPONSE on channel != 0 uses CONTROL message type
    frames_ctl = encode(1, _MSG_CHANNEL_OPEN_RESPONSE, b"ok", ssl_active=False)
    flags_ctl = frames_ctl[0][1]
    assert flags_ctl & 0x04 == _MT_CONTROL

    # CHANNEL_OPEN_RESPONSE on channel 0 stays SPECIFIC
    frames_ch0 = encode(0, _MSG_CHANNEL_OPEN_RESPONSE, b"ok", ssl_active=False)
    flags_ch0 = frames_ch0[0][1]
    assert flags_ch0 & 0x04 == _MT_SPECIFIC

    # Other messages on channel 1 stay SPECIFIC
    frames_other = encode(1, 0x8001, b"other", ssl_active=False)
    flags_other = frames_other[0][1]
    assert flags_other & 0x04 == _MT_SPECIFIC


def test_frame_codec_encode_encryption_active_and_fallback():
    mock_cryptor = MagicMock()
    mock_cryptor.is_active.return_value = True
    mock_cryptor.encrypt_records.return_value = [b"encrypted_cipher"]

    # When ssl_active is True and cryptor is active, uses _ET_ENCRYPTED
    frames = encode(1, 0x8001, b"plain_body", ssl_active=True, cryptor=mock_cryptor)
    assert len(frames) == 1
    flags = frames[0][1]
    assert flags & 0x08 == _ET_ENCRYPTED
    mock_cryptor.encrypt_records.assert_called_once()

    # Fallback to plain if cryptor is None or not active
    frames_fallback = encode(1, 0x8001, b"plain_body", ssl_active=True, cryptor=None)
    flags_fb = frames_fallback[0][1]
    assert flags_fb & 0x08 == _ET_PLAIN


def test_frame_codec_encode_fragmentation():
    channel_id = 3
    message_id = 0x8001
    large_body = b"X" * (FRAME_SIZE_THRESHOLD * 2 + 500)

    frames = encode(channel_id, message_id, large_body, ssl_active=False)
    assert len(frames) == 3  # FIRST + MIDDLE + LAST

    # 1. FIRST frame has 8-byte header: [ch: 1B][flags: 1B][len: 2B BE][total_size: 4B BE]
    f1 = frames[0]
    ch1, flags1, len1 = struct.unpack_from(">BBH", f1, 0)
    assert ch1 == channel_id
    assert flags1 & 0x03 == _FT_FIRST
    assert len1 == FRAME_SIZE_THRESHOLD
    total_size = struct.unpack_from(">I", f1, 4)[0]
    assert total_size == 2 + len(large_body)

    # 2. MIDDLE frame has 4-byte header
    f2 = frames[1]
    ch2, flags2, len2 = struct.unpack_from(">BBH", f2, 0)
    assert ch2 == channel_id
    assert flags2 & 0x03 == _FT_MIDDLE
    assert len2 == FRAME_SIZE_THRESHOLD

    # 3. LAST frame has 4-byte header
    f3 = frames[2]
    ch3, flags3, len3 = struct.unpack_from(">BBH", f3, 0)
    assert ch3 == channel_id
    assert flags3 & 0x03 == _FT_LAST
    assert len3 == (2 + len(large_body)) - (FRAME_SIZE_THRESHOLD * 2)


def test_frame_assembler_bulk():
    assembler = FrameAssembler()
    payload = b"\x00\x01test"
    res = assembler.feed(channel_id=0, flags=_FT_BULK, payload=payload, total_size=len(payload))
    assert res == (0, _FT_BULK, payload, len(payload))


def test_frame_assembler_multi_frame_flow():
    assembler = FrameAssembler()
    chunk1 = b"chunk_1"
    chunk2 = b"chunk_2"
    chunk3 = b"chunk_3"
    total_len = len(chunk1) + len(chunk2) + len(chunk3)

    # Feed FIRST
    r1 = assembler.feed(1, _FT_FIRST | _MT_SPECIFIC, chunk1, total_size=total_len)
    assert r1 is None

    # Feed MIDDLE
    r2 = assembler.feed(1, _FT_MIDDLE | _MT_SPECIFIC, chunk2)
    assert r2 is None

    # Feed LAST
    r3 = assembler.feed(1, _FT_LAST | _MT_SPECIFIC, chunk3)
    assert r3 is not None
    ch, out_flags, full_data, declared_total = r3
    assert ch == 1
    assert out_flags & 0x03 == _FT_BULK
    assert full_data == chunk1 + chunk2 + chunk3
    assert declared_total == total_len


def test_frame_assembler_orphan_middle_and_reset():
    assembler = FrameAssembler()
    # Feeding MIDDLE without FIRST drops and returns None
    assert assembler.feed(2, _FT_MIDDLE, b"orphan") is None

    # Feeding FIRST then resetting clears state
    assembler.feed(2, _FT_FIRST, b"first_chunk", total_size=100)
    assert 2 in assembler._buffers
    assembler.reset(channel_id=2)
    assert 2 not in assembler._buffers
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/tcp_server/test_frame_codec.py -v`
Expected: 7 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tcp_server/__init__.py tests/unit/tcp_server/test_frame_codec.py
git commit -m "test(unit): add frame_codec wire encoding and assembler unit tests"
```

---

### Task 2: In-Band TLS Cryptor Unit Tests

**Files:**
- Create: `tests/unit/tcp_server/test_aa_cryptor.py`

**Interfaces:**
- Consumes: `modules.tcp_server.aa_cryptor.AACryptor`, `_AA_CERT_PEM`, `_AA_KEY_PEM`
- Produces: 5 unit tests verifying client-side TLS 1.2 initialization, ClientHello generation, TLS record parsing, multi-record splitting, and memory BIO encryption/decryption.

- [ ] **Step 1: Write failing test file `tests/unit/tcp_server/test_aa_cryptor.py`**

```python
import ssl
import pytest
from modules.tcp_server.aa_cryptor import AACryptor

pytestmark = pytest.mark.unit


def test_aa_cryptor_init_and_deinit():
    cryptor = AACryptor()
    assert not cryptor.is_active()

    cryptor.init()
    assert cryptor._ctx is not None
    assert cryptor._ssl_obj is not None
    assert cryptor._in_bio is not None
    assert cryptor._out_bio is not None
    assert not cryptor.is_active()

    cryptor.deinit()
    assert cryptor._ctx is None
    assert cryptor._ssl_obj is None
    assert not cryptor.is_active()


def test_aa_cryptor_handshake_client_hello():
    cryptor = AACryptor()
    cryptor.init()

    # Client speaks first — drive_handshake() generates ClientHello
    client_hello = cryptor.drive_handshake()
    assert len(client_hello) > 0
    # TLS Handshake record header: 0x16 (Handshake), version 0x03 0x01 or 0x03 0x03
    assert client_hello[0] == 0x16
    assert client_hello[1] == 0x03

    parsed = cryptor.parse_tls_record_header(client_hello)
    assert parsed["valid"] is True
    assert parsed["content_type"] == 0x16
    assert parsed["type_name"] == "Handshake"
    cryptor.deinit()


def test_aa_cryptor_parse_tls_record_header_varieties():
    cryptor = AACryptor()

    # Short payload (< 5 bytes)
    assert cryptor.parse_tls_record_header(b"\x16\x03")["valid"] is False

    # ApplicationData (0x17)
    app_data = b"\x17\x03\x03\x00\x04test"
    parsed_app = cryptor.parse_tls_record_header(app_data)
    assert parsed_app["valid"] is True
    assert parsed_app["type_name"] == "ApplicationData"
    assert parsed_app["record_len"] == 4
    assert parsed_app["len_match"] is True

    # Alert (0x15)
    alert_data = b"\x15\x03\x03\x00\x02\x01\x00"
    parsed_alert = cryptor.parse_tls_record_header(alert_data)
    assert parsed_alert["type_name"] == "Alert"


def test_aa_cryptor_encrypt_before_active_raises():
    cryptor = AACryptor()
    cryptor.init()
    with pytest.raises(RuntimeError, match="before handshake complete"):
        cryptor.encrypt(b"secret")
    with pytest.raises(RuntimeError, match="before handshake complete"):
        cryptor.decrypt(b"secret")
    cryptor.deinit()


def test_aa_cryptor_encrypt_records_splitting():
    cryptor = AACryptor()
    cryptor._active = True
    cryptor._ssl_obj = object()  # dummy object to pass guard

    # Synthetic multi-record stream: record1 (len 4) + record2 (len 2)
    rec1 = b"\x17\x03\x03\x00\x04ABCD"
    rec2 = b"\x17\x03\x03\x00\x02EF"
    stream = rec1 + rec2

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cryptor, "encrypt", lambda data: stream)
        records = cryptor.encrypt_records(b"input")
        assert len(records) == 2
        assert records[0] == rec1
        assert records[1] == rec2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/tcp_server/test_aa_cryptor.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tcp_server/test_aa_cryptor.py
git commit -m "test(unit): add aa_cryptor TLS handshake and record processing unit tests"
```

---

### Task 3: Frame Relay Socket Streaming Unit Tests

**Files:**
- Create: `tests/unit/tcp_server/test_frame_relay.py`

**Interfaces:**
- Consumes: `modules.tcp_server.frame_relay.FrameRelay`, `FRAMETYPE_FIRST`
- Produces: 4 unit tests verifying bulk frame reading, first frame total size extraction, EOF socket closure handling, and `send_raw` partial-write chunking.

- [ ] **Step 1: Write failing test file `tests/unit/tcp_server/test_frame_relay.py`**

```python
import socket
import struct
import pytest
from unittest.mock import MagicMock
from modules.tcp_server.frame_relay import FrameRelay, FRAMETYPE_FIRST

pytestmark = pytest.mark.unit


def test_frame_relay_read_bulk_frame():
    channel_id = 0
    flags = 0x03  # BULK
    payload = b"\x00\x01ping"
    header = struct.pack(">BBH", channel_id, flags, len(payload))
    wire_bytes = header + payload

    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [wire_bytes[:2], wire_bytes[2:4], wire_bytes[4:], b""]

    frames_received = []
    closed_called = []

    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda ch, fl, pay, tot: frames_received.append((ch, fl, pay, tot)),
        on_closed_cb=lambda: closed_called.append(True),
    )

    relay.start()

    assert len(frames_received) == 1
    assert frames_received[0] == (channel_id, flags, payload, 0)
    assert len(closed_called) == 1


def test_frame_relay_read_first_frame():
    channel_id = 1
    flags = FRAMETYPE_FIRST  # 0x01
    payload = b"first_chunk"
    total_size = 5000
    # FIRST header: [ch: 1B][flags: 1B][this_len: 2B BE][total_len: 4B BE]
    wire_bytes = struct.pack(">BBHI", channel_id, flags, len(payload), total_size) + payload

    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [wire_bytes, b""]

    frames_received = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=lambda ch, fl, pay, tot: frames_received.append((ch, fl, pay, tot)),
    )

    relay.start()

    assert len(frames_received) == 1
    ch, fl, pay, tot = frames_received[0]
    assert ch == channel_id
    assert fl == flags
    assert pay == payload
    assert tot == total_size


def test_frame_relay_socket_exception_and_stop():
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = OSError("Socket read fault")

    closed_called = []
    relay = FrameRelay(
        sock=mock_sock,
        on_frame_cb=MagicMock(),
        on_closed_cb=lambda: closed_called.append(True),
    )

    relay.start()
    assert len(closed_called) == 1

    relay.stop()
    mock_sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)


def test_frame_relay_send_raw():
    mock_sock = MagicMock()
    # Simulate partial write: 5 bytes then remaining 5 bytes
    mock_sock.send.side_effect = [5, 5]

    relay = FrameRelay(sock=mock_sock, on_frame_cb=MagicMock())
    data = b"0123456789"
    relay.send_raw(data)

    assert mock_sock.send.call_count == 2

    # Verify BrokenPipeError on zero-byte return
    mock_sock.send.side_effect = [0]
    with pytest.raises(BrokenPipeError):
        relay.send_raw(b"fail")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/tcp_server/test_frame_relay.py -v`
Expected: 4 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tcp_server/test_frame_relay.py
git commit -m "test(unit): add frame_relay socket parsing and transmission unit tests"
```

---

### Task 4: Protobuf Message Dispatcher Unit Tests

**Files:**
- Create: `tests/unit/tcp_server/test_message_to_proto.py`

**Interfaces:**
- Consumes: `modules.tcp_server.message_to_proto.message_id_to_proto_name`, `proto_name_to_class`, `frame_data_to_dict`
- Produces: 4 unit tests verifying enum name mappings, class resolutions, and dictionary conversions.

- [ ] **Step 1: Write failing test file `tests/unit/tcp_server/test_message_to_proto.py`**

```python
import pytest
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from modules.tcp_server.message_to_proto import (
    message_id_to_proto_name,
    proto_name_to_class,
    frame_data_to_dict,
)

pytestmark = pytest.mark.unit


def test_message_id_to_proto_name():
    assert message_id_to_proto_name(ControlMessage.Enum.VERSION_REQUEST) == "VERSION_REQUEST"
    assert message_id_to_proto_name(ControlMessage.Enum.CHANNEL_OPEN_REQUEST) == "CHANNEL_OPEN_REQUEST"
    assert message_id_to_proto_name(AVChannelMessage.Enum.SETUP_REQUEST) == "SETUP_REQUEST"
    assert message_id_to_proto_name(999999) == "UnknownMessageId 999999"


def test_proto_name_to_class():
    cls_open = proto_name_to_class("CHANNEL_OPEN_REQUEST")
    assert cls_open is ChannelOpenRequest

    cls_ping = proto_name_to_class("PING_REQUEST")
    assert cls_ping is PingRequest

    with pytest.raises(ValueError, match="Unknown proto_name"):
        proto_name_to_class("NONEXISTENT_PROTO_MESSAGE")


def test_frame_data_to_dict_valid_protobuf():
    req = PingRequest(timestamp=123456789)
    req_hex = req.SerializeToString().hex()

    frame_data = {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.PING_REQUEST,
        "payload_hex": req_hex,
    }

    parsed = frame_data_to_dict(frame_data)
    assert parsed["message_name"] == "PING_REQUEST"
    assert isinstance(parsed["payload_as_dict"], dict)
    assert str(parsed["payload_as_dict"]["timestamp"]) == "123456789"


def test_frame_data_to_dict_fallback_on_corrupt():
    frame_data = {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.PING_REQUEST,
        "payload_hex": "corrupt_hex_not_valid",
    }
    parsed = frame_data_to_dict(frame_data)
    # Corrupt hex triggers error handling fallback to original payload_hex
    assert parsed["payload_as_dict"] == "corrupt_hex_not_valid"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/tcp_server/test_message_to_proto.py -v`
Expected: 4 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tcp_server/test_message_to_proto.py
git commit -m "test(unit): add message_to_proto lookup and serialization unit tests"
```

---

### Task 5: TCP Server Module Orchestration Unit Tests

**Files:**
- Create: `tests/unit/tcp_server/test_tcp_server_module.py`

**Interfaces:**
- Consumes: `modules.tcp_server.main.TCPServerModule`
- Produces: 5 unit tests verifying configuration schema, REST API, outbound frame encoding & send, inbound frame reassembly & routing, and media SHM bypass.

- [ ] **Step 1: Write failing test file `tests/unit/tcp_server/test_tcp_server_module.py`**

```python
import json
import struct
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.tcp_server.main import TCPServerModule
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_tcp_server_mod():
    with patch("shared.base_module.BusClient"), \
         patch("modules.tcp_server.main.BidirectionalMediaSHM"):
        mod = TCPServerModule()
        yield mod


def test_tcp_server_module_config_and_schema(mock_tcp_server_mod):
    defaults = mock_tcp_server_mod.get_default_config()
    assert defaults["host"] == "0.0.0.0"
    assert defaults["port"] == 5288
    assert defaults["autostart"] is True

    schema = mock_tcp_server_mod.get_schema()
    assert "port" in schema
    assert schema["port"].min == 1024


@pytest.mark.asyncio
async def test_tcp_server_module_status_rest_api(mock_tcp_server_mod):
    req = MagicMock()
    resp = await mock_tcp_server_mod.handle_get_status(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert data["port"] == 5288
    assert data["server_running"] is False
    assert data["tls_active"] is False


def test_tcp_server_module_on_frame_send(mock_tcp_server_mod):
    mock_relay = MagicMock()
    mock_tcp_server_mod._relay = mock_relay

    mock_tcp_server_mod.on_frame_send("aa.frame.send", {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.VERSION_REQUEST,
        "payload_hex": "0102",
        "encrypted": False,
    })

    mock_relay.send_raw.assert_called_once()
    assert mock_tcp_server_mod._frames_sent_count == 1


def test_tcp_server_module_on_raw_frame_control_dispatch(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod._assembler = MagicMock()
    # Assembler returns completed message: ch 0, flags 0x03, body: [msg_id: 2B][payload: 2B]
    assembled_bytes = bytes.fromhex("00010203")
    mock_tcp_server_mod._assembler.feed.return_value = (0, 0x03, assembled_bytes, len(assembled_bytes))

    mock_tcp_server_mod._on_raw_frame(channel_id=0, flags=0x03, payload=assembled_bytes, total_size=len(assembled_bytes))

    mock_tcp_server_mod.publish.assert_any_call("aa.frame.received", {
        "channel_id": 0,
        "message_id": 1,
        "encrypted": False,
        "payload_hex": "0203",
        "payload_head": "0203",
    })
    mock_tcp_server_mod.publish.assert_any_call("aa.frame.ch0", {
        "channel_id": 0,
        "message_id": 1,
        "encrypted": False,
        "payload_hex": "0203",
    })


def test_tcp_server_module_media_shm_routing(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod.channel_type_map[3] = "VIDEO"
    mock_tcp_server_mod._assembler = MagicMock()

    msg_id = AVChannelMessage.Enum.AV_MEDIA_INDICATION
    assembled_bytes = struct.pack(">H", msg_id) + b"video_frame_data"
    mock_tcp_server_mod._assembler.feed.return_value = (3, 0x03, assembled_bytes, len(assembled_bytes))

    mock_tcp_server_mod._shm.transcode_in.write_frame.return_value = 1234

    mock_tcp_server_mod._on_raw_frame(channel_id=3, flags=0x03, payload=assembled_bytes, total_size=len(assembled_bytes))

    mock_tcp_server_mod.publish.assert_called_once_with("aa.frame.shm", {
        "channel_id": 3,
        "message_id": msg_id,
        "encrypted": False,
        "shm_offset": 1234,
        "timestamp_us": 0,
        "payload_len": len(b"video_frame_data"),
    })
```

- [ ] **Step 2: Run test to verify it passes**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/tcp_server/test_tcp_server_module.py -v`
Expected: 5 passed in < 0.5s.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tcp_server/test_tcp_server_module.py
git commit -m "test(unit): add tcp_server module orchestration and frame routing unit tests"
```

---

### Task 6: Phase 3 Verification & Integration Gate

**Files:**
- None (verification only)

- [ ] **Step 1: Run full Phase 3 test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/tcp_server/ -v`
Expected: 25 passed in < 1.5s.

- [ ] **Step 2: Run entire unit test suite**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest tests/unit/ -v`
Expected: 69 passed in < 2.0s.

- [ ] **Step 3: Run full repository pytest suite to prove zero regressions**

Run: `micromamba run -n NemoHeadUnit-Wireless pytest -q`
Expected: 159 passed.

- [ ] **Step 4: Execute orchestrator smoke test**

Run: `timeout 7 micromamba run -n NemoHeadUnit-Wireless python backend/main.py`
Expected: Clean startup across Waves 0–5 and clean exit with code 124 on timeout.

- [ ] **Step 5: Run graphify update**

Run: `/home/nemo/miniconda3/bin/graphify update .`

- [ ] **Step 6: Commit plan and spec documentation**

```bash
git add docs/superpowers/specs/2026-09-07-unit-tests-phase3-tcp-server-design.md docs/superpowers/plans/2026-09-07-unit-tests-phase3-tcp-server.md
git commit -m "docs(tests): add Phase 3 AAP wire and cryptography unit test spec and plan"
```

- [ ] **Step 7: Run whole-branch review subagent**
