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
