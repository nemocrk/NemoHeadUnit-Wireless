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
    start_resp = WifiStartResponse(status=0, ip_address="192.168.50.100").SerializeToString()
    pkt1 = encode(MSG_WIFI_START_RESPONSE, start_resp)

    # 2. WifiInfoRequest
    pkt2 = encode(MSG_WIFI_INFO_REQUEST, b"")

    # 3. WifiConnectStatus (phone joined)
    status_msg = WifiConnectStatus(state=0, status_text="OK")
    pkt3 = encode(MSG_WIFI_CONNECT_STATUS, status_msg.SerializeToString())

    # Socket reads: each recv returns exact header + payload
    mock_handshake_socket.recv.side_effect = [
        p for p in [
            pkt1[:4], pkt1[4:],
            pkt2[:4], pkt2[4:],
            pkt3[:4], pkt3[4:],
        ] if p
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
    status_msg = WifiConnectStatus(state=0, status_text="OK")
    pkt_status = encode(MSG_WIFI_CONNECT_STATUS, status_msg.SerializeToString())

    mock_handshake_socket.recv.side_effect = [
        p for p in [
            pkt_info[:4], pkt_info[4:],
            pkt_status[:4], pkt_status[4:],
        ] if p
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
