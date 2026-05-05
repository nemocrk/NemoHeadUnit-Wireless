"""
NemoHeadUnit-Wireless v2 — channel_modules/_template

TEMPLATE — copy this folder to channel_modules/<your_module>/ and:
  1. Rename the class:   TemplateModule  →  <YourModule>Module
  2. Set MODULE_NAME:    "_template"     →  "<your_module>"
  3. Fill get_schema()   with real config keys (or leave {} if none).
  4. Fill _init()        to allocate your external resource.
  5. Fill _cleanup()     to release it.
  6. Implement _is_ready() if the resource can be unavailable at startup.
  7. Implement on_channel_open / on_channel_close / on_frame.
  8. Add your _handle_*() methods for each AA message_id you need.
  9. Update get_schema(), on_config_loaded(), on_config_changed() if needed.
 10. Adjust the module-level docstring (Subscribes / Publishes / Config keys).

---
Module contract (fill in before shipping):
  Name        : <--module-name>
  Priority    : 1
  Channel ID  : <--channel-id>    (parsed by BaseChannelModule from CLI)
  SDR bytes   : <--sdr-bytes-hex> (parsed by base into self.channel_config)
  Subscribes  : channel_manager.module_readytostart
                channel_manager.module_start
                channel_manager.module_stop
                config.response      (auto via ConfigClient)
                config.changed       (auto via ConfigClient)
                aa.channel.open     {channel_id, ...}
                aa.channel.close    {channel_id}
                aa.frame.ch<ch_id>  raw bytes
                aa.session.shutdown {}
                # TODO: add module-specific subscriptions
  Publishes   : channel_manager.module_ready  {name, priority}
                aa.frame.send        {channel_id, flags, payload_hex}
                <module>.state       {channel_id, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED
                # TODO: add module-specific publications
  Config keys : # TODO: document config keys added in get_schema()

---
Boot / readiness flow (handled by BaseChannelModule — no changes needed):

  run() → bus.subscribe(all topics) → bus.start()
        → channel_manager.module_ready {name, priority}   (immediate announce)

  channel_manager.module_start {priority=1}
        → cfg.get(schema)                                  (async config request)
        → _init()                                          (allocate resource)
        → _init_done = True
        → _try_publish_ready()                             (maybe re-publish)

  config.response → on_config_loaded()
        → _config_loaded = True
        → _try_publish_ready()                             (maybe publish)

  channel_manager.module_ready published only when ALL of:
    _init_done AND _config_loaded (or no schema) AND _is_ready() AND channel_config is not None

---
AA channel lifecycle (frames dispatched by on_frame):

  SETUP_REQUEST       → AVChannelSetupResponse    state → SETUP
  CHANNEL_OPEN_REQ    → ChannelOpenResponse       state → OPEN
  START_INDICATION    → store session_id          state → PLAYING
  STOP_INDICATION     → clear session_id          state → STOPPED
  AV_MEDIA_INDICATION            → (channel-type specific)
  AV_MEDIA_WITH_TIMESTAMP        → (channel-type specific)
  aa.session.shutdown → clear session_id          state → IDLE
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap — identical to audio / video
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent          # v2/modules/channel_modules/_template/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/
_PROTOS       = _V2 / "protos"                 # v2/protos/

for _p in (_V2, _MODULES, _CHANNEL_MODS, _PROTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
from shared.config_schema import field_int, field_enum          # noqa: E402
from shared.proto_utils import (                                 # noqa: E402
    encode_aa_frame,
    decode_aa_frame,
    parse_media_with_timestamp,
)
from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports — AV shared (same set used by audio + video)
# ---------------------------------------------------------------------------
from oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage                   # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage                  # noqa: E402
from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse       # noqa: E402
from oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus              # noqa: E402
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse        # noqa: E402
from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication   # noqa: E402
from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication           # noqa: E402

# TODO: add channel-type–specific proto imports here, e.g.:
# from oaa.input.InputEventIndication_pb2 import InputEventIndication

# ---------------------------------------------------------------------------
# AA message ID aliases — copy the full block, add channel-specific ones below
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

# TODO: add channel-specific message IDs here, e.g.:
# _MSG_INPUT_EVENT_INDICATION = InputChannelMessageIdsEnum.INPUT_EVENT_INDICATION


# ---------------------------------------------------------------------------
# TemplateModule
# ---------------------------------------------------------------------------

class TemplateModule(BaseChannelModule):
    """
    Template AA channel module.

    Replace every occurrence of "Template" / "_template" with your module name.
    Read the class-level docstring in base_channel_module.py before starting.

    Required class attributes (set at class level, not in __init__):
      MODULE_NAME — overridden by --module-name CLI
      CHANNEL_ID  — overridden by --channel-id  CLI
      PRIORITY    — boot priority (1 = default services tier)
    """

    MODULE_NAME: str = "_template"  # TODO: replace with e.g. "input"
    CHANNEL_ID:  int = -1            # always overridden by --channel-id
    PRIORITY:    int = 1

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        """
        Declare typed config keys for this module.

        Use field_int / field_enum / field_str / field_bool from
        shared.config_schema.  Return {} if this module needs no config.

        Example (copy from audio):
            return {
                "max_unacked": field_int(default=1, min_value=1, max_value=16),
            }
        """
        # TODO: replace with real schema or return {}
        return {
            "max_unacked": field_int(
                default=1,
                min_value=1,
                max_value=16,
            ),
        }

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()

        # Session state — identical lifecycle to audio / video
        self._session_id: int = 0
        self._state:      str = "IDLE"   # IDLE | SETUP | OPEN | PLAYING | STOPPED

        # TODO: declare module-specific instance variables here, e.g.:
        # self._pipeline: SomePipeline | None = None

    # ------------------------------------------------------------------
    # Readiness gate
    # ------------------------------------------------------------------

    def _is_ready(self) -> bool:
        """
        Return True when the module's external resource is operational.

        Default (no external resource):  return True
        With a resource (like audio stream):
            return self._pipeline is not None

        BaseChannelModule will NOT publish channel_manager.module_ready
        until this returns True.
        """
        return True  # TODO: replace with real resource check if needed

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """
        Called once during channel_manager.module_start, after cfg.get() is dispatched.

        Read codec / format parameters from self.channel_config (populated
        by base from the SDR hex passed via --sdr-bytes-hex).

        NOTE: on_config_loaded() may arrive before OR after _init() completes
        (async bus).  If your resource setup depends on both SDR params AND
        persisted config, open the resource in both _init() and on_config_loaded()
        (guarded by self._init_done), exactly as AudioModule does.
        """
        cfg = self.channel_config
        if cfg is not None:
            # TODO: read channel-specific params from cfg, e.g.:
            # configs = cfg.get("av_channel", {}).get("audio_configs", [])
            self.log.info("_init: channel_config ok for ch=%d", self.CHANNEL_ID)
        else:
            self.log.warning("_init: channel_config is None — using defaults")

        # TODO: allocate your resource here, e.g.:
        # self._pipeline = _open_pipeline(...)

    def _cleanup(self) -> None:
        """Called on channel_manager.module_stop — release all resources."""
        # TODO: close your resource here, e.g.:
        # _close_pipeline(self._pipeline)
        # self._pipeline = None
        self._set_state("IDLE")

    # ------------------------------------------------------------------
    # Config callbacks — override only when needed
    # ------------------------------------------------------------------

    def on_config_loaded(self, config: dict) -> None:
        """
        Called once the persisted config arrives from config_manager.

        super() merges config into self._config, sets _config_loaded=True,
        and calls _try_publish_ready().

        Only override when you need to react to the loaded values
        (e.g. reopening a stream with a user-chosen device — see AudioModule).
        """
        super().on_config_loaded(config)
        # TODO: react to loaded config if needed, e.g.:
        # if self._init_done:
        #     self._reopen_resource()

    def on_config_changed(self, key: str, value: Any) -> None:
        """Called at runtime when a single config key changes."""
        super().on_config_changed(key, value)
        # TODO: react to hot-reload of individual keys, e.g.:
        # if key == "max_unacked":
        #     self.log.info("max_unacked changed to %r", value)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        """Reset state when the AA session ends (phone disconnected)."""
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("AA session shutdown — ch=%d reset", self.CHANNEL_ID)

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        """Called when aa.channel.open arrives for self.CHANNEL_ID."""
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("Channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        """Called when aa.channel.close arrives for self.CHANNEL_ID."""
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("Channel %d closed — session_id reset", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """
        Entry point for every raw binary frame on this channel.

        Decode the AA frame header, then dispatch by message_id.
        Add a branch for every AA message_id your channel needs to handle.
        """
        result = decode_aa_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload on ch=%d — dropping", channel_id)
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
        # TODO: add channel-specific branches, e.g.:
        # elif message_id == _MSG_INPUT_EVENT_INDICATION:
        #     self._handle_input_event(body)
        else:
            self.log.debug(
                "Unhandled msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(body),
            )

    # ------------------------------------------------------------------
    # AA message handlers — standard AVChannel handshake
    # (identical to audio / video — do NOT change unless the channel
    #  type requires a different setup response format)
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        """Send AVChannelSetupResponse and transition to SETUP."""
        max_unacked = self._config.get("max_unacked", 1)
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked  = max_unacked
        resp.configs.append(0)
        self.bus.publish("aa.frame.send", encode_aa_frame(
            self.CHANNEL_ID, _MSG_AV_CHANNEL_SETUP_RESPONSE, resp.SerializeToString(),
        ))
        self._set_state("SETUP")
        self.log.info(
            "AVChannelSetupRequest ch=%d → AVChannelSetupResponse sent (max_unacked=%d)",
            self.CHANNEL_ID, max_unacked,
        )
        # TODO: some channel types (e.g. video) send an extra indication here.

    def _handle_open_request(self, body: bytes) -> None:
        """Send ChannelOpenResponse and transition to OPEN."""
        resp = ChannelOpenResponse()
        resp.status = 0  # STATUS_SUCCESS
        self.bus.publish("aa.frame.send", encode_aa_frame(
            self.CHANNEL_ID, _MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString(),
        ))
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_start_indication(self, body: bytes) -> None:
        """Extract session_id from AVChannelStartIndication and transition to PLAYING."""
        try:
            msg = AVChannelStartIndication()
            msg.ParseFromString(body)
            self._session_id = msg.session
            self.log.info(
                "AVChannelStartIndication ch=%d session_id=%d — state → PLAYING",
                self.CHANNEL_ID, self._session_id,
            )
        except Exception as exc:
            self.log.warning(
                "AVChannelStartIndication parse error ch=%d — %s (session_id remains %d)",
                self.CHANNEL_ID, exc, self._session_id,
            )
        self._set_state("PLAYING")

    def _handle_stop_indication(self, body: bytes) -> None:
        """Reset session_id and transition to STOPPED."""
        self._session_id = 0
        self._set_state("STOPPED")
        self.log.info(
            "AVChannelStopIndication ch=%d — session_id reset, state → STOPPED",
            self.CHANNEL_ID,
        )

    def _handle_media(self, body: bytes) -> None:
        """
        AV_MEDIA_INDICATION handler.

        In audio: decode PCM/AAC.
        In video: parse codec config message.
        TODO: implement channel-specific media handling here.
        """
        # Always ACK immediately — do NOT wait for downstream processing.
        self._send_media_ack()
        # TODO: process `body` (codec config bytes, raw media, etc.)

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        """
        AV_MEDIA_WITH_TIMESTAMP_INDICATION handler.

        Parse timestamp + payload, ACK immediately, then process/publish data.
        Frames arriving before StartIndication (session_id == 0) MUST be dropped.
        """
        if self._session_id == 0:
            self.log.debug(
                "MediaWithTimestamp ch=%d dropped — session_id not yet set",
                self.CHANNEL_ID,
            )
            return

        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")

        ts_us, payload = parse_media_with_timestamp(body)
        self.log.debug(
            "MediaWithTimestamp ch=%d ts_us=%d len=%d",
            self.CHANNEL_ID, ts_us, len(payload),
        )

        # ACK immediately — fire-and-forget, do NOT wait for downstream.
        self._send_media_ack()

        # TODO: process / publish payload, e.g.:
        # if payload:
        #     self.bus.publish("<module>.frame", {
        #         "channel_id": self.CHANNEL_ID,
        #         "ts_us":      ts_us,
        #         "data":       payload,
        #     })

    # ------------------------------------------------------------------
    # MediaAck helper — identical to audio / video, do not change
    # ------------------------------------------------------------------

    def _send_media_ack(self) -> None:
        ack = AVMediaAckIndication()
        ack.session_id = self._session_id
        ack.ack_count  = 1
        self.bus.publish("aa.frame.send", encode_aa_frame(
            self.CHANNEL_ID, _MSG_AV_CHANNEL_MEDIA_ACK, ack.SerializeToString(),
        ))
        self.log.debug("MediaAck sent ch=%d session_id=%d", self.CHANNEL_ID, self._session_id)

    # ------------------------------------------------------------------
    # State helper — keep identical signature/behaviour to audio / video
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        # TODO: replace "_template.state" with "<your_module>.state"
        self.bus.publish("_template.state", {
            "channel_id": self.CHANNEL_ID,
            "state":      new_state,
        })
        self.log.info("_template.state ch=%d → %s", self.CHANNEL_ID, new_state)

    # ------------------------------------------------------------------
    # run() override — add extra bus subscriptions before calling super()
    # ------------------------------------------------------------------

    def run(self) -> None:
        # Subscribe to aa.session.shutdown exactly as audio / video do.
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)

        # TODO: add module-specific subscriptions here, e.g.:
        # self.bus.subscribe("aa.session.active", self.on_aa_session_active)

        super().run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    TemplateModule().run()
