"""
NemoHeadUnit-Wireless v2 — channel_modules/video

Module contract:
  Name        : video  (overridden by --module-name)
  Priority    : 1
  Channel ID  : supplied via --channel-id CLI arg (parsed by BaseChannelModule)
  SDR bytes   : supplied via --sdr-bytes-hex CLI arg, parsed by base into
                self.channel_config (codec negotiation).
  Subscribes  : channel_manager.module_readytostart
                channel_manager.module_start
                channel_manager.module_stop
                aa.channel.open         {channel_id, av_type?, ...}  ← from BaseChannelModule
                aa.channel.close        {channel_id}                 ← from BaseChannelModule
                aa.frame.ch<channel_id>   raw bytes                    ← from BaseChannelModule
                aa.session.active        {}
                aa.session.shutdown      {}
  Publishes   : channel_manager.module_ready             {name, priority}
                aa.frame.send            bytes  (via BaseChannelModule.send_frame)
                video.frame              {channel_id, ts_us, data_b64, codec}
                video.state              {state}  IDLE | SETUP | OPEN | PLAYING | STOPPED

Flow:
  1. BaseChannelModule parses CLI → self.CHANNEL_ID, self.channel_config.
  2. channel_manager.module_ready published lazily by base once ready.
  3. On aa.channel.open (channel_id matches): record channel open.
  4. On aa.frame.ch<channel_id>: decode AA media frame, dispatch by message_id:
       - AVChannelSetupRequest    → AVChannelSetupResponse + VideoFocusIndication(PROJECTED)
       - ChannelOpenRequest       → ChannelOpenResponse,   video.state=OPEN
       - AVChannelStartIndication → extract session_id,    video.state=PLAYING
       - AVChannelStopIndication  → reset session_id,      video.state=STOPPED
       - AV_MEDIA_INDICATION      → codec config: ACK + update self._codec
       - AV_MEDIA_WITH_TIMESTAMP  → ACK + publish video.frame (drop if session_id==0)
       - VIDEO_FOCUS_REQUEST      → VideoFocusIndication(PROJECTED)
  5. On aa.session.shutdown: reset state + session_id → IDLE.

ACK strategy:
  MediaAck is sent immediately after each media frame (with or without timestamp).
  video_ui is fire-and-forget: the ACK does NOT depend on video_ui processing.

session_id lifecycle:
  - 0 at construction and on channel close / session shutdown / stop indication.
  - Populated from AVChannelStartIndication.session on StartIndication.
  - Frames arriving before StartIndication (session_id==0) are dropped.

codec lifecycle:
  - self._codec_sdr : codec negotiated in SDR (from channel_config), set in _init().
  - self._codec      : active codec, initialised from _codec_sdr, updated on
                       AV_MEDIA_INDICATION (codec config message).
  - Both are propagated in video.frame payload.
"""

from __future__ import annotations

import base64
import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent
_CHANNEL_MODS = _HERE.parent
_MODULES      = _CHANNEL_MODS.parent
_V2           = _MODULES.parent
_PROTOS       = _V2 / "protos"

for _p in (_V2, _MODULES, _CHANNEL_MODS, _PROTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.config_schema import field_int                                                  # noqa: E402
from shared.proto_utils import (
    parse_media_with_timestamp,
    decode_aa_frame,
)                                                                                           # noqa: E402
from channel_modules.base_channel_module import BaseChannelModule                           # noqa: E402

# Proto — AV shared
from oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage                   # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage                  # noqa: E402
from oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType                          # noqa: E402
from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse       # noqa: E402
from oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus              # noqa: E402
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse        # noqa: E402
from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication   # noqa: E402
from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication           # noqa: E402

# Proto — video specific
from oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication        # noqa: E402
from oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode                       # noqa: E402


# ---------------------------------------------------------------------------
# AA message ID aliases
# ---------------------------------------------------------------------------

_MSG_AV_CHANNEL_SETUP_REQUEST                      = AVChannelMessage.SETUP_REQUEST
_MSG_AV_CHANNEL_SETUP_RESPONSE                     = AVChannelMessage.SETUP_RESPONSE
_MSG_CHANNEL_OPEN_REQUEST                          = ControlMessage.CHANNEL_OPEN_REQUEST
_MSG_CHANNEL_OPEN_RESPONSE                         = ControlMessage.CHANNEL_OPEN_RESPONSE
_MSG_AV_CHANNEL_START_INDICATION                   = AVChannelMessage.START_INDICATION
_MSG_AV_CHANNEL_STOP_INDICATION                    = AVChannelMessage.STOP_INDICATION
_MSG_AV_CHANNEL_AV_MEDIA_INDICATION                = AVChannelMessage.AV_MEDIA_INDICATION
_MSG_AV_CHANNEL_AV_MEDIA_WITH_TIMESTAMP_INDICATION = AVChannelMessage.AV_MEDIA_WITH_TIMESTAMP_INDICATION
_MSG_AV_CHANNEL_MEDIA_ACK                          = AVChannelMessage.AV_MEDIA_ACK_INDICATION
_MSG_VIDEO_FOCUS_REQUEST                           = AVChannelMessage.VIDEO_FOCUS_REQUEST
_MSG_VIDEO_FOCUS_INDICATION                        = AVChannelMessage.VIDEO_FOCUS_INDICATION
_MSG_VIDEO_FOCUS_NOTIFICATION                      = AVChannelMessage.VIDEO_FOCUS_NOTIFICATION

_CODEC_H264_BP = MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP
_CODEC_VP9     = MediaCodecType.MEDIA_CODEC_VIDEO_VP9
_CODEC_AV1     = MediaCodecType.MEDIA_CODEC_VIDEO_AV1
_CODEC_H265    = MediaCodecType.MEDIA_CODEC_VIDEO_H265


# ---------------------------------------------------------------------------
# VideoModule
# ---------------------------------------------------------------------------

class VideoModule(BaseChannelModule):
    """
    AA Video channel module.

    Handles the full AVChannel handshake (Setup → Open → Start → media frames)
    and publishes decoded NAL data on video.frame for video_ui.

    All outgoing AA frames are sent via self.send_frame(message_id, proto_body)
    which is provided by BaseChannelModule and always sets the encrypted flag
    consistently for post-handshake channel traffic.

    codec lifecycle:
      self._codec_sdr  — codec negotiated in SDR, read in _init().
      self._codec      — active codec, starts equal to _codec_sdr, updated
                         on AV_MEDIA_INDICATION (codec config message from phone).

    session_id lifecycle:
      0 until AVChannelStartIndication is received; frames arriving before
      StartIndication are dropped.
    """

    MODULE_NAME = "video"   # overridden by --module-name CLI
    CHANNEL_ID  = -1         # overridden by --channel-id CLI
    PRIORITY    = 1

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        return {
            "max_unacked": field_int(
                default=1,
                min=1,
                max=16,
            ),
        }

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()
        self._session_id:  int = 0
        self._state:       str = "IDLE"   # IDLE | SETUP | OPEN | PLAYING | STOPPED
        self._codec_sdr:   int = _CODEC_H264_BP   # from SDR, set in _init()
        self._codec:       int = _CODEC_H264_BP   # active codec, updated on MEDIA_INDICATION

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Read codec from channel_config (SDR). Falls back to H264-BP."""
        cfg = self.channel_config
        if cfg is not None:
            configs = cfg.get("av_channel", {}).get("video_configs", [])
            if configs:
                c = configs[0]
                self._codec_sdr = c.get("codec", self._codec_sdr)
                self._codec     = self._codec_sdr
                self.log.info(
                    "VideoModule _init: channel_id=%d codec_sdr=%s",
                    self.CHANNEL_ID, self._codec_sdr,
                )
            else:
                self.log.warning(
                    "_init: channel_id=%d has no video_configs in SDR — using H264-BP default",
                    self.CHANNEL_ID,
                )
        else:
            self.log.warning("_init: channel_config is None — using H264-BP default")

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
        self._session_id = 0
        self._set_state("IDLE")

    def on_channel_close(self, channel_id: int) -> None:
        self.log.info("Channel %d closed", channel_id)
        self._session_id = 0
        self._set_state("IDLE")

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming raw frame bytes by AA message_id."""
        result = decode_aa_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload — dropping (len=%d)", len(data))
            return

        message_id, body = result

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(body)
        elif message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(body)
        elif message_id == _MSG_AV_CHANNEL_START_INDICATION:
            self._handle_start_indication(body)
        elif message_id == _MSG_AV_CHANNEL_STOP_INDICATION:
            self._handle_stop_indication(body)
        elif message_id == _MSG_AV_CHANNEL_AV_MEDIA_INDICATION:
            self._handle_media(body)
        elif message_id == _MSG_AV_CHANNEL_AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            self._handle_media_with_timestamp(body)
        elif message_id == _MSG_VIDEO_FOCUS_REQUEST:
            self._handle_video_focus_request(body)
        else:
            self.log.warning("Unhandled video msg_id=0x%04x len=%d", message_id, len(body))

    # ------------------------------------------------------------------
    # AA message handlers
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        """Reply AVChannelSetupResponse (proto), then send VideoFocusIndication."""
        max_unacked = self._config.get("max_unacked", 1)
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked  = max_unacked
        resp.configs.append(0)
        self.send_frame(_MSG_AV_CHANNEL_SETUP_RESPONSE, resp.SerializeToString())
        self._set_state("SETUP")
        self.log.info(
            "AVChannelSetupRequest ch=%d → AVChannelSetupResponse sent (max_unacked=%d)",
            self.CHANNEL_ID, max_unacked,
        )
        # Proactively grant video focus after setup
        self._send_video_focus_indication(unrequested=False)

    def _handle_open_request(self, body: bytes) -> None:
        """Reply ChannelOpenResponse (proto)."""
        resp = ChannelOpenResponse()
        resp.status = 0  # STATUS_SUCCESS
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString())
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_start_indication(self, body: bytes) -> None:
        """Parse AVChannelStartIndication, extract session_id."""
        try:
            msg = AVChannelStartIndication()
            msg.ParseFromString(body)
            self._session_id = msg.session
            self._set_state("PLAYING")
            self.log.info(
                "AVChannelStartIndication ch=%d session_id=%d → PLAYING (no response sent)",
                self.CHANNEL_ID, self._session_id,
            )
        except Exception as exc:
            self.log.warning(
                "AVChannelStartIndication parse error ch=%d — %s (session_id remains %d)",
                self.CHANNEL_ID, exc, self._session_id,
            )

    def _handle_stop_indication(self, body: bytes) -> None:
        """Reset session_id and transition to STOPPED."""
        self._session_id = 0
        self._set_state("STOPPED")
        self.log.info(
            "AVChannelStopIndication ch=%d → STOPPED (session_id reset, no response sent)",
            self.CHANNEL_ID,
        )

    def _handle_media(self, body: bytes) -> None:
        """
        AV_MEDIA_INDICATION = codec config message (MEDIA_MESSAGE_CODEC_CONFIG).
        The body contains codec configuration bytes; first 2 bytes encode the codec type.
        ACK immediately, then update self._codec.
        """
        self._send_media_ack()

        if len(body) >= 2:
            codec_raw = struct.unpack_from(">H", body, 0)[0]
            self._codec = codec_raw
            self.log.info(
                "AV_MEDIA_INDICATION ch=%d codec updated to 0x%04x (was %s)",
                self.CHANNEL_ID, codec_raw, self._codec_sdr,
            )
        else:
            self.log.warning(
                "AV_MEDIA_INDICATION ch=%d: body too short (%d bytes) to read codec",
                self.CHANNEL_ID, len(body),
            )

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        """Parse MediaWithTimestamp, ACK immediately, publish video.frame."""
        if self._session_id == 0:
            self.log.warning(
                "MediaWithTimestamp ch=%d dropped — session_id==0 (waiting for StartIndication)",
                self.CHANNEL_ID,
            )
            return

        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")

        ts_us, data = parse_media_with_timestamp(body)
        self.log.debug(
            "MediaWithTimestamp ch=%d ts_us=%d len=%d codec=%s — sending ACK + publishing frame",
            self.CHANNEL_ID, ts_us, len(data), self._codec,
        )

        # ACK immediately — do NOT wait for video_ui
        self._send_media_ack()

        if data:
            self.bus.publish("video.frame", {
                "channel_id": self.CHANNEL_ID,
                "ts_us":      ts_us,
                "data_b64":   base64.b64encode(data).decode("ascii"),
                "codec":      self._codec,
                "codec_sdr":  self._codec_sdr,
            })

    def _handle_video_focus_request(self, body: bytes) -> None:
        """Respond to an explicit VideoFocusRequest from the phone."""
        self.log.debug("VIDEO_FOCUS_REQUEST ch=%d", self.CHANNEL_ID)
        self._send_video_focus_indication(unrequested=False)

    # ------------------------------------------------------------------
    # VideoFocus helper
    # ------------------------------------------------------------------

    def _send_video_focus_indication(self, *, unrequested: bool) -> None:
        """Send VideoFocusIndication with focus_mode=PROJECTED."""
        msg = VideoFocusIndication()
        msg.focus_mode  = VideoFocusMode.Enum.PROJECTED
        msg.unrequested = unrequested
        self.send_frame(_MSG_VIDEO_FOCUS_INDICATION, msg.SerializeToString())
        self.log.debug(
            "VideoFocusIndication ch=%d focus=PROJECTED unrequested=%s",
            self.CHANNEL_ID, unrequested,
        )

    # ------------------------------------------------------------------
    # MediaAck helper
    # ------------------------------------------------------------------

    def _send_media_ack(self) -> None:
        ack = AVMediaAckIndication()
        ack.session_id = self._session_id
        ack.ack_count  = 1
        self.send_frame(_MSG_AV_CHANNEL_MEDIA_ACK, ack.SerializeToString())
        self.log.debug("MediaAck sent ch=%d session_id=%d", self.CHANNEL_ID, self._session_id)

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish("video.state", {"state": new_state})
        self.log.info("video.state → %s", new_state)

    # ------------------------------------------------------------------
    # run() override: add extra subscriptions before calling super
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("aa.session.active",   self.on_aa_session_active)
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        super().run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    VideoModule().run()
