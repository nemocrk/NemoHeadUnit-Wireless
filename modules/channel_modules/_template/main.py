"""
NemoHeadUnit-Wireless — channel_modules/_template

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
  Subscribes  : channel_manager.module_start
                channel_manager.module_stop
                config.response      (auto via ConfigClient)
                config.changed       (auto via ConfigClient)
                aa.channel.open     {channel_id, ...}
                aa.channel.close    {channel_id}
                aa.frame.ch<ch_id>  raw bytes
                aa.session.shutdown {}
                # TODO: add module-specific subscriptions
  Publishes   : channel_manager.module_ready  {name, priority}
                aa.frame.send        bytes  (via BaseChannelModule.send_frame)
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

---
Outgoing frame pattern:
  Use self.send_frame(message_id, proto_body) for ALL outgoing AA frames.
  Never call encode_aa_frame() + bus.publish("aa.frame.send") directly.
  BaseChannelModule.send_frame() handles channel_id and encrypted flag
  consistently for all post-handshake channel traffic.

  Exception — ChannelOpenResponse:
    ChannelOpenResponse (ControlMessage.CHANNEL_OPEN_RESPONSE = 0x0008) belongs
    to the ControlMessage namespace even when sent on a non-zero AV channel.
    Always pass control=True when sending it:
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, body, control=True)
    This has NO runtime effect (wire flags = 0x0B in both cases) but documents
    the namespace boundary explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent   # modules/channel_modules/_template/
_CHANNEL_MODS = _HERE.parent            # modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent    # modules/
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

for _p in (_REPO_ROOT, _MODULES, _CHANNEL_MODS, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
from shared.config_schema import field_int                       # noqa: E402
from shared.proto_utils import (                                 # noqa: E402
    decode_aa_frame,
    parse_media_with_timestamp,
)
from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports — AV shared
# ---------------------------------------------------------------------------
from oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage                   # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage                  # noqa: E402
from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse       # noqa: E402
from oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus              # noqa: E402
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse        # noqa: E402
from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication   # noqa: E402
from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication           # noqa: E402
from oaa.common.StatusEnum_pb2 import Status                                      # noqa: E402

# TODO: add channel-type-specific proto imports here

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


# ---------------------------------------------------------------------------
# TemplateModule
# ---------------------------------------------------------------------------

class TemplateModule(BaseChannelModule):
    """
    Template AA channel module.

    Replace every occurrence of "Template" / "_template" with your module name.
    Read the class-level docstring in base_channel_module.py before starting.
    """

    MODULE_NAME: str = "_template"
    CHANNEL_ID:  int = -1
    PRIORITY:    int = 1

    def get_schema(self) -> dict:
        return {}

    def __init__(self) -> None:
        super().__init__()
        self._session_id: int = 0
        self._state:      str = "IDLE"

    def _is_ready(self) -> bool:
        return True

    def _init(self) -> None:
        cfg = self.channel_config
        if cfg is not None:
            self.log.info("_init: channel_config ok for ch=%d", self.CHANNEL_ID)
        else:
            self.log.warning("_init: channel_config is None — using defaults")

    def _cleanup(self) -> None:
        self._set_state("IDLE")

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("AA session shutdown — ch=%d reset", self.CHANNEL_ID)

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("Channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("Channel %d closed — session_id reset", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
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
        else:
            self.log.debug("Unhandled msg_id=0x%04x ch=%d len=%d", message_id, channel_id, len(body))

    def _handle_setup_request(self, body: bytes) -> None:
        max_unacked = self._config.get("max_unacked", 1)
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked  = max_unacked
        resp.configs.append(0)
        self.send_frame(_MSG_AV_CHANNEL_SETUP_RESPONSE, resp.SerializeToString())
        self._set_state("SETUP")
        self.log.info("AVChannelSetupRequest ch=%d → AVChannelSetupResponse sent (max_unacked=%d)",
                      self.CHANNEL_ID, max_unacked)

    def _handle_open_request(self, body: bytes) -> None:
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), control=True)
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_start_indication(self, body: bytes) -> None:
        try:
            msg = AVChannelStartIndication()
            msg.ParseFromString(body)
            self._session_id = msg.session
            self.log.info("AVChannelStartIndication ch=%d session_id=%d — state → PLAYING",
                          self.CHANNEL_ID, self._session_id)
        except Exception as exc:
            self.log.warning("AVChannelStartIndication parse error ch=%d — %s",
                             self.CHANNEL_ID, exc)
        self._set_state("PLAYING")

    def _handle_stop_indication(self, body: bytes) -> None:
        self._session_id = 0
        self._set_state("STOPPED")
        self.log.info("AVChannelStopIndication ch=%d — state → STOPPED", self.CHANNEL_ID)

    def _handle_media(self, body: bytes) -> None:
        self._send_media_ack()

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        if self._session_id == 0:
            self.log.debug("MediaWithTimestamp ch=%d dropped — session_id not yet set", self.CHANNEL_ID)
            return
        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")
        ts_us, payload = parse_media_with_timestamp(body)
        self.log.debug("MediaWithTimestamp ch=%d ts_us=%d len=%d", self.CHANNEL_ID, ts_us, len(payload))
        self._send_media_ack()

    def _send_media_ack(self) -> None:
        ack = AVMediaAckIndication()
        ack.session_id = self._session_id
        ack.ack_count  = 1
        self.send_frame(_MSG_AV_CHANNEL_MEDIA_ACK, ack.SerializeToString())
        self.log.debug("MediaAck sent ch=%d session_id=%d", self.CHANNEL_ID, self._session_id)

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish(f"{self.MODULE_NAME}.state", {"channel_id": self.CHANNEL_ID, "state": new_state})
        self.log.info("%s.state ch=%d → %s", self.MODULE_NAME, self.CHANNEL_ID, new_state)

    def run(self) -> None:
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        super().run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    TemplateModule().run()
