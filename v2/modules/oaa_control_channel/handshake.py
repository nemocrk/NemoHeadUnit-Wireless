"""
handshake.py — AA control-channel (ch 0) handshake state machine.

Handshake sequence handled here:

  Phone → HU : VERSION_REQUEST       (0x0001)
  HU → Phone : VERSION_RESPONSE      (0x0002)
  Phone → HU : SSL_HANDSHAKE         (0x0003)  [TLS ClientHello pass-through]
  HU → Phone : SSL_HANDSHAKE         (0x0003)  [TLS ServerHello pass-through]
  Phone → HU : AUTH_COMPLETE         (0x0006)
  Phone → HU : SERVICE_DISCOVERY_REQ (0x0005)
  HU → Phone : SERVICE_DISCOVERY_RES (0x0007)
  Phone → HU : CHANNEL_OPEN_REQ      (0x0008)  [one per channel]
  HU → Phone : CHANNEL_OPEN_RES      (0x0009)  [STATUS_OK]
  Phone → HU : PING_REQUEST          (0x000B)
  HU → Phone : PING_RESPONSE         (0x000C)
  → Session ACTIVE

Note on TLS: AA wireless runs with TLS but the Python daemon delegates
crypto to the OpenSSL layer of the TCP stack.  The SSL_HANDSHAKE blobs
(0x0003) are forwarded verbatim; no Python-level crypto is required.

After the session becomes ACTIVE the module keeps the channel alive by
responding to PING_REQUEST messages.
"""

from __future__ import annotations

import logging
import struct
import time
from enum import IntEnum, auto
from typing import Callable

from shared.proto_utils import decode_proto, encode_proto

# Control proto imports
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessageIds
from protos.oaa.control.ServiceDiscoveryRequestMessage_pb2 import ServiceDiscoveryRequest
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from protos.oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from protos.oaa.control.PingResponseMessage_pb2 import PingResponse

from oaa_control_channel.frame_codec import encode_control_frame, decode_control_frame
from oaa_control_channel.service_discovery import build_service_discovery_response

log = logging.getLogger("oaa_control_channel.handshake")

# ---------------------------------------------------------------------------
# Control message IDs (mirrors ControlMessageIds proto enum)
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

# AA protocol version advertised by HU
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

    The caller feeds raw payloads via on_message() and sends responses
    by passing a *send_fn* callable.

    Args:
        send_fn : callable(message_id: int, proto_body: bytes, encrypted: bool)
                  Must publish the frame onto aa.frame.send.
        bt_mac        : local BT MAC address (for ServiceDiscovery)
        wifi_bssid    : local WiFi BSSID
        on_active_cb  : called when session becomes ACTIVE
        on_shutdown_cb: called on SHUTDOWN_REQUEST
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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def on_message(self, channel_id: int, flags: int, payload: bytes) -> None:
        """Feed a raw payload from FrameRelay into the state machine."""
        frame = decode_control_frame(channel_id, flags, payload)
        if frame is None:
            log.warning("on_message: malformed frame (payload too short)")
            return

        msg_id = frame.message_id
        body   = frame.body
        encrypted = bool(flags & 0x08)

        log.debug("CH0 ← msg_id=0x%04x state=%s len=%d", msg_id, self._state.name, len(body))

        handler = {
            MSG_VERSION_REQUEST:       self._on_version_request,
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

    def _on_version_request(self, body: bytes, encrypted: bool) -> None:
        """Phone sends VERSION_REQUEST; HU replies with its supported version."""
        # body: [major:u16_be][minor:u16_be] from phone
        if len(body) >= 4:
            p_major, p_minor = struct.unpack_from(">HH", body, 0)
            log.info("VERSION_REQUEST: phone=%d.%d, HU=%d.%d",
                     p_major, p_minor, AA_VERSION_MAJOR, AA_VERSION_MINOR)
        else:
            log.info("VERSION_REQUEST: (no version in body) HU=%d.%d",
                     AA_VERSION_MAJOR, AA_VERSION_MINOR)

        # VERSION_RESPONSE: [major:u16_be][minor:u16_be][status:u16_be=0x00 (OK)]
        response_body = struct.pack(">HHH", AA_VERSION_MAJOR, AA_VERSION_MINOR, 0)
        self._send(MSG_VERSION_RESPONSE, response_body, encrypted=False)
        self._state = HandshakeState.VERSION_SENT
        log.info("VERSION_RESPONSE sent")

    def _on_ssl_handshake(self, body: bytes, encrypted: bool) -> None:
        """TLS handshake blob — forward verbatim back to phone as server hello.

        In the Python daemon the TLS termination is handled by the OpenSSL
        layer underneath.  This handler is a no-op acknowledgement so the
        state machine progresses; the actual crypto bytes are already consumed
        by the TLS socket wrapper before reaching FrameRelay.
        """
        log.debug("SSL_HANDSHAKE blob received (%d bytes) — TLS handled by socket layer", len(body))
        self._state = HandshakeState.TLS_IN_PROGRESS

    def _on_auth_complete(self, body: bytes, encrypted: bool) -> None:
        """Phone signals TLS auth is done — session is now encrypted."""
        log.info("AUTH_COMPLETE — TLS session established")
        self._state = HandshakeState.AUTH_OK

    def _on_service_discovery_request(self, body: bytes, encrypted: bool) -> None:
        """Build and send the ServiceDiscoveryResponse with all channel descriptors."""
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
        """Phone opens a channel; HU responds with STATUS_OK (0)."""
        req = decode_proto(ChannelOpenRequest, body)
        ch_id = getattr(req, 'channel_id', -1) if req else -1

        self._open_channels.add(ch_id)
        log.info("CHANNEL_OPEN_REQUEST ch=%d — sending OK", ch_id)

        resp = ChannelOpenResponse()
        resp.status = 0  # STATUS_OK
        self._send(MSG_CHANNEL_OPEN_RES, encode_proto(resp), encrypted=encrypted)

    def _on_ping_request(self, body: bytes, encrypted: bool) -> None:
        """Echo PING_REQUEST back as PING_RESPONSE; mark session ACTIVE on first ping."""
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
        """Phone is disconnecting; send shutdown response and notify caller."""
        log.info("SHUTDOWN_REQUEST received")
        self._state = HandshakeState.SHUTDOWN

        # ShutdownResponse has no fields
        self._send(MSG_SHUTDOWN_RESPONSE, b"", encrypted=encrypted)

        if self._on_shutdown:
            self._on_shutdown()
