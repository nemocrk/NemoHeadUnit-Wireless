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

        self.channel_queues = {
            "media": bytearray(),
            "speech": bytearray(),
        }
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
                q_media_len = len(self.channel_queues["media"])
                q_speech_len = len(self.channel_queues["speech"])
            total_q_len = q_media_len + q_speech_len
            buffer_latency_ms = total_q_len / 192.0
            bytes_free = self.audio_sink.bytesFree() if self.audio_sink else 0
            sink_status = "active" if (self.sink_io and self.sink_io.isOpen()) else "inactive"
            logger.info(
                f"Frame processed: {len(audio_bytes)} bytes (cumulative: {self.frame_count}) | "
                f"Pipeline: sink={sink_status}, media_buf={q_media_len}B, speech_buf={q_speech_len}B ({buffer_latency_ms:.1f}ms), "
                f"sink_free={bytes_free}B"
            )

        if not self.aac_decoder:
            self._init_aac_decoder()

        if not self.sink_io or not self.sink_io.isOpen():
            self._init_playback()
            if not self.sink_io or not self.sink_io.isOpen():
                return

        decoded_pcm = bytearray()
        decode_success = False

        # Detect AAC ADTS frame header (syncword 0xFFF at start of packet)
        is_adts_aac = len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xF0) == 0xF0
        stream_key = "media" if is_adts_aac else "speech"

        if is_adts_aac and self.aac_decoder:
            try:
                import av
                packet = av.Packet(audio_bytes)
                frames = self.aac_decoder.decode(packet)
                if frames:
                    decode_success = True
                    for frame in frames:
                        if self.resampler:
                            resampled = self.resampler.resample(frame)
                            for r_frame in resampled:
                                decoded_pcm.extend(r_frame.to_ndarray().tobytes())
                        else:
                            decoded_pcm.extend(frame.to_ndarray().tobytes())
            except Exception as exc:
                logger.debug(f"AAC decode exception: {exc}")
        elif not is_adts_aac:
            # Channel 5 Speech / Channel 6 System uncompressed Linear PCM (16kHz 16-bit Mono)
            # Upsample 16kHz Mono to 48kHz Stereo by duplicating samples 3x across 2 channels
            try:
                import numpy as np
                pcm_16 = np.frombuffer(audio_bytes, dtype=np.int16)
                # If frame length corresponds to 16kHz mono (2048 bytes = 1024 int16 samples = 64ms @ 16kHz)
                # Repeat 3x (16kHz -> 48kHz) and duplicate to 2 channels (mono -> stereo)
                upsampled = np.repeat(pcm_16, 3)
                stereo = np.column_stack((upsampled, upsampled)).flatten()
                decoded_pcm.extend(stereo.tobytes())
                decode_success = True
            except Exception as exc:
                logger.debug(f"PCM upsample exception: {exc}")
                decoded_pcm.extend(audio_bytes)

        if not decoded_pcm:
            return

        # Push decoded PCM into isolated per-stream jitter buffer (thread-safe lock)
        with self._queue_lock:
            q = self.channel_queues[stream_key]
            q.extend(decoded_pcm)

            # Cap each isolated stream buffer to 1.5s latency (48000Hz * 2ch * 2B * 1.5s = 288,000 bytes)
            MAX_QUEUE_LEN = 288000
            if len(q) > MAX_QUEUE_LEN:
                dropped = len(q) - MAX_QUEUE_LEN
                del q[:dropped]
                logger.warning(f"Audio Mixer ({stream_key}): buffer overflow! Dropped {dropped} bytes (now at {len(q)}/{MAX_QUEUE_LEN})")

        if decode_success:
            logger.debug(f"Pipeline quality: decoded {len(decoded_pcm)} bytes for {stream_key} stream")
        else:
            logger.debug(f"Pipeline quality: passed through as raw PCM ({len(decoded_pcm)} bytes)")

    def _feed_loop(self):
        """
        Autonomous background thread continuously feeding mixed audio to QAudioSink.
        Operates completely independently of Qt UI event loop and window events.
        """
        import time
        while self._running:
            try:
                self._feed_audio_sink()
                self._poll_mic()
            except Exception as exc:
                logger.warning("Audio feed loop exception: %s", exc)
            time.sleep(0.005)  # 5ms feeding interval

    def _feed_audio_sink(self):
        """
        Mix active audio streams (Media + Speech/Assistant) dynamically into a single
        48kHz 16-bit Stereo stream and feed into QAudioSink based on available buffer space.
        """
        if not self.audio_sink or not self.sink_io or not self.sink_io.isOpen():
            return

        try:
            bytes_free = self.audio_sink.bytesFree()
            # Feed while hardware audio buffer has room for at least 10ms (1920 bytes)
            while bytes_free >= 1920:
                # 20ms mix slice (48000 * 2ch * 2B * 0.020s = 1920 bytes)
                slice_bytes = 1920
                if bytes_free < slice_bytes:
                    break

                with self._queue_lock:
                    q_media = self.channel_queues["media"]
                    q_speech = self.channel_queues["speech"]

                    has_media = len(q_media) >= slice_bytes
                    has_speech = len(q_speech) >= slice_bytes

                    if not has_media and not has_speech:
                        break

                    # Duck media volume to 25% when assistant/speech is actively playing
                    media_gain = 0.25 if (has_speech or len(q_speech) > 0) else 1.0
                    speech_gain = 1.0

                    if has_media and has_speech:
                        import numpy as np
                        chunk_m = np.frombuffer(q_media[:slice_bytes], dtype=np.int16).astype(np.int32)
                        chunk_s = np.frombuffer(q_speech[:slice_bytes], dtype=np.int16).astype(np.int32)
                        del q_media[:slice_bytes]
                        del q_speech[:slice_bytes]

                        # Software Mix with soft clipping
                        mixed = (chunk_m * media_gain + chunk_s * speech_gain).astype(np.int32)
                        # Vectorized soft-clipping saturation
                        np.clip(mixed, -32768, 32767, out=mixed)
                        mixed_chunk = mixed.astype(np.int16).tobytes()
                    elif has_media:
                        if media_gain < 1.0:
                            import numpy as np
                            chunk_m = np.frombuffer(q_media[:slice_bytes], dtype=np.int16).astype(np.int32)
                            del q_media[:slice_bytes]
                            mixed_chunk = (chunk_m * media_gain).astype(np.int16).tobytes()
                        else:
                            mixed_chunk = bytes(q_media[:slice_bytes])
                            del q_media[:slice_bytes]
                    else:  # has_speech
                        mixed_chunk = bytes(q_speech[:slice_bytes])
                        del q_speech[:slice_bytes]

                written = self.sink_io.write(mixed_chunk)
                if written > 0:
                    bytes_free -= written
                else:
                    break
        except Exception as exc:
            logger.debug("Audio mixer feed exception: %s", exc)





    def start_microphone(self) -> bool:
        """Start capturing 16kHz 16-bit Mono PCM mic audio from default input device."""
        if self.audio_source:
            return True  # Already running

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
                logger.info(f"🎤 [Microphone] 16kHz Int16 Mono not natively supported, requested fallback format: {pref_fmt.sampleFormat()} (rate={pref_fmt.sampleRate()}, ch={pref_fmt.channelCount()})")
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
        """Read any available mic data synchronously from source_io (called by background feed loop and readyRead)."""
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
        CHUNK_SIZE = 640  # 320 samples * 2 bytes/sample = 640 bytes (20ms at 16kHz)

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


