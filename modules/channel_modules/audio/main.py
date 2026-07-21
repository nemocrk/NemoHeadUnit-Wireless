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
                aa.frame.ch<ch_id>  {channel_id, message_id, encrypted, payload_hex}
                aa.session.shutdown  {}
                audio.sink.selected  {sink: str}   — from audio_manager
  Publishes   : channel_manager.module_ready  {name, priority}
                aa.frame.send        bytes  (via BaseChannelModule.send_frame)
                audio.state          {channel_id, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED
  Config keys : max_unacked    int   1           AVChannelSetupResponse.max_unacked
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import struct
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent
_CHANNEL_MODS = _HERE.parent
_MODULES      = _CHANNEL_MODS.parent
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

for _p in (_REPO_ROOT, _MODULES, _CHANNEL_MODS, _PROTO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import av                                      # noqa: E402

from shared.config_schema import field_int  # noqa: E402
from shared.proto_utils import parse_media_with_timestamp  # noqa: E402
from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports
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
from oaa.audio.AudioTypeEnum_pb2 import AudioType                                # noqa: E402
from oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest        # noqa: E402

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

_AAC_CODECS = (_CODEC_AAC_LC, _CODEC_AAC_LC_ADTS)

# Inline prebuffer threshold: accumulate ~100 ms of PCM before sending to pacat.
# Computed at runtime from sample_rate / channel_count / bit_depth; this is the
# fallback used when _init has not yet run (16-bit stereo 48 kHz → 9600 bytes).
_PREBUFFER_MS = 100
_PREBUFFER_BYTES_DEFAULT = int(48000 * 2 * 2 * _PREBUFFER_MS / 1000)  # 9600

# ---------------------------------------------------------------------------
# AudioModule
# ---------------------------------------------------------------------------


class AudioModule(BaseChannelModule):
    MODULE_NAME: str = "channel_audio"
    CHANNEL_ID:  int = -1
    PRIORITY:    int = 1

    def get_schema(self) -> dict:
        return {
            "max_unacked": field_int(
                default=1,
                min=1,
                max=16,
            ),
        }

    def __init__(self) -> None:
        super().__init__()
        self._sample_rate:   int = 48000
        self._bit_depth:     int = 16
        self._channel_count: int = 2
        self._codec          = _CODEC_AAC_LC
        self._audio_type:    int | None = None
        self._state:      str = "IDLE"
        self._session_id: int = 0
        self._proc:      subprocess.Popen | None = None
        self._proc_lock: threading.Lock          = threading.Lock()
        self._av_codec_ctx: Any | None = None
        self._av_codec:     Any | None = None
        self._media_debug_count: int = 0
        self._pcm_debug_count: int = 0
        # Selected sink — updated by audio.sink.selected from audio_manager.
        # None means use pacat default.
        self._selected_sink: str | None = None
        # AAC codec_data (AudioSpecificConfig, 2 bytes) received via
        # AV_MEDIA_INDICATION before actual audio frames.  Persists across
        # StopIndication — reset only on aa.session.shutdown or _cleanup.
        self._aac_codec_data: bytes | None = None
        # Inline PCM prebuffer: accumulate _prebuffer_threshold bytes before
        # writing to pacat.stdin to avoid underrun on stream start.
        self._prebuffer: list[bytes] = []
        self._prebuffer_bytes: int = 0
        self._prebuffer_threshold: int = _PREBUFFER_BYTES_DEFAULT

    def _is_ready(self) -> bool:
        return self._proc is not None

    def _init(self) -> None:
        cfg = self.channel_config
        if cfg is not None:
            configs = cfg.get("av_channel", {}).get("audio_configs", [])
            if configs:
                c = configs[0]
                self._audio_type = cfg.get("av_channel", {}).get("audio_type")
                self._sample_rate   = c.get("sample_rate",   self._sample_rate)
                self._bit_depth     = c.get("bit_depth",     self._bit_depth)
                self._channel_count = c.get("channel_count", self._channel_count)
                self._codec         = _normalise_audio_codec(c.get("codec"), self._audio_type)
                self.log.info(
                    "Audio config ch=%d: rate=%d depth=%d channels=%d codec=%s raw_config=%r audio_type=%r",
                    self.CHANNEL_ID, self._sample_rate, self._bit_depth,
                    self._channel_count, _codec_name(self._codec), c, self._audio_type,
                )
            else:
                self.log.warning(
                    "_init: channel_id=%d has no audio_configs in SDR — using defaults",
                    self.CHANNEL_ID,
                )
        else:
            self.log.warning("_init: channel_config is None — using default audio params")
        # Recompute prebuffer threshold from actual stream params.
        bytes_per_sec = self._sample_rate * self._channel_count * (self._bit_depth // 8)
        self._prebuffer_threshold = int(bytes_per_sec * _PREBUFFER_MS / 1000)
        self.log.debug(
            "_init: prebuffer_threshold=%d bytes (%d ms) ch=%d",
            self._prebuffer_threshold, _PREBUFFER_MS, self.CHANNEL_ID,
        )
        self._open_av_codec()
        self._open_stream()

    def _cleanup(self) -> None:
        self._close_stream()
        self._close_av_codec()
        self._aac_codec_data = None
        self._prebuffer.clear()
        self._prebuffer_bytes = 0
        self._set_state("IDLE")

    def on_config_loaded(self, config: dict) -> None:
        super().on_config_loaded(config)
        if self._init_done:
            self._open_stream()
        self._try_publish_ready()

    def on_config_changed(self, key: str, value: Any) -> None:
        super().on_config_changed(key, value)
        if key == "max_unacked":
            self.log.info("max_unacked changed to %r", value)

    def on_audio_sink_selected(self, topic: str, payload: dict) -> None:
        """Handle audio.sink.selected published by audio_manager.

        Reopens the pacat stream on the newly selected sink if it changed.
        """
        sink = payload.get("sink", "default")
        if sink == "default":
            sink = None
        if sink == self._selected_sink:
            return
        self.log.info(
            "audio.sink.selected ch=%d: %r → %r — reopening stream",
            self.CHANNEL_ID, self._selected_sink, sink,
        )
        self._selected_sink = sink
        if self._proc is not None:
            self._open_stream()

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._session_id = 0
        self._aac_codec_data = None
        self._prebuffer.clear()
        self._prebuffer_bytes = 0
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
        self._prebuffer.clear()
        self._prebuffer_bytes = 0
        self._set_state("IDLE")
        self.log.info("Channel %d closed — session_id reset", channel_id)

    def on_frame(self, channel_id: int, message_id: int, encrypted: bool, data: bytes) -> None:
        """Dispatch incoming frame by AA message_id (already extracted by tcp_server)."""
        self.log.debug("on_frame ch=%d msg=0x%04x enc=%s len=%d", channel_id, message_id, encrypted, len(data))

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(data)
        elif message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(data)
        elif message_id == _MSG_AV_CHANNEL_START_INDICATION:
            self._handle_start_indication(data)
        elif message_id == _MSG_AV_CHANNEL_STOP_INDICATION:
            self._handle_stop_indication(data)
        elif message_id == _MSG_AV_CHANNEL_AV_MEDIA_INDICATION:
            self._handle_media(data)
        elif message_id == _MSG_AV_CHANNEL_AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            self._handle_media_with_timestamp(data)
        else:
            self.log.debug(
                "Unhandled audio msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(data),
            )

    # ------------------------------------------------------------------
    # AA message handlers
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        max_unacked = self._config.get("max_unacked", 1)
        req = AVChannelSetupRequest()
        req.ParseFromString(body)
        self._codec         = _normalise_audio_codec(req.media_codec_type, self._audio_type)
        self.log.info(
            "Audio config after AVChannelSetupRequest ch=%d: rate=%d depth=%d channels=%d codec=%s audio_type=%r",
            self.CHANNEL_ID, self._sample_rate, self._bit_depth,
            self._channel_count, _codec_name(self._codec), self._audio_type,
        )
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
        # codec_data persists across StopIndication: AA reuses the same ASC
        # when resuming the stream, so we keep it to avoid a missing-codec_data
        # window if on_media_indication does not re-arrive.
        # Reset only on aa.session.shutdown or _cleanup.
        self._prebuffer.clear()
        self._prebuffer_bytes = 0
        self._set_state("STOPPED")
        self.log.info(
            "AVChannelStopIndication ch=%d — session_id/prebuffer reset, state → STOPPED"
            " (codec_data preserved: %s)",
            self.CHANNEL_ID,
            self._aac_codec_data.hex() if self._aac_codec_data else "None",
        )

    def _handle_media_with_timestamp(self, body: bytes) -> None:
        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")
        ts_us, encoded = parse_media_with_timestamp(body)
        self._log_media_sample("MediaWithTimestamp", body, encoded, ts_us)
        self._send_media_ack()
        self._write_audio(encoded)

    def _handle_media(self, body: bytes) -> None:
        """Handle AV_MEDIA_INDICATION.

        AA sends one codec_data frame (AudioSpecificConfig, exactly 2 bytes)
        before actual audio data.  The frame layout is:

            [8 bytes timestamp header][2 bytes ASC]

        We detect it by stripping the 8-byte header and checking the remaining
        payload length.  The ASC is stored and prepended to every subsequent
        AAC_LC frame fed to pyav so that the decoder can initialise correctly.
        If another codec_data arrives (e.g. after a stream restart) we simply
        update the stored value.
        """
        _TS_HEADER = 8  # bytes — same layout as AV_MEDIA_WITH_TIMESTAMP_INDICATION

        if len(body) > _TS_HEADER:
            payload = body[_TS_HEADER:]
        else:
            payload = body

        if len(payload) == 2 and self._codec in _AAC_CODECS:
            # This is an AudioSpecificConfig (codec_data) frame.
            if self._aac_codec_data != payload:
                self._aac_codec_data = payload
                self.log.info(
                    "AV_MEDIA_INDICATION: codec_data received ch=%d asc=%s",
                    self.CHANNEL_ID, payload.hex(),
                )
            else:
                self.log.debug(
                    "AV_MEDIA_INDICATION: codec_data unchanged ch=%d asc=%s",
                    self.CHANNEL_ID, payload.hex(),
                )
            # ACK the codec_data frame and do not pass it to the audio pipeline.
            self._send_media_ack()
            return

        # Regular media frame arriving via AV_MEDIA_INDICATION (uncommon but possible).
        self.log.debug(
            "AV_MEDIA_INDICATION ch=%d body=%s — ACK sent, parsing codec",
            self.CHANNEL_ID, body.hex(),
        )
        if self._state not in ("OPEN", "PLAYING"):
            self._set_state("PLAYING")
        self._send_media_ack()
        self._log_media_sample("Media", body, payload, 0)
        self._write_audio(payload)

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
        self._close_av_codec()
        if self._codec not in _AAC_CODECS:
            self.log.info("Codec %s — no pyav decoder needed", _codec_name(self._codec))
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
                self._sample_rate, self._channel_count, _codec_name(self._codec),
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
        self._close_stream()
        # Use the sink selected by audio_manager via audio.sink.selected bus event.
        # None / "default" → let pacat use the PulseAudio default sink.
        sink = self._selected_sink
        fmt = {8: "u8", 16: "s16le", 24: "s24le", 32: "s32le"}.get(self._bit_depth, "s16le")
        candidates: list[str | None] = []
        if sink and sink != "default":
            candidates.append(sink)
        candidates.append(None)  # PulseAudio/PipeWire default
        for candidate in candidates:
            cmd = [
                "pacat", "--playback",
                f"--format={fmt}",
                f"--channels={self._channel_count}",
                f"--rate={self._sample_rate}",
                f"--client-name=NemoHU",
                f"--stream-name=ch{self.CHANNEL_ID}",
            ]
            if candidate is not None:
                cmd.append(f"--device={candidate}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._proc = proc
                self.log.info(
                    "pacat spawned: device=%r rate=%d channels=%d fmt=%s pid=%d name=NemoHU/ch%d",
                    candidate or "default", self._sample_rate, self._channel_count, fmt,
                    proc.pid, self.CHANNEL_ID,
                )
                return
            except Exception as exc:
                self.log.warning("_open_stream: device=%r failed — %s", candidate, exc)
        self.log.error("_open_stream: all candidates exhausted")
        self._proc = None

    def _close_stream(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None

    def _write_audio(self, encoded: bytes) -> None:
        if not encoded:
            return
        if self._codec in _AAC_CODECS:
            pcm = self._decode_aac(encoded)
        elif self._codec == _CODEC_PCM:
            pcm = encoded
        else:
            self.log.error(
                "_write_audio: unsupported codec %r on ch=%d — dropping frame len=%d head=%s",
                self._codec, self.CHANNEL_ID, len(encoded), encoded[:16].hex(),
            )
            return
        if not pcm or self._proc is None:
            return
        self._log_pcm_sample(pcm)

        # Inline prebuffer: accumulate PCM until threshold is reached, then
        # flush everything at once to avoid underrun on stream start.
        if self._prebuffer_bytes < self._prebuffer_threshold:
            self._prebuffer.append(pcm)
            self._prebuffer_bytes += len(pcm)
            if self._prebuffer_bytes < self._prebuffer_threshold:
                self.log.debug(
                    "prebuffer ch=%d accumulated=%d threshold=%d",
                    self.CHANNEL_ID, self._prebuffer_bytes, self._prebuffer_threshold,
                )
                return
            # Threshold reached — flush the whole buffer in one write.
            pcm = b"".join(self._prebuffer)
            self._prebuffer.clear()
            self._prebuffer_bytes = 0  # reset accounting after flush
            self.log.info(
                "prebuffer ch=%d threshold reached (%d bytes) — flushing to pacat",
                self.CHANNEL_ID, len(pcm),
            )

        try:
            with self._proc_lock:
                self._proc.stdin.write(pcm)
                self._proc.stdin.flush()
        except BrokenPipeError:
            self.log.warning("pacat stdin broken on ch=%d — closing stream", self.CHANNEL_ID)
            self._close_stream()
        except Exception as exc:
            self.log.warning("pacat write error ch=%d — %s", self.CHANNEL_ID, exc)

    def _decode_aac(self, adts_frame: bytes) -> bytes:
        """Decode one AAC frame to raw s16le PCM.

        For AAC_LC (raw, no ADTS header), pyav needs the AudioSpecificConfig
        (ASC) to initialise the decoder.  We prepend the stored codec_data to
        every frame.  For AAC_LC_ADTS the ADTS header already carries the
        necessary info, so codec_data is not prepended.
        """
        if self._av_codec_ctx is None:
            return b""

        feed = adts_frame
        if self._codec == _CODEC_AAC_LC and self._aac_codec_data:
            feed = self._aac_codec_data + adts_frame

        try:
            packet = av.Packet(feed)
            pcm_chunks: list[bytes] = []
            for frame in self._av_codec_ctx.decode(packet):
                layout = "stereo" if self._channel_count > 1 else "mono"
                resampled = frame.to_ndarray(format="s16", layout=layout)
                pcm_chunks.append(resampled.tobytes())
            return b"".join(pcm_chunks)
        except Exception as exc:
            self.log.warning("AAC decode error ch=%d — %s", self.CHANNEL_ID, exc)
            return b""

    def _log_pcm_sample(self, pcm: bytes) -> None:
        self._pcm_debug_count += 1
        if self._pcm_debug_count > 8:
            return
        stats = _pcm_s16le_stats(pcm)
        if stats is None:
            self.log.debug(
                "PCM write ch=%d len=%d codec=%s stats=unavailable head=%s",
                self.CHANNEL_ID, len(pcm), _codec_name(self._codec), pcm[:16].hex(),
            )
            return
        self.log.info(
            "PCM write ch=%d len=%d codec=%s samples=%d peak=%d rms=%d zero_ratio=%.3f head=%s",
            self.CHANNEL_ID,
            len(pcm),
            _codec_name(self._codec),
            stats["samples"],
            stats["peak"],
            stats["rms"],
            stats["zero_ratio"],
            pcm[:16].hex(),
        )

    def _log_media_sample(self, label: str, body: bytes, encoded: bytes, ts_us: int) -> None:
        self._media_debug_count += 1
        if self._media_debug_count <= 5 or not encoded:
            self.log.debug(
                "%s ch=%d ts_us=%d body_len=%d payload_len=%d codec=%s body_head=%s payload_head=%s",
                label,
                self.CHANNEL_ID,
                ts_us,
                len(body),
                len(encoded),
                _codec_name(self._codec),
                body[:16].hex(),
                encoded[:16].hex(),
            )

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish("audio.state", {
            "channel_id": self.CHANNEL_ID,
            "state":      new_state,
        })
        self.log.info("audio.state ch=%d → %s", self.CHANNEL_ID, new_state)

    def run(self) -> None:
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        self.bus.subscribe("audio.sink.selected", self.on_audio_sink_selected)
        super().run()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _normalise_audio_codec(raw: Any, audio_type: Any) -> int:
    """Return a numeric MediaCodecType for the SDR audio config."""
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw:
        by_name = {
            "MEDIA_CODEC_AUDIO_PCM": _CODEC_PCM,
            "MEDIA_CODEC_AUDIO_AAC_LC": _CODEC_AAC_LC,
            "MEDIA_CODEC_AUDIO_AAC_LC_ADTS": _CODEC_AAC_LC_ADTS,
        }
        if raw in by_name:
            return by_name[raw]
    if audio_type in (AudioType.MEDIA, AudioType.SPEECH, AudioType.SYSTEM):
        return _CODEC_PCM
    return _CODEC_PCM


def _pcm_s16le_stats(pcm: bytes) -> dict[str, float | int] | None:
    if len(pcm) < 2:
        return None
    usable = len(pcm) - (len(pcm) % 2)
    samples = [sample for (sample,) in struct.iter_unpack("<h", pcm[:usable])]
    if not samples:
        return None
    abs_values = [abs(sample) for sample in samples]
    square_sum = sum(sample * sample for sample in samples)
    return {
        "samples": len(samples),
        "peak": max(abs_values),
        "rms": int((square_sum / len(samples)) ** 0.5),
        "zero_ratio": sum(1 for sample in samples if sample == 0) / len(samples),
    }


def _codec_name(codec: Any) -> str:
    names = {
        _CODEC_PCM: "MEDIA_CODEC_AUDIO_PCM",
        _CODEC_AAC_LC: "MEDIA_CODEC_AUDIO_AAC_LC",
        _CODEC_AAC_LC_ADTS: "MEDIA_CODEC_AUDIO_AAC_LC_ADTS",
    }
    return names.get(codec, repr(codec))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    AudioModule().run()
