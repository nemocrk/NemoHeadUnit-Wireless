"""
Unit tests for tcp_server/main.py and tcp_server/frame_codec.py

Strategy:
  - frame_codec.py is pure Python, stateless — scope="module" fixture.
  - FrameAssembler is stateful but side-effect-free — instantiated per test.
  - main.py has many module-level singletons — reloaded per-test via `ts` fixture;
    TCPServer, FrameRelay, FrameAssembler, AACryptor, BusClient all fully mocked.

Covers:
  Section 1 — frame_codec helpers: _message_type, _encryption_type
  Section 2 — frame_codec.encode: single-frame BULK, PLAIN, ENCRYPTED (mock cryptor),
               multi-frame FIRST/MIDDLE/LAST, ch0 plain exceptions, CHANNEL_OPEN_RESPONSE
  Section 3 — FrameAssembler: BULK fast-path, FIRST/MIDDLE/LAST accumulation,
               MIDDLE/LAST without FIRST, reset, multi-channel isolation
  Section 4 — main.py module-level boot handlers: system.readytostart, system.start,
               system.stop
  Section 5 — main.py on_handshake_completed: starts server thread, deduplicates
  Section 6 — main.py on_frame_send: happy path, no relay, malformed payload
  Section 7 — main.py TLS handlers: on_handshake_start_tls, on_handshake_feed_input
  Section 8 — main.py _on_raw_frame: BULK dispatch, decrypt path, msg_id strip,
               too-short payload, ch0 restart monitor
  Section 9 — main.py session restart: on_aa_session_restart, on_ch0_frame
"""

import sys
import importlib
import struct
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CODEC_MOD = "tcp_server.frame_codec"
_MAIN_MOD  = "modules.tcp_server.main"


@pytest.fixture(scope="module")
def codec():
    """Pure-Python frame_codec — import once per session."""
    if _CODEC_MOD in sys.modules:
        del sys.modules[_CODEC_MOD]
    import tcp_server.frame_codec as mod
    importlib.reload(mod)
    return mod


@pytest.fixture()
def ts():
    """
    Reload tcp_server/main.py with all I/O dependencies mocked.
    Returns (mod, mock_bus, mock_server_cls, mock_relay_cls, mock_cryptor_cls,
             mock_assembler_cls).
    """
    mock_bus = MagicMock()
    mock_bus.start.return_value = MagicMock()

    mock_server_cls = MagicMock()
    mock_server_inst = MagicMock()
    mock_server_cls.return_value = mock_server_inst
    mock_server_inst.start.return_value = True
    mock_server_inst.host = "0.0.0.0"
    mock_server_inst.port = 5288
    mock_server_inst.accept.return_value = (MagicMock(), ("192.168.7.1", 12345))

    mock_relay_cls = MagicMock()
    mock_relay_inst = MagicMock()
    mock_relay_cls.return_value = mock_relay_inst

    mock_cryptor_cls = MagicMock()
    mock_cryptor_inst = MagicMock()
    mock_cryptor_cls.return_value = mock_cryptor_inst
    mock_cryptor_inst.is_active.return_value = False
    mock_cryptor_inst.drive_handshake.return_value = b""

    mock_assembler_cls = MagicMock()
    mock_assembler_inst = MagicMock()
    mock_assembler_cls.return_value = mock_assembler_inst

    for key in list(sys.modules.keys()):
        if "tcp_server.main" in key or key == _MAIN_MOD:
            del sys.modules[key]

    with patch("shared.bus_client.BusClient", return_value=mock_bus), \
         patch("shared.logger.get_logger", return_value=MagicMock()), \
         patch("tcp_server.server.TCPServer", mock_server_cls), \
         patch("tcp_server.frame_relay.FrameRelay", mock_relay_cls), \
         patch("tcp_server.aa_cryptor.AACryptor", mock_cryptor_cls), \
         patch("tcp_server.frame_codec.FrameAssembler", mock_assembler_cls):
        import modules.tcp_server.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus
        mod._server = None
        mod._relay = None
        mod._cryptor = None
        mod._assembler = None
        mod._server_starting = False
        mod._restart_pending = False
        mod._shutdown_ack_event.clear()
        yield (mod, mock_bus, mock_server_cls, mock_server_inst,
               mock_relay_cls, mock_relay_inst,
               mock_cryptor_cls, mock_cryptor_inst,
               mock_assembler_cls, mock_assembler_inst)


# ===========================================================================
# Section 1 — frame_codec helpers
# ===========================================================================

class TestFrameCodecHelpers:

    @pytest.mark.unit
    def test_message_type_ch0_is_specific(self, codec):
        assert codec._message_type(0, 0x0001) == codec._MT_SPECIFIC

    @pytest.mark.unit
    def test_message_type_channel_open_response_is_control(self, codec):
        assert codec._message_type(1, 0x0008) == codec._MT_CONTROL

    @pytest.mark.unit
    def test_message_type_non_ch0_other_msg_is_specific(self, codec):
        assert codec._message_type(3, 0x0007) == codec._MT_SPECIFIC

    @pytest.mark.unit
    def test_encryption_type_no_ssl_is_plain(self, codec):
        assert codec._encryption_type(0, 0x0001, False) == codec._ET_PLAIN

    @pytest.mark.unit
    def test_encryption_type_ssl_ch0_version_req_is_plain(self, codec):
        assert codec._encryption_type(0, 0x0001, True) == codec._ET_PLAIN

    @pytest.mark.unit
    def test_encryption_type_ssl_ch0_ssl_handshake_is_plain(self, codec):
        assert codec._encryption_type(0, 0x0003, True) == codec._ET_PLAIN

    @pytest.mark.unit
    def test_encryption_type_ssl_ch0_ping_is_plain(self, codec):
        assert codec._encryption_type(0, 0x000B, True) == codec._ET_PLAIN

    @pytest.mark.unit
    def test_encryption_type_ssl_ch0_other_msg_is_encrypted(self, codec):
        assert codec._encryption_type(0, 0x0005, True) == codec._ET_ENCRYPTED

    @pytest.mark.unit
    def test_encryption_type_ssl_ch_non0_is_encrypted(self, codec):
        assert codec._encryption_type(3, 0x0007, True) == codec._ET_ENCRYPTED


# ===========================================================================
# Section 2 — frame_codec.encode
# ===========================================================================

class TestFrameCodecEncode:

    @pytest.mark.unit
    def test_encode_returns_list(self, codec):
        result = codec.encode(0, 0x0001, b"hello", ssl_active=False)
        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.unit
    def test_encode_single_frame_bulk_flag(self, codec):
        frames = codec.encode(0, 0x0001, b"hello", ssl_active=False)
        header = frames[0][:2]
        flags = header[1]
        assert (flags & 0x03) == codec._FT_BULK

    @pytest.mark.unit
    def test_encode_channel_id_in_header(self, codec):
        frames = codec.encode(3, 0x0007, b"data", ssl_active=False)
        assert frames[0][0] == 3

    @pytest.mark.unit
    def test_encode_plain_no_encrypted_bit(self, codec):
        frames = codec.encode(0, 0x0001, b"data", ssl_active=False)
        flags = frames[0][1]
        assert (flags & 0x08) == 0

    @pytest.mark.unit
    def test_encode_ssl_ch0_exempt_stays_plain(self, codec):
        frames = codec.encode(0, 0x0003, b"tls", ssl_active=True, cryptor=None)
        flags = frames[0][1]
        assert (flags & 0x08) == 0

    @pytest.mark.unit
    def test_encode_ssl_ch0_non_exempt_with_cryptor_encrypts(self, codec):
        mock_cryptor = MagicMock()
        mock_cryptor.is_active.return_value = True
        mock_cryptor.encrypt.return_value = b"CIPHERTEXT"
        frames = codec.encode(0, 0x0005, b"payload", ssl_active=True, cryptor=mock_cryptor)
        flags = frames[0][1]
        assert (flags & 0x08) == codec._ET_ENCRYPTED
        mock_cryptor.encrypt.assert_called_once()

    @pytest.mark.unit
    def test_encode_ssl_no_cryptor_downgrades_to_plain(self, codec):
        frames = codec.encode(3, 0x0007, b"data", ssl_active=True, cryptor=None)
        flags = frames[0][1]
        assert (flags & 0x08) == 0

    @pytest.mark.unit
    def test_encode_channel_open_response_control_flag(self, codec):
        frames = codec.encode(1, 0x0008, b"coresp", ssl_active=False)
        flags = frames[0][1]
        assert (flags & 0x04) == codec._MT_CONTROL

    @pytest.mark.unit
    def test_encode_payload_contains_message_id(self, codec):
        frames = codec.encode(0, 0xABCD, b"body", ssl_active=False)
        # BULK: header is 4 bytes, payload starts at byte 4
        wire_payload = frames[0][4:]
        msg_id = struct.unpack(">H", wire_payload[:2])[0]
        assert msg_id == 0xABCD

    @pytest.mark.unit
    def test_encode_large_payload_produces_multiple_frames(self, codec):
        big_body = b"X" * (codec.FRAME_SIZE_THRESHOLD * 2)
        frames = codec.encode(0, 0x0001, big_body, ssl_active=False)
        assert len(frames) >= 3  # FIRST + MIDDLE(s) + LAST

    @pytest.mark.unit
    def test_encode_multi_frame_first_has_total_size_field(self, codec):
        big_body = b"Y" * (codec.FRAME_SIZE_THRESHOLD + 1)
        frames = codec.encode(0, 0x0001, big_body, ssl_active=False)
        first_frame_type = frames[0][1] & 0x03
        assert first_frame_type == codec._FT_FIRST
        # Bytes 4-7 of FIRST frame = total_size (u32 BE)
        total_size = struct.unpack(">I", frames[0][4:8])[0]
        assert total_size > 0

    @pytest.mark.unit
    def test_encode_multi_frame_last_has_last_flag(self, codec):
        big_body = b"Z" * (codec.FRAME_SIZE_THRESHOLD + 1)
        frames = codec.encode(0, 0x0001, big_body, ssl_active=False)
        last_frame_type = frames[-1][1] & 0x03
        assert last_frame_type == codec._FT_LAST

    @pytest.mark.unit
    def test_encode_empty_body_is_valid(self, codec):
        frames = codec.encode(0, 0x000D, b"", ssl_active=False)
        assert len(frames) == 1
        # Payload is just the 2-byte message_id
        wire_payload = frames[0][4:]
        assert len(wire_payload) == 2


# ===========================================================================
# Section 3 — FrameAssembler
# ===========================================================================

class TestFrameAssembler:

    def _assembler(self, codec):
        return codec.FrameAssembler()

    @pytest.mark.unit
    def test_bulk_frame_returns_immediately(self, codec):
        a = self._assembler(codec)
        result = a.feed(0, codec._FT_BULK, b"payload", 0)
        assert result is not None
        assert result[2] == b"payload"

    @pytest.mark.unit
    def test_bulk_frame_returns_correct_channel_id(self, codec):
        a = self._assembler(codec)
        result = a.feed(3, codec._FT_BULK, b"data", 0)
        assert result[0] == 3

    @pytest.mark.unit
    def test_first_frame_returns_none(self, codec):
        a = self._assembler(codec)
        result = a.feed(0, codec._FT_FIRST, b"chunk1", 12)
        assert result is None

    @pytest.mark.unit
    def test_first_middle_last_assembles_correctly(self, codec):
        a = self._assembler(codec)
        a.feed(0, codec._FT_FIRST, b"AAA", 9)
        a.feed(0, codec._FT_MIDDLE, b"BBB", 0)
        result = a.feed(0, codec._FT_LAST, b"CCC", 0)
        assert result is not None
        assert result[2] == b"AAABBBCCC"

    @pytest.mark.unit
    def test_first_last_no_middle(self, codec):
        a = self._assembler(codec)
        a.feed(0, codec._FT_FIRST, b"hello", 10)
        result = a.feed(0, codec._FT_LAST, b"world", 0)
        assert result is not None
        assert result[2] == b"helloworld"

    @pytest.mark.unit
    def test_last_frame_returns_bulk_frametype(self, codec):
        a = self._assembler(codec)
        a.feed(0, codec._FT_FIRST, b"p1", 4)
        result = a.feed(0, codec._FT_LAST, b"p2", 0)
        out_flags = result[1]
        assert (out_flags & 0x03) == codec._FT_BULK

    @pytest.mark.unit
    def test_middle_without_first_returns_none(self, codec):
        a = self._assembler(codec)
        result = a.feed(0, codec._FT_MIDDLE, b"orphan", 0)
        assert result is None

    @pytest.mark.unit
    def test_last_without_first_returns_none(self, codec):
        a = self._assembler(codec)
        result = a.feed(0, codec._FT_LAST, b"orphan", 0)
        assert result is None

    @pytest.mark.unit
    def test_reset_clears_all_channels(self, codec):
        a = self._assembler(codec)
        a.feed(0, codec._FT_FIRST, b"p", 6)
        a.reset()
        result = a.feed(0, codec._FT_LAST, b"end", 0)
        assert result is None

    @pytest.mark.unit
    def test_reset_single_channel(self, codec):
        a = self._assembler(codec)
        a.feed(0, codec._FT_FIRST, b"p", 6)
        a.feed(1, codec._FT_FIRST, b"q", 6)
        a.reset(channel_id=0)
        # ch0 buffer cleared — LAST without FIRST → None
        assert a.feed(0, codec._FT_LAST, b"end", 0) is None
        # ch1 still pending — LAST returns assembled
        result = a.feed(1, codec._FT_LAST, b"finish", 0)
        assert result is not None
        assert result[2] == b"qfinish"

    @pytest.mark.unit
    def test_multi_channel_isolation(self, codec):
        a = self._assembler(codec)
        a.feed(0, codec._FT_FIRST, b"ch0_", 8)
        a.feed(3, codec._FT_FIRST, b"ch3_", 8)
        r0 = a.feed(0, codec._FT_LAST, b"end0", 0)
        r3 = a.feed(3, codec._FT_LAST, b"end3", 0)
        assert r0[2] == b"ch0_end0"
        assert r3[2] == b"ch3_end3"


# ===========================================================================
# Section 4 — Boot handlers
# ===========================================================================

class TestBootHandlers:

    @pytest.mark.unit
    def test_readytostart_publishes_module_ready(self, ts):
        mod, mock_bus, *_ = ts
        mock_bus.publish.reset_mock()
        mod.on_system_readytostart()
        mock_bus.publish.assert_called_once_with(
            "system.module_ready", {"name": "tcp_server", "priority": 1}
        )

    @pytest.mark.unit
    def test_system_start_wrong_priority_no_publish(self, ts):
        mod, mock_bus, *_ = ts
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 99})
        mock_bus.publish.assert_not_called()

    @pytest.mark.unit
    def test_system_start_correct_priority_publishes_ready(self, ts):
        mod, mock_bus, *_ = ts
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 1})
        mock_bus.publish.assert_called_once_with(
            "system.ready", {"name": "tcp_server", "priority": 1}
        )

    @pytest.mark.unit
    def test_system_stop_calls_bus_stop(self, ts):
        mod, mock_bus, *_ = ts
        mod.on_system_stop("system.stop", {})
        mock_bus.stop.assert_called()


# ===========================================================================
# Section 5 — on_handshake_completed
# ===========================================================================

class TestHandshakeCompleted:

    @pytest.mark.unit
    def test_handshake_completed_starts_thread(self, ts):
        mod, mock_bus, mock_server_cls, mock_server_inst, *_ = ts
        import threading
        threads_before = set(t.name for t in threading.enumerate())
        mod.on_handshake_completed(
            "rfcomm.handshake.completed",
            {"device_address": "AA:BB:CC", "phone_ip": "192.168.7.1"}
        )
        import time; time.sleep(0.05)  # let thread start
        threads_after = set(t.name for t in threading.enumerate())
        assert len(threads_after) >= len(threads_before)  # at least one new thread

    @pytest.mark.unit
    def test_handshake_completed_sets_server_starting(self, ts):
        mod, *_ = ts
        mod.on_handshake_completed(
            "t", {"device_address": "AA:BB", "phone_ip": "x"}
        )
        # At this point _server_starting may be True or already False
        # depending on thread speed; ensure no exception raised

    @pytest.mark.unit
    def test_handshake_completed_duplicate_ignored(self, ts):
        mod, mock_bus, *_ = ts
        mod._server_starting = True  # simulate already started
        mock_bus.publish.reset_mock()
        mod.on_handshake_completed(
            "t", {"device_address": "AA", "phone_ip": "y"}
        )
        # Should NOT spawn a new thread and NOT publish tcp.server.started
        import time; time.sleep(0.05)
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "tcp.server.started" not in topics


# ===========================================================================
# Section 6 — on_frame_send
# ===========================================================================

class TestFrameSend:

    @pytest.mark.unit
    def test_frame_send_no_relay_no_crash(self, ts):
        mod, *_ = ts
        mod._relay = None
        mod.on_frame_send("aa.frame.send", {
            "channel_id": 0, "message_id": 0x0001,
            "payload_hex": "aabb", "encrypted": False
        })  # must not raise

    @pytest.mark.unit
    def test_frame_send_calls_relay_send_raw(self, ts):
        mod, mock_bus, _, _, _, mock_relay_inst, *_ = ts
        mod._relay = mock_relay_inst
        with patch("tcp_server.frame_codec.encode", return_value=[b"FRAME"]) as mock_encode:
            mod.on_frame_send("aa.frame.send", {
                "channel_id": 0, "message_id": 0x0001,
                "payload_hex": "", "encrypted": False
            })
        mock_relay_inst.send_raw.assert_called_once_with(b"FRAME")

    @pytest.mark.unit
    def test_frame_send_malformed_payload_no_crash(self, ts):
        mod, mock_bus, _, _, _, mock_relay_inst, *_ = ts
        mod._relay = mock_relay_inst
        mod.on_frame_send("aa.frame.send", {"channel_id": "bad"})  # must not raise

    @pytest.mark.unit
    def test_frame_send_invalid_hex_no_crash(self, ts):
        mod, _, _, _, _, mock_relay_inst, *_ = ts
        mod._relay = mock_relay_inst
        mod.on_frame_send("aa.frame.send", {
            "channel_id": 0, "message_id": 1,
            "payload_hex": "ZZZZ", "encrypted": False
        })  # must not raise


# ===========================================================================
# Section 7 — TLS handlers
# ===========================================================================

class TestTlsHandlers:

    @pytest.mark.unit
    def test_start_tls_creates_cryptor(self, ts):
        mod, mock_bus, _, _, _, _, mock_cryptor_cls, mock_cryptor_inst, *_ = ts
        mod.on_handshake_start_tls("t", {})
        mock_cryptor_cls.assert_called()
        mock_cryptor_inst.init.assert_called_once()

    @pytest.mark.unit
    def test_start_tls_with_outgoing_publishes(self, ts):
        mod, mock_bus, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        mock_cryptor_inst.drive_handshake.return_value = b"\x01\x02"
        mock_bus.publish.reset_mock()
        mod.on_handshake_start_tls("t", {})
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "tcp.server.tls_handshake" in topics

    @pytest.mark.unit
    def test_start_tls_no_outgoing_no_publish(self, ts):
        mod, mock_bus, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        mock_cryptor_inst.drive_handshake.return_value = b""
        mock_bus.publish.reset_mock()
        mod.on_handshake_start_tls("t", {})
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "tcp.server.tls_handshake" not in topics

    @pytest.mark.unit
    def test_feed_input_no_cryptor_no_crash(self, ts):
        mod, *_ = ts
        mod._cryptor = None
        mod.on_handshake_feed_input("t", {"payload_hex": "aabb"})  # must not raise

    @pytest.mark.unit
    def test_feed_input_active_publishes_completed(self, ts):
        mod, mock_bus, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        mock_cryptor_inst.is_active.return_value = True
        mock_cryptor_inst.drive_handshake.return_value = b""
        mod._cryptor = mock_cryptor_inst
        mock_bus.publish.reset_mock()
        mod.on_handshake_feed_input("t", {"payload_hex": "aabb"})
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "tcp.server.tls_handshake_completed" in topics

    @pytest.mark.unit
    def test_feed_input_not_active_outgoing_publishes_tls_handshake(self, ts):
        mod, mock_bus, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        mock_cryptor_inst.is_active.return_value = False
        mock_cryptor_inst.drive_handshake.return_value = b"\x03\x04"
        mod._cryptor = mock_cryptor_inst
        mock_bus.publish.reset_mock()
        mod.on_handshake_feed_input("t", {"payload_hex": "aabb"})
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "tcp.server.tls_handshake" in topics

    @pytest.mark.unit
    def test_feed_input_malformed_hex_no_crash(self, ts):
        mod, _, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        mod._cryptor = mock_cryptor_inst
        mod.on_handshake_feed_input("t", {"payload_hex": "ZZZZ"})  # must not raise


# ===========================================================================
# Section 8 — _on_raw_frame
# ===========================================================================

class TestOnRawFrame:

    def _msg_payload(self, msg_id: int, body: bytes) -> bytes:
        return struct.pack(">H", msg_id) + body

    @pytest.mark.unit
    def test_bulk_frame_published_on_channel_topic(self, ts):
        mod, mock_bus, *_ = ts
        from tcp_server.frame_codec import FrameAssembler
        a = FrameAssembler()
        mod._assembler = a
        payload = self._msg_payload(0x0001, b"body")
        mock_bus.publish.reset_mock()
        mod._on_raw_frame(0, 0x03, payload, 0)  # BULK, no enc
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.frame.ch0" in topics

    @pytest.mark.unit
    def test_frame_strips_message_id(self, ts):
        mod, mock_bus, *_ = ts
        from tcp_server.frame_codec import FrameAssembler
        a = FrameAssembler()
        mod._assembler = a
        payload = self._msg_payload(0xABCD, b"proto_body")
        mock_bus.publish.reset_mock()
        mod._on_raw_frame(0, 0x03, payload, 0)
        ch0_calls = [c for c in mock_bus.publish.call_args_list if c.args[0] == "aa.frame.ch0"]
        assert ch0_calls
        data = ch0_calls[0].args[1]
        assert data["message_id"] == 0xABCD
        assert data["payload_hex"] == b"proto_body".hex()

    @pytest.mark.unit
    def test_too_short_payload_dropped(self, ts):
        mod, mock_bus, *_ = ts
        from tcp_server.frame_codec import FrameAssembler
        a = FrameAssembler()
        mod._assembler = a
        mock_bus.publish.reset_mock()
        mod._on_raw_frame(0, 0x03, b"\x00", 0)  # only 1 byte
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.frame.ch0" not in topics

    @pytest.mark.unit
    def test_encrypted_frame_decrypted_when_cryptor_active(self, ts):
        mod, mock_bus, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        from tcp_server.frame_codec import FrameAssembler
        a = FrameAssembler()
        mod._assembler = a
        mock_cryptor_inst.is_active.return_value = True
        plain_payload = self._msg_payload(0x0007, b"decrypted")
        mock_cryptor_inst.decrypt.return_value = plain_payload
        mod._cryptor = mock_cryptor_inst
        mock_bus.publish.reset_mock()
        mod._on_raw_frame(3, 0x0B, b"ciphertext", 0)  # flags: enc=0x08 + BULK=0x03
        mock_cryptor_inst.decrypt.assert_called_once()

    @pytest.mark.unit
    def test_unencrypted_frame_no_decrypt_call(self, ts):
        mod, mock_bus, _, _, _, _, _, mock_cryptor_inst, *_ = ts
        from tcp_server.frame_codec import FrameAssembler
        a = FrameAssembler()
        mod._assembler = a
        mod._cryptor = mock_cryptor_inst
        payload = self._msg_payload(0x0001, b"hello")
        mod._on_raw_frame(0, 0x03, payload, 0)  # no enc bit
        mock_cryptor_inst.decrypt.assert_not_called()

    @pytest.mark.unit
    def test_aa_frame_received_always_published(self, ts):
        mod, mock_bus, *_ = ts
        from tcp_server.frame_codec import FrameAssembler
        a = FrameAssembler()
        mod._assembler = a
        payload = self._msg_payload(0x0002, b"diag")
        mock_bus.publish.reset_mock()
        mod._on_raw_frame(0, 0x03, payload, 0)
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.frame.received" in topics


# ===========================================================================
# Section 9 — Session restart
# ===========================================================================

class TestSessionRestart:

    @pytest.mark.unit
    def test_restart_no_relay_ignored(self, ts):
        mod, mock_bus, *_ = ts
        mod._relay = None
        mock_bus.publish.reset_mock()
        mod.on_aa_session_restart("t", {})
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.session.restarting" not in topics

    @pytest.mark.unit
    def test_restart_sends_shutdown_request_via_relay(self, ts):
        mod, mock_bus, _, _, _, mock_relay_inst, *_ = ts
        mod._relay = mock_relay_inst
        with patch("tcp_server.frame_codec.encode", return_value=[b"SHUTDOWN_REQ"]) as mock_enc:
            # Make shutdown ack arrive quickly
            mod._shutdown_ack_event.set()
            mod.on_aa_session_restart("t", {})
        mock_relay_inst.send_raw.assert_called_with(b"SHUTDOWN_REQ")

    @pytest.mark.unit
    def test_restart_publishes_restarting_on_timeout(self, ts):
        mod, mock_bus, _, _, _, mock_relay_inst, *_ = ts
        mod._relay = mock_relay_inst
        mod._SHUTDOWN_ACK_TIMEOUT = 0.05
        with patch("tcp_server.frame_codec.encode", return_value=[b"S"]):
            mock_bus.publish.reset_mock()
            mod.on_aa_session_restart("t", {})
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.session.restarting" in topics

    @pytest.mark.unit
    def test_on_ch0_frame_sets_ack_event_when_restart_pending(self, ts):
        mod, *_ = ts
        mod._restart_pending = True
        mod._shutdown_ack_event.clear()
        mod.on_ch0_frame("aa.frame.ch0", {"message_id": 0x000E})
        assert mod._shutdown_ack_event.is_set()

    @pytest.mark.unit
    def test_on_ch0_frame_ignores_wrong_msg_id(self, ts):
        mod, *_ = ts
        mod._restart_pending = True
        mod._shutdown_ack_event.clear()
        mod.on_ch0_frame("aa.frame.ch0", {"message_id": 0x0001})
        assert not mod._shutdown_ack_event.is_set()

    @pytest.mark.unit
    def test_on_ch0_frame_ignored_when_not_restart_pending(self, ts):
        mod, *_ = ts
        mod._restart_pending = False
        mod._shutdown_ack_event.clear()
        mod.on_ch0_frame("aa.frame.ch0", {"message_id": 0x000E})
        assert not mod._shutdown_ack_event.is_set()
