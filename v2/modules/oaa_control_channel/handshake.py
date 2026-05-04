"""
handshake.py — AA control-channel (ch 0) handshake state machine.

Handshake sequence (HU speaks first):

  HU → Phone : VERSION_REQUEST          (0x0001)  ← sent immediately on tcp.session.connected
  Phone → HU : VERSION_RESPONSE         (0x0002)
  HU → Bus  : aa.handshake.start_tls    {}        ← tcp_server inits AACryptor + drives ClientHello
  Bus → HU  : tcp.server.tls_handshake  {outgoing_hex}
  HU → Phone : SSL_HANDSHAKE            (0x0003)  [TLS ClientHello]
  Phone → HU : SSL_HANDSHAKE            (0x0003)  [TLS rounds]
  HU → Bus  : aa.handshake.feed_input   {payload_hex}  ← tcp_server feeds cryptor + drives
  Bus → HU  : tcp.server.tls_handshake  {outgoing_hex} | tcp.server.tls_handshake_completed {}
  HU → Phone : SSL_HANDSHAKE            (0x0003)  [TLS response round]
  ... repeat until tcp.server.tls_handshake_completed ...
  HU → Phone : AUTH_COMPLETE            (0x0006)  ← sent on tls_handshake_completed
  Phone → HU : SERVICE_DISCOVERY_REQ   (0x0005)
  HU → Phone : SERVICE_DISCOVERY_RES   (0x0007)
  Phone → HU : CHANNEL_OPEN_REQ         (0x0008)  [one per channel]
  HU → Phone : CHANNEL_OPEN_RES         (0x0009)  [STATUS_OK]
  Phone → HU : PING_REQUEST             (0x000B)
  HU → Phone : PING_RESPONSE            (0x000C)
  → Session ACTIVE

TLS note:
  AA uses TLS 1.2 in-band: SSL bytes are exchanged as AA frame payloads
  on channel 0, msgId 0x0003.  AACryptor is now owned by tcp_server.
  handshake.py is purely a protocol state machine — it delegates all
  TLS operations to tcp_server via bus messages.
  Post-handshake frames with encryptionType=Encrypted are decrypted by
  tcp_server before being published on aa.frame.ch<N>.
"""

from __future__ import annotations

import logging
from pathlib import Path
import struct
import sys
import time
from enum import IntEnum, auto
from typing import Callable


_REPO_ROOT  = Path(__file__).parent.parent.parent.parent
_PROTO_ROOT = _REPO_ROOT / "v2" / "protos"

for _p in (_REPO_ROOT, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


from shared.proto_utils import decode_proto, encode_proto

# Control proto imports
from v2.protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from v2.protos.oaa.control.AuthCompleteIndicationMessage_pb2 import AuthCompleteIndication
from v2.protos.oaa.control.ServiceDiscoveryRequestMessage_pb2 import ServiceDiscoveryRequest
from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from v2.protos.oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
from v2.protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from v2.protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from v2.protos.oaa.control.PingResponseMessage_pb2 import PingResponse

from oaa_control_channel.frame_codec import encode_control_frame, decode_control_frame
from oaa_control_channel.service_discovery import build_service_discovery_response

log = logging.getLogger("oaa_control_channel.handshake")

# ---------------------------------------------------------------------------
# Control message IDs (mirrors ControlMessageIds proto enum)
# ---------------------------------------------------------------------------

MSG_VERSION_REQUEST        = ControlMessage.Enum.VERSION_REQUEST
MSG_VERSION_RESPONSE       = ControlMessage.Enum.VERSION_RESPONSE
MSG_SSL_HANDSHAKE          = ControlMessage.Enum.SSL_HANDSHAKE
MSG_AUTH_COMPLETE          = ControlMessage.Enum.AUTH_COMPLETE
MSG_SERVICE_DISCOVERY_REQ  = ControlMessage.Enum.SERVICE_DISCOVERY_REQUEST
MSG_SERVICE_DISCOVERY_RES  = ControlMessage.Enum.SERVICE_DISCOVERY_RESPONSE
MSG_CHANNEL_OPEN_REQ       = ControlMessage.Enum.CHANNEL_OPEN_REQUEST
MSG_CHANNEL_OPEN_RES       = ControlMessage.Enum.CHANNEL_OPEN_RESPONSE
MSG_PING_REQUEST           = ControlMessage.Enum.PING_REQUEST
MSG_PING_RESPONSE          = ControlMessage.Enum.PING_RESPONSE
MSG_SHUTDOWN_REQUEST       = ControlMessage.Enum.SHUTDOWN_REQUEST
MSG_SHUTDOWN_RESPONSE      = ControlMessage.Enum.SHUTDOWN_RESPONSE
MSG_BYEBYE_RESPONSE        = ControlMessage.Enum.SHUTDOWN_RESPONSE

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

    TLS is fully delegated to tcp_server via bus messages:
      publish  aa.handshake.start_tls    {}               ← triggers AACryptor.init + ClientHello
      publish  aa.handshake.feed_input   {payload_hex}    ← feeds SSL round bytes to cryptor
      receive  on_tls_handshake_blob(outgoing)            ← forwards blob to phone as SSL_HANDSHAKE
      receive  on_tls_complete()                          ← sends AUTH_COMPLETE to phone

    Args:
        send_fn        : callable(message_id: int, proto_body: bytes, encrypted: bool)
        publish_fn     : callable(topic: str, payload: dict)  — bus.publish
        cfg            : config dict pre-loaded from config_manager (keys as in service_discovery.DEFAULTS)
        bt_mac         : local BT MAC address (for ServiceDiscovery)
        wifi_bssid     : local WiFi BSSID
        on_active_cb   : called when session becomes ACTIVE
        on_shutdown_cb : called on SHUTDOWN_REQUEST
    """

    def __init__(
        self,
        send_fn: Callable[[int, bytes, bool], None],
        publish_fn: Callable[[str, dict], None],
        cfg: dict | None = None,
        bt_mac: str = "00:00:00:00:00:00",
        wifi_bssid: str = "",
        on_active_cb: Callable[[], None] | None = None,
        on_shutdown_cb: Callable[[], None] | None = None,
    ):
        self._send         = send_fn
        self._publish      = publish_fn
        self._cfg          = cfg if cfg is not None else {}
        self._bt_mac       = bt_mac
        self._wifi_bssid   = wifi_bssid
        self._on_active    = on_active_cb
        self._on_shutdown  = on_shutdown_cb
        self._state        = HandshakeState.IDLE
        self._open_channels: set[int] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_version_request(self) -> None:
        """
        Send VERSION_REQUEST (msgId 0x0001) to the phone.
        Must be called immediately after TCP connect — the phone waits in
        silence until the HU speaks first.
        """
        body = struct.pack(">HH", AA_VERSION_MAJOR, AA_VERSION_MINOR)
        self._send(MSG_VERSION_REQUEST, body, encrypted=False)
        self._state = HandshakeState.VERSION_SENT
        log.info(
            "VERSION_REQUEST sent (v%d.%d) — waiting for VERSION_RESPONSE",
            AA_VERSION_MAJOR, AA_VERSION_MINOR,
        )

    def on_message(self, channel_id: int, flags: int, payload: bytes) -> None:
        """Feed a raw payload from FrameRelay into the state machine.

        Note: tcp_server decrypts encrypted frames before publishing,
        so payload is always plaintext here.
        """
        frame = decode_control_frame(channel_id, flags, payload)
        if frame is None:
            log.warning("on_message: malformed frame (payload too short)")
            return

        msg_id = frame.message_id
        body   = frame.body
        encrypted = bool(flags & 0x08)

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

    def on_tls_handshake_blob(self, outgoing_hex: str) -> None:
        """Called by oaa_control_channel/main.py on tcp.server.tls_handshake.

        Forwards TLS bytes from tcp_server's AACryptor to the phone
        as an SSL_HANDSHAKE frame (msgId 0x0003).
        """
        outgoing = bytes.fromhex(outgoing_hex)
        log.debug("TLS blob from tcp_server (%d bytes) — forwarding to phone", len(outgoing))
        self._send(MSG_SSL_HANDSHAKE, outgoing, encrypted=False)

    def on_tls_complete(self) -> None:
        """Called by oaa_control_channel/main.py on tcp.server.tls_handshake_completed.

        TLS is now active in tcp_server. Send AUTH_COMPLETE to the phone.
        """
        log.info("TLS handshake complete (tcp_server) — sending AUTH_COMPLETE")
        auth = AuthCompleteIndication()
        auth.status = 0  # STATUS_OK
        self._send(MSG_AUTH_COMPLETE, auth.SerializeToString(), encrypted=False)
        self._state = HandshakeState.AUTH_OK

    @property
    def state(self) -> HandshakeState:
        return self._state

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_version_response(self, body: bytes, encrypted: bool) -> None:
        if len(body) >= 4:
            p_major, p_minor = struct.unpack_from(">HH", body, 0)
            log.info("VERSION_RESPONSE: phone=%d.%d — requesting TLS init from tcp_server",
                     p_major, p_minor)
        else:
            log.info("VERSION_RESPONSE received — requesting TLS init from tcp_server")
        self._publish("aa.handshake.start_tls", {})
        self._state = HandshakeState.TLS_IN_PROGRESS

    def _on_ssl_handshake(self, body: bytes, encrypted: bool) -> None:
        """TLS handshake blob from phone — relay to tcp_server for processing."""
        log.info("SSL_HANDSHAKE blob from phone (%d bytes) — forwarding to tcp_server", len(body))
        self._publish("aa.handshake.feed_input", {"payload_hex": body.hex()})

    def _on_auth_complete(self, body: bytes, encrypted: bool) -> None:
        log.info("AUTH_COMPLETE from phone — TLS session established")
        self._state = HandshakeState.AUTH_OK

    def _on_service_discovery_request(self, body: bytes, encrypted: bool) -> None:
        req = decode_proto(ServiceDiscoveryRequest, body)
        if req is not None:
            log.info("SERVICE_DISCOVERY_REQUEST from '%s'", getattr(req, 'phone_name', '?'))

        sdr_bytes = build_service_discovery_response(
            cfg=self._cfg,
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
