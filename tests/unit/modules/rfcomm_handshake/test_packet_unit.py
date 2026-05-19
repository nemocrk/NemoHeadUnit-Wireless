"""
Unit tests for rfcomm_handshake/packet.py

Covers:
  Section 1 — encode: header format, empty payload, large payload
  Section 2 — decode: happy path, truncated buffer, short header, trailing bytes
  Section 3 — Packet dataclass: repr, equality
  Section 4 — recv_packet: happy path via mock socket, short header, empty recv
  Section 5 — send_packet: success, sendall exception
  Section 6 — _recv_exact: accumulates multiple chunks, returns None on EOF
"""

import struct
import pytest
from unittest.mock import MagicMock, patch, call

import sys
from pathlib import Path

# tests/unit/modules/rfcomm_handshake/test_packet_unit.py
# parents: [0]=rfcomm_handshake, [1]=modules, [2]=unit, [3]=tests, [4]=root
_REPO_ROOT = Path(__file__).parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

with patch("shared.logger.get_logger", return_value=MagicMock()):
    from rfcomm_handshake.packet import (
        encode, decode, recv_packet, send_packet, _recv_exact,
        Packet, HEADER_SIZE,
        MSG_WIFI_START_REQUEST, MSG_WIFI_INFO_REQUEST,
        MSG_WIFI_INFO_RESPONSE, MSG_WIFI_START_RESPONSE, MSG_WIFI_CONNECT_STATUS,
    )


# ===========================================================================
# Section 1 — encode
# ===========================================================================

class TestEncode:

    @pytest.mark.unit
    def test_encode_empty_payload_length_zero(self):
        wire = encode(MSG_WIFI_START_RESPONSE, b"")
        length, msg_id = struct.unpack(">HH", wire[:4])
        assert length == 0
        assert msg_id == MSG_WIFI_START_RESPONSE

    @pytest.mark.unit
    def test_encode_returns_header_plus_payload(self):
        payload = b"\x01\x02\x03"
        wire = encode(MSG_WIFI_INFO_REQUEST, payload)
        assert wire[HEADER_SIZE:] == payload

    @pytest.mark.unit
    def test_encode_length_field_matches_payload(self):
        payload = b"hello world"
        wire = encode(MSG_WIFI_INFO_RESPONSE, payload)
        length = struct.unpack(">H", wire[:2])[0]
        assert length == len(payload)

    @pytest.mark.unit
    def test_encode_msg_id_field_correct(self):
        wire = encode(MSG_WIFI_START_REQUEST, b"")
        msg_id = struct.unpack(">H", wire[2:4])[0]
        assert msg_id == MSG_WIFI_START_REQUEST

    @pytest.mark.unit
    def test_encode_total_length_equals_header_plus_payload(self):
        payload = b"X" * 100
        wire = encode(MSG_WIFI_CONNECT_STATUS, payload)
        assert len(wire) == HEADER_SIZE + len(payload)

    @pytest.mark.unit
    def test_encode_default_payload_is_empty(self):
        wire = encode(MSG_WIFI_START_REQUEST)
        assert len(wire) == HEADER_SIZE


# ===========================================================================
# Section 2 — decode
# ===========================================================================

class TestDecode:

    @pytest.mark.unit
    def test_decode_roundtrip(self):
        payload = b"\xDE\xAD\xBE\xEF"
        wire = encode(MSG_WIFI_INFO_RESPONSE, payload)
        pkt = decode(wire)
        assert pkt is not None
        assert pkt.msg_id == MSG_WIFI_INFO_RESPONSE
        assert pkt.payload == payload

    @pytest.mark.unit
    def test_decode_empty_payload(self):
        wire = encode(MSG_WIFI_START_RESPONSE, b"")
        pkt = decode(wire)
        assert pkt is not None
        assert pkt.payload == b""

    @pytest.mark.unit
    def test_decode_too_short_returns_none(self):
        assert decode(b"\x00\x01") is None

    @pytest.mark.unit
    def test_decode_empty_buffer_returns_none(self):
        assert decode(b"") is None

    @pytest.mark.unit
    def test_decode_truncated_payload_returns_none(self):
        # Declares payload_len=10 but only provides 3 bytes
        data = struct.pack(">HH", 10, MSG_WIFI_INFO_REQUEST) + b"ABC"
        assert decode(data) is None

    @pytest.mark.unit
    def test_decode_ignores_trailing_bytes(self):
        payload = b"proto"
        wire = encode(MSG_WIFI_START_REQUEST, payload) + b"\xFF" * 20
        pkt = decode(wire)
        assert pkt is not None
        assert pkt.payload == payload

    @pytest.mark.unit
    def test_decode_all_msg_id_constants(self):
        for msg_id in [
            MSG_WIFI_START_REQUEST, MSG_WIFI_INFO_REQUEST,
            MSG_WIFI_INFO_RESPONSE, MSG_WIFI_START_RESPONSE,
            MSG_WIFI_CONNECT_STATUS,
        ]:
            pkt = decode(encode(msg_id, b"x"))
            assert pkt is not None
            assert pkt.msg_id == msg_id


# ===========================================================================
# Section 3 — Packet dataclass
# ===========================================================================

class TestPacketDataclass:

    @pytest.mark.unit
    def test_repr_contains_msg_id(self):
        pkt = Packet(msg_id=3, payload=b"abc")
        assert "3" in repr(pkt)

    @pytest.mark.unit
    def test_repr_contains_payload_len(self):
        pkt = Packet(msg_id=1, payload=b"hello")
        assert "5" in repr(pkt)

    @pytest.mark.unit
    def test_default_payload_is_empty_bytes(self):
        pkt = Packet(msg_id=2)
        assert pkt.payload == b""

    @pytest.mark.unit
    def test_equality(self):
        assert Packet(msg_id=1, payload=b"a") == Packet(msg_id=1, payload=b"a")
        assert Packet(msg_id=1, payload=b"a") != Packet(msg_id=2, payload=b"a")


# ===========================================================================
# Section 4 — recv_packet
# ===========================================================================

class TestRecvPacket:

    def _make_sock(self, *chunks):
        """Mock socket that returns chunks sequentially."""
        sock = MagicMock()
        sock.recv.side_effect = list(chunks)
        return sock

    @pytest.mark.unit
    def test_recv_packet_happy_path(self):
        payload = b"\xAA\xBB"
        wire = encode(MSG_WIFI_INFO_RESPONSE, payload)
        # recv_exact reads HEADER_SIZE then payload_len
        header = wire[:HEADER_SIZE]
        body   = wire[HEADER_SIZE:]
        sock = self._make_sock(header, body)
        pkt = recv_packet(sock)
        assert pkt is not None
        assert pkt.msg_id == MSG_WIFI_INFO_RESPONSE
        assert pkt.payload == payload

    @pytest.mark.unit
    def test_recv_packet_empty_recv_returns_none(self):
        sock = self._make_sock(b"")  # connection closed immediately
        pkt = recv_packet(sock)
        assert pkt is None

    @pytest.mark.unit
    def test_recv_packet_exception_returns_none(self):
        sock = MagicMock()
        sock.recv.side_effect = OSError("broken pipe")
        pkt = recv_packet(sock)
        assert pkt is None

    @pytest.mark.unit
    def test_recv_packet_zero_payload_length(self):
        wire = encode(MSG_WIFI_START_RESPONSE, b"")
        sock = self._make_sock(wire[:HEADER_SIZE])
        pkt = recv_packet(sock)
        assert pkt is not None
        assert pkt.payload == b""


# ===========================================================================
# Section 5 — send_packet
# ===========================================================================

class TestSendPacket:

    @pytest.mark.unit
    def test_send_packet_returns_true_on_success(self):
        sock = MagicMock()
        result = send_packet(sock, MSG_WIFI_START_REQUEST, b"payload")
        assert result is True

    @pytest.mark.unit
    def test_send_packet_calls_sendall_with_correct_wire(self):
        sock = MagicMock()
        payload = b"\x01\x02"
        send_packet(sock, MSG_WIFI_INFO_REQUEST, payload)
        expected = encode(MSG_WIFI_INFO_REQUEST, payload)
        sock.sendall.assert_called_once_with(expected)

    @pytest.mark.unit
    def test_send_packet_returns_false_on_exception(self):
        sock = MagicMock()
        sock.sendall.side_effect = OSError("broken")
        result = send_packet(sock, MSG_WIFI_START_REQUEST)
        assert result is False

    @pytest.mark.unit
    def test_send_packet_default_payload_empty(self):
        sock = MagicMock()
        send_packet(sock, MSG_WIFI_CONNECT_STATUS)
        args = sock.sendall.call_args[0][0]
        length = struct.unpack(">H", args[:2])[0]
        assert length == 0


# ===========================================================================
# Section 6 — _recv_exact
# ===========================================================================

class TestRecvExact:

    @pytest.mark.unit
    def test_recv_exact_single_chunk(self):
        sock = MagicMock()
        sock.recv.return_value = b"ABCD"
        result = _recv_exact(sock, 4)
        assert result == b"ABCD"

    @pytest.mark.unit
    def test_recv_exact_multiple_chunks(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"AB", b"CD"]
        result = _recv_exact(sock, 4)
        assert result == b"ABCD"

    @pytest.mark.unit
    def test_recv_exact_eof_returns_none(self):
        sock = MagicMock()
        sock.recv.return_value = b""
        result = _recv_exact(sock, 4)
        assert result is None

    @pytest.mark.unit
    def test_recv_exact_three_chunks(self):
        sock = MagicMock()
        sock.recv.side_effect = [b"A", b"B", b"C"]
        result = _recv_exact(sock, 3)
        assert result == b"ABC"
