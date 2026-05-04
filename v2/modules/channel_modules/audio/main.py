"""
NemoHeadUnit-Wireless v2 — channel_modules/audio

Module contract:
  Name        : audio
  Priority    : 1
  Channel IDs : up to three — resolved at boot from oaa_control_channel config
                  MEDIA   (stream_type == "MEDIA_AUDIO")    fallback: 1
                  SPEECH  (stream_type == "SPEECH_AUDIO")   fallback: 2
                  SYSTEM  (stream_type == "SYSTEM_AUDIO")   fallback: 7
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response          {module, config, requester}
                oaa.channel.open         {channel_id, av_type?, ...}
                oaa.channel.close        {channel_id}
                oaa.frame.<ch_media>     raw bytes
                oaa.frame.<ch_speech>    raw bytes
                oaa.frame.<ch_system>    raw bytes
                aa.session.active        {}
                aa.session.shutdown      {}
  Publishes   : system.module_ready      {name, priority}
                system.ready             {name, priority}
                aa.frame.send            {channel_id, flags, payload_hex}  ← AVChannelSetup/Open resp + MediaAck
                audio.pcm                {channel_id, stream_type, session_id, ts_us, data_b64}
                audio.state              {channel_id, stream_type, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED

Flow:
  1. On system.start: request oaa_control_channel config to discover MEDIA/SPEECH/SYSTEM channel ids.
  2. On config.response (module=oaa_control_channel): scan channels list for
       av_channel.stream_type in {MEDIA_AUDIO, SPEECH_AUDIO, SYSTEM_AUDIO}.
  3. Publish system.ready once channel ids are resolved.
  4. On oaa.frame.<ch>: decode AA media frame, dispatch by message_id:
       - AVChannelSetupRequest  → reply AVChannelSetupResponse  (audio.state=SETUP)
       - AVChannelOpenRequest   → reply AVChannelOpenResponse   (audio.state=OPEN)
       - MediaWithTimestamp     → send MediaAck, publish audio.pcm
       - Media (no timestamp)   → send MediaAck, publish audio.pcm
       - AVChannelStopIndication → audio.state=STOPPED
  5. On aa.session.shutdown: reset all channels to IDLE.

ACK strategy:
  MediaAck is sent immediately after each MediaWithTimestamp or Media frame.
  Downstream consumers (audio_ui / ALSA sink) are fire-and-forget.

Channel discovery:
  Channel ids are resolved at boot from oaa_control_channel config.
  Fallbacks: MEDIA=1, SPEECH=2, SYSTEM=7 (AASDK defaults).
"""

from __future__ import annotations

import base64
import struct
import sys
from pathlib import Path
from typing import Dict

_HERE         = Path(__file__).parent          # v2/modules/channel_modules/audio/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/

for _p in (_V2, _MODULES, _CHANNEL_MODS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# AA media frame constants  (identical to video — re-declared for clarity)
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
_MSG_MEDIA                      = 0x0003
_MSG_MEDIA_ACK                  = 0x0002

# Stream type constants (as returned in oaa_control_channel config)
_STREAM_MEDIA  = "MEDIA_AUDIO"
_STREAM_SPEECH = "SPEECH_AUDIO"
_STREAM_SYSTEM = "SYSTEM_AUDIO"

# Fallback channel ids (AASDK defaults)
_CHANNEL_MEDIA_FALLBACK  = 1
_CHANNEL_SPEECH_FALLBACK = 2
_CHANNEL_SYSTEM_FALLBACK = 7


# ---------------------------------------------------------------------------
# Per-channel state tracker
# ---------------------------------------------------------------------------

class _AudioChannelState:
    """Minimal state machine for a single audio channel."""

    def __init__(self, stream_type: str, fallback_id: int) -> None:
        self.stream_type = stream_type
        self.channel_id  = fallback_id
        self.session_id  = 0
        self.state       = "IDLE"  # IDLE | SETUP | OPEN | PLAYING | STOPPED

    def __repr__(self) -> str:
        return (
            f"<AudioChannel {self.stream_type} ch={self.channel_id} "
            f"session={self.session_id} state={self.state}>"
        )


# ---------------------------------------------------------------------------
# AudioModule
# ---------------------------------------------------------------------------

class AudioModule(BaseChannelModule):
    """
    OAA Audio channel module.

    Handles three audio sub-channels (MEDIA, SPEECH, SYSTEM) within a single
    module process.  Each sub-channel has its own _AudioChannelState.

    Unlike VideoModule (single channel), AudioModule registers three frame
    subscriptions and a manual channel-id lookup because BaseChannelModule
    only models one CHANNEL_ID.  We use CHANNEL_ID = _CHANNEL_MEDIA_FALLBACK
    as the primary (satisfies the base class constructor), but subscribe to
    all three frame topics ourselves.
    """

    MODULE_NAME = "audio"
    CHANNEL_ID  = _CHANNEL_MEDIA_FALLBACK  # overwritten after config.response
    PRIORITY    = 1

    def __init__(self) -> None:
        super().__init__()
        self._channels: Dict[int, _AudioChannelState] = {}
        self._by_stream: Dict[str, _AudioChannelState] = {}
        self._channel_resolved = False

        for stream_type, fallback in (
            (_STREAM_MEDIA,  _CHANNEL_MEDIA_FALLBACK),
            (_STREAM_SPEECH, _CHANNEL_SPEECH_FALLBACK),
            (_STREAM_SYSTEM, _CHANNEL_SYSTEM_FALLBACK),
        ):
            ch = _AudioChannelState(stream_type, fallback)
            self._channels[fallback] = ch
            self._by_stream[stream_type] = ch

    # ------------------------------------------------------------------
    # Channel discovery
    # ------------------------------------------------------------------

    def _request_oaa_config(self) -> None:
        self.bus.publish("config.get", {
            "module":    "oaa_control_channel",
            "requester": self.MODULE_NAME,
        })
        self.log.info("Requested oaa_control_channel config for AUDIO channel discovery")

    def on_config_response(self, topic: str, payload: dict) -> None:
        if payload.get("module") != "oaa_control_channel":
            return
        if payload.get("requester") != self.MODULE_NAME:
            return

        channels = payload.get("config", {}).get("channels", [])
        resolved = self._resolve_audio_channels(channels)

        # Rebuild index with resolved ids
        new_channels: Dict[int, _AudioChannelState] = {}
        for stream_type, channel_id in resolved.items():
            ch = self._by_stream.get(stream_type)
            if ch is None:
                continue
            old_id = ch.channel_id
            if old_id in self._channels:
                del self._channels[old_id]
            ch.channel_id = channel_id
            new_channels[channel_id] = ch
            self.log.info("AUDIO %s resolved: channel_id=%d", stream_type, channel_id)

        # Keep entries not overwritten (fallbacks not found in config)
        for cid, ch in self._channels.items():
            if cid not in new_channels:
                new_channels[cid] = ch
                self.log.warning(
                    "AUDIO %s not in config — fallback channel_id=%d",
                    ch.stream_type, cid,
                )
        self._channels = new_channels

        # Update primary CHANNEL_ID to MEDIA
        media_ch = self._by_stream.get(_STREAM_MEDIA)
        if media_ch:
            self.CHANNEL_ID = media_ch.channel_id

        if not self._channel_resolved:
            self._channel_resolved = True
            # Re-subscribe all audio frame topics
            for cid in self._channels:
                topic = f"oaa.frame.{cid}"
                self.bus.subscribe(topic, self._on_oaa_frame)
                self.log.info("Subscribed to %s", topic)
            self.bus.publish("system.ready", {
                "name":     self.MODULE_NAME,
                "priority": self.PRIORITY,
            })
            self.log.info("system.ready published (priority=%d)", self.PRIORITY)

    @staticmethod
    def _resolve_audio_channels(channels: list) -> dict:
        """Scan channel list and return {stream_type: channel_id} for known audio types."""
        result = {}
        audio_types = {_STREAM_MEDIA, _STREAM_SPEECH, _STREAM_SYSTEM}
        for ch in channels:
            av = ch.get("av_channel", {})
            st = av.get("stream_type", "")
            if st in audio_types:
                cid = ch.get("channel_id")
                if cid is not None:
                    result[st] = int(cid)
        return result

    # ------------------------------------------------------------------
    # _init / _cleanup hooks (called by BaseChannelModule.run())
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Trigger config discovery; system.ready is deferred to on_config_response."""
        self._request_oaa_config()

    def _cleanup(self) -> None:
        for ch in self._channels.values():
            self._set_channel_state(ch, "IDLE")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_active(self, topic: str, payload: dict) -> None:
        for ch in self._channels.values():
            ch.session_id = 0
            self._set_channel_state(ch, "IDLE")
        self.log.info("AA session active — audio ready")

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        for ch in self._channels.values():
            ch.session_id = 0
            self._set_channel_state(ch, "IDLE")
        self.log.info("AA session shutdown — audio reset")

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        ch = self._channels.get(channel_id)
        if ch:
            self._set_channel_state(ch, "IDLE")
            self.log.info("Channel %d (%s) open", channel_id, ch.stream_type)
        else:
            self.log.debug("on_channel_open: unknown channel_id=%d", channel_id)

    def on_channel_close(self, channel_id: int) -> None:
        ch = self._channels.get(channel_id)
        if ch:
            self._set_channel_state(ch, "IDLE")
            self.log.info("Channel %d (%s) closed", channel_id, ch.stream_type)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming raw frame bytes by AA message_id."""
        ch = self._channels.get(channel_id)
        if ch is None:
            self.log.debug("on_frame: unknown channel_id=%d — dropping", channel_id)
            return

        result = self._decode_media_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload on ch=%d — dropping", channel_id)
            return

        message_id, body = result

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(ch, body)
        elif message_id == _MSG_AV_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(ch, body)
        elif message_id == _MSG_MEDIA_WITH_TIMESTAMP:
            self._handle_media_with_timestamp(ch, body)
        elif message_id == _MSG_MEDIA:
            self._handle_media(ch, body)
        elif message_id == _MSG_AV_CHANNEL_STOP_INDICATION:
            self.log.info("AVChannelStopIndication on ch=%d (%s)", channel_id, ch.stream_type)
            self._set_channel_state(ch, "STOPPED")
        else:
            self.log.debug(
                "Unhandled audio msg_id=0x%04x ch=%d (%s) len=%d",
                message_id, channel_id, ch.stream_type, len(body),
            )

    # ------------------------------------------------------------------
    # BaseChannelModule._on_oaa_channel_open override
    # (base only checks self.CHANNEL_ID; we check all known ids)
    # ------------------------------------------------------------------

    def _on_oaa_channel_open(self, topic: str, payload: dict) -> None:
        cid = payload.get("channel_id")
        if cid not in self._channels:
            return
        self._channel_open = True
        self.log.info("oaa.channel.open for audio ch=%d", cid)
        self.on_channel_open(cid, payload)

    def _on_oaa_channel_close(self, topic: str, payload: dict) -> None:
        cid = payload.get("channel_id")
        if cid not in self._channels:
            return
        self.log.info("oaa.channel.close for audio ch=%d", cid)
        self.on_channel_close(cid)

    # ------------------------------------------------------------------
    # AA message handlers
    # ------------------------------------------------------------------

    def _handle_setup_request(self, ch: _AudioChannelState, body: bytes) -> None:
        """Reply AVChannelSetupResponse (status=OK, max_unacked=1)."""
        proto_body = b"\x08\x00\x10\x01"
        frame = self._encode_media_frame(
            ch.channel_id, _MSG_AV_CHANNEL_SETUP_RESPONSE, proto_body
        )
        self.bus.publish("aa.frame.send", frame)
        self._set_channel_state(ch, "SETUP")
        self.log.info(
            "AVChannelSetupRequest ch=%d (%s) → AVChannelSetupResponse sent",
            ch.channel_id, ch.stream_type,
        )

    def _handle_open_request(self, ch: _AudioChannelState, body: bytes) -> None:
        """Reply AVChannelOpenResponse (status=OK)."""
        proto_body = b"\x08\x00"
        frame = self._encode_media_frame(
            ch.channel_id, _MSG_AV_CHANNEL_OPEN_RESPONSE, proto_body
        )
        self.bus.publish("aa.frame.send", frame)
        self._set_channel_state(ch, "OPEN")
        self.log.info(
            "AVChannelOpenRequest ch=%d (%s) → AVChannelOpenResponse sent",
            ch.channel_id, ch.stream_type,
        )

    def _handle_media_with_timestamp(
        self, ch: _AudioChannelState, body: bytes
    ) -> None:
        """Parse MediaWithTimestamp, send MediaAck, publish audio.pcm."""
        ts_us, pcm_data = self._parse_media_with_timestamp(body)

        if ch.state not in ("OPEN", "PLAYING"):
            self._set_channel_state(ch, "PLAYING")

        # Extract session_id from body field 3 (varint) if present
        session_id = _parse_session_id(body) or ch.session_id
        if session_id:
            ch.session_id = session_id

        self._send_media_ack(ch)

        if pcm_data:
            self.bus.publish("audio.pcm", {
                "channel_id":  ch.channel_id,
                "stream_type": ch.stream_type,
                "session_id":  ch.session_id,
                "ts_us":       ts_us,
                "data_b64":    base64.b64encode(pcm_data).decode("ascii"),
            })
            self.log.debug(
                "audio.pcm published ch=%d (%s) ts=%d len=%d",
                ch.channel_id, ch.stream_type, ts_us, len(pcm_data),
            )

    def _handle_media(self, ch: _AudioChannelState, body: bytes) -> None:
        """Media frame without timestamp — treat identically, ts_us=0."""
        if ch.state not in ("OPEN", "PLAYING"):
            self._set_channel_state(ch, "PLAYING")

        self._send_media_ack(ch)

        if body:
            self.bus.publish("audio.pcm", {
                "channel_id":  ch.channel_id,
                "stream_type": ch.stream_type,
                "session_id":  ch.session_id,
                "ts_us":       0,
                "data_b64":    base64.b64encode(body).decode("ascii"),
            })

    def _send_media_ack(self, ch: _AudioChannelState) -> None:
        proto_body = (
            _encode_varint_field(1, ch.session_id)
            + _encode_varint_field(2, 1)
        )
        frame = self._encode_media_frame(ch.channel_id, _MSG_MEDIA_ACK, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self.log.debug(
            "MediaAck sent ch=%d (%s) session_id=%d",
            ch.channel_id, ch.stream_type, ch.session_id,
        )

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_channel_state(self, ch: _AudioChannelState, new_state: str) -> None:
        ch.state = new_state
        self.bus.publish("audio.state", {
            "channel_id":  ch.channel_id,
            "stream_type": ch.stream_type,
            "state":       new_state,
        })
        self.log.info(
            "audio.state ch=%d (%s) → %s",
            ch.channel_id, ch.stream_type, new_state,
        )

    # ------------------------------------------------------------------
    # MediaWithTimestamp parser
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_media_with_timestamp(body: bytes) -> tuple[int, bytes]:
        """Minimal manual proto parse: field 1=timestamp, field 2=data."""
        ts_us = 0
        data  = b""
        pos   = 0

        while pos < len(body):
            tag_byte     = body[pos]; pos += 1
            field_number = tag_byte >> 3
            wire_type    = tag_byte & 0x07

            if field_number == 1 and wire_type == 1:    # timestamp fixed64
                if pos + 8 > len(body):
                    break
                ts_us = struct.unpack_from("<Q", body, pos)[0]
                pos += 8
            elif field_number == 2 and wire_type == 2:  # PCM data bytes
                length, pos = _read_varint(body, pos)
                if length is None:
                    break
                data = body[pos: pos + length]
                pos += length
            else:
                # Skip unknown fields
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

        return ts_us, data

    # ------------------------------------------------------------------
    # Frame encode / decode helpers  (identical to VideoModule)
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_media_frame(
        channel_id: int, message_id: int, proto_body: bytes
    ) -> dict:
        payload = struct.pack(">H", message_id) + proto_body
        return {
            "channel_id":  channel_id,
            "flags":       _FLAG_FULL,
            "payload_hex": payload.hex(),
        }

    @staticmethod
    def _decode_media_frame(data: bytes) -> tuple[int, bytes] | None:
        if len(data) < 2:
            return None
        message_id = struct.unpack_from(">H", data, 0)[0]
        return message_id, data[2:]

    # ------------------------------------------------------------------
    # run() override: add extra subscriptions before calling super
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("config.response",     self.on_config_response)
        self.bus.subscribe("aa.session.active",   self.on_aa_session_active)
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        # Override oaa.channel lifecycle for multi-channel handling
        self.bus.subscribe("oaa.channel.open",  self._on_oaa_channel_open)
        self.bus.subscribe("oaa.channel.close", self._on_oaa_channel_close)
        super().run()


# ---------------------------------------------------------------------------
# Minimal protobuf varint helpers  (no proto dependency)
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


def _parse_session_id(body: bytes) -> int | None:
    """Extract proto field 3 (varint) = session_id from MediaWithTimestamp."""
    pos = 0
    while pos < len(body):
        tag_byte     = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07
        if field_number == 3 and wire_type == 0:
            val, _ = _read_varint(body, pos)
            return val
        # skip field
        if wire_type == 0:
            _, pos = _read_varint(body, pos)
        elif wire_type == 1:
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(body, pos)
            if length:
                pos += (length or 0)
        elif wire_type == 5:
            pos += 4
        else:
            break
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    AudioModule().run()
