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
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response      (auto via ConfigClient)
                config.changed       (auto via ConfigClient)
                oaa.channel.open     {channel_id, ...}
                oaa.channel.close    {channel_id}
                oaa.frame.<ch_id>    raw bytes
                aa.session.shutdown  {}
  Publishes   : system.module_ready  {name, priority}
                system.ready         {name, priority}
                aa.frame.send        {channel_id, flags, payload_hex}
                audio.state          {channel_id, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED
  Config keys : audio_device  enum  "default"  sounddevice output device name

Flow:
  1. BaseChannelModule parses CLI and populates self.channel_config from SDR.
  2. On system.start: cfg.get(schema) triggers config load + _init().
  3. _init(): read codec params from self.channel_config, open sounddevice
     RawOutputStream, open pyav codec context if AAC-LC ADTS.
  4. BaseChannelModule handles oaa.channel.open/close and oaa.frame.<id>.
  5. on_frame(): decode AA frame header, dispatch by message_id, write PCM.
  6. on_config_changed("audio_device"): re-open stream on the fly.
  7. on_aa_session_shutdown: reset state to IDLE.

Readiness:
  system.ready is emitted lazily by BaseChannelModule._try_publish_ready()
  once _init_done AND _config_loaded AND _is_ready() AND channel_config is
  not None are all True.  _is_ready() returns True only when the sounddevice
  stream is open, so channel_manager never unblocks the phone before audio
  is operational.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Any

_HERE         = Path(__file__).parent          # v2/modules/channel_modules/audio/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/

for _p in (_V2, _MODULES, _CHANNEL_MODS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import sounddevice as sd                       # noqa: E402  (python-sounddevice)
import av                                      # noqa: E402  (PyAV / FFmpeg)

from shared.config_schema import field_enum    # noqa: E402
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
_MSG_MEDIA                      = 0x0003
_MSG_MEDIA_ACK                  = 0x0002

_CODEC_AAC = "MEDIA_CODEC_AUDIO_AAC_LC_ADTS"
_CODEC_PCM = "MEDIA_CODEC_AUDIO_PCM"

# ---------------------------------------------------------------------------
# AudioModule
# ---------------------------------------------------------------------------


class AudioModule(BaseChannelModule):
    """
    Single-channel OAA audio module with sounddevice (PortAudio) output.

    One process per channel; all parameters (channel_id, SDR bytes) are
    provided via CLI by channel_manager at spawn time and parsed by
    BaseChannelModule into self.CHANNEL_ID and self.channel_config.
    Codec parameters are read from self.channel_config in _init().
    AAC-LC ADTS frames are decoded via pyav before writing PCM to the stream.
    Audio errors are logged but do not crash the module.

    system.ready is gated on the sounddevice stream being open (_is_ready)
    AND on self.channel_config being populated (base hard-fail guard).
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
        self._codec:         str = _CODEC_AAC

        # Runtime state
        self._state:      str = "IDLE"
        self._session_id: int = 0

        # sounddevice RawOutputStream handle
        self._stream: sd.RawOutputStream | None = None

        # PyAV codec context for AAC decoding (None when PCM)
        self._av_codec_ctx: Any | None = None
        self._av_codec:     Any | None = None

    # ------------------------------------------------------------------
    # Readiness gate — system.ready only when stream is open
    # ------------------------------------------------------------------

    def _is_ready(self) -> bool:
        return self._stream is not None

    # ------------------------------------------------------------------
    # _init hook — called by BaseChannelModule._on_system_start after cfg.get
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """
        Read audio params from self.channel_config (populated by base from SDR).
        Opens sounddevice RawOutputStream and pyav codec context.
        Falls back to defaults when channel_config is None (base will also
        block system.ready in that case, so this path is defensive only).
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
            self.log.warning(
                "_init: channel_config is None — using default audio params"
            )
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
        # and calls _try_publish_ready() — which checks _is_ready() after
        # we re-open the stream below.
        super().on_config_loaded(config)
        self._open_stream()  # reopen with persisted device
        self._try_publish_ready()  # re-check now that stream may be open

    def on_config_changed(self, key: str, value: Any) -> None:
        super().on_config_changed(key, value)
        if key == "audio_device":
            self.log.info("audio_device changed to %r — reopening stream", value)
            self._open_stream()

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
        self._set_state("IDLE")
        self.log.info("Channel %d closed", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming frame by AA message_id."""
        result = _decode_frame_header(data)
        if result is None:
            self.log.error("on_frame: malformed payload on ch=%d — dropping", channel_id)
            return

        message_id, body = result

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(body)
        elif message_id == _MSG_AV_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(body)
        elif message_id == _MSG_MEDIA_WITH_TIMESTAMP:
            self._handle_media_with_timestamp(body)
        elif message_id == _MSG_MEDIA:
            self._handle_media(body)
        elif message_id == _MSG_AV_CHANNEL_STOP_INDICATION:
            self.log.info("AVChannelStopIndication on ch=%d", channel_id)
            self._set_state("STOPPED")
        else:
            self.log.debug(
                "Unhandled audio msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(body),
            )

    # ------------------------------------------------------------------
    # AA message handlers
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        proto_body = b"\x08\x00\x10\x01"
        frame = _encode_frame(self.CHANNEL_ID, _MSG_AV_CHANNEL_SETUP_RESPONSE, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self._set_state("SETUP")
        self.log.info("AVChannelSetupRequest ch=%d → AVChannelSetupResponse sent", self.CHANNEL_ID)

    def _handle_open_request(self, body: bytes) -> None:
        proto_body = b"\x08\x00"
        frame = _encode_frame(self.CHANNEL_ID, _MSG_AV_CHANNEL_OPEN_RESPONSE, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self._set_state("OPEN")
        self.log.info("AVChannelOpenRequest ch=%d → AVChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        ts_us, encoded = _parse_media_with_timestamp(body)
        session_id     = _parse_session_id(body) or self._session_id
        if session_id:
            self._session_id = session_id

        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")

        self._send_media_ack()
        self._write_audio(encoded)

    def _handle_media(self, body: bytes) -> None:
        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")
        self._send_media_ack()
        self._write_audio(body)

    def _send_media_ack(self) -> None:
        proto_body = (
            _encode_varint_field(1, self._session_id)
            + _encode_varint_field(2, 1)
        )
        frame = _encode_frame(self.CHANNEL_ID, _MSG_MEDIA_ACK, proto_body)
        self.bus.publish("aa.frame.send", frame)
        self.log.debug("MediaAck sent ch=%d session_id=%d", self.CHANNEL_ID, self._session_id)

    # ------------------------------------------------------------------
    # Audio pipeline
    # ------------------------------------------------------------------

    def _open_av_codec(self) -> None:
        """Open pyav codec context if codec is AAC, close it otherwise."""
        self._close_av_codec()
        if self._codec != _CODEC_AAC:
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
                "pyav AAC decoder opened: rate=%d channels=%d",
                self._sample_rate, self._channel_count,
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
        """Decode (if AAC) and write raw PCM bytes to sounddevice stream."""
        if not encoded:
            return
        pcm = self._decode_aac(encoded) if self._codec == _CODEC_AAC else encoded
        if not pcm or self._stream is None:
            return
        try:
            self._stream.write(pcm)
        except sd.PortAudioError as exc:
            self.log.warning("sounddevice write error ch=%d — %s", self.CHANNEL_ID, exc)

    def _decode_aac(self, adts_frame: bytes) -> bytes:
        """Decode a single AAC-LC ADTS frame to interleaved signed-16 PCM."""
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
# Proto frame helpers  (no proto dependency — manual varint)
# ---------------------------------------------------------------------------

def _decode_frame_header(data: bytes) -> tuple[int, bytes] | None:
    if len(data) < 2:
        return None
    message_id = struct.unpack_from(">H", data, 0)[0]
    return message_id, data[2:]


def _encode_frame(channel_id: int, message_id: int, proto_body: bytes) -> dict:
    payload = struct.pack(">H", message_id) + proto_body
    return {
        "channel_id":  channel_id,
        "flags":       _FLAG_FULL,
        "payload_hex": payload.hex(),
    }


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


def _parse_media_with_timestamp(body: bytes) -> tuple[int, bytes]:
    """Manual proto parse: field 1=timestamp (fixed64), field 2=data (bytes)."""
    ts_us = 0
    data  = b""
    pos   = 0
    while pos < len(body):
        tag_byte     = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07
        if field_number == 1 and wire_type == 1:
            if pos + 8 > len(body):
                break
            ts_us = struct.unpack_from("<Q", body, pos)[0]
            pos += 8
        elif field_number == 2 and wire_type == 2:
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
    return ts_us, data


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
