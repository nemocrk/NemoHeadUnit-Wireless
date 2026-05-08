"""
NemoHeadUnit-Wireless v2 — channel_modules/av_input

AVInput channel module: captures PCM audio from the HU microphone and
streams it upstream to the connected Android Auto phone.

Launch example (by channel_manager):
    python -m channel_modules.av_input.main \\
        --module-name  channel_av_input_7 \\
        --channel-id   7 \\
        --sdr-bytes-hex <hex string from ServiceDiscoveryResponse>

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
                aa.frame.ch<ch_id>  {channel_id, message_id, encrypted, payload_hex}
                aa.session.shutdown {}
  Publishes   : channel_manager.module_ready  {name, priority}
                aa.frame.send        bytes  (via BaseChannelModule.send_frame)
                av_input.state       {channel_id, state}  IDLE|SETUP|OPEN|PLAYING|STOPPED
                av_input.mic_started {channel_id}
                av_input.mic_stopped {channel_id}
  Config keys : mic_device    enum  "default"  PulseAudio source name
                max_unacked   int   1           AVChannelSetupResponse.max_unacked
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

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

from shared.config_schema import field_enum, field_int           # noqa: E402
from shared.proto_utils import build_media_with_timestamp        # noqa: E402
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
# Constants
# ---------------------------------------------------------------------------
_CHUNK_BYTES  = 2048
_MAX_RETRIES  = 3
_RETRY_BACKOFF = 0.5


# ---------------------------------------------------------------------------
# AVInputModule
# ---------------------------------------------------------------------------

class AVInputModule(BaseChannelModule):
    MODULE_NAME: str = "channel_av_input"
    CHANNEL_ID:  int = -1
    PRIORITY:    int = 1

    _SAMPLE_RATE:   int = 48000
    _BIT_DEPTH:     int = 16
    _CHANNEL_COUNT: int = 1

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

    def __init__(self) -> None:
        super().__init__()
        self._state:       str  = "IDLE"
        self._capturing:   bool = False
        self._max_unacked: int  = 1
        self._sample_rate:   int = self._SAMPLE_RATE
        self._bit_depth:     int = self._BIT_DEPTH
        self._channel_count: int = self._CHANNEL_COUNT
        self._pacat_ok: bool = False
        self._proc: subprocess.Popen | None = None
        self._stop_event:  threading.Event      = threading.Event()
        self._send_queue:  queue.SimpleQueue     = queue.SimpleQueue()
        self._reader_thread: threading.Thread | None = None

    def _is_ready(self) -> bool:
        return self._pacat_ok

    def _init(self) -> None:
        try:
            subprocess.run(
                ["pacat", "--version"],
                capture_output=True, timeout=3, check=True,
            )
            self._pacat_ok = True
            self.log.info("pacat available — AVInput ready")
        except Exception as exc:
            self._pacat_ok = False
            self.log.error("pacat not available — AVInput will NOT be ready: %s", exc)
            return
        cfg = self.channel_config
        if cfg is not None:
            av_input  = cfg.get("av_input_channel", {})
            audio_cfg = av_input.get("audio_config", {})
            if audio_cfg:
                self._sample_rate   = audio_cfg.get("sample_rate",   self._SAMPLE_RATE)
                self._bit_depth     = audio_cfg.get("bit_depth",     self._BIT_DEPTH)
                self._channel_count = audio_cfg.get("channel_count", self._CHANNEL_COUNT)
                self.log.info(
                    "_init: ch=%d rate=%d depth=%d channels=%d (from SDR)",
                    self.CHANNEL_ID, self._sample_rate, self._bit_depth, self._channel_count,
                )
            else:
                self.log.info(
                    "_init: no audio_config in SDR for ch=%d — using defaults %dHz/%dbit/%dch",
                    self.CHANNEL_ID, self._sample_rate, self._bit_depth, self._channel_count,
                )
        else:
            self.log.warning("_init: channel_config is None for ch=%d — using defaults", self.CHANNEL_ID)

    def _cleanup(self) -> None:
        self._stop_stream(publish=False)
        self._set_state("IDLE")

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

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._stop_stream(publish=True)
        self._set_state("IDLE")
        self.log.info("AA session shutdown — ch=%d reset", self.CHANNEL_ID)

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self._capturing = False
        self._set_state("IDLE")
        self.log.info("Channel %d open", channel_id)

    def on_channel_close(self, channel_id: int) -> None:
        if self._capturing:
            self._stop_stream(publish=True)
        self._set_state("IDLE")
        self.log.info("Channel %d closed", channel_id)

    def on_frame(self, channel_id: int, message_id: int, encrypted: bool, data: bytes) -> None:
        """Dispatch incoming AA frame by message_id (already extracted by tcp_server)."""
        self._drain_send_queue()

        if message_id == _MSG_AV_CHANNEL_SETUP_REQUEST:
            self._handle_setup_request(data)
        elif message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(data)
        elif message_id == _MSG_AV_INPUT_OPEN_REQUEST:
            self._handle_input_open_request(data)
        elif message_id == _MSG_AV_MEDIA_ACK:
            self.log.debug("ACK_INDICATION ch=%d — no-op", channel_id)
        else:
            self.log.debug(
                "Unhandled av_input msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(data),
            )

    def _handle_setup_request(self, body: bytes) -> None:
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
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString())
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_input_open_request(self, body: bytes) -> None:
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
        resp = AVInputOpenResponse()
        resp.session = 0
        resp.value   = 0
        self.send_frame(_MSG_AV_INPUT_OPEN_RESPONSE, resp.SerializeToString())
        if req.open:
            self._start_stream()
        else:
            self._stop_stream(publish=True)

    def _start_stream(self, retry: int = 0) -> None:
        self._stop_stream(publish=False)
        self._stop_event.clear()
        device = self._config.get("mic_device", "default")
        fmt    = {8: "u8", 16: "s16le", 24: "s24le", 32: "s32le"}.get(self._bit_depth, "s16le")
        cmd = [
            "pacat", "--record",
            f"--format={fmt}",
            f"--channels={self._channel_count}",
            f"--rate={self._sample_rate}",
        ]
        if device != "default":
            cmd.append(f"--device={device}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            self.log.error("_start_stream: failed to spawn pacat — %s", exc)
            self._proc = None
            self._capturing = False
            return
        self._capturing = True
        self._set_state("PLAYING")
        self.bus.publish("av_input.mic_started", {"channel_id": self.CHANNEL_ID})
        self.log.info(
            "pacat --record spawned: device=%r rate=%d channels=%d fmt=%s pid=%d",
            device, self._sample_rate, self._channel_count, fmt, self._proc.pid,
        )
        self._reader_thread = threading.Thread(
            target=self._mic_reader,
            args=(self._proc, retry),
            daemon=True,
            name=f"mic-reader-ch{self.CHANNEL_ID}",
        )
        self._reader_thread.start()
        threading.Thread(
            target=self._stderr_drain,
            args=(self._proc,),
            daemon=True,
            name=f"mic-stderr-ch{self.CHANNEL_ID}",
        ).start()

    def _stop_stream(self, publish: bool = True) -> None:
        self._stop_event.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except Exception:
                break
        if self._capturing:
            self._capturing = False
            self._set_state("STOPPED")
            if publish:
                self.bus.publish("av_input.mic_stopped", {"channel_id": self.CHANNEL_ID})
                self.log.info("Mic capture stopped ch=%d", self.CHANNEL_ID)

    def _mic_reader(self, proc: subprocess.Popen, retry: int) -> None:
        try:
            while not self._stop_event.is_set():
                ts_us = time.monotonic_ns() // 1000
                chunk = proc.stdout.read(_CHUNK_BYTES)
                if not chunk:
                    break
                self._send_queue.put((ts_us, chunk))
        except Exception as exc:
            self.log.warning("_mic_reader exception ch=%d — %s", self.CHANNEL_ID, exc)
        if self._stop_event.is_set():
            return
        if retry < _MAX_RETRIES:
            backoff = _RETRY_BACKOFF * (2 ** retry)
            self.log.warning(
                "pacat stdout EOF ch=%d — retry %d/%d in %.1fs",
                self.CHANNEL_ID, retry + 1, _MAX_RETRIES, backoff,
            )
            time.sleep(backoff)
            self._start_stream(retry=retry + 1)
        else:
            self.log.error(
                "pacat stdout EOF ch=%d — max retries (%d) exhausted, giving up",
                self.CHANNEL_ID, _MAX_RETRIES,
            )
            self._capturing = False
            self._set_state("STOPPED")
            self.bus.publish("av_input.mic_stopped", {"channel_id": self.CHANNEL_ID})

    def _stderr_drain(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stderr:
                decoded = line.decode(errors="replace").rstrip()
                if decoded:
                    self.log.warning("pacat stderr ch=%d: %s", self.CHANNEL_ID, decoded)
        except Exception:
            pass

    def _drain_send_queue(self) -> None:
        while True:
            try:
                ts_us, pcm = self._send_queue.get_nowait()
            except queue.Empty:
                break
            payload = build_media_with_timestamp(ts_us, pcm)
            self.send_frame(_MSG_AV_MEDIA_WITH_TIMESTAMP, payload)

    def _set_state(self, new_state: str) -> None:
        if self._state == new_state:
            return
        self._state = new_state
        self.bus.publish("av_input.state", {
            "channel_id": self.CHANNEL_ID,
            "state":      new_state,
        })
        self.log.info("av_input.state ch=%d → %s", self.CHANNEL_ID, new_state)

    def run(self) -> None:
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        super().run()


# ---------------------------------------------------------------------------
# Mic device discovery
# ---------------------------------------------------------------------------

def _list_mic_devices() -> list[str]:
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=3,
        )
        devices = ["default"]
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[1].strip()
                if name and name not in devices and ".monitor" not in name:
                    devices.append(name)
        return devices
    except Exception:
        return ["default"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    AVInputModule().run()
