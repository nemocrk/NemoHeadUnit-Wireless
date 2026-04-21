"""
test_rfcomm_packet.py — Unit tests for rfcomm_handshake/packet.py

No hardware, no ZMQ, no D-Bus required.
"""

import socket
import struct
import threading
import pytest

from rfcomm_handshake.packet import (
    encode, decode, recv_packet, send_packet,
    Packet,
    MSG_WIFI_START_REQUEST,
    MSG_WIFI_START_RESPONSE,
    MSG_WIFI_INFO_REQUEST,
    MSG_WIFI_INFO_RESPONSE,
    MSG_WIFI_CONNECT_STATUS,
    HEADER_SIZE,
)


# ---------------------------------------------------------------------------
# encode / decode
# ---------------------------------------------------------------------------

class TestEncode:
    def test_empty_payload(self):
        result = encode(MSG_WIFI_START_RESPONSE, b"")
        # [length=0: 2B][msg_id=7: 2B]
        assert result == struct.pack(">HH", 0, MSG_WIFI_START_RESPONSE)

    def test_with_payload(self):
        payload = b"hello"
        result = encode(MSG_WIFI_INFO_RESPONSE, payload)
        expected = struct.pack(">HH", 5, MSG_WIFI_INFO_RESPONSE) + payload
        assert result == expected

    def test_header_size(self):
        raw = encode(1, b"")
        assert len(raw) == HEADER_SIZE


class TestDecode:
    def test_roundtrip(self):
        payload = b"test payload"
        raw = encode(MSG_WIFI_INFO_REQUEST, payload)
        pkt = decode(raw)
        assert pkt is not None
        assert pkt.msg_id == MSG_WIFI_INFO_REQUEST
        assert pkt.payload == payload

    def test_empty_payload_roundtrip(self):
        raw = encode(MSG_WIFI_CONNECT_STATUS, b"")
        pkt = decode(raw)
        assert pkt is not None
        assert pkt.msg_id == MSG_WIFI_CONNECT_STATUS
        assert pkt.payload == b""

    def test_too_short_returns_none(self):
        assert decode(b"\x00\x01") is None

    def test_truncated_payload_returns_none(self):
        # Header says payload_len=10 but we only give 3 bytes
        raw = struct.pack(">HH", 10, 1) + b"abc"
        assert decode(raw) is None

    def test_all_message_ids(self):
        for msg_id in [
            MSG_WIFI_START_REQUEST,
            MSG_WIFI_START_RESPONSE,
            MSG_WIFI_INFO_REQUEST,
            MSG_WIFI_INFO_RESPONSE,
            MSG_WIFI_CONNECT_STATUS,
        ]:
            pkt = decode(encode(msg_id, b""))
            assert pkt is not None
            assert pkt.msg_id == msg_id


# ---------------------------------------------------------------------------
# recv_packet / send_packet via loopback socket pair
# ---------------------------------------------------------------------------

@pytest.fixture
def socket_pair():
    """Create a connected socket pair for testing."""
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


class TestSocketIO:
    def test_send_recv_roundtrip(self, socket_pair):
        sender, receiver = socket_pair
        payload = b"android auto"
        assert send_packet(sender, MSG_WIFI_INFO_RESPONSE, payload)
        pkt = recv_packet(receiver)
        assert pkt is not None
        assert pkt.msg_id == MSG_WIFI_INFO_RESPONSE
        assert pkt.payload == payload

    def test_send_empty_payload(self, socket_pair):
        sender, receiver = socket_pair
        assert send_packet(sender, MSG_WIFI_START_RESPONSE)
        pkt = recv_packet(receiver)
        assert pkt is not None
        assert pkt.payload == b""

    def test_recv_on_closed_socket_returns_none(self):
        a, b = socket.socketpair()
        b.close()
        pkt = recv_packet(a)
        assert pkt is None
        a.close()

    def test_multiple_packets_in_sequence(self, socket_pair):
        sender, receiver = socket_pair
        messages = [
            (MSG_WIFI_START_REQUEST,  b"ip:port"),
            (MSG_WIFI_START_RESPONSE, b""),
            (MSG_WIFI_INFO_REQUEST,   b""),
            (MSG_WIFI_INFO_RESPONSE,  b"ssid|key"),
            (MSG_WIFI_CONNECT_STATUS, b""),
        ]
        for msg_id, payload in messages:
            send_packet(sender, msg_id, payload)
        for msg_id, payload in messages:
            pkt = recv_packet(receiver)
            assert pkt is not None
            assert pkt.msg_id == msg_id
            assert pkt.payload == payload


# ---------------------------------------------------------------------------
# Packet dataclass
# ---------------------------------------------------------------------------

class TestPacket:
    def test_repr(self):
        p = Packet(msg_id=3, payload=b"abc")
        assert "3" in repr(p)
        assert "3" in repr(p)  # payload_len

    def test_empty_payload_default(self):
        p = Packet(msg_id=1)
        assert p.payload == b""
