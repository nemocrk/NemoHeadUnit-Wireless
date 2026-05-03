"""
handshake.py — AA control-channel (ch 0) handshake state machine.

Handshake sequence (HU speaks first):

  HU → Phone : VERSION_REQUEST          (0x0001)  ← sent immediately on tcp.session.connected
  Phone → HU : VERSION_RESPONSE         (0x0002)
  HU → Phone : SSL_HANDSHAKE            (0x0003)  [TLS ServerHello+Cert+Done via AACryptor]
  Phone → HU : SSL_HANDSHAKE            (0x0003)  [TLS rounds until complete]
  HU → Phone : AUTH_COMPLETE            (0x0006)
  Phone → HU : SERVICE_DISCOVERY_REQ   (0x0005)
  HU → Phone : SERVICE_DISCOVERY_RES   (0x0007)
  Phone → HU : CHANNEL_OPEN_REQ         (0x0008)  [one per channel]
  HU → Phone : CHANNEL_OPEN_RES         (0x0009)  [STATUS_OK]
  Phone → HU : PING_REQUEST             (0x000B)
  HU → Phone : PING_RESPONSE            (0x000C)
  → Session ACTIVE

TLS note:
  AA uses TLS 1.2 in-band: SSL bytes are exchanged as AA frame payloads
  on channel 0, msgId 0x0003.  AACryptor implements the memory-BIO pattern
  of openauto-prodigy Cryptor.cpp.  Post-handshake frames with
  encryptionType=Encrypted are decrypted/encrypted via AACryptor.
"""

from __future__ import annotations

import logging
import struct
import time
from enum import IntEnum, auto
from typing import Callable

from shared.proto_utils import decode_proto, encode_proto

from protos.oaa.control.ServiceDiscoveryRequestMessage_pb2 import ServiceDiscoveryRequest
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from protos.oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from protos.oaa.control.PingResponseMessage_pb2 import PingResponse

from oaa_control_channel.frame_codec import encode_control_frame, decode_control_frame
from oaa_control_channel.service_discovery import build_service_discovery_response
from oaa_control_channel.aa_cryptor import AACryptor

log = logging.getLogger("oaa_control_channel.handshake")

# ---------------------------------------------------------------------------
# Control message IDs
# ---------------------------------------------------------------------------

MSG_VERSION_REQUEST        = 0x0001
MSG_VERSION_RESPONSE       = 0x0002
MSG_SSL_HANDSHAKE          = 0x0003
MSG_AUTH_COMPLETE          = 0x0006
MSG_SERVICE_DISCOVERY_REQ  = 0x0005
MSG_SERVICE_DISCOVERY_RES  = 0x0007
MSG_CHANNEL_OPEN_REQ       = 0x0008
MSG_CHANNEL_OPEN_RES       = 0x0009
MSG_PING_REQUEST           = 0x000B
MSG_PING_RESPONSE          = 0x000C
MSG_SHUTDOWN_REQUEST       = 0x000D
MSG_SHUTDOWN_RESPONSE      = 0x000E
MSG_BYEBYE_RESPONSE        = 0x000F

AA_VERSION_MAJOR = 1
AA_VERSION_MINOR = 7


class HandshakeState(IntEnum):
    IDLE             = auto()
    VERSION_SENT     = auto()
    TLS_IN_PROGRESS  = auto()
    AUTH_OK          = auto()
    CHANNELS_OPENING = auto()
    ACTIVE           = auto()
    SHUTDOWN         = auto()


class ControlChannelHandshake:
    """
    Drives the AA control channel (ch 0) handshake.

    Args:
        send_fn        : callable(message_id: int, proto_body: bytes, encrypted: bool)
        bt_mac         : local BT MAC address (for ServiceDiscovery)
        wifi_bssid     : local WiFi BSSID
        on_active_cb   : called when session becomes ACTIVE
        on_shutdown_cb : called on SHUTDOWN_REQUEST
    """

    def __init__(
        self,
        send_fn: Callable[[int, bytes, bool], None],
        bt_mac: str = "00:00:00:00:00:00",
        wifi_bssid: str = "",
        on_active_cb: Callable[[], None] | None = None,
        on_shutdown_cb: Callable[[], None] | None = None,
    ):
        self._send         = send_fn
        self._bt_mac       = bt_mac
        self._wifi_bssid   = wifi_bssid
        self._on_active    = on_active_cb
        self._on_shutdown  = on_shutdown_cb
        self._state        = HandshakeState.IDLE
        self._open_channels: set[int] = set()
        self._cryptor      = AACryptor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_version_request(self) -> None:
        """
        Send VERSION_REQUEST (msgId 0x0001) to the phone.
        Must be called immediately after TCP connect — the phone waits in
        silence until the HU speaks first.
        Also initialises AACryptor so it is ready when 0x0003 arrives.
        """
        body = struct.pack(">HH", AA_VERSION_MAJOR, AA_VERSION_MINOR)
        self._send(MSG_VERSION_REQUEST, body, encrypted=False)
        self._cryptor.init()
        self._state = HandshakeState.VERSION_SENT
        log.info(
            "VERSION_REQUEST sent (v%d.%d) — AACryptor initialised, waiting for VERSION_RESPONSE",
            AA_VERSION_MAJOR, AA_VERSION_MINOR,
        )

    def on_message(self, channel_id: int, flags: int, payload: bytes) -> None:
        """Feed a raw payload from FrameRelay into the state machine."""
        encrypted = bool(flags & 0x08)
        if encrypted and self._cryptor.is_active():
            try:
                payload = self._cryptor.decrypt(payload)
            except Exception as e:
                log.error("decrypt failed: %s", e)
                return

        frame = decode_control_frame(channel_id, flags, payload)
        if frame is None:
            log.warning("on_message: malformed frame (payload too short)")
            return

        msg_id = frame.message_id
        body   = frame.body

        log.debug("CH0 ← msg_id=0x%04x state=%s len=%d", msg_id, self._state.name, len(body))

        handler = {
            MSG_VERSION_RESPONSE:      self._on_version_response,
            MSG_SSL_HANDSHAKE:         self._on_ssl_handshake,
            MSG_AUTH_COMPLETE:         self._on_auth_complete,
            MSG_SERVICE_DISCOVERY_REQ: self._on_service_discovery_request,
            MSG_CHANNEL_OPEN_REQ:      self._on_channel_open_request,
            MSG_PING_REQUEST:          self._on_ping_request,
            MSG_SHUTDOWN_REQUEST:      self._on_shutdown_request,
        }.get(msg_id)

        if handler:
            handler(body, encrypted)
        else:
            log.debug("CH0: unhandled msg_id=0x%04x — ignoring", msg_id)

    @property
    def state(self) -> HandshakeState:
        return self._state

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_version_response(self, body: bytes, encrypted: bool) -> None:
        if len(body) >= 4:
            p_major, p_minor = struct.unpack_from(">HH", body, 0)
            log.info("VERSION_RESPONSE: phone=%d.%d — starting TLS handshake",
                     p_major, p_minor)
        else:
            log.info("VERSION_RESPONSE received (no version body) — starting TLS handshake")
        # AACryptor already init'd in send_version_request; drive the first round
        outgoing = self._cryptor.drive_handshake()
        if outgoing:
            self._send(MSG_SSL_HANDSHAKE, outgoing, encrypted=False)
            log.debug("SSL_HANDSHAKE initial blob sent (%d bytes)", len(outgoing))
        self._state = HandshakeState.TLS_IN_PROGRESS

    def _on_ssl_handshake(self, body: bytes, encrypted: bool) -> None:
        """
        TLS handshake blob from phone — feed into AACryptor and send response.
        Mirrors Messenger::handleHandshakeData() + Messenger::driveHandshake().
        """
        log.debug("SSL_HANDSHAKE blob received (%d bytes)", len(body))

        self._cryptor.write_handshake_input(body)
        outgoing = self._cryptor.drive_handshake()

        if outgoing:
            log.debug("SSL_HANDSHAKE response (%d bytes) — sending", len(outgoing))
            self._send(MSG_SSL_HANDSHAKE, outgoing, encrypted=False)

        if self._cryptor.is_active():
            log.info("TLS handshake complete via AACryptor")
            self._state = HandshakeState.AUTH_OK

    def _on_auth_complete(self, body: bytes, encrypted: bool) -> None:
        log.info("AUTH_COMPLETE — TLS session established")
        self._state = HandshakeState.AUTH_OK

    def _on_service_discovery_request(self, body: bytes, encrypted: bool) -> None:
        req = decode_proto(ServiceDiscoveryRequest, body)
        if req is not None:
            log.info("SERVICE_DISCOVERY_REQUEST from '%s'", getattr(req, 'phone_name', '?'))

        sdr_bytes = build_service_discovery_response(
            bt_mac=self._bt_mac,
            wifi_bssid=self._wifi_bssid,
        )

        self._send(MSG_SERVICE_DISCOVERY_RES, sdr_bytes, encrypted=encrypted)
        self._state = HandshakeState.CHANNELS_OPENING
        log.info("SERVICE_DISCOVERY_RESPONSE sent (%d bytes)", len(sdr_bytes))

    def _on_channel_open_request(self, body: bytes, encrypted: bool) -> None:
        req = decode_proto(ChannelOpenRequest, body)
        ch_id = getattr(req, 'channel_id', -1) if req else -1

        self._open_channels.add(ch_id)
        log.info("CHANNEL_OPEN_REQUEST ch=%d — sending OK", ch_id)

        resp = ChannelOpenResponse()
        resp.status = 0  # STATUS_OK
        self._send(MSG_CHANNEL_OPEN_RES, encode_proto(resp), encrypted=encrypted)

    def _on_ping_request(self, body: bytes, encrypted: bool) -> None:
        req = decode_proto(PingRequest, body)
        timestamp = getattr(req, 'timestamp', int(time.time() * 1_000_000)) if req else 0

        resp = PingResponse()
        resp.timestamp = timestamp
        self._send(MSG_PING_RESPONSE, encode_proto(resp), encrypted=encrypted)
        log.debug("PING ts=%d → PONG", timestamp)

        if self._state != HandshakeState.ACTIVE:
            self._state = HandshakeState.ACTIVE
            log.info("Session ACTIVE — all channels open: %s", sorted(self._open_channels))
            if self._on_active:
                self._on_active()

    def _on_shutdown_request(self, body: bytes, encrypted: bool) -> None:
        log.info("SHUTDOWN_REQUEST received")
        self._state = HandshakeState.SHUTDOWN
        self._send(MSG_SHUTDOWN_RESPONSE, b"", encrypted=encrypted)
        if self._on_shutdown:
            self._on_shutdown()
