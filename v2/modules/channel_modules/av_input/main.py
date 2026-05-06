"""
NemoHeadUnit-Wireless v2 — channel_modules/av_input

AVInput channel module: captures PCM audio from the HU microphone and
streams it upstream to the connected Android Auto phone.

This is the inverse of the audio channel: instead of receiving media from
the phone and playing it locally, this module records from a local input
device and sends raw PCM frames to the phone via AV_MEDIA_WITH_TIMESTAMP.

Launch example (by channel_manager):
    python -m channel_modules.av_input.main \\
        --module-name  channel_av_input_7 \\
        --channel-id   7 \\
        --sdr-bytes-hex <hex string from ServiceDiscoveryResponse>

---
Module contract:
  Name        : <--module-name>   (e.g. channel_av_input_7)
  Priority    : 1
  Channel ID  : 7                 (overridden by --channel-id CLI)
  SDR bytes   : <--sdr-bytes-hex> parsed by base into self.channel_config
  Subscribes  : channel_manager.module_start
                channel_manager.module_stop
                config.response      (auto via ConfigClient)
                config.changed       (auto via ConfigClient)
                aa.channel.open     {channel_id, ...}
                aa.channel.close    {channel_id}
                aa.frame.ch<ch_id>  raw bytes
                aa.session.shutdown {}
  Publishes   : channel_manager.module_ready  {name, priority}
                aa.frame.send        bytes  (via BaseChannelModule.send_frame)
                av_input.state       {channel_id, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED
                av_input.mic_started {channel_id}
                av_input.mic_stopped {channel_id}
  Config keys : mic_device    enum  "default"  sounddevice input device name
                max_unacked   int   1           AVChannelSetupResponse.max_unacked

---
AA channel lifecycle:

  SETUP_REQUEST         → AVChannelSetupResponse(OK, max_unacked)  state → SETUP
  CHANNEL_OPEN_REQUEST  → ChannelOpenResponse(0)                   state → OPEN
  INPUT_OPEN_REQ(T)     → AVInputOpenResponse(session=0, value=0)
                          _start_stream()                           state → PLAYING
                          publishes av_input.mic_started
  INPUT_OPEN_REQ(F)     → AVInputOpenResponse(session=0, value=0)
                          _stop_stream()                            state → STOPPED
                          publishes av_input.mic_stopped
  ACK_INDICATION        → log debug, no-op (phone acks our frames)
  aa.session.shutdown   → _stop_stream(), reset                    state → IDLE
  aa.channel.close      → if capturing: _stop_stream() + mic_stopped

---
Capture pipeline:

  sd.RawInputStream(callback=_mic_callback) opened in _start_stream().
  Callback runs in sounddevice internal thread — no heavy I/O allowed.
  Each callback invocation:
    1. Reads raw PCM bytes from indata.
    2. Gets current monotonic timestamp in microseconds.
    3. Calls build_media_with_timestamp(ts_us, pcm) from proto_utils.
    4. Calls self.send_frame(AV_MEDIA_WITH_TIMESTAMP, payload).
  Guard: callback is a no-op when self._capturing is False.

---
Outgoing frame convention:
  Use self.send_frame(message_id, proto_body) for ALL outgoing AA frames.
  For AV_MEDIA_WITH_TIMESTAMP the proto_body is a raw packed buffer
  (not a serialised protobuf) built by build_media_with_timestamp().
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent          # v2/modules/channel_modules/av_input/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/
_PROTOS       = _V2 / "protos"                 # v2/protos/

for _p in (_V2, _MODULES, _CHANNEL_MODS, _PROTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import sounddevice as sd                       # noqa: E402

from shared.config_schema import field_enum, field_int           # noqa: E402
from shared.proto_utils import (                                 # noqa: E402
    build_media_with_timestamp,
    decode_aa_frame,
)
from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports
# ---------------------------------------------------------------------------
from oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage                    # noqa: E402
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage                   # noqa: E402
from oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse        # noqa: E402
from oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus               # noqa: E402
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse         # noqa: E402
from oaa.av.AVInputOpenRequestMessage_pb2 import AVInputOpenRequest                # noqa: E402
from oaa.av.AVInputOpenResponseMessage_pb2 import AVInputOpenResponse              # noqa: E402
from oaa.common.StatusEnum_pb2 import Status                                       # noqa: E402

# ---------------------------------------------------------------------------
# AA message ID aliases
# ---------------------------------------------------------------------------
_MSG_AV_CHANNEL_SETUP_REQUEST   = AVChannelMessage.SETUP_REQUEST
_MSG_AV_CHANNEL_SETUP_RESPONSE  = AVChannelMessage.SETUP_RESPONSE
_MSG_CHANNEL_OPEN_REQUEST       = ControlMessage.CHANNEL_OPEN_REQUEST
_MSG_CHANNEL_OPEN_RESPONSE      = ControlMessage.CHANNEL_OPEN_RESPONSE
_MSG_AV_INPUT_OPEN_REQUEST      = AVChannelMessage.AV_INPUT_OPEN_REQUEST
_MSG_AV_INPUT_OPEN_RESPONSE     = AVChannelMessage.AV_INPUT_OPEN_RESPONSE
_MSG_AV_MEDIA_WITH_TIMESTAMP    = AVChannelMessage.AV_MEDIA_WITH_TIMESTAMP_INDICATION
_MSG_AV_MEDIA_ACK               = AVChannelMessage.AV_MEDIA_ACK_INDICATION


# ---------------------------------------------------------------------------
# AVInputModule
# ---------------------------------------------------------------------------

class AVInputModule(BaseChannelModule):
    """
    AA AVInput channel module — HU microphone capture upstream to phone.

    Captures PCM 48kHz/16-bit/mono from a local sounddevice input device
    and streams it to the phone via AV_MEDIA_WITH_TIMESTAMP frames.

    The phone controls capture lifecycle via INPUT_OPEN_REQUEST(open=True/False).
    The module responds with AVInputOpenResponse and starts/stops the
    sounddevice RawInputStream accordingly.

    session_ is always 0 (AVInput does not use AVChannelStartIndication).
    max_unacked is updated from INPUT_OPEN_REQUEST if the phone provides it;
    it is also configurable via the config key "max_unacked" used in
    AVChannelSetupResponse.
    """

    MODULE_NAME: str = "channel_av_input"
    CHANNEL_ID:  int = -1
    PRIORITY:    int = 1

    # Default audio params for AVInput (PCM 48kHz mono 16-bit)
    _SAMPLE_RATE:   int = 48000
    _BIT_DEPTH:     int = 16
    _CHANNEL_COUNT: int = 1

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        return {
            "mic_device": field_enum(
                default="default",
                choices=_list_mic_devices(),
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

        self._state:       str  = "IDLE"
        self._capturing:   bool = False
        self._max_unacked: int  = 1

        # sounddevice RawInputStream handle (opened on INPUT_OPEN_REQUEST)
        self._stream: sd.RawInputStream | None = None

    # ------------------------------------------------------------------
    # Readiness gate
    # ------------------------------------------------------------------

    def _is_ready(self) -> bool:
        """
        AVInput is always ready at startup — the mic stream is opened
        only on demand (INPUT_OPEN_REQUEST), not at init time.
        """
        return True

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """
        Read audio params from self.channel_config (populated by base from SDR).
        Audio params are fixed at 48kHz/mono/16-bit for AVInput; the SDR
        av_input_channel config is parsed here for logging purposes.
        """
        cfg = self.channel_config
        if cfg is not None:
            av_input = cfg.get("av_input_channel", {})
            audio_cfg = av_input.get("audio_config", {})
            if audio_cfg:
                self.log.info(
                    "_init: av_input_channel ch=%d rate=%s depth=%s channels=%s",
                    self.CHANNEL_ID,
                    audio_cfg.get("sample_rate", self._SAMPLE_RATE),
                    audio_cfg.get("bit_depth",   self._BIT_DEPTH),
                    audio_cfg.get("channel_count", self._CHANNEL_COUNT),
                )
            else:
                self.log.info("_init: no audio_config in SDR for ch=%d — using defaults", self.CHANNEL_ID)
        else:
            self.log.warning("_init: channel_config is None for ch=%d", self.CHANNEL_ID)

    def _cleanup(self) -> None:
        """Stop capture and release stream on module_stop."""
        self._stop_stream(publish=False)
        self._set_state("IDLE")

    # ------------------------------------------------------------------
    # Config callbacks
    # ------------------------------------------------------------------

    def on_config_changed(self, key: str, value: Any) -> None:
        super().on_config_changed(key, value)
        if key == "mic_device":
            self.log.info("mic_device changed to %r", value)
            if self._capturing:
                self._stop_stream(publish=False)
                self._start_stream()
        elif key == "max_unacked":
            self.log.info("max_unacked changed to %r", value)
            self._max_unacked = int(value)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        """AA session ended — stop capture and reset."""
        self._stop_stream(publish=True)
        self._set_state("IDLE")
        self.log.info("AA session shutdown — ch=%d reset", self.CHANNEL_ID)

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self._capturing = False
        self._set_state("IDLE")
        self.log.info("Channel %d open", channel_id)

    def on_channel_close(self, channel_id: int) -> None:
        """Stop capture if active, then reset."""
        if self._capturing:
            self._stop_stream(publish=True)
        self._set_state("IDLE")
        self.log.info("Channel %d closed", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming AA frame by message_id."""
        result = decode_aa_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload on ch=%d — dropping", channel_id)
            return

        message_id, body = result

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(body)
        elif message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(body)
        elif message_id == _MSG_AV_INPUT_OPEN_REQUEST:
            self._handle_input_open_request(body)
        elif message_id == _MSG_AV_MEDIA_ACK:
            self.log.debug("ACK_INDICATION ch=%d — no-op", channel_id)
        else:
            self.log.debug(
                "Unhandled av_input msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(body),
            )

    # ------------------------------------------------------------------
    # AA message handlers — standard AVChannel handshake
    # ------------------------------------------------------------------

    def _handle_setup_request(self, body: bytes) -> None:
        """Send AVChannelSetupResponse and transition to SETUP."""
        max_unacked = self._config.get("max_unacked", 1)
        self._max_unacked = max_unacked
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

    def _handle_open_request(self, body: bytes) -> None:
        """Send ChannelOpenResponse and transition to OPEN."""
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString())
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_input_open_request(self, body: bytes) -> None:
        """
        Parse AVInputOpenRequest and start or stop mic capture.

        Fields read:
          open        (bool)  — True = start capture, False = stop
          max_unacked (int)   — optional, update if present
          anc, ec     (bool)  — logged, not implemented

        Always responds with AVInputOpenResponse(session=0, value=0).
        """
        try:
            req = AVInputOpenRequest()
            req.ParseFromString(body)
        except Exception as exc:
            self.log.warning("AVInputOpenRequest parse error ch=%d — %s", self.CHANNEL_ID, exc)
            return

        if req.HasField("max_unacked") if hasattr(req, "HasField") else req.max_unacked:
            self._max_unacked = req.max_unacked

        self.log.info(
            "AVInputOpenRequest ch=%d open=%s anc=%s ec=%s max_unacked=%d",
            self.CHANNEL_ID, req.open, req.anc, req.ec, self._max_unacked,
        )

        # Send response before touching stream state
        resp = AVInputOpenResponse()
        resp.session = 0
        resp.value   = 0
        self.send_frame(_MSG_AV_INPUT_OPEN_RESPONSE, resp.SerializeToString())

        if req.open:
            self._start_stream()
        else:
            self._stop_stream(publish=True)

    # ------------------------------------------------------------------
    # Capture pipeline
    # ------------------------------------------------------------------

    def _start_stream(self) -> None:
        """Open sounddevice RawInputStream with callback and start capture."""
        self._stop_stream(publish=False)
        device = self._config.get("mic_device", "default")
        sd_device = None if device == "default" else device
        try:
            self._stream = sd.RawInputStream(
                samplerate=self._SAMPLE_RATE,
                channels=self._CHANNEL_COUNT,
                dtype="int16",
                blocksize=1024,
                device=sd_device,
                callback=self._mic_callback,
            )
            self._stream.start()
            self._capturing = True
            self._set_state("PLAYING")
            self.bus.publish("av_input.mic_started", {"channel_id": self.CHANNEL_ID})
            self.log.info(
                "Mic capture started: device=%r rate=%d channels=%d",
                device, self._SAMPLE_RATE, self._CHANNEL_COUNT,
            )
        except sd.PortAudioError as exc:
            self.log.error("_start_stream: failed to open device %r — %s", device, exc)
            self._stream = None
            self._capturing = False

    def _stop_stream(self, publish: bool = True) -> None:
        """Stop and close sounddevice RawInputStream."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._capturing:
            self._capturing = False
            self._set_state("STOPPED")
            if publish:
                self.bus.publish("av_input.mic_stopped", {"channel_id": self.CHANNEL_ID})
                self.log.info("Mic capture stopped ch=%d", self.CHANNEL_ID)

    def _mic_callback(
        self,
        indata: bytes,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """
        sounddevice RawInputStream callback — runs in sounddevice internal thread.

        Kept intentionally minimal: no blocking I/O, no heavy processing.
        Guards against spurious calls when not capturing.
        """
        if not self._capturing:
            return
        if status:
            self.log.debug("_mic_callback status ch=%d: %s", self.CHANNEL_ID, status)
        ts_us  = time.monotonic_ns() // 1000
        payload = build_media_with_timestamp(ts_us, bytes(indata))
        self.send_frame(_MSG_AV_MEDIA_WITH_TIMESTAMP, payload)

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish("av_input.state", {
            "channel_id": self.CHANNEL_ID,
            "state":      new_state,
        })
        self.log.info("av_input.state ch=%d → %s", self.CHANNEL_ID, new_state)

    # ------------------------------------------------------------------
    # run() override
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        super().run()


# ---------------------------------------------------------------------------
# Mic device discovery
# ---------------------------------------------------------------------------

def _list_mic_devices() -> list[str]:
    """Return available input device names via sounddevice, always starting with 'default'.

    Enumerates all PortAudio input devices (ALSA, PulseAudio, JACK, PipeWire…).
    Silently returns ['default'] if sounddevice is unavailable or no devices found.
    """
    try:
        devices = ["default"]
        for info in sd.query_devices():
            if info["max_input_channels"] > 0:
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
    AVInputModule().run()
