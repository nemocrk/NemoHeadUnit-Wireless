"""
handshake.py — Android Auto 5-stage RFCOMM wireless handshake.

Stages:
  1. Recv WifiStartRequest  (msg_id=1)  — phone sends its TCP IP + port
  2. Send WifiStartResponse (msg_id=7)  — head unit acks
  3. Recv WifiInfoRequest   (msg_id=2)  — phone requests WiFi credentials
  4. Send WifiInfoResponse  (msg_id=3)  — head unit sends SSID/key/BSSID
  5. Recv WifiConnectStatus (msg_id=6)  — phone confirms WiFi join

Credentials come from the APConfig dict published by hostapd_helper
(hostapd.ready payload).

No ZMQ dependency — caller (main.py) provides the socket and credentials.
"""

import sys
import logging
import socket
from pathlib import Path
from typing import Callable, Optional

# Ensure repo root (NemoHeadUnit-Wireless/) is on sys.path
# so that "v2.protos.*" imports resolve correctly.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # .../NemoHeadUnit-Wireless
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rfcomm_handshake.packet import (
    MSG_WIFI_START_REQUEST,
    MSG_WIFI_START_RESPONSE,
    MSG_WIFI_INFO_REQUEST,
    MSG_WIFI_INFO_RESPONSE,
    MSG_WIFI_CONNECT_STATUS,
    WPA2_SECURITY_MODE,
    AP_TYPE_DYNAMIC,
    recv_packet,
    send_packet,
)
from v2.protos.oaa.wifi.WifiInfoResponseMessage_pb2 import WifiInfoResponse

log = logging.getLogger("rfcomm_handshake.handshake")


class HandshakeResult:
    """Result returned by RfcommHandshake.run()."""

    def __init__(self, success: bool, phone_ip: str = "", error: str = ""):
        self.success  = success
        self.phone_ip = phone_ip
        self.error    = error

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        if self.success:
            return f"HandshakeResult(ok, phone_ip={self.phone_ip})"
        return f"HandshakeResult(FAILED, error={self.error})"


class RfcommHandshake:
    """
    Executes the 5-stage Android Auto wireless RFCOMM handshake.

    Usage:
        creds = {
            "ssid": "AndroidAutoAP",
            "key": "secret123",
            "bssid": "DC:A6:32:E7:5A:FE",
            "security_mode": WPA2_SECURITY_MODE,
            "ap_type": AP_TYPE_DYNAMIC,
        }
        hs = RfcommHandshake(sock, creds, on_stage_cb=log_stage)
        result = hs.run()
        if result:
            print(result.phone_ip)
    """

    def __init__(
        self,
        sock: socket.socket,
        credentials: dict,
        on_stage_cb: Optional[Callable[[str], None]] = None,
    ):
        self._sock = sock
        self._creds = credentials
        self._on_stage = on_stage_cb or (lambda s: None)
        self._phone_ip: str = ""

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> HandshakeResult:
        """Execute the full 5-stage handshake. Blocking."""
        try:
            self._sock.settimeout(15.0)

            if not self._stage1_recv_start_request():
                return HandshakeResult(False, error="Stage 1 failed: WifiStartRequest")

            if not self._stage2_send_start_response():
                return HandshakeResult(False, error="Stage 2 failed: WifiStartResponse")

            if not self._stage3_recv_info_request():
                return HandshakeResult(False, error="Stage 3 failed: WifiInfoRequest")

            if not self._stage4_send_info_response():
                return HandshakeResult(False, error="Stage 4 failed: WifiInfoResponse")

            if not self._stage5_recv_connect_status():
                return HandshakeResult(False, error="Stage 5 failed: WifiConnectStatus")

            log.info(f"Handshake completed. Phone IP: {self._phone_ip}")
            return HandshakeResult(True, phone_ip=self._phone_ip)

        except Exception as e:
            log.error(f"Handshake exception: {e}")
            return HandshakeResult(False, error=str(e))

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    def _stage1_recv_start_request(self) -> bool:
        self._on_stage("WifiStartRequest")
        pkt = recv_packet(self._sock)
        if not pkt or pkt.msg_id != MSG_WIFI_START_REQUEST:
            log.error(f"Stage 1: expected msg_id={MSG_WIFI_START_REQUEST}, got {pkt}")
            return False
        self._phone_ip = self._extract_ip(pkt.payload)
        log.info(f"Stage 1 OK: phone_ip={self._phone_ip}")
        return True

    def _stage2_send_start_response(self) -> bool:
        self._on_stage("WifiStartResponse")
        import struct
        payload = struct.pack(">H", 0)
        ok = send_packet(self._sock, MSG_WIFI_START_RESPONSE, payload)
        if ok:
            log.info("Stage 2 OK: WifiStartResponse sent")
        return ok

    def _stage3_recv_info_request(self) -> bool:
        self._on_stage("WifiInfoRequest")
        pkt = recv_packet(self._sock)
        if not pkt or pkt.msg_id != MSG_WIFI_INFO_REQUEST:
            log.error(f"Stage 3: expected msg_id={MSG_WIFI_INFO_REQUEST}, got {pkt}")
            return False
        log.info("Stage 3 OK: WifiInfoRequest received")
        return True

    def _stage4_send_info_response(self) -> bool:
        self._on_stage("WifiInfoResponse")
        payload = self._encode_wifi_info()
        ok = send_packet(self._sock, MSG_WIFI_INFO_RESPONSE, payload)
        if ok:
            log.info(f"Stage 4 OK: WifiInfoResponse sent (ssid={self._creds.get('ssid')}")
        return ok

    def _stage5_recv_connect_status(self) -> bool:
        self._on_stage("WifiConnectStatus")
        pkt = recv_packet(self._sock)
        if not pkt or pkt.msg_id != MSG_WIFI_CONNECT_STATUS:
            log.error(f"Stage 5: expected msg_id={MSG_WIFI_CONNECT_STATUS}, got {pkt}")
            return False
        log.info("Stage 5 OK: phone joined WiFi AP")
        return True

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def _encode_wifi_info(self) -> bytes:
        """Encode WifiInfoResponse using the compiled protobuf message."""
        msg = WifiInfoResponse(
            ssid          = self._creds.get("ssid", ""),
            bssid         = self._creds.get("bssid", ""),
            passphrase    = self._creds.get("key", ""),
            security_mode = self._creds.get("security_mode", WPA2_SECURITY_MODE),
            ap_type       = self._creds.get("ap_type", AP_TYPE_DYNAMIC),
        )
        return msg.SerializeToString()

    @staticmethod
    def _extract_ip(payload: bytes) -> str:
        """Best-effort: scan for a printable IP-like string in the protobuf payload."""
        try:
            text = payload.decode("utf-8", errors="ignore")
            import re
            match = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", text)
            if match:
                return match.group(1)
        except Exception:
            pass
        return ""
