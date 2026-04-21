"""
test_rfcomm_handshake.py — Unit tests for rfcomm_handshake/handshake.py

Uses socket loopback pairs to simulate the phone side.
No hardware, no ZMQ, no D-Bus required.
"""

import socket
import threading
import pytest

from rfcomm_handshake.packet import (
    encode,
    MSG_WIFI_START_REQUEST,
    MSG_WIFI_INFO_REQUEST,
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
# Phone simulator
# ---------------------------------------------------------------------------

def _phone_side(sock: socket.socket, send_bad_stage: int = 0) -> None:
    """
    Simulate the Android phone through the 5-stage handshake.
    OSError/BrokenPipeError are suppressed: the HU may close
    the socket early (e.g. on wrong msg_id), which is expected.
    """
    try:
        sock.sendall(encode(
            MSG_WIFI_START_REQUEST if send_bad_stage != 1 else 99,
            b"192.168.50.10",
        ))
        sock.recv(1024)   # WifiStartResponse
        sock.sendall(encode(
            MSG_WIFI_INFO_REQUEST if send_bad_stage != 3 else 99,
            b"",
        ))
        sock.recv(4096)   # WifiInfoResponse
        sock.sendall(encode(
            MSG_WIFI_CONNECT_STATUS if send_bad_stage != 5 else 99,
            b"",
        ))
    except OSError:
        # Expected when the HU closes the socket after detecting bad msg_id
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


@pytest.fixture
def handshake_sockets():
    """Return (head_unit_sock, phone_sock) connected pair."""
    a, b = socket.socketpair()
    yield a, b
    for s in (a, b):
        try:
            s.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestRfcommHandshakeSuccess:
    def test_full_handshake_succeeds(self, handshake_sockets):
        hu_sock, phone_sock = handshake_sockets
        t = threading.Thread(target=_phone_side, args=(phone_sock,), daemon=True)
        t.start()
        hs = RfcommHandshake(sock=hu_sock, credentials=CREDS)
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
        assert stages == [
            "WifiStartRequest",
            "WifiStartResponse",
            "WifiInfoRequest",
            "WifiInfoResponse",
            "WifiConnectStatus",
        ]

    def test_phone_ip_extracted_when_present(self, handshake_sockets):
        hu_sock, phone_sock = handshake_sockets
        threading.Thread(target=_phone_side, args=(phone_sock,), daemon=True).start()
        result = RfcommHandshake(sock=hu_sock, credentials=CREDS).run()
        assert result.phone_ip == "192.168.50.10"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

class TestRfcommHandshakeFailure:
    @pytest.mark.parametrize("bad_stage", [1, 3, 5])
    def test_wrong_msg_id_at_stage_fails(self, bad_stage):
        a, b = socket.socketpair()
        t = threading.Thread(target=_phone_side, args=(b, bad_stage), daemon=True)
        t.start()
        result = RfcommHandshake(sock=a, credentials=CREDS).run()
        t.join(timeout=3)   # wait for phone thread to exit cleanly
        try:
            a.close()
        except OSError:
            pass
        assert result.success is False
        assert result.error != ""

    def test_closed_socket_fails(self):
        a, b = socket.socketpair()
        b.close()
        result = RfcommHandshake(sock=a, credentials=CREDS).run()
        try:
            a.close()
        except OSError:
            pass
        assert result.success is False

    def test_handshake_result_bool(self):
        assert bool(HandshakeResult(True)) is True
        assert bool(HandshakeResult(False)) is False


# ---------------------------------------------------------------------------
# _extract_ip
# ---------------------------------------------------------------------------

class TestExtractIp:
    def test_extracts_valid_ip(self):
        assert RfcommHandshake._extract_ip(b"connect to 192.168.1.5 port") == "192.168.1.5"

    def test_returns_empty_when_no_ip(self):
        assert RfcommHandshake._extract_ip(b"no ip here") == ""

    def test_handles_binary_garbage(self):
        assert RfcommHandshake._extract_ip(b"\x00\xff\xfe\xab") == ""
