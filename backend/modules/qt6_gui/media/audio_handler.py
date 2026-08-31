"""
audio_handler.py — Dynamic Multi-Sink Qt6 Multimedia Audio Output Playback & Microphone Capture Engine.

Manages dedicated `QAudioSink` instances per channel to delegate multi-stream mixing directly to the
operating system audio subsystem (PipeWire / PulseAudio on Linux, WASAPI on Windows), preventing digital clipping
and inter-stream jitter distortion.
"""

import logging
import threading
import time
from typing import Callable, Dict, Optional
from PyQt6.QtCore import QByteArray, QIODevice, QObject, QTimer, pyqtSignal, QThread
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


class DynamicChannelAudioSink:
    """
    Dedicated QAudioSink pipeline for an individual audio channel.
    Manages format detection (AAC ADTS vs PCM), dedicated decoding contexts, and jitter buffering.
    """

    def __init__(self, channel_id: int, sample_rate: int = 48000, channel_count: int = 2):
        self.channel_id = channel_id
        self.sample_rate = sample_rate
        self.channel_count = channel_count
        self.audio_sink: Optional[QAudioSink] = None
        self.sink_io: Optional[QIODevice] = None
        self.queue = bytearray()
        self.queue_lock = threading.Lock()

        self.aac_decoder = None
        self.resampler = None
        self._is_aac: Optional[bool] = None
        self.frame_count = 0

    def _init_aac_decoder(self):
        """Initialize per-channel PyAV FFmpeg AAC decoder."""
        try:
            import av
            self.aac_decoder = av.CodecContext.create("aac", "r")
            self.resampler = av.AudioResampler(format="s16", layout="stereo", rate=48000)
            logger.info(f"Audio Channel {self.channel_id}: AAC decoder initialized (48kHz Int16 Stereo)")
        except Exception as exc:
            logger.warning(f"Audio Channel {self.channel_id}: AAC decoder init failed: {exc}")

    def _init_playback(self, sample_rate: int, channel_count: int):
        """Initialize QAudioSink with specified rate and channels."""
        if self.sink_io and self.sink_io.isOpen():
            return

        self.sample_rate = sample_rate
        self.channel_count = channel_count

        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(sample_rate)
            fmt.setChannelCount(channel_count)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            default_device = QMediaDevices.defaultAudioOutput()
            if not default_device or not default_device.isFormatSupported(fmt):
                logger.debug(f"Audio Channel {self.channel_id}: Format {sample_rate}Hz {channel_count}ch falling back to preferred")
                if default_device:
                    fmt = default_device.preferredFormat()

            self.audio_sink = QAudioSink(default_device, fmt)
            # 500ms hardware buffer
            self.audio_sink.setBufferSize(sample_rate * channel_count * 2 // 2)
            self.sink_io = self.audio_sink.start()
            logger.info(f"🔊 Audio Channel {self.channel_id}: QAudioSink opened ({sample_rate}Hz, {channel_count}ch)")
        except Exception as exc:
            logger.warning(f"Failed to initialize QAudioSink for channel {self.channel_id}: {exc}")

    def push_frame(self, audio_bytes: bytes):
        """Decode or buffer incoming audio frame."""
        if not audio_bytes or len(audio_bytes) < 100:
            return

        self.frame_count += 1

        # Detect format on first frame if not yet determined
        if self._is_aac is None:
            is_adts_aac = len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xF0) == 0xF0
            self._is_aac = is_adts_aac
            if self._is_aac:
                self._init_aac_decoder()
                self._init_playback(48000, 2)
            else:
                # Default PCM to 16kHz 1-ch if ~64ms packet (2048B) or 16k mono size, else 48k 2-ch
                rate = 16000 if len(audio_bytes) in (640, 1024, 2048, 4096) else 48000
                channels = 1 if rate == 16000 else 2
                self._init_playback(rate, channels)

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

        with self.queue_lock:
            self.queue.extend(decoded_pcm)
            # Cap buffer to 1.5s max
            max_bytes = self.sample_rate * self.channel_count * 2 * 3 // 2
            if len(self.queue) > max_bytes:
                dropped = len(self.queue) - max_bytes
                del self.queue[:dropped]
                logger.warning(f"Audio Channel {self.channel_id}: buffer overflow! Dropped {dropped}B")

    def feed(self):
        """Write available audio slices directly to QAudioSink."""
        if not self.audio_sink or not self.sink_io or not self.sink_io.isOpen():
            return

        try:
            bytes_free = self.audio_sink.bytesFree()
            slice_bytes = int(self.sample_rate * self.channel_count * 2 * 0.02)  # 20ms slice
            if slice_bytes <= 0:
                slice_bytes = 640

            while bytes_free >= slice_bytes:
                with self.queue_lock:
                    if len(self.queue) < slice_bytes:
                        break
                    chunk = bytes(self.queue[:slice_bytes])
                    del self.queue[:slice_bytes]

                written = self.sink_io.write(chunk)
                if written > 0:
                    bytes_free -= written
                else:
                    break
        except Exception as exc:
            logger.debug(f"Audio Channel {self.channel_id} feed exception: {exc}")

    def close(self):
        """Release audio sink and decoder resources."""
        with self.queue_lock:
            self.queue.clear()

        if self.audio_sink:
            try:
                self.audio_sink.stop()
            except Exception:
                pass
            self.audio_sink = None
            self.sink_io = None

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

        self.audio_source: Optional[QAudioSource] = None
        self.source_io: Optional[QIODevice] = None
        self.mic_accumulator = bytearray()

        self.sinks: Dict[int, DynamicChannelAudioSink] = {}
        self._sinks_lock = threading.Lock()

        # Dedicated background feeding thread
        self._feed_thread: Optional[threading.Thread] = None
        self._running: bool = True

        self._start_feed_thread()
        logger.info("🔍 [Audio Engine Trace] QtAudioEngine initialized with dynamic multi-sink backend!")

    def _start_feed_thread(self):
        if not self._feed_thread or not self._feed_thread.is_alive():
            self._running = True
            self._feed_thread = threading.Thread(target=self._feed_loop, daemon=True, name="QtAudioFeedThread")
            self._feed_thread.start()

    def play_pcm_frame(self, audio_bytes: bytes, channel_id: int = 0):
        """Dispatch audio frame to its dedicated channel sink."""
        if not audio_bytes:
            return

        with self._sinks_lock:
            sink = self.sinks.get(channel_id)
            if sink is None:
                sink = DynamicChannelAudioSink(channel_id)
                self.sinks[channel_id] = sink
                logger.info(f"🔊 Registered new dynamic audio sink for channel {channel_id}")

        sink.push_frame(audio_bytes)

    def _feed_loop(self):
        """Autonomous background thread feeding all active channel sinks."""
        while self._running:
            try:
                with self._sinks_lock:
                    active_sinks = list(self.sinks.values())

                for sink in active_sinks:
                    sink.feed()

                self._poll_mic()
            except Exception as exc:
                logger.warning("Audio feed loop exception: %s", exc)
            time.sleep(0.005)  # 5ms feeding interval

    def start_microphone(self) -> bool:
        """Start capturing 16kHz 16-bit Mono PCM mic audio from default input device."""
        if self.audio_source:
            return True

        try:
            default_device = QMediaDevices.defaultAudioInput()
            if not default_device or default_device.isNull():
                logger.warning("🎤 [Microphone] No default audio input device found!")
                return False

            dev_name = default_device.description()
            logger.info(f"🎤 [Microphone] Starting microphone on input device: '{dev_name}'")

            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            if not default_device.isFormatSupported(fmt):
                pref_fmt = default_device.preferredFormat()
                logger.info(f"🎤 [Microphone] Fallback format: {pref_fmt.sampleFormat()} (rate={pref_fmt.sampleRate()}, ch={pref_fmt.channelCount()})")
                fmt = pref_fmt

            self.audio_source = QAudioSource(default_device, fmt)
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
        """Cleanly stop all audio sinks, microphone capture, and background threads."""
        self._running = False
        self.stop_microphone()
        if self._feed_thread and self._feed_thread.is_alive() and threading.current_thread() != self._feed_thread:
            try:
                self._feed_thread.join(timeout=0.2)
            except Exception:
                pass
            self._feed_thread = None

        with self._sinks_lock:
            for sink in self.sinks.values():
                sink.close()
            self.sinks.clear()

        logger.info("QtAudioEngine closed and all audio resources released.")
