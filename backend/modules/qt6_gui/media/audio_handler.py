"""
audio_handler.py — Dynamic Multi-Sink Qt6 Multimedia Audio Output Playback & Microphone Capture Engine.

Manages dedicated `QAudioSink` instances per channel to delegate multi-stream mixing directly to the
operating system audio subsystem (PipeWire / PulseAudio on Linux, WASAPI on Windows), preventing digital clipping
and inter-stream jitter distortion.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from typing import Callable, Dict, Optional
from PyQt6.QtCore import Qt, QByteArray, QIODevice, QObject, QTimer, pyqtSignal, pyqtSlot, QThread
from shared.logger import get_logger


try:
    from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QAudioSource, QMediaDevices
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False
    QAudioSink = None
    QAudioSource = None
    QMediaDevices = None


logger = get_logger("qt6_gui.audio_handler")


class Signal:
    """Lightweight pure-Python signal object compatible with pyqtSignal API."""
    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        if handler not in self._handlers:
            self._handlers.append(handler)

    def emit(self, *args, **kwargs):
        for h in self._handlers:
            try:
                h(*args, **kwargs)
            except Exception as exc:
                logger.warning("Signal emit exception: %s", exc)


def find_audio_output_device(name: str):
    if not HAS_MULTIMEDIA or not QMediaDevices:
        return None
    if not name or name == "default":
        return QMediaDevices.defaultAudioOutput()
    for dev in QMediaDevices.audioOutputs():
        if name.lower() in dev.description().lower() or name.lower() in str(dev.id()).lower():
            return dev
    return QMediaDevices.defaultAudioOutput()


def find_audio_input_device(name: str):
    if not HAS_MULTIMEDIA or not QMediaDevices:
        return None
    if not name or name == "default":
        return QMediaDevices.defaultAudioInput()
    for dev in QMediaDevices.audioInputs():
        if name.lower() in dev.description().lower() or name.lower() in str(dev.id()).lower():
            return dev
    return QMediaDevices.defaultAudioInput()


class AudioPcmStream(QIODevice):
    """
    Thread-Safe Pull-Mode QIODevice for QAudioSink with Jitter Pre-Buffering.
    Feeds decoded Int16 PCM samples directly to QAudioSink at hardware DAC clock speed.
    Accumulates a jitter pre-buffer before releasing audio data to eliminate Active/Idle thrashing.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2, prebuffer_ms: int = 150, parent=None):
        super().__init__(parent)
        self._sample_rate = sample_rate
        self._channels = channels
        self._prebuffer_ms = prebuffer_ms
        self._underrun_count = 0
        if prebuffer_ms <= 0:
            self._prebuffer_bytes = 0
            self._is_buffering = False
        else:
            self._prebuffer_bytes = max(1024, int(sample_rate * channels * 2 * (prebuffer_ms / 1000.0)))
            self._is_buffering = True
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def configure_format(self, sample_rate: int, channels: int, prebuffer_ms: int = 150):
        with self._lock:
            self._sample_rate = sample_rate
            self._channels = channels
            self._prebuffer_ms = prebuffer_ms
            if prebuffer_ms <= 0:
                self._prebuffer_bytes = 0
                self._is_buffering = False
            else:
                self._prebuffer_bytes = max(1024, int(sample_rate * channels * 2 * (prebuffer_ms / 1000.0)))
                self._is_buffering = True

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        with self._lock:
            if self._is_buffering:
                return 0 + super().bytesAvailable()
            avail = len(self._buffer)
        return avail + super().bytesAvailable()

    def write_pcm(self, pcm_bytes: bytes, max_buffer_bytes: int = 192000):
        """Append incoming PCM frames from decoder thread."""
        with self._lock:
            self._buffer.extend(pcm_bytes)
            if len(self._buffer) > max_buffer_bytes:
                dropped = len(self._buffer) - max_buffer_bytes
                del self._buffer[:dropped]
            if self._is_buffering and len(self._buffer) >= self._prebuffer_bytes:
                self._is_buffering = False

    def readData(self, max_len: int) -> bytes:
        """Called synchronously by QAudioSink to pull PCM samples at DAC clock speed."""
        if max_len <= 0:
            return b""
        with self._lock:
            if self._is_buffering:
                return b""
            if self._buffer:
                frame_size = max(1, self._channels * 2)
                take = min(len(self._buffer), max_len)
                take = (take // frame_size) * frame_size
                if take == 0:
                    return b""
                chunk = bytes(self._buffer[:take])
                del self._buffer[:take]
                if len(self._buffer) == 0:
                    self._is_buffering = True
                    self._underrun_count += 1
                return chunk
            self._is_buffering = True
            self._underrun_count += 1
            return b""

    def writeData(self, data: bytes) -> int:
        """Required pure virtual method implementation for QIODevice."""
        return -1

    def clear(self):
        with self._lock:
            self._buffer.clear()
            self._is_buffering = True

    def get_buffer_metrics(self) -> dict:
        with self._lock:
            buf_len = len(self._buffer)
            bps = max(1, self._sample_rate * self._channels * 2)
            return {
                "buffered_bytes": buf_len,
                "buffered_ms": int((buf_len / bps) * 1000),
                "prebuffer_bytes": self._prebuffer_bytes,
                "prebuffer_ms": self._prebuffer_ms,
                "is_buffering": self._is_buffering,
                "underruns": self._underrun_count,
            }


class DynamicChannelAudioSink(QObject):
    """
    Dedicated audio playback pipeline for an individual audio channel.
    Uses PyQt6 QAudioSink in native Push Mode with event-driven buffer pumping
    (via 10ms QTimer and QIODevice.bytesWritten) and thread-safe jitter pre-buffering.
    """

    _start_signal = pyqtSignal()
    _stop_signal = pyqtSignal()
    _push_signal = pyqtSignal()

    PREBUFFER_MS = 150

    def __init__(self, channel_id: int, sample_rate: int = 48000, channel_count: int = 2, target_device: str = "default", prebuffer_ms: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.channel_id = channel_id
        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self.bit_depth = 16
        self.target_device = target_device
        if prebuffer_ms is not None:
            self.PREBUFFER_MS = prebuffer_ms

        self.audio_sink: Optional[QAudioSink] = None
        self.audio_io: Optional[QIODevice] = None
        self._is_started: bool = False

        # Active pump timer on Qt main thread to guarantee continuous buffer draining
        self._pump_timer = QTimer(self)
        self._pump_timer.setInterval(10)
        self._pump_timer.timeout.connect(self._flush_to_sink)

        # Thread-safe jitter buffer state
        self._app_buffer = bytearray()
        self._app_lock = threading.Lock()
        self._is_buffering: bool = True
        self._underrun_count: int = 0

        self._start_signal.connect(self._do_start, Qt.ConnectionType.QueuedConnection)
        self._stop_signal.connect(self._do_stop, Qt.ConnectionType.QueuedConnection)
        self._push_signal.connect(self._flush_to_sink, Qt.ConnectionType.QueuedConnection)

        self.aac_decoder = None
        self.resampler = None
        self._is_aac: Optional[bool] = None
        self.frame_count = 0
        self.total_bytes_in = 0
        self.total_bytes_out = 0
        self.last_frame_time = 0.0
        self._last_log_time = 0.0

        # Timestamp & Lag Tracking
        self.first_sys_time: Optional[float] = None
        self.first_ts_us: Optional[int] = None
        self.last_ts_us: int = 0
        self.current_lag_ms: float = 0.0

        # Audio Dump (/tmp/nemo_audio_ch{id}_{rate}hz.wav when NEMO_AUDIO_DUMP=1)
        self._dump_wav = None
        self._dump_enabled = os.environ.get("NEMO_AUDIO_DUMP") == "1"

    def _init_aac_decoder(self):
        """Initialize per-channel PyAV FFmpeg AAC decoder targeting channel sample rate and layout."""
        if self.aac_decoder is not None:
            return
        try:
            import av
            self.aac_decoder = av.CodecContext.create("aac", "r")
            layout = "stereo" if self.channel_count == 2 else "mono"
            self.resampler = av.AudioResampler(format="s16", layout=layout, rate=self.sample_rate)
            logger.info(f"🔊 Audio Channel {self.channel_id}: AAC decoder initialized ({self.sample_rate}Hz Int16 {layout})")
        except Exception as exc:
            logger.warning(f"Audio Channel {self.channel_id}: AAC decoder init failed: {exc}")

    def _init_playback(self, sample_rate: int = 48000, channel_count: int = 2):
        """Configure QAudioSink for channel native rate and channels in Push Mode."""
        if self.audio_sink:
            try:
                self.audio_sink.stop()
            except Exception:
                pass
            self.audio_sink = None
            self.audio_io = None
            self._is_started = False

        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(sample_rate)
            fmt.setChannelCount(channel_count)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            output_device = find_audio_output_device(self.target_device)
            self.audio_sink = QAudioSink(output_device, fmt, self)
            self.audio_sink.setVolume(1.0)
            bps = max(1, sample_rate * channel_count * 2)
            self.audio_sink.setBufferSize(max(48000, int(bps * 0.25)))

            def _on_state_changed(state):
                err = self.audio_sink.error() if self.audio_sink else "None"
                logger.info(f"🔊 [Audio Ch{self.channel_id}] QAudioSink state: {state} (error={err})")

            self.audio_sink.stateChanged.connect(_on_state_changed)

            dev_desc = output_device.description() if output_device else "Default"
            logger.info(f"🔊 Audio Channel {self.channel_id}: QAudioSink Push-Mode configured for '{dev_desc}' ({sample_rate}Hz, {channel_count}ch, Int16, buf={self.audio_sink.bufferSize()}B)")
        except Exception as exc:
            logger.warning(f"Failed to configure QAudioSink for channel {self.channel_id}: {exc}")

    def configure_codec(
        self,
        codec: str,
        sample_rate: int = 48000,
        channel_count: int = 2,
        bit_depth: int = 16,
    ):
        """Explicitly configure incoming stream codec, sample rate, and channel count."""
        c = str(codec).upper()
        self._is_aac = "AAC" in c
        if self._is_aac:
            self._init_aac_decoder()

        if self.sample_rate == sample_rate and self.channel_count == channel_count and self.audio_sink is not None:
            return

        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self.bit_depth = bit_depth
        self._close_sink()
        logger.info(
            f"🔊 [Audio Ch{self.channel_id}] Configured native sink: {sample_rate}Hz {channel_count}ch ({codec})"
        )

    @pyqtSlot()
    def _flush_to_sink(self):
        """Push available buffered audio directly into QAudioSink io device on Qt main thread."""
        if self._is_buffering or not self.audio_io or not self.audio_sink:
            return

        bytes_free = self.audio_sink.bytesFree()
        if bytes_free <= 0:
            return

        chunk = None
        frame_size = max(1, self.channel_count * 2)

        with self._app_lock:
            avail = len(self._app_buffer)
            if avail <= 0:
                if self.audio_sink and self.audio_sink.bytesFree() >= self.audio_sink.bufferSize():
                    self._is_buffering = True
                    self._underrun_count += 1
                return

            to_write = min(bytes_free, avail)
            to_write = (to_write // frame_size) * frame_size
            if to_write <= 0:
                return

            # Slice without removing yet; unconsumed bytes must stay aligned
            chunk = bytes(self._app_buffer[:to_write])

        if chunk:
            try:
                written = self.audio_io.write(chunk)
                if written > 0:
                    # Drop only fully-written sample-aligned frames
                    consumed = (written // frame_size) * frame_size
                    if consumed > 0:
                        with self._app_lock:
                            del self._app_buffer[:consumed]
                        self.total_bytes_out += consumed
            except Exception as exc:
                logger.debug(f"Audio Channel {self.channel_id} flush error: {exc}")

    @pyqtSlot()
    def _do_start(self):
        """Executed strictly on Main Qt Thread to start QAudioSink in Push Mode."""
        if self.audio_sink is None or not self._is_started:
            self._init_playback(self.sample_rate, self.channel_count)
            if self.audio_sink:
                self.audio_io = self.audio_sink.start()
                if self.audio_io:
                    self.audio_io.bytesWritten.connect(lambda n: self._flush_to_sink())
                self._is_started = True
                self._pump_timer.start()
                logger.info(f"🔊 [Audio Ch{self.channel_id}] Stream ACTIVE — QAudioSink started in Push Mode on '{self.target_device}' ({self.sample_rate}Hz, {self.channel_count}ch)")
                self._flush_to_sink()

    def _ensure_started(self):
        """Lazily activate hardware audio sink in Push Mode via thread-safe QueuedConnection signal."""
        if not self._is_started:
            self._start_signal.emit()

    def push_frame(self, audio_bytes: bytes, ts_us: int = 0):
        """Decode and push audio frame into thread-safe jitter buffer."""
        if not audio_bytes:
            return

        now = time.time()
        self.frame_count += 1
        self.total_bytes_in += len(audio_bytes)

        # Gap / Pause detection: if gap between audio frames > 0.35s, reset buffering so prebuffer fills before playing
        time_since_last = now - self.last_frame_time if self.last_frame_time > 0 else 0.0
        self.last_frame_time = now

        if time_since_last > 0.35:
            with self._app_lock:
                self._is_buffering = True

        if ts_us > 0:
            self.last_ts_us = ts_us
            if self.first_sys_time is None or self.first_ts_us is None:
                self.first_sys_time = now
                self.first_ts_us = ts_us
                self.current_lag_ms = 0.0
            else:
                elapsed_sys = now - self.first_sys_time
                elapsed_phone = (ts_us - self.first_ts_us) / 1_000_000.0
                lag = (elapsed_sys - elapsed_phone) * 1000.0
                # If stream paused, rewound, or phone PTS jumped by more than 1.5 seconds, reset baseline & prebuffer
                if abs(lag) > 1500.0 or elapsed_phone < 0:
                    self.first_sys_time = now
                    self.first_ts_us = ts_us
                    self.current_lag_ms = 0.0
                    with self._app_lock:
                        self._is_buffering = True
                else:
                    self.current_lag_ms = max(0.0, lag)

        # Detect format on first frame if not explicitly configured
        if self._is_aac is None:
            # Check for ADTS syncword (0xFFF) AND validate frame length to prevent false positives on signed Int16 PCM
            is_adts_aac = False
            if len(audio_bytes) >= 7 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xF6) == 0xF0:
                frame_len = ((audio_bytes[3] & 0x03) << 11) | (audio_bytes[4] << 3) | ((audio_bytes[5] & 0xE0) >> 5)
                if frame_len == len(audio_bytes):
                    is_adts_aac = True
            self._is_aac = is_adts_aac
            if self._is_aac:
                self._init_aac_decoder()

        decoded_pcm = bytearray()
        if self._is_aac and self.aac_decoder:
            try:
                import av
                packet = av.Packet(audio_bytes)
                frames = self.aac_decoder.decode(packet)
                if frames:
                    for frame in frames:
                        if self.resampler:
                            resampled = self.resampler.resample(frame)
                            for r_frame in resampled:
                                decoded_pcm.extend(r_frame.to_ndarray().tobytes())
                        else:
                            decoded_pcm.extend(frame.to_ndarray().tobytes())
            except Exception as exc:
                logger.debug(f"Audio Channel {self.channel_id} AAC decode error: {exc}")
        elif not self._is_aac:
            decoded_pcm.extend(audio_bytes)

        if not decoded_pcm:
            return

        if self._dump_enabled:
            if self._dump_wav is None:
                dump_path = os.path.join(tempfile.gettempdir(), f"nemo_audio_ch{self.channel_id}_{self.sample_rate}hz.wav")
                try:
                    self._dump_wav = wave.open(dump_path, "wb")
                    self._dump_wav.setnchannels(self.channel_count)
                    self._dump_wav.setsampwidth(self.bit_depth // 8)
                    self._dump_wav.setframerate(self.sample_rate)
                    logger.info(f"🎙️ [Audio Dump] Recording audio ch{self.channel_id} to {dump_path}")
                except Exception as exc:
                    logger.warning(f"Audio dump failed to open {dump_path}: {exc}")
                    self._dump_enabled = False
            if self._dump_wav:
                try:
                    self._dump_wav.writeframes(decoded_pcm)
                except Exception as exc:
                    logger.debug(f"Audio dump write error: {exc}")

        bps = max(1, self.sample_rate * self.channel_count * 2)
        prebuffer_bytes = int(bps * (self.PREBUFFER_MS / 1000.0))
        max_app_bytes = max(bps * 2, len(decoded_pcm) * 4)

        with self._app_lock:
            self._app_buffer.extend(decoded_pcm)
            if len(self._app_buffer) > max_app_bytes:
                dropped = len(self._app_buffer) - max_app_bytes
                del self._app_buffer[:dropped]

            should_start = False
            if self._is_buffering:
                if len(self._app_buffer) >= prebuffer_bytes:
                    self._is_buffering = False
                    should_start = True
                    logger.info(
                        f"🔊 [Audio Ch{self.channel_id}] Prebuffer FILLED ({len(self._app_buffer)}B / {self.PREBUFFER_MS}ms) "
                        f"— starting push playback"
                    )
            else:
                should_start = True

        if should_start:
            self._ensure_started()
            self._push_signal.emit()

    @pyqtSlot()
    def _do_stop(self):
        """Executed strictly on Main Qt Thread to stop QAudioSink safely."""
        self._pump_timer.stop()
        if self.audio_sink:
            try:
                self.audio_sink.stop()
            except Exception:
                pass
            self.audio_sink = None
        self.audio_io = None
        self._is_started = False
        with self._app_lock:
            self._app_buffer.clear()
            self._is_buffering = True

    def _close_sink(self):
        """Safely terminate QAudioSink via QueuedConnection."""
        self._stop_signal.emit()

    def get_metrics(self) -> dict:
        bps = max(1, self.sample_rate * self.channel_count * 2)
        sink_data = {
            "buffer_size": 0,
            "bytes_free": 0,
            "bytes_queued": 0,
            "queued_ms": 0,
            "state": "None",
            "error": "None",
        }
        if self.audio_sink:
            try:
                buf_size = self.audio_sink.bufferSize()
                bytes_free = self.audio_sink.bytesFree()
                bytes_queued = max(0, buf_size - bytes_free)
                state_val = self.audio_sink.state()
                error_val = self.audio_sink.error()
                sink_data = {
                    "buffer_size": buf_size,
                    "bytes_free": bytes_free,
                    "bytes_queued": bytes_queued,
                    "queued_ms": int((bytes_queued / bps) * 1000),
                    "state": getattr(state_val, "name", str(state_val)),
                    "error": getattr(error_val, "name", str(error_val)),
                }
            except Exception:
                pass

        with self._app_lock:
            app_len = len(self._app_buffer)
            is_buf = self._is_buffering
            underruns = self._underrun_count

        app_data = {
            "buffered_bytes": app_len,
            "buffered_ms": int((app_len / bps) * 1000),
            "prebuffer_bytes": int(bps * (self.PREBUFFER_MS / 1000.0)),
            "prebuffer_ms": self.PREBUFFER_MS,
            "is_buffering": is_buf,
            "underruns": underruns,
        }

        return {
            "channel_id": self.channel_id,
            "sample_rate": self.sample_rate,
            "channel_count": self.channel_count,
            "is_started": self._is_started,
            "frame_count": self.frame_count,
            "total_bytes_in": self.total_bytes_in,
            "total_bytes_out": self.total_bytes_out,
            "lag_ms": int(self.current_lag_ms),
            "app_buffer": app_data,
            "sink_buffer": sink_data,
        }

    def close(self):
        """Release audio sink and decoder resources."""
        self._close_sink()
        if self._dump_wav:
            try:
                self._dump_wav.close()
            except Exception:
                pass
            self._dump_wav = None
        if self.audio_sink:
            self.audio_sink = None
        self.aac_decoder = None
        self.resampler = None


class QtAudioEngine:
    """
    Dynamic Multi-Sink Qt6 Multimedia Audio Engine managing Audio Output & Microphone Input.
    Maintains independent QAudioSink instances per active audio channel, delegating mixing
    directly to the operating system mixer without software clipping or static.
    """

    def __init__(self):
        logger.info("🔍 [Audio Engine Trace] Entering QtAudioEngine.__init__...")
        self.mic_data_captured = Signal()

        self.target_output_sink: str = "default"
        self.target_input_source: str = "default"

        self.audio_source: Optional[QAudioSource] = None
        self.source_io: Optional[QIODevice] = None
        self.mic_accumulator = bytearray()

        self.sinks: Dict[int, DynamicChannelAudioSink] = {}
        self._sinks_lock = threading.Lock()
        self._running: bool = True

        logger.info("🔍 [Audio Engine Trace] QtAudioEngine initialized with event-driven pull-mode audio backend!")

    def set_output_sink(self, sink_name: str):
        """Reconfigure target audio output device for all channel sinks."""
        self.target_output_sink = sink_name or "default"
        with self._sinks_lock:
            for sink in self.sinks.values():
                sink.target_device = self.target_output_sink
                sink._close_sink()
        logger.info(f"QtAudioEngine target output sink set to '{self.target_output_sink}'")

    def set_input_source(self, source_name: str):
        """Reconfigure target audio input source for microphone capture."""
        self.target_input_source = source_name or "default"
        if self.audio_source:
            self.stop_microphone()
            self.start_microphone()
        logger.info(f"QtAudioEngine target input source set to '{self.target_input_source}'")

    def configure_channel_codec(
        self,
        channel_id: int,
        codec: str,
        sample_rate: int = 48000,
        channel_count: int = 2,
        bit_depth: int = 16,
    ):
        """Explicitly configure channel codec, sample rate, and channel count ahead of stream delivery."""
        with self._sinks_lock:
            sink = self.sinks.get(channel_id)
            if sink is None:
                sink = DynamicChannelAudioSink(
                    channel_id,
                    sample_rate=sample_rate,
                    channel_count=channel_count,
                    target_device=self.target_output_sink,
                )
                self.sinks[channel_id] = sink
                logger.info(
                    f"🔊 Registered new dynamic audio sink for channel {channel_id} "
                    f"(codec={codec}, rate={sample_rate}Hz, channels={channel_count})"
                )
        sink.configure_codec(
            codec=codec,
            sample_rate=sample_rate,
            channel_count=channel_count,
            bit_depth=bit_depth,
        )

    def play_pcm_frame(self, audio_bytes: bytes, channel_id: int = 0, ts_us: int = 0):
        """Dispatch audio frame to its dedicated channel sink."""
        if not audio_bytes or not self._running:
            return

        with self._sinks_lock:
            sink = self.sinks.get(channel_id)
            if sink is None:
                # Default rate based on standard Android Auto channel map
                default_rate = 16000 if channel_id in (5, 6, 7) else 48000
                default_ch = 1 if channel_id in (5, 6, 7) else 2
                sink = DynamicChannelAudioSink(
                    channel_id,
                    sample_rate=default_rate,
                    channel_count=default_ch,
                    target_device=self.target_output_sink,
                )
                self.sinks[channel_id] = sink
                logger.info(
                    f"🔊 Registered fallback dynamic audio sink for channel {channel_id} "
                    f"({default_rate}Hz, {default_ch}ch)"
                )

        sink.push_frame(audio_bytes, ts_us=ts_us)

    def start_microphone(self) -> bool:
        """Start capturing 16kHz 16-bit Mono PCM mic audio from default or target input device."""
        if self.audio_source:
            return True

        try:
            input_device = find_audio_input_device(self.target_input_source)
            if not input_device or input_device.isNull():
                logger.warning("🎤 [Microphone] No audio input device found!")
                return False

            dev_name = input_device.description()
            logger.info(f"🎤 [Microphone] Starting microphone on input device: '{dev_name}'")

            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            if not input_device.isFormatSupported(fmt):
                pref_fmt = input_device.preferredFormat()
                logger.info(f"🎤 [Microphone] Fallback format: {pref_fmt.sampleFormat()} (rate={pref_fmt.sampleRate()}, ch={pref_fmt.channelCount()})")
                fmt = pref_fmt

            self.audio_source = QAudioSource(input_device, fmt)
            self.source_io = self.audio_source.start()
            if self.source_io:
                self.source_io.readyRead.connect(self._on_mic_ready_read)
                logger.info(f"🎤 [Microphone] Uplink stream ACTIVE via Qt6 QAudioSource on '{dev_name}'")
                return True
            else:
                logger.warning("🎤 [Microphone] QAudioSource.start() returned null QIODevice")
        except Exception as exc:
            logger.warning("Failed to start Qt6 QAudioSource microphone: %s", exc)
            self.stop_microphone()
        return False

    def _poll_mic(self):
        """Read any available mic data synchronously from source_io."""
        if not self.source_io or not self.audio_source:
            return
        try:
            bytes_available = self.source_io.bytesAvailable()
            if bytes_available > 0:
                data = self.source_io.readAll().data()
                if data:
                    self._on_raw_mic_data(data)
        except Exception as exc:
            logger.debug("Mic poll error: %s", exc)

    def _on_mic_ready_read(self):
        """Qt readyRead slot handler for mic source_io."""
        self._poll_mic()

    def _on_raw_mic_data(self, data: bytes):
        """Accumulate raw mic PCM bytes and emit standard 20ms (640-byte) packets."""
        self.mic_accumulator.extend(data)
        CHUNK_SIZE = 640  # 20ms @ 16kHz mono

        while len(self.mic_accumulator) >= CHUNK_SIZE:
            chunk = bytes(self.mic_accumulator[:CHUNK_SIZE])
            del self.mic_accumulator[:CHUNK_SIZE]
            logger.info(f"🎤 [Microphone Uplink] Emitted 20ms mic frame: {len(chunk)} bytes -> SHM upstream")
            self.mic_data_captured.emit(chunk)

    def stop_microphone(self):
        """Stop microphone input stream."""
        if self.audio_source:
            try:
                self.audio_source.stop()
            except Exception:
                pass
            self.audio_source = None
            self.source_io = None
            self.mic_accumulator.clear()
            logger.info("🎤 [Microphone] Uplink stream STOPPED")

    def get_metrics(self) -> dict:
        """Return real-time buffer & streaming telemetry for all active audio sinks."""
        with self._sinks_lock:
            return {
                ch_id: sink.get_metrics()
                for ch_id, sink in self.sinks.items()
            }

    def close(self):
        """Cleanly stop all audio sinks and microphone capture."""
        self._running = False
        self.stop_microphone()

        with self._sinks_lock:
            for sink in self.sinks.values():
                sink.close()
            self.sinks.clear()

        logger.info("QtAudioEngine closed and all audio resources released.")
