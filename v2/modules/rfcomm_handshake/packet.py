"""
packet.py — RFCOMM packet encode/decode for Android Auto wireless handshake.

Packet format:
  [length : u16 big-endian]  — payload length in bytes
  [msg_id : u16 big-endian]  — message type identifier
  [payload: bytes]           — protobuf or raw bytes

Message IDs (Android Auto wireless protocol):
  1  WifiStartRequest   — phone → head unit: TCP IP + port
  2  WifiInfoRequest    — phone → head unit: request WiFi credentials
  3  WifiInfoResponse   — head unit → phone: SSID, key, BSSID, security
  6  WifiConnectStatus  — phone → head unit: joined WiFi OK
  7  WifiStartResponse  — head unit → phone: ack WifiStartRequest

No ZMQ dependency.
"""

import struct
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("rfcomm_handshake.packet")

# ---------------------------------------------------------------------------
# Message ID constants
# ---------------------------------------------------------------------------

MSG_WIFI_START_REQUEST  = 1
MSG_WIFI_INFO_REQUEST   = 2
MSG_WIFI_INFO_RESPONSE  = 3
MSG_WIFI_CONNECT_STATUS = 6
MSG_WIFI_START_RESPONSE = 7

# Security / AP type constants
# Names match WifiSecurityModeEnum and WifiAccessPointTypeEnum proto values
WPA2_SECURITY_MODE = 8   # WifiSecurityMode.WPA2
AP_TYPE_DYNAMIC    = 1   # WifiAccessPointType.DYNAMIC

HEADER_SIZE = 4  # 2 bytes length + 2 bytes msg_id


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class Packet:
    msg_id:  int
    payload: bytes = b""

    def __repr__(self) -> str:
        return f"Packet(msg_id={self.msg_id}, payload_len={len(self.payload)})"


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def encode(msg_id: int, payload: bytes = b"") -> bytes:
    """
    Encode a packet into bytes ready to send over the RFCOMM socket.

    >>> encode(7, b"") == b'\x00\x00\x00\x07'
    True
    """
    header = struct.pack(">HH", len(payload), msg_id)
    return header + payload


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def decode(data: bytes) -> Optional[Packet]:
    """
    Decode a raw bytes buffer into a Packet.
    Returns None if the buffer is too short or malformed.
    """
    if len(data) < HEADER_SIZE:
        log.warning(f"decode: buffer too short ({len(data)} bytes)")
        return None
    payload_len, msg_id = struct.unpack_from(">HH", data, 0)
    payload = data[HEADER_SIZE: HEADER_SIZE + payload_len]
    if len(payload) < payload_len:
        log.warning(f"decode: truncated payload (got {len(payload)}, expected {payload_len})")
        return None
    return Packet(msg_id=msg_id, payload=payload)


def recv_packet(sock) -> Optional[Packet]:
    """
    Read exactly one packet from a blocking socket.
    Returns None on error or connection close.
    """
    try:
        header = _recv_exact(sock, HEADER_SIZE)
        if not header:
            return None
        payload_len, msg_id = struct.unpack(">HH", header)
        payload = _recv_exact(sock, payload_len) if payload_len else b""
        if payload is None:
            return None
        pkt = Packet(msg_id=msg_id, payload=payload)
        log.debug(f"recv_packet: {pkt}")
        return pkt
    except Exception as e:
        log.error(f"recv_packet error: {e}")
        return None


def send_packet(sock, msg_id: int, payload: bytes = b"") -> bool:
    """Encode and send a packet over the socket. Returns True on success."""
    try:
        sock.sendall(encode(msg_id, payload))
        log.debug(f"send_packet: msg_id={msg_id} payload_len={len(payload)}")
        return True
    except Exception as e:
        log.error(f"send_packet error: {e}")
        return False


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _recv_exact(sock, n: int) -> Optional[bytes]:
    """Read exactly n bytes from socket, blocking."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf
