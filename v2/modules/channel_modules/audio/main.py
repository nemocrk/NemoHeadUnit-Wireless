"""
NemoHeadUnit-Wireless v2 — channel_modules/audio

Single-channel audio module.  One process instance per audio channel
(MEDIA / SPEECH / SYSTEM).  All channel parameters are supplied by the
orchestrator (channel_manager) at launch via CLI arguments parsed by
BaseChannelModule:

    python -m channel_modules.audio.main \\
        --module-name  channel_audio_4 \\
        --channel-id   4 \\
        --sdr-bytes-hex <hex string from ServiceDiscoveryResponse>

Module contract:
  Name        : <--module-name>   (e.g. channel_audio_4)
  Priority    : 1
  Channel ID  : <--channel-id>    (populated into self.CHANNEL_ID by base)
  SDR bytes   : <--sdr-bytes-hex> parsed by base into self.channel_config
  Subscribes  : channel_manager.module_readytostart
                channel_manager.module_start
                channel_manager.module_stop
                config.response      (auto via ConfigClient)
                config.changed       (auto via ConfigClient)
                aa.channel.open     {channel_id, ...}
                aa.channel.close    {channel_id}
                aa.frame.ch<ch_id>    raw bytes
                aa.session.shutdown  {}
  Publishes   : channel_manager.module_ready  {name, priority}
                aa.frame.send        bytes  (via BaseChannelModule.send_frame)
                audio.state          {channel_id, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED
  Config keys : audio_device   enum  "default"  sounddevice output device name
                max_unacked    int   1           AVChannelSetupResponse.max_unacked

Flow:
  1. BaseChannelModule parses CLI and populates self.channel_config from SDR.
  2. On channel_manager.module_start: cfg.get(schema) triggers config load + _init().
     NOTE: on_config_loaded() may arrive before OR after _init() completes (async bus).
     _open_stream() is therefore called both at end of _init() and at end of
     on_config_loaded() (guarded by _init_done) to guarantee the stream is always
     opened with the most up-to-date params.
  3. _init(): read codec params from self.channel_config, open pyav codec context
     if needed, open sounddevice RawOutputStream.
  4. BaseChannelModule handles aa.channel.open/close and aa.frame.ch<channel_id>.
  5. on_frame(): decode AA frame header, dispatch by message_id, write PCM.
  6. on_config_changed("audio_device" | "max_unacked"): re-open stream on the fly.
  7. on_aa_session_shutdown: reset state + session_id to IDLE.

Readiness:
  channel_manager.module_ready is emitted lazily by BaseChannelModule._try_publish_ready()
  once _init_done AND _config_loaded AND _is_ready() AND channel_config is
  not None are all True.  _is_ready() returns True only when the sounddevice
  stream is open, so channel_manager never unblocks the phone before audio
  is operational.

Codec support:
  MEDIA_CODEC_AUDIO_PCM          — raw PCM, written directly to sounddevice
  MEDIA_CODEC_AUDIO_AAC          — AAC, decoded via pyav before writing
  MEDIA_CODEC_AUDIO_AAC_LC_ADTS  — AAC-LC ADTS, decoded via pyav before writing
  Unknown codecs                 — logged as error, audio data dropped
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

import sounddevice as sd                       # noqa: E402  (python-sounddevice)
import av                                      # noqa: E402  (PyAV / FFmpeg)

from shared.config_schema import field_enum, field_int  # noqa: E402
from shared.proto_utils import (               # noqa: E402
    decode_aa_frame,
    parse_media_with_timestamp,
)
from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports — generated from v2/protos/oaa/
# ---------------------------------------------------------------------------
from oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage                  # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage                 # noqa: E402
from oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType                         # noqa: E402
from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse      # noqa: E402
from oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus             # noqa: E402
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse       # noqa: E402
from oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication  # noqa: E402
from oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication          # noqa: E402
from oaa.common.StatusEnum_pb2 import Status                                     # noqa: E402

# ---------------------------------------------------------------------------
# AA message ID constants
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

_CODEC_PCM         = MediaCodecType.MEDIA_CODEC_AUDIO_PCM
_CODEC_AAC_LC      = MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC
_CODEC_AAC_LC_ADTS = MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC_ADTS

# Codecs that require pyav decoding
_AAC_CODECS = (_CODEC_AAC_LC, _CODEC_AAC_LC_ADTS)

# ---------------------------------------------------------------------------
# AudioModule
# ---------------------------------------------------------------------------


class AudioModule(BaseChannelModule):
    """
    Single-channel AA audio module with sounddevice (PortAudio) output.

    One process per channel; all parameters (channel_id, SDR bytes) are
    provided via CLI by channel_manager at spawn time and parsed by
    BaseChannelModule into self.CHANNEL_ID and self.channel_config.

    Codec parameters are read from self.channel_config in _init().
    AAC / AAC-LC ADTS frames are decoded via pyav before writing PCM to
    the sounddevice stream.  PCM frames are written directly.

    All outgoing AA frames are sent via self.send_frame(message_id, proto_body)
    which is provided by BaseChannelModule and always sets the encrypted flag
    consistently for post-handshake channel traffic.

    session_id lifecycle:
      - Set to 0 at construction and on channel close / session shutdown.
      - Populated from AVChannelStartIndication.session on StartIndication.
      - Used in every AVMediaAckIndication sent to the phone.

    max_unacked:
      - Configurable via config key "max_unacked" (default 1).
      - Sent to the phone in AVChannelSetupResponse.
      - Controls how many unacknowledged media frames the phone may send.
    """

    MODULE_NAME: str = "channel_audio"   # overridden by --module-name CLI
    CHANNEL_ID:  int = -1                 # overridden by --channel-id CLI
    PRIORITY:    int = 1

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        return {
            "audio_device": field_enum(
                default="default",
                choices=_list_audio_devices(),
            ),
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

        # Audio params — populated from self.channel_config in _init()
        self._sample_rate:   int = 48000
        self._bit_depth:     int = 16
        self._channel_count: int = 2
        self._codec:         int = _CODEC_AAC_LC

        # Runtime state
        self._state:      str = "IDLE"
        self._session_id: int = 0

        # sounddevice RawOutputStream handle
        self._stream: sd.RawOutputStream | None = None

        # PyAV codec context for AAC decoding (None when PCM)
        self._av_codec_ctx: Any | None = None
        self._av_codec:     Any | None = None

    # ------------------------------------------------------------------
    # Readiness gate — channel_manager.module_ready only when stream is open
    # ------------------------------------------------------------------

    def _is_ready(self) -> bool:
        return self._stream is not None

    # ------------------------------------------------------------------
    # _init hook — called by BaseChannelModule after cfg.get() is dispatched
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """
        Read audio params from self.channel_config (populated by base from SDR).
        Opens pyav codec context and sounddevice RawOutputStream.

        NOTE: on_config_loaded() may arrive before or after _init() due to
        async bus delivery.  _open_stream() is called here with SDR params,
        and again in on_config_loaded() (guarded by _init_done) so the stream
        is always opened/reopened with the correct persisted device.
        """
        cfg = self.channel_config
        if cfg is not None:
            configs = cfg.get("av_channel", {}).get("audio_configs", [])
            if configs:
                c = configs[0]
                self._sample_rate   = c.get("sample_rate",   self._sample_rate)
                self._bit_depth     = c.get("bit_depth",     self._bit_depth)
                self._channel_count = c.get("channel_count", self._channel_count)
                self._codec         = c.get("codec",         self._codec)
                self.log.info(
                    "Audio config ch=%d: rate=%d depth=%d channels=%d codec=%s",
                    self.CHANNEL_ID, self._sample_rate, self._bit_depth,
                    self._channel_count, self._codec,
                )
            else:
                self.log.warning(
                    "_init: channel_id=%d has no audio_configs in SDR — using defaults",
                    self.CHANNEL_ID,
                )
        else:
            self.log.warning("_init: channel_config is None — using default audio params")

        self._open_av_codec()
        self._open_stream()

    def _cleanup(self) -> None:
        self._close_stream()
        self._close_av_codec()
        self._set_state("IDLE")

    # ------------------------------------------------------------------
    # Config callbacks
    # ------------------------------------------------------------------

    def on_config_loaded(self, config: dict) -> None:
        # super() merges config into self._config, sets _config_loaded=True
        # and calls _try_publish_ready().
        super().on_config_loaded(config)
        # Reopen stream with persisted device only after _init() has set
        # the correct sample_rate / channels from SDR.
        if self._init_done:
            self._open_stream()
        self._try_publish_ready()

    def on_config_changed(self, key: str, value: Any) -> None:
        super().on_config_changed(key, value)
        if key == "audio_device":
            self.log.info("audio_device changed to %r — reopening stream", value)
            self._open_stream()
        elif key == "max_unacked":
            self.log.info("max_unacked changed to %r", value)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("AA session shutdown — ch=%d reset", self.CHANNEL_ID)

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self._set_state("IDLE")
        self.log.info("Channel %d open", channel_id)

    def on_channel_close(self, channel_id: int) -> None:
        self._session_id = 0
        self._set_state("IDLE")
        self.log.info("Channel %d closed — session_id reset", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming frame by AA message_id."""
        result = decode_aa_frame(data)
        self.log.debug("Decoded frame header on ch=%d: %s", channel_id, result)
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
            self.log.debug(
                "Unhandled audio msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(body),
            )

    # ------------------------------------------------------------------
    # AA message handlers
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        max_unacked = self._config.get("max_unacked", 1)
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.OK
        resp.max_unacked  = max_unacked
        resp.configs.append(0)
        self.send_frame(_MSG_AV_CHANNEL_SETUP_RESPONSE, resp.SerializeToString())
        self._set_state("SETUP")
        self.log.info(
            "AVChannelSetupRequest ch=%d → AVChannelSetupResponse sent (max_unacked=%d)",
            self.CHANNEL_ID, max_unacked,
        )

    def _handle_open_request(self, body: bytes) -> None:
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString())
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_start_indication(self, body: bytes) -> None:
        """
        Parse AVChannelStartIndication to extract session_id.
        The phone sends this before the first media frame; session_id
        must be stored here so ACKs are valid from the very first frame.
        """
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
        self._session_id = 0
        self._set_state("STOPPED")
        self.log.info("AVChannelStopIndication ch=%d — session_id reset, state → STOPPED", self.CHANNEL_ID)

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")
        # body: field1=timestamp (fixed64), field2=audio data (bytes)
        ts_us, encoded = parse_media_with_timestamp(body)
        self.log.debug("MediaWithTimestamp ch=%d ts_us=%d len=%d", self.CHANNEL_ID, ts_us, len(encoded))
        self._send_media_ack()
        self._write_audio(encoded)

    def _handle_media(self, body: bytes) -> None:
        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")
        self._send_media_ack()
        self._write_audio(body)

    def _send_media_ack(self) -> None:
        ack = AVMediaAckIndication()
        ack.session_id = self._session_id
        ack.ack_count  = 1
        self.send_frame(_MSG_AV_CHANNEL_MEDIA_ACK, ack.SerializeToString())
        self.log.debug("MediaAck sent ch=%d session_id=%d", self.CHANNEL_ID, self._session_id)

    # ------------------------------------------------------------------
    # Audio pipeline
    # ------------------------------------------------------------------

    def _open_av_codec(self) -> None:
        """Open pyav codec context for AAC / AAC-LC ADTS; close it for PCM."""
        self._close_av_codec()
        if self._codec not in _AAC_CODECS:
            self.log.info("Codec %s — no pyav decoder needed", self._codec)
            return
        try:
            codec = av.codec.Codec("aac", "r")
            ctx   = codec.create()
            ctx.sample_rate = self._sample_rate
            ctx.channels    = self._channel_count
            self._av_codec_ctx = ctx
            self._av_codec     = codec
            self.log.info(
                "pyav AAC decoder opened: rate=%d channels=%d codec_enum=%s",
                self._sample_rate, self._channel_count, self._codec,
            )
        except Exception as exc:
            self.log.error("_open_av_codec: failed — %s", exc)
            self._av_codec_ctx = None

    def _close_av_codec(self) -> None:
        if self._av_codec_ctx is not None:
            try:
                self._av_codec_ctx.close()
            except Exception:
                pass
            self._av_codec_ctx = None
            self._av_codec     = None

    def _open_stream(self) -> None:
        """Open (or re-open) a sounddevice RawOutputStream with current audio params."""
        self._close_stream()
        device = self._config.get("audio_device", "default")
        dtype = {
            8:  "int8",
            16: "int16",
            24: "int24",
            32: "int32",
        }.get(self._bit_depth, "int16")
        sd_device = None if device == "default" else device
        try:
            self._stream = sd.RawOutputStream(
                samplerate=self._sample_rate,
                channels=self._channel_count,
                dtype=dtype,
                blocksize=1024,
                device=sd_device,
            )
            self._stream.start()
            self.log.info(
                "sounddevice RawOutputStream opened: device=%r rate=%d channels=%d dtype=%s",
                device, self._sample_rate, self._channel_count, dtype,
            )
        except sd.PortAudioError as exc:
            self.log.error("_open_stream: failed to open device %r — %s", device, exc)
            self._stream = None

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _write_audio(self, encoded: bytes) -> None:
        """Decode (if AAC / AAC-LC ADTS) and write raw PCM bytes to sounddevice stream."""
        if not encoded:
            return

        if self._codec in _AAC_CODECS:
            pcm = self._decode_aac(encoded)
        elif self._codec == _CODEC_PCM:
            pcm = encoded
        else:
            self.log.error(
                "_write_audio: unsupported codec %s on ch=%d — dropping frame",
                self._codec, self.CHANNEL_ID,
            )
            return

        if not pcm or self._stream is None:
            return
        try:
            self._stream.write(pcm)
        except sd.PortAudioError as exc:
            self.log.warning("sounddevice write error ch=%d — %s", self.CHANNEL_ID, exc)

    def _decode_aac(self, adts_frame: bytes) -> bytes:
        """Decode a single AAC / AAC-LC ADTS frame to interleaved signed-16 PCM."""
        if self._av_codec_ctx is None:
            return b""
        try:
            packet = av.Packet(adts_frame)
            pcm_chunks: list[bytes] = []
            for frame in self._av_codec_ctx.decode(packet):
                layout = "stereo" if self._channel_count > 1 else "mono"
                resampled = frame.to_ndarray(format="s16", layout=layout)
                pcm_chunks.append(resampled.tobytes())
            return b"".join(pcm_chunks)
        except Exception as exc:
            self.log.warning("AAC decode error ch=%d — %s", self.CHANNEL_ID, exc)
            return b""

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish("audio.state", {
            "channel_id": self.CHANNEL_ID,
            "state":      new_state,
        })
        self.log.info("audio.state ch=%d → %s", self.CHANNEL_ID, new_state)

    # ------------------------------------------------------------------
    # run() override: add session shutdown subscription then delegate
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        super().run()


# ---------------------------------------------------------------------------
# Audio device discovery (sounddevice)
# ---------------------------------------------------------------------------

def _list_audio_devices() -> list[str]:
    """Return available output device names via sounddevice, always starting with 'default'.

    Enumerates all PortAudio output devices (ALSA, PulseAudio, JACK, PipeWire…).
    Silently returns ['default'] if sounddevice is unavailable or no devices found.
    """
    try:
        devices = ["default"]
        for info in sd.query_devices():
            if info["max_output_channels"] > 0:
                name = info["name"]
                if name not in devices:
                    devices.append(name)
        return devices
    except Exception:
        return ["default"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    AudioModule().run()
