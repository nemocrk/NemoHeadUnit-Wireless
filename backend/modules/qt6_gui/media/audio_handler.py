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
import threading
import time
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
    Thread-Safe Pull-Mode QIODevice for QAudioSink.
    Feeds decoded Int16 PCM samples directly to QAudioSink at hardware DAC clock speed.
    Pads with digital silence during active stream micro-gaps to prevent ALSA/PipeWire DMA underruns.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._is_active = False

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        with self._lock:
            avail = len(self._buffer)
        return avail + super().bytesAvailable()

    def write_pcm(self, pcm_bytes: bytes, max_buffer_bytes: int = 192000):
        """Append incoming PCM frames from decoder thread."""
        with self._lock:
            self._buffer.extend(pcm_bytes)
            if len(self._buffer) > max_buffer_bytes:
                dropped = len(self._buffer) - max_buffer_bytes
                del self._buffer[:dropped]
            self._is_active = True

    def readData(self, max_len: int) -> bytes:
        """Called synchronously by QAudioSink to pull PCM samples at DAC clock speed."""
        if max_len <= 0:
            return b""
        with self._lock:
            if self._buffer:
                take = min(len(self._buffer), max_len)
                chunk = bytes(self._buffer[:take])
                del self._buffer[:take]
                return chunk
            return b""

    def writeData(self, data: bytes) -> int:
        """Required pure virtual method implementation for QIODevice."""
        return -1

    def clear(self):
        with self._lock:
            self._buffer.clear()
            self._is_active = False


class DynamicChannelAudioSink(QObject):
    """
    Dedicated audio playback pipeline for an individual audio channel.
    Uses PyQt6 QAudioSink in Pull Mode via AudioPcmStream QIODevice for zero-jitter,
    underrun-free ALSA/PipeWire playback.
    """

    _start_signal = pyqtSignal()
    _stop_signal = pyqtSignal()

    def __init__(self, channel_id: int, sample_rate: int = 48000, channel_count: int = 2, target_device: str = "default", parent=None):
        super().__init__(parent)
        self.channel_id = channel_id
        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self.bit_depth = 16
        self.target_device = target_device

        self._start_signal.connect(self._do_start, Qt.ConnectionType.QueuedConnection)
        self._stop_signal.connect(self._do_stop, Qt.ConnectionType.QueuedConnection)

        self.pcm_stream = AudioPcmStream(self)
        self.pcm_stream.open(QIODevice.OpenModeFlag.ReadOnly)

        self.audio_sink: Optional[QAudioSink] = None
        self._is_started: bool = False

        self.aac_decoder = None
        self.resampler = None
        self._is_aac: Optional[bool] = None
        self.frame_count = 0
        self.total_bytes_in = 0
        self.total_bytes_out = 0
        self.last_frame_time = 0.0
        self._last_log_time = 0.0

    def _init_aac_decoder(self, target_sample_rate: int = 48000, target_channel_count: int = 2):
        """Initialize per-channel PyAV FFmpeg AAC decoder."""
        if self.aac_decoder is not None:
            return
        try:
            import av
            self.aac_decoder = av.CodecContext.create("aac", "r")
            layout = "stereo" if target_channel_count == 2 else "mono"
            self.resampler = av.AudioResampler(format="s16", layout=layout, rate=target_sample_rate)
            logger.info(f"Audio Channel {self.channel_id}: AAC decoder initialized ({target_sample_rate}Hz Int16 {layout})")
        except Exception as exc:
            logger.warning(f"Audio Channel {self.channel_id}: AAC decoder init failed: {exc}")

    def _init_playback(self, sample_rate: int, channel_count: int):
        """Configure QAudioSink for specified rate and channels."""
        if self.audio_sink and self.sample_rate == sample_rate and self.channel_count == channel_count:
            return

        if self.audio_sink:
            try:
                self.audio_sink.stop()
            except Exception:
                pass
            self.audio_sink = None
            self._is_started = False

        self.sample_rate = sample_rate
        self.channel_count = channel_count

        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(sample_rate)
            fmt.setChannelCount(channel_count)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            output_device = find_audio_output_device(self.target_device)
            self.audio_sink = QAudioSink(output_device, fmt, self)
            self.audio_sink.setVolume(1.0)
            self.audio_sink.setBufferSize(sample_rate * channel_count * 2 // 2)

            def _on_state_changed(state):
                err = self.audio_sink.error() if self.audio_sink else "None"
                logger.info(f"🔊 [Audio Ch{self.channel_id}] QAudioSink state: {state} (error={err})")

            self.audio_sink.stateChanged.connect(_on_state_changed)

            dev_desc = output_device.description() if output_device else "Default"
            logger.info(f"🔊 Audio Channel {self.channel_id}: QAudioSink Pull-Mode configured for '{dev_desc}' ({sample_rate}Hz, {channel_count}ch, Int16)")
        except Exception as exc:
            logger.warning(f"Failed to configure QAudioSink for channel {self.channel_id}: {exc}")

    def configure_codec(
        self,
        codec: str,
        sample_rate: int = 48000,
        channel_count: int = 2,
        bit_depth: int = 16,
    ):
        """Explicitly configure codec, sample rate, and channel count from AVChannelSetupRequest."""
        if self.sample_rate == sample_rate and self.channel_count == channel_count and self._is_aac is not None:
            return

        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self.bit_depth = bit_depth
        c = str(codec).upper()
        if "AAC" in c:
            self._is_aac = True
            self._init_aac_decoder(target_sample_rate=sample_rate, target_channel_count=channel_count)
            self._init_playback(sample_rate, channel_count)
        elif "PCM" in c:
            self._is_aac = False
            self._init_playback(sample_rate, channel_count)

    @pyqtSlot()
    def _do_start(self):
        """Executed strictly on Main Qt Thread to start QAudioSink timer safely."""
        if self.audio_sink is None or not self._is_started:
            self._init_playback(self.sample_rate, self.channel_count)
            if self.audio_sink:
                if not self.pcm_stream.isOpen():
                    self.pcm_stream.open(QIODevice.OpenModeFlag.ReadOnly)
                self.audio_sink.start(self.pcm_stream)
                self._is_started = True
                logger.info(f"🔊 [Audio Ch{self.channel_id}] Stream ACTIVE — QAudioSink started in Pull Mode on '{self.target_device}'")

    def _ensure_started(self):
        """Lazily activate hardware audio sink in Pull Mode via thread-safe QueuedConnection signal."""
        if not self._is_started:
            self._start_signal.emit()

    def push_frame(self, audio_bytes: bytes):
        """Decode and buffer incoming audio frame into the pull-mode PCM stream."""
        if not audio_bytes:
            return

        self.frame_count += 1
        self.total_bytes_in += len(audio_bytes)
        self.last_frame_time = time.time()

        # Detect format on first frame if not yet determined
        if self._is_aac is None:
            is_adts_aac = len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xF0) == 0xF0
            self._is_aac = is_adts_aac
            if self._is_aac:
                self._init_aac_decoder(target_sample_rate=self.sample_rate, target_channel_count=self.channel_count)

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

        max_bytes = max(self.sample_rate * self.channel_count * 2 * 3, len(decoded_pcm) * 2)
        self.pcm_stream.write_pcm(bytes(decoded_pcm), max_buffer_bytes=max_bytes)
        self.total_bytes_out += len(decoded_pcm)
        self._ensure_started()

    @pyqtSlot()
    def _do_stop(self):
        """Executed strictly on Main Qt Thread to stop QAudioSink timer safely."""
        if self.audio_sink:
            try:
                self.audio_sink.stop()
            except Exception:
                pass
            self.audio_sink = None
        self._is_started = False
        self.pcm_stream.clear()
        if self.pcm_stream.isOpen():
            try:
                self.pcm_stream.close()
            except Exception:
                pass

    def _close_sink(self):
        """Safely terminate QAudioSink and reset PCM stream via QueuedConnection."""
        self._stop_signal.emit()

    def feed(self):
        """Periodic maintenance and telemetry check."""
        now = time.time()
        # Suspend idle sink after 3.0s of silence to avoid ALSA starvation
        if self._is_started and (now - self.last_frame_time > 3.0):
            self._close_sink()
            logger.info(f"🔊 [Audio Ch{self.channel_id}] Stream IDLE (>3s) — suspended QAudioSink to release ALSA")
            return

        # Periodic throughput telemetry (every 5 seconds)
        if now - self._last_log_time >= 5.0:
            self._last_log_time = now
            state = self.audio_sink.state() if self.audio_sink else "None"
            logger.info(
                f"🔊 [Audio Ch{self.channel_id}] Metrics: backend=QAudioSink(Pull), frames={self.frame_count}, "
                f"in={self.total_bytes_in // 1024}KB, out={self.total_bytes_out // 1024}KB, "
                f"buf={self.pcm_stream.bytesAvailable()}B, state={state}"
            )

    def close(self):
        """Release audio sink and decoder resources."""
        self._close_sink()
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
                sink._init_playback(sink.sample_rate, sink.channel_count)
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

    def play_pcm_frame(self, audio_bytes: bytes, channel_id: int = 0):
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

        sink.push_frame(audio_bytes)

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

    def close(self):
        """Cleanly stop all audio sinks and microphone capture."""
        self._running = False
        self.stop_microphone()

        with self._sinks_lock:
            for sink in self.sinks.values():
                sink.close()
            self.sinks.clear()

        logger.info("QtAudioEngine closed and all audio resources released.")
