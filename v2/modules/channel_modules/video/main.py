"""
NemoHeadUnit-Wireless v2 — channel_modules/video

Module contract:
  Name        : video  (overridden by --module-name)
  Priority    : 1
  Channel ID  : supplied via --channel-id CLI arg (parsed by BaseChannelModule)
  SDR bytes   : supplied via --sdr-bytes-hex CLI arg, parsed by base into
                self.channel_config (used for future codec negotiation).
  Subscribes  : system.readytostart
                system.start
                system.stop
                oaa.channel.open         {channel_id, av_type?, ...}  ← from BaseChannelModule
                oaa.channel.close        {channel_id}                 ← from BaseChannelModule
                oaa.frame.<channel_id>   raw bytes                    ← from BaseChannelModule
                aa.session.active        {}
                aa.session.shutdown      {}
  Publishes   : system.module_ready      {name, priority}
                system.ready             {name, priority}
                aa.frame.send            {channel_id, flags, payload_hex}  ← MediaAck
                video.frame              {channel_id, session_id, ts_us, data_b64}
                video.state              {state}  IDLE | SETUP | OPEN | PLAYING | STOPPED

Flow:
  1. BaseChannelModule parses CLI and populates self.CHANNEL_ID and
     self.channel_config from --channel-id / --sdr-bytes-hex.
  2. system.ready is published lazily by base once _init_done, config_loaded
     and channel_config is not None.
  3. On oaa.channel.open (channel_id matches): record channel open.
  4. On oaa.frame.<channel_id>: decode AA media frame, dispatch by message_id:
       - AVChannelSetupRequest   → reply AVChannelSetupResponse, video.state=SETUP
       - AVChannelOpenRequest    → reply AVChannelOpenResponse,  video.state=OPEN
       - MediaWithTimestamp      → send MediaAck, publish video.frame (for video_ui)
       - AVChannelStopIndication → video.state=STOPPED
  5. On aa.session.shutdown: reset state, video.state=IDLE.

ACK strategy:
  MediaAck is sent immediately after each MediaWithTimestamp frame is received.
  video_ui is fire-and-forget: the ACK does NOT depend on video_ui processing.
"""

from __future__ import annotations

import base64
import struct
import sys
from pathlib import Path

_HERE           = Path(__file__).parent          # v2/modules/channel_modules/video/
_CHANNEL_MODS   = _HERE.parent                   # v2/modules/channel_modules/
_MODULES        = _CHANNEL_MODS.parent           # v2/modules/
_V2             = _MODULES.parent                # v2/

for _p in (_V2, _MODULES, _CHANNEL_MODS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# AA media frame constants
# ---------------------------------------------------------------------------

_FLAG_FIRST     = 0x01
_FLAG_LAST      = 0x02
_FLAG_ENCRYPTED = 0x08
_FLAG_FULL      = _FLAG_FIRST | _FLAG_LAST | _FLAG_ENCRYPTED  # 0x0B

_MSG_AV_CHANNEL_SETUP_REQUEST   = 0x8000
_MSG_AV_CHANNEL_SETUP_RESPONSE  = 0x8001
_MSG_AV_CHANNEL_OPEN_REQUEST    = 0x8003
_MSG_AV_CHANNEL_OPEN_RESPONSE   = 0x8005
_MSG_AV_CHANNEL_STOP_INDICATION = 0x8004
_MSG_MEDIA_WITH_TIMESTAMP       = 0x0001
_MSG_MEDIA_ACK                  = 0x0002


# ---------------------------------------------------------------------------
# VideoModule
# ---------------------------------------------------------------------------

class VideoModule(BaseChannelModule):
    """
    OAA Video channel module.

    Handles AVChannelSetup / Open handshake and MediaWithTimestamp frames.
    Publishes decoded H.264 NAL data on video.frame for video_ui.

    channel_id and SDR bytes are provided at spawn time via CLI by
    channel_manager and parsed by BaseChannelModule into self.CHANNEL_ID
    and self.channel_config.  system.ready is hard-blocked by base if
    channel_config is None.
    """

    MODULE_NAME = "video"   # overridden by --module-name CLI
    CHANNEL_ID  = -1         # overridden by --channel-id CLI
    PRIORITY    = 1

    def __init__(self) -> None:
        super().__init__()
        self._session_id: int = 0
        self._state: str      = "IDLE"  # IDLE | SETUP | OPEN | PLAYING | STOPPED

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Log resolved channel info; system.ready is handled by base."""
        self.log.info(
            "VideoModule _init: channel_id=%d channel_config=%s",
            self.CHANNEL_ID,
            self.channel_config,
        )

    def _cleanup(self) -> None:
        self._set_state("IDLE")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_active(self, topic: str, payload: dict) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("AA session active — video ready")

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("AA session shutdown — video reset")

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self.log.info("Channel %d open (descriptor: %s)", channel_id, descriptor)
        self._set_state("IDLE")

    def on_channel_close(self, channel_id: int) -> None:
        self.log.info("Channel %d closed", channel_id)
        self._set_state("IDLE")

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming raw frame bytes by AA message_id."""
        result = self._decode_media_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload — dropping")
            return

        message_id, body = result

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(body)
        elif message_id == _MSG_AV_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(body)
        elif message_id == _MSG_MEDIA_WITH_TIMESTAMP:
            self._handle_media_with_timestamp(body)
        elif message_id == _MSG_AV_CHANNEL_STOP_INDICATION:
            self.log.info("AVChannelStopIndication received")
            self._set_state("STOPPED")
        else:
            self.log.debug("Unhandled video msg_id=0x%04x len=%d", message_id, len(body))

    # ------------------------------------------------------------------
    # AA message handlers
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        """Reply AVChannelSetupResponse (status=OK, max_unacked=1)."""
        proto_body = b"\x08\x00\x10\x01"
        frame = self._encode_media_frame(self.CHANNEL_ID, _MSG_AV_CHANNEL_SETUP_RESPONSE, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self._set_state("SETUP")
        self.log.info("AVChannelSetupRequest → AVChannelSetupResponse sent (max_unacked=1)")

    def _handle_open_request(self, body: bytes) -> None:
        """Reply AVChannelOpenResponse (status=OK)."""
        proto_body = b"\x08\x00"
        frame = self._encode_media_frame(self.CHANNEL_ID, _MSG_AV_CHANNEL_OPEN_RESPONSE, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self._set_state("OPEN")
        self.log.info("AVChannelOpenRequest → AVChannelOpenResponse sent")

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        """Parse MediaWithTimestamp, send MediaAck, publish video.frame."""
        ts_us = 0
        data  = b""
        pos   = 0

        while pos < len(body):
            tag_byte     = body[pos]; pos += 1
            field_number = tag_byte >> 3
            wire_type    = tag_byte & 0x07

            if field_number == 1 and wire_type == 1:   # timestamp fixed64
                if pos + 8 > len(body):
                    break
                ts_us = struct.unpack_from("<Q", body, pos)[0]
                pos += 8
            elif field_number == 2 and wire_type == 2:  # H.264 data bytes
                length, pos = _read_varint(body, pos)
                if length is None:
                    break
                data = body[pos: pos + length]
                pos += length
            else:
                if wire_type == 0:
                    _, pos = _read_varint(body, pos)
                elif wire_type == 1:
                    pos += 8
                elif wire_type == 2:
                    length, pos = _read_varint(body, pos)
                    if length:
                        pos += length
                elif wire_type == 5:
                    pos += 4
                else:
                    break

        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")

        # ACK immediately — do NOT wait for video_ui
        self._send_media_ack()

        if data:
            self.bus.publish("video.frame", {
                "channel_id": self.CHANNEL_ID,
                "session_id": self._session_id,
                "ts_us":      ts_us,
                "data_b64":   base64.b64encode(data).decode("ascii"),
            })

    def _send_media_ack(self) -> None:
        proto_body = (_encode_varint_field(1, self._session_id)
                      + _encode_varint_field(2, 1))
        frame = self._encode_media_frame(self.CHANNEL_ID, _MSG_MEDIA_ACK, proto_body)
        self.bus.publish("aa.frame.send", frame)

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        self._state = new_state
        self.bus.publish("video.state", {"state": new_state})
        self.log.info("video.state → %s", new_state)

    # ------------------------------------------------------------------
    # Frame encode / decode helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_media_frame(channel_id: int, message_id: int, proto_body: bytes) -> dict:
        payload = struct.pack(">H", message_id) + proto_body
        return {
            "channel_id":  channel_id,
            "flags":       _FLAG_FULL,
            "payload_hex": payload.hex(),
        }

    @staticmethod
    def _decode_media_frame(data: bytes) -> tuple[int, bytes] | None:
        """Decode raw frame bytes → (message_id, body). Returns None on error."""
        if len(data) < 2:
            return None
        message_id = struct.unpack_from(">H", data, 0)[0]
        return message_id, data[2:]

    # ------------------------------------------------------------------
    # run() override: add extra subscriptions before calling super
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("aa.session.active",   self.on_aa_session_active)
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        super().run()


# ---------------------------------------------------------------------------
# Minimal protobuf varint helpers (no proto dependency)
# ---------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int | None, int]:
    result = 0
    shift  = 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            return None, pos
    return None, pos


def _encode_varint(value: int) -> bytes:
    out = []
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _encode_varint_field(field_number: int, value: int) -> bytes:
    tag = (field_number << 3) | 0x00
    return _encode_varint(tag) + _encode_varint(value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    VideoModule().run()
