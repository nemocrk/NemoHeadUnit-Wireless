"""
handshake.py — Android Auto RFCOMM wireless handshake (event-driven).

Flow (aligned to openauto-prodigy BluetoothDiscoveryService.cpp):
  1. HU sends WifiStartRequest (msg_id=1)  — proactive, on connect
  2. Phone may send WifiStartResponse (msg_id=6)  — optional ack, logged
  3. Phone sends WifiInfoRequest (msg_id=2)  — triggers credential response
  4. HU sends WifiInfoResponse (msg_id=3)  — SSID/key/BSSID
  5. Phone sends WifiConnectionStatus (msg_id=7)  — WiFi joined, done

Steps 2–5 are handled by an event loop that dispatches on msg_id.
Order is not enforced — tolerates phones that skip the ack (step 2).

No ZMQ dependency — caller (main.py) provides the socket and credentials.
"""

import sys
from shared.logger import get_logger
import socket
from pathlib import Path
from typing import Callable, Optional

from rfcomm_handshake.packet import (
    Packet,
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
from protos.oaa.wifi.WifiSecurityResponseMessage_pb2 import WifiSecurityResponse
from protos.oaa.wifi.WifiStartRequestMessage_pb2  import WifiStartRequest
from protos.oaa.wifi.WifiStartResponseMessage_pb2 import WifiStartResponse
from protos.oaa.wifi.WifiConnectStatusMessage_pb2 import WifiConnectStatus

log = get_logger("rfcomm_handshake.handshake")

DEFAULT_TCP_PORT = 5288

# Maximum number of messages to process in the event loop before giving up
_MAX_MESSAGES = 20


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
    Executes the Android Auto wireless RFCOMM handshake.

    Sends WifiStartRequest immediately on run(), then enters an event loop
    that dispatches incoming messages by msg_id — same model as openauto-prodigy.

    Usage:
        creds = {
            "ssid": "AndroidAutoAP",
            "key": "secret123",
            "bssid": "DC:A6:32:E7:5A:FE",
            "gateway_ip": "192.168.50.1",
            "tcp_port": 5288,
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
        self._sock     = sock
        self._creds    = credentials
        self._on_stage = on_stage_cb or (lambda s: None)
        self._phone_ip = ""

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> HandshakeResult:
        """Send WifiStartRequest then enter event loop until WiFi joined."""
        try:
            self._sock.settimeout(15.0)

            # Step 1 — always proactive
            if not self._send_start_request():
                return HandshakeResult(False, error="Failed to send WifiStartRequest")

            # Event loop — react to whatever the phone sends
            info_response_sent = False

            for _ in range(_MAX_MESSAGES):
                pkt = recv_packet(self._sock)
                if pkt is None:
                    return HandshakeResult(False, error="Socket closed or recv error")

                log.debug(f"Event loop: received msg_id={pkt.msg_id}")

                if pkt.msg_id == MSG_WIFI_START_RESPONSE:
                    # Optional ack from phone
                    self._handle_start_response(pkt)

                elif pkt.msg_id == MSG_WIFI_INFO_REQUEST:
                    # Phone wants credentials
                    self._on_stage("WifiInfoRequest")
                    log.info("WifiInfoRequest received")
                    if not self._send_info_response():
                        return HandshakeResult(False, error="Failed to send WifiInfoResponse")
                    info_response_sent = True

                elif pkt.msg_id == MSG_WIFI_CONNECT_STATUS:
                    # Phone reports WiFi join result
                    self._on_stage("WifiConnectionStatus")
                    if not info_response_sent:
                        log.warning("WifiConnectionStatus received before WifiInfoResponse was sent")
                    self._handle_connect_status(pkt)
                    log.info(f"Handshake completed. Phone IP: {self._phone_ip}")
                    return HandshakeResult(True, phone_ip=self._phone_ip)

                else:
                    log.warning(f"Unknown msg_id={pkt.msg_id}, ignoring")

            return HandshakeResult(False, error=f"Event loop exhausted after {_MAX_MESSAGES} messages without completion")

        except Exception as e:
            log.error(f"Handshake exception: {e}")
            return HandshakeResult(False, error=str(e))

    # ------------------------------------------------------------------
    # Senders
    # ------------------------------------------------------------------

    def _send_start_request(self) -> bool:
        self._on_stage("WifiStartRequest")
        ip_address = self._creds.get("gateway_ip", "")
        port       = int(self._creds.get("tcp_port", DEFAULT_TCP_PORT))
        payload    = WifiStartRequest(
            ip_address=ip_address,
            port=port,
        ).SerializeToString()
        ok = send_packet(self._sock, MSG_WIFI_START_REQUEST, payload)
        if ok:
            log.info(f"WifiStartRequest sent (ip={ip_address}, port={port})")
        return ok

    def _send_info_response(self) -> bool:
        self._on_stage("WifiInfoResponse")
        ssid          = self._creds.get("ssid", "")
        bssid         = self._creds.get("bssid", "")
        passphrase    = self._creds.get("key", "")
        security_mode = self._creds.get("security_mode", WPA2_SECURITY_MODE)
        ap_type       = self._creds.get("ap_type", AP_TYPE_DYNAMIC)

        if not ssid:
            log.warning("WifiInfoResponse: ssid is EMPTY — phone will likely reject")
        if not bssid:
            log.warning("WifiInfoResponse: bssid is EMPTY — phone may not find AP")
        if not passphrase:
            log.warning("WifiInfoResponse: passphrase is EMPTY — phone will fail auth")

        log.debug(
            f"WifiInfoResponse: ssid={ssid!r} bssid={bssid!r} "
            f"passphrase={'*' * len(passphrase) if passphrase else '(empty)'} "
            f"security_mode={security_mode} ap_type={ap_type}"
        )

        payload = WifiSecurityResponse(
            ssid               = ssid,
            bssid              = bssid,
            key                = passphrase,
            security_mode      = security_mode,
            access_point_type  = ap_type,
        ).SerializeToString()

        ok = send_packet(self._sock, MSG_WIFI_INFO_RESPONSE, payload)
        if ok:
            log.info(f"WifiInfoResponse sent (ssid={ssid!r}, bssid={bssid!r})")
        return ok

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_start_response(self, pkt: Packet) -> None:
        self._on_stage("WifiStartResponse")
        response = WifiStartResponse()
        try:
            response.ParseFromString(pkt.payload)
            if response.ip_address:
                self._phone_ip = response.ip_address
            log.info(
                f"WifiStartResponse (ack): status={response.status} "
                f"ip={response.ip_address} port={response.port}"
            )
        except Exception as e:
            log.warning(f"WifiStartResponse: could not parse payload: {e}")
            log.info("WifiStartResponse (ack) received")

    def _handle_connect_status(self, pkt: Packet) -> None:
        status = WifiConnectStatus()
        try:
            status.ParseFromString(pkt.payload)
            log.info(
                f"WifiConnectionStatus: state={status.state} "
                f"status_text={status.status_text!r}"
            )
        except Exception as e:
            log.warning(f"WifiConnectionStatus: could not parse payload: {e}")
            log.info("WifiConnectionStatus received")
