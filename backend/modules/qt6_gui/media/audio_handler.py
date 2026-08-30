"""
audio_handler.py — Qt6 Multimedia Audio Output Playback & Microphone Capture Engine.

Uses Qt6 `QAudioSink` for PCM playback and `QAudioSource` for 16kHz 16-bit Mono mic capture.
"""

import logging
import threading
from typing import Callable, Optional
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


class QtAudioEngine:
    """
    Qt6 Multimedia Audio Engine managing Audio Output & Microphone Input.
    Uses a dedicated background thread for feeding QAudioSink to guarantee continuous
    playback even when the main Qt GUI event loop is blocked (e.g. window dragging/resizing).
    """

    def __init__(self):
        logger.info("🔍 [Audio Engine Trace] Entering QtAudioEngine.__init__...")
        self.mic_data_captured = Signal()

        self.audio_sink: Optional[QAudioSink] = None
        self.sink_io: Optional[QIODevice] = None

        self.audio_source: Optional[QAudioSource] = None
        self.source_io: Optional[QIODevice] = None
        self.mic_accumulator = bytearray()

        self.pcm_queue = bytearray()
        self._queue_lock = threading.Lock()

        # Dedicated background feeding thread (bypasses main UI thread stalls during window move/drag)
        self._feed_thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Anti-jitter pre-roll state (60ms = 3 cycles of 20ms @ 48kHz 16-bit stereo = 11,520 bytes)
        self.is_pre_rolling: bool = True
        self.pre_roll_threshold_bytes: int = 48000 * 2 * 2 * 3 // 50  # ~11520 bytes (60ms)

        self.aac_decoder = None
        self.resampler = None

        self.frame_count = 0

        self._init_aac_decoder()
        # Eagerly initialize QAudioSink immediately on boot to prevent startup queue drops
        self._init_playback()
        logger.info("Pipeline quality state: AAC decoder=%s, sink status=%s",
                    "not initialized" if not self.aac_decoder else "ready",
                    "not initialized" if not self.audio_sink else "active")
        logger.info("🔍 [Audio Engine Trace] QtAudioEngine.__init__ completed cleanly!")

    def _init_aac_decoder(self):
        """Initialize PyAV FFmpeg AAC decoder and 48kHz Stereo AudioResampler."""
        try:
            import av
            self.aac_decoder = av.CodecContext.create("aac", "r")
            self.resampler = av.AudioResampler(format="s16", layout="stereo", rate=48000)
            logger.info("AAC decoder ready (48kHz Int16 Stereo)")
        except Exception as exc:
            logger.warning("AAC decoder init failed, will use raw PCM: %s", exc)

    def _init_playback(self):
        """Initialize and eagerly start QAudioSink for 48kHz 16-bit Stereo PCM output."""
        if self.sink_io and self.sink_io.isOpen():
            return
        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            default_device = QMediaDevices.defaultAudioOutput()
            if not default_device or not default_device.isFormatSupported(fmt):
                logger.warning("Default audio output format not supported directly, falling back...")
                if default_device:
                    fmt = default_device.preferredFormat()

            self.audio_sink = QAudioSink(default_device, fmt)
            # 500ms hardware buffer size (48000Hz * 2ch * 2B * 0.5s = 96000B)
            self.audio_sink.setBufferSize(48000 * 2 * 2 // 2)
            self.sink_io = self.audio_sink.start()

            # Start dedicated background feeding thread (runs independently of Qt UI event loop)
            self._running = True
            if not self._feed_thread or not self._feed_thread.is_alive():
                self._feed_thread = threading.Thread(target=self._feed_loop, daemon=True, name="QtAudioFeedThread")
                self._feed_thread.start()

            logger.info("QtAudioEngine playback initialized eagerly with dedicated background thread (48kHz Int16 Stereo)")

        except Exception as exc:
            logger.warning("Failed to initialize Qt6 QAudioSink playback: %s", exc)

    @staticmethod
    def _soft_clip(sample: int) -> int:
        """Soft-clipping saturation curve to prevent digital wrap-around distortion when channels mix."""
        if sample > 20480:
            diff = sample - 20480
            return int(20480 + (diff * 12287) / (diff + 24574))
        elif sample < -20480:
            diff = -sample - 20480
            return int(-(20480 + (diff * 12287) / (diff + 24574)))
        return sample

    def play_pcm_frame(self, audio_bytes: bytes):
        """Decode compressed AAC or push raw PCM audio frame into jitter queue."""
        if not audio_bytes:
            return

        # Discard tiny frames (< 100 bytes) - likely config messages, not real AAC
        if len(audio_bytes) < 100:
            logger.debug(f"Discarding tiny frame ({len(audio_bytes)} bytes) - treating as config message")
            return

        self.frame_count += 1
        if self.frame_count % 50 == 0:
            with self._queue_lock:
                q_len = len(self.pcm_queue)
            # 48000 Hz * 2 channels * 2 bytes = 192,000 bytes/sec -> 192 bytes/ms
            buffer_latency_ms = q_len / 192.0
            bytes_free = self.audio_sink.bytesFree() if self.audio_sink else 0
            sink_status = "active" if (self.sink_io and self.sink_io.isOpen()) else "inactive"
            logger.info(
                f"Frame processed: {len(audio_bytes)} bytes (cumulative: {self.frame_count}) | "
                f"Pipeline: sink={sink_status}, buffer={q_len}B ({buffer_latency_ms:.1f}ms), "
                f"sink_free={bytes_free}B, pre_roll={self.is_pre_rolling}"
            )

        if not self.aac_decoder:
            self._init_aac_decoder()

        if not self.sink_io or not self.sink_io.isOpen():
            self._init_playback()
            if not self.sink_io or not self.sink_io.isOpen():
                return

        decoded_pcm = bytearray()
        decode_success = False
        if self.aac_decoder:
            try:
                import av
                packet = av.Packet(audio_bytes)
                frames = self.aac_decoder.decode(packet)
                decode_success = True if frames else False
                if frames:
                    for frame in frames:
                        if self.resampler:
                            resampled = self.resampler.resample(frame)
                            for r_frame in resampled:
                                decoded_pcm.extend(r_frame.to_ndarray().tobytes())
                        else:
                            decoded_pcm.extend(frame.to_ndarray().tobytes())
            except Exception as exc:
                logger.debug(f"AAC decode exception, using raw: {exc}")
                pass  # Fall back to raw PCM if decoding fails

        if not decoded_pcm:
            decoded_pcm.extend(audio_bytes)

        # Enqueue decoded PCM into jitter queue (thread-safe lock)
        with self._queue_lock:
            self.pcm_queue.extend(decoded_pcm)

            # Cap max buffer to 2.0s latency (48000Hz * 2ch * 2B * 2.0s = 384000 bytes)
            # Allows headroom during window drags, modal resizing, or UI event loop stalls
            MAX_QUEUE_LEN = 384000
            if len(self.pcm_queue) > MAX_QUEUE_LEN:
                dropped = len(self.pcm_queue) - MAX_QUEUE_LEN
                del self.pcm_queue[:dropped]
                logger.warning(f"Pipeline quality: queue overflow! Dropped {dropped} bytes (now at {len(self.pcm_queue)}/{MAX_QUEUE_LEN})")

        if decode_success:
            logger.debug(f"Pipeline quality: decoded {len(decoded_pcm)} bytes from AAC stream")
        else:
            logger.debug(f"Pipeline quality: passed through as raw PCM ({len(decoded_pcm)} bytes)")

    def _feed_loop(self):
        """
        Autonomous background thread continuously feeding QAudioSink independently of Qt UI event loop.
        Guarantees audio never pauses or jitters when the user moves, drags, or resizes the Qt window.
        """
        import time
        while self._running:
            try:
                self._feed_audio_sink()
            except Exception as exc:
                logger.warning("Audio feed loop exception: %s", exc)
            time.sleep(0.005)  # 5ms feeding interval

    def _feed_audio_sink(self):
        """Feed PCM audio chunks dynamically based on QAudioSink.bytesFree() with pre-roll protection."""
        if not self.audio_sink or not self.sink_io or not self.sink_io.isOpen():
            return

        with self._queue_lock:
            if not self.pcm_queue:
                return

            # Pre-roll check: wait until buffer accumulates 60ms before starting playback
            if self.is_pre_rolling:
                if len(self.pcm_queue) >= self.pre_roll_threshold_bytes:
                    self.is_pre_rolling = False
                    logger.debug("QtAudioEngine: Pre-roll threshold reached (%d bytes), starting playback", len(self.pcm_queue))
                else:
                    return  # Keep buffering to prevent underruns

            try:
                bytes_free = self.audio_sink.bytesFree()
                # Feed while hardware audio buffer has room for at least 10ms (1920 bytes)
                while bytes_free >= 1920 and self.pcm_queue:
                    to_write = min(len(self.pcm_queue), bytes_free)
                    # Align to 4-byte sample boundaries (16-bit stereo = 4 bytes per frame)
                    to_write = (to_write // 4) * 4
                    if to_write == 0:
                        break

                    chunk = bytes(self.pcm_queue[:to_write])
                    written = self.sink_io.write(chunk)
                    if written > 0:
                        del self.pcm_queue[:written]
                        bytes_free -= written
                        logger.debug(f"Pipeline quality: wrote {written} bytes (free: {bytes_free}, queue now: {len(self.pcm_queue)})")
                    else:
                        break

                # If queue emptied out completely, enter pre-rolling again to prevent jitter on next packet burst
                if len(self.pcm_queue) == 0:
                    self.is_pre_rolling = True

            except Exception as exc:
                logger.warning(f"Sink write exception: {exc}")




    def start_microphone(self) -> bool:
        """Start capturing 16kHz 16-bit Mono PCM mic audio from default input device."""
        if self.audio_source:
            return True  # Already running

        try:
            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            default_device = QMediaDevices.defaultAudioInput()
            if not default_device.isFormatSupported(fmt):
                fmt = default_device.preferredFormat()

            self.audio_source = QAudioSource(default_device, fmt)
            self.source_io = self.audio_source.start()
            if self.source_io:
                self.source_io.readyRead.connect(self._on_mic_ready_read)
                logger.info("Microphone uplink started via Qt6 QAudioSource (16kHz 16-bit Mono PCM)")
                return True
        except Exception as exc:
            logger.warning("Failed to start Qt6 QAudioSource microphone: %s", exc)
            self.stop_microphone()
        return False

    def _on_mic_ready_read(self):
        """Read captured audio from mic QIODevice, accumulator into 20ms (640B) packets."""
        if not self.source_io:
            return
        data = self.source_io.readAll().data()
        if not data:
            return

        self.mic_accumulator.extend(data)
        CHUNK_SIZE = 640  # 320 samples * 2 bytes/sample = 640 bytes (20ms at 16kHz)

        while len(self.mic_accumulator) >= CHUNK_SIZE:
            chunk = bytes(self.mic_accumulator[:CHUNK_SIZE])
            del self.mic_accumulator[:CHUNK_SIZE]
            logger.debug(f"Pipeline quality: mic emitted {len(chunk)} bytes (queue now: {len(self.mic_accumulator)})")
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
            logger.info("Microphone uplink stopped")

    def close(self):
        """Cleanly stop audio sink playback, microphone capture, and AAC decoder."""
        self._running = False
        self.stop_microphone()
        if self._feed_thread and self._feed_thread.is_alive() and threading.current_thread() != self._feed_thread:
            try:
                self._feed_thread.join(timeout=0.2)
            except Exception:
                pass
        self._feed_thread = None

        with self._queue_lock:
            self.pcm_queue.clear()

        if self.audio_sink:
            try:
                self.audio_sink.stop()
            except Exception:
                pass
            self.audio_sink = None
            self.sink_io = None
        self.aac_decoder = None
        self.resampler = None
        logger.info("QtAudioEngine closed and all audio resources released.")


