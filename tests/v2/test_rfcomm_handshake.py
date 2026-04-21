"""
test_rfcomm_handshake.py — Unit tests for rfcomm_handshake/handshake.py

Uses socket loopback pairs to simulate the phone side.
No hardware, no ZMQ, no D-Bus required.
"""

import socket
import struct
import threading
import pytest

from rfcomm_handshake.packet import (
    encode,
    MSG_WIFI_START_REQUEST,
    MSG_WIFI_START_RESPONSE,
    MSG_WIFI_INFO_REQUEST,
    MSG_WIFI_INFO_RESPONSE,
    MSG_WIFI_CONNECT_STATUS,
)
from rfcomm_handshake.handshake import RfcommHandshake, HandshakeResult


CREDS = {
    "ssid":          "TestAP",
    "key":           "secret123",
    "bssid":         "AA:BB:CC:DD:EE:FF",
    "security_mode": 8,
    "ap_type":       1,
}


# ---------------------------------------------------------------------------
# Phone simulator — runs the protocol from the phone side
# ---------------------------------------------------------------------------

def _phone_side(sock: socket.socket, send_bad_stage: int = 0) -> None:
    """
    Simulate the Android phone:
      1. Send WifiStartRequest
      2. Recv WifiStartResponse
      3. Send WifiInfoRequest
      4. Recv WifiInfoResponse
      5. Send WifiConnectStatus

    If send_bad_stage is set, send a wrong msg_id at that stage.
    """
    try:
        # Stage 1 — phone sends WifiStartRequest
        msg_id = MSG_WIFI_START_REQUEST if send_bad_stage != 1 else 99
        sock.sendall(encode(msg_id, b"192.168.50.10"))

        # Stage 2 — phone receives WifiStartResponse
        sock.recv(1024)

        # Stage 3 — phone sends WifiInfoRequest
        msg_id = MSG_WIFI_INFO_REQUEST if send_bad_stage != 3 else 99
        sock.sendall(encode(msg_id, b""))

        # Stage 4 — phone receives WifiInfoResponse
        sock.recv(4096)

        # Stage 5 — phone sends WifiConnectStatus
        msg_id = MSG_WIFI_CONNECT_STATUS if send_bad_stage != 5 else 99
        sock.sendall(encode(msg_id, b""))
    finally:
        sock.close()


@pytest.fixture
def handshake_sockets():
    """Return (head_unit_sock, phone_sock) connected pair."""
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestRfcommHandshakeSuccess:
    def test_full_handshake_succeeds(self, handshake_sockets):
        hu_sock, phone_sock = handshake_sockets

        stages = []
        t = threading.Thread(
            target=_phone_side, args=(phone_sock,), daemon=True
        )
        t.start()

        hs = RfcommHandshake(
            sock=hu_sock,
            credentials=CREDS,
            on_stage_cb=stages.append,
        )
        result = hs.run()
        t.join(timeout=5)

        assert result.success is True
        assert result.error == ""

    def test_stage_callbacks_called_in_order(self, handshake_sockets):
        hu_sock, phone_sock = handshake_sockets
        stages = []
        threading.Thread(target=_phone_side, args=(phone_sock,), daemon=True).start()

        hs = RfcommHandshake(sock=hu_sock, credentials=CREDS, on_stage_cb=stages.append)
        hs.run()

        expected = [
            "WifiStartRequest",
            "WifiStartResponse",
            "WifiInfoRequest",
            "WifiInfoResponse",
            "WifiConnectStatus",
        ]
        assert stages == expected

    def test_phone_ip_extracted_when_present(self, handshake_sockets):
        hu_sock, phone_sock = handshake_sockets
        threading.Thread(target=_phone_side, args=(phone_sock,), daemon=True).start()

        hs = RfcommHandshake(sock=hu_sock, credentials=CREDS)
        result = hs.run()

        # _phone_side sends "192.168.50.10" in stage 1 payload
        assert result.phone_ip == "192.168.50.10"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

class TestRfcommHandshakeFailure:
    @pytest.mark.parametrize("bad_stage", [1, 3, 5])
    def test_wrong_msg_id_at_stage_fails(self, bad_stage):
        a, b = socket.socketpair()
        threading.Thread(
            target=_phone_side, args=(b, bad_stage), daemon=True
        ).start()
        hs = RfcommHandshake(sock=a, credentials=CREDS)
        result = hs.run()
        assert result.success is False
        assert result.error != ""
        a.close()

    def test_closed_socket_fails(self):
        a, b = socket.socketpair()
        b.close()  # close phone side immediately
        hs = RfcommHandshake(sock=a, credentials=CREDS)
        result = hs.run()
        assert result.success is False
        a.close()

    def test_handshake_result_bool(self):
        assert bool(HandshakeResult(True)) is True
        assert bool(HandshakeResult(False)) is False


# ---------------------------------------------------------------------------
# _extract_ip
# ---------------------------------------------------------------------------

class TestExtractIp:
    def test_extracts_valid_ip(self):
        ip = RfcommHandshake._extract_ip(b"connect to 192.168.1.5 port")
        assert ip == "192.168.1.5"

    def test_returns_empty_when_no_ip(self):
        ip = RfcommHandshake._extract_ip(b"no ip here")
        assert ip == ""

    def test_handles_binary_garbage(self):
        ip = RfcommHandshake._extract_ip(b"\x00\xff\xfe\xab")
        assert ip == ""
