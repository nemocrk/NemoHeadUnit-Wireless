"""
phone_mock.py — E2E helper: simulate an Android phone in the AA wireless flow.

Responsibilities:
  1. RFCOMM handshake simulation via socketpair() — no real Bluetooth hardware.
     PhoneMock takes the "phone side" of a connected socket pair and drives
     the 5-stage RFCOMM WiFi-credential exchange with the head unit.

  2. TCP connection simulation — PhoneMock acts as the AA TCP client that
     connects to the NemoHeadUnit tcp_server after credentials are exchanged.

  3. AA frame exchange — send/recv raw AA frames for the oaa_control_channel
     smoke layer (channel 0 handshake frames).

Usage:
    # --- RFCOMM phase ---
    hu_sock, phone_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    phone = PhoneMock(phone_sock)
    phone.start()                          # starts background thread

    # Pass hu_sock to rfcomm_handshake RfcommHandshake
    result = RfcommHandshake(hu_sock, creds).run()

    phone.wait_done(timeout=5.0)
    assert phone.phone_ip_received == creds["gateway_ip"]
    assert phone.completed          # WifiConnectionStatus sent

    # --- TCP / AA phase ---
    aa_client = PhoneMock.connect_tcp(host="127.0.0.1", port=5288)
    aa_client.send_aa_frame(channel_id=0, message_id=MSG_VERSION_RESPONSE, body=bytes)
    frame = aa_client.recv_aa_frame(timeout=2.0)

No ZMQ / bus dependency — pure socket I/O.
"""

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# RFCOMM wire constants (mirror rfcomm_handshake/packet.py)
# ---------------------------------------------------------------------------

MSG_WIFI_START_REQUEST  = 1   # HU → phone
MSG_WIFI_INFO_REQUEST   = 2   # phone → HU
MSG_WIFI_INFO_RESPONSE  = 3   # HU → phone
MSG_WIFI_START_RESPONSE = 6   # phone → HU (optional ack)
MSG_WIFI_CONNECT_STATUS = 7   # phone → HU (WiFi joined)

RFCOMM_HEADER_SIZE = 4  # u16 length + u16 msg_id, big-endian

# ---------------------------------------------------------------------------
# AA TCP wire constants (mirror oaa_control_channel/serializer.py)
# ---------------------------------------------------------------------------

AA_FRAME_HEADER_SIZE = 6   # channel_id u8 + flags u8 + msg_id u16 + length u16


# ---------------------------------------------------------------------------
# RFCOMM helpers (stateless)
# ---------------------------------------------------------------------------

def _rfcomm_encode(msg_id: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HH", len(payload), msg_id) + payload


def _rfcomm_recv_packet(sock: socket.socket, timeout: float = 5.0):
    """
    Read one RFCOMM packet from *sock*.
    Returns (msg_id, payload) or (None, None) on error/timeout.
    """
    sock.settimeout(timeout)
    try:
        header = _recv_exact(sock, RFCOMM_HEADER_SIZE)
        if not header:
            return None, None
        payload_len, msg_id = struct.unpack(">HH", header)
        payload = _recv_exact(sock, payload_len) if payload_len else b""
        return msg_id, payload
    except (socket.timeout, OSError):
        return None, None


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (OSError, socket.timeout):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


# ---------------------------------------------------------------------------
# AA frame helpers (stateless)
# ---------------------------------------------------------------------------

def aa_frame_encode(channel_id: int, msg_id: int, body: bytes, flags: int = 0) -> bytes:
    """
    Encode a minimal AA frame.
    Header: channel_id (u8) | flags (u8) | msg_id (u16 BE) | body_len (u16 BE)
    """
    return struct.pack(">BBHH", channel_id, flags, msg_id, len(body)) + body


def aa_frame_decode(data: bytes) -> Optional[tuple]:
    """
    Decode bytes into (channel_id, flags, msg_id, body).
    Returns None if data is too short.
    """
    if len(data) < AA_FRAME_HEADER_SIZE:
        return None
    channel_id, flags, msg_id, body_len = struct.unpack_from(">BBHH", data, 0)
    body = data[AA_FRAME_HEADER_SIZE: AA_FRAME_HEADER_SIZE + body_len]
    return channel_id, flags, msg_id, body


# ---------------------------------------------------------------------------
# PhoneMock — RFCOMM handshake responder
# ---------------------------------------------------------------------------

@dataclass
class RfcommHandshakeResult:
    """What the phone recorded during the RFCOMM exchange."""
    completed: bool = False
    phone_ip_received: str = ""        # from WifiStartRequest
    ssid_received: str = ""            # from WifiInfoResponse
    key_received: str = ""             # from WifiInfoResponse
    bssid_received: str = ""           # from WifiInfoResponse
    error: str = ""


class PhoneMock:
    """
    Simulates the Android phone side of the AA RFCOMM handshake.

    Run in a background thread; the HU side runs RfcommHandshake concurrently.

    RFCOMM exchange order (what phone does):
      1. Receives WifiStartRequest  → extracts ip:port, optionally sends WifiStartResponse ack
      2. Sends    WifiInfoRequest   → asks HU for WiFi credentials
      3. Receives WifiInfoResponse  → extracts ssid/key/bssid
      4. Sends    WifiConnectionStatus → announces WiFi joined successfully

    Parameters:
        sock            : phone-side socket (already connected, e.g. from socketpair)
        send_start_ack  : if True, send WifiStartResponse ack before WifiInfoRequest
        wifi_join_delay : seconds to wait before sending WifiConnectionStatus
        on_error        : optional callback for unexpected errors
    """

    def __init__(
        self,
        sock: socket.socket,
        send_start_ack: bool = True,
        wifi_join_delay: float = 0.05,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self._sock = sock
        self._send_start_ack = send_start_ack
        self._wifi_join_delay = wifi_join_delay
        self._on_error = on_error or (lambda msg: None)
        self._result = RfcommHandshakeResult()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> "PhoneMock":
        """Start the handshake responder in a daemon thread."""
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="phone-mock-rfcomm",
        )
        self._thread.start()
        return self

    def wait_done(self, timeout: float = 5.0) -> bool:
        """Block until handshake complete or timeout. Returns True if completed."""
        return self._done.wait(timeout=timeout)

    @property
    def result(self) -> RfcommHandshakeResult:
        return self._result

    # Convenience shortcuts
    @property
    def completed(self) -> bool:
        return self._result.completed

    @property
    def phone_ip_received(self) -> str:
        return self._result.phone_ip_received

    @property
    def ssid_received(self) -> str:
        return self._result.ssid_received

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._do_handshake()
        except Exception as e:
            self._result.error = str(e)
            self._on_error(str(e))
        finally:
            self._done.set()

    def _do_handshake(self) -> None:
        # Step 1 — receive WifiStartRequest from HU
        msg_id, payload = _rfcomm_recv_packet(self._sock, timeout=5.0)
        if msg_id != MSG_WIFI_START_REQUEST:
            self._result.error = f"Expected WifiStartRequest (1), got {msg_id}"
            return

        # Parse ip:port from protobuf-like payload (best-effort, no hard proto dep)
        # We extract using the real proto if available, otherwise just record raw.
        self._result.phone_ip_received = _parse_wifi_start_request_ip(payload)

        # Optional ack
        if self._send_start_ack:
            ack_payload = _build_wifi_start_response(ip_address=self._result.phone_ip_received)
            self._sock.sendall(_rfcomm_encode(MSG_WIFI_START_RESPONSE, ack_payload))

        # Step 2 — send WifiInfoRequest
        self._sock.sendall(_rfcomm_encode(MSG_WIFI_INFO_REQUEST, b""))

        # Step 3 — receive WifiInfoResponse from HU
        msg_id, payload = _rfcomm_recv_packet(self._sock, timeout=5.0)
        if msg_id != MSG_WIFI_INFO_RESPONSE:
            self._result.error = f"Expected WifiInfoResponse (3), got {msg_id}"
            return

        ssid, key, bssid = _parse_wifi_info_response(payload)
        self._result.ssid_received  = ssid
        self._result.key_received   = key
        self._result.bssid_received = bssid

        # Step 4 — simulate WiFi join delay then send WifiConnectionStatus
        if self._wifi_join_delay > 0:
            time.sleep(self._wifi_join_delay)

        status_payload = _build_wifi_connect_status()
        self._sock.sendall(_rfcomm_encode(MSG_WIFI_CONNECT_STATUS, status_payload))
        self._result.completed = True


# ---------------------------------------------------------------------------
# TcpPhoneClient — AA TCP client (after RFCOMM handshake completed)
# ---------------------------------------------------------------------------

class TcpPhoneClient:
    """
    Simulates the phone TCP client for the AA session phase.

    Connects to the NemoHeadUnit tcp_server and exchanges raw AA frames.
    Used in E2E smoke tests that verify the oaa_control_channel handshake
    without full TLS (tests that stub the cryptor).

    Usage:
        client = TcpPhoneClient.connect(host="127.0.0.1", port=5288, timeout=5.0)
        frame  = client.recv_frame(timeout=2.0)    # (channel_id, flags, msg_id, body)
        client.send_frame(channel_id=0, msg_id=MSG_VERSION_RESPONSE, body=body_bytes)
        client.close()
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._lock = threading.Lock()

    @classmethod
    def connect(cls, host: str = "127.0.0.1", port: int = 5288, timeout: float = 5.0) -> "TcpPhoneClient":
        """Create a connected TcpPhoneClient. Raises OSError on failure."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.settimeout(None)   # switch to blocking after connect
        return cls(sock)

    def send_frame(self, channel_id: int | bytes, msg_id: int | None = None, body: bytes = b"", flags: int = 0) -> bool:
        """Encode and send one AA frame. Raw pre-encoded frame bytes are also accepted."""
        if isinstance(channel_id, (bytes, bytearray)) and msg_id is None:
            frame = bytes(channel_id)
        else:
            frame = aa_frame_encode(channel_id, msg_id, body, flags)
        with self._lock:
            try:
                self._sock.sendall(frame)
                return True
            except OSError:
                return False

    def recv_frame(self, timeout: float = 5.0) -> Optional[tuple]:
        """
        Read one AA frame.  Returns (channel_id, flags, msg_id, body) or None.
        """
        self._sock.settimeout(timeout)
        try:
            header = _recv_exact(self._sock, AA_FRAME_HEADER_SIZE)
            if not header:
                return None
            channel_id, flags, msg_id, body_len = struct.unpack(">BBHH", header)
            body = _recv_exact(self._sock, body_len) if body_len else b""
            return channel_id, flags, msg_id, body
        except (socket.timeout, OSError):
            return None
        finally:
            self._sock.settimeout(None)

    def recv_frames_until(
        self,
        predicate: Callable[[int, int, int, bytes], bool],
        timeout: float = 5.0,
        max_frames: int = 50,
    ) -> List[tuple]:
        """
        Collect frames until predicate(ch, flags, msg_id, body) returns True
        or max_frames / timeout are exceeded.  Returns collected frames list.
        """
        frames: List[tuple] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(frames) < max_frames:
            remaining = deadline - time.monotonic()
            frame = self.recv_frame(timeout=max(0.05, remaining))
            if frame is None:
                break
            frames.append(frame)
            decoded = _aa_frame_to_event(frame)
            try:
                matched = predicate(decoded)
            except TypeError:
                matched = predicate(*frame)
            if matched:
                break
        return frames

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Proto helpers (best-effort — graceful fallback if proto not available)
# ---------------------------------------------------------------------------

def _parse_wifi_start_request_ip(payload: bytes) -> str:
    """
    Extract ip_address string from WifiStartRequest protobuf bytes.
    Falls back to empty string if protobuf is unavailable.
    """
    try:
        from oaa.wifi.WifiStartRequestMessage_pb2 import WifiStartRequest
        msg = WifiStartRequest()
        msg.ParseFromString(payload)
        return msg.ip_address or ""
    except Exception:
        return ""


def _aa_frame_to_event(frame: tuple) -> dict:
    channel_id, flags, msg_id, body = frame
    names = {
        0x0001: "version_request",
        0x0002: "version_response",
        0x0003: "auth_request",
        0x0004: "auth_complete",
        0x0005: "service_discovery_request",
        0x0006: "service_discovery_response",
        0x0007: "channel_open_request",
        0x0008: "channel_open_response",
        0x000B: "ping",
        0x000C: "pong",
        0x0100: "shutdown_request",
        0x0101: "shutdown_response",
    }
    return {
        "channel_id": channel_id,
        "flags": flags,
        "message_id": msg_id,
        "msg_id": msg_id,
        "msg_type": names.get(msg_id, f"unknown_{msg_id:04x}"),
        "body": body,
    }


def _parse_wifi_info_response(payload: bytes) -> tuple:
    """
    Extract (ssid, key, bssid) from WifiSecurityResponse protobuf bytes.
    Falls back to ("", "", "") if protobuf unavailable.
    """
    try:
        from oaa.wifi.WifiSecurityResponseMessage_pb2 import WifiSecurityResponse
        msg = WifiSecurityResponse()
        msg.ParseFromString(payload)
        return msg.ssid, msg.key, msg.bssid
    except Exception:
        return "", "", ""


def _build_wifi_start_response(ip_address: str = "") -> bytes:
    """
    Build WifiStartResponse ack protobuf bytes.
    Returns empty bytes if proto unavailable (ack is optional in the protocol).
    """
    try:
        from oaa.wifi.WifiStartResponseMessage_pb2 import WifiStartResponse
        msg = WifiStartResponse(ip_address=ip_address, port=0, status=0)
        return msg.SerializeToString()
    except Exception:
        return b""


def _build_wifi_connect_status() -> bytes:
    """
    Build WifiConnectionStatus bytes indicating success (state=1).
    Returns minimal bytes if proto unavailable.
    """
    try:
        from oaa.wifi.WifiConnectStatusMessage_pb2 import WifiConnectStatus
        msg = WifiConnectStatus(state=1, status_text="connected")
        return msg.SerializeToString()
    except Exception:
        # Minimal valid protobuf: field 1 (state), varint 1
        return b"\x08\x01"
