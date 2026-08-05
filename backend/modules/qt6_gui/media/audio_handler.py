"""
audio_handler.py — Qt6 Multimedia Audio Output Playback & Microphone Capture Engine.

Uses Qt6 `QAudioSink` for PCM playback and `QAudioSource` for 16kHz 16-bit Mono mic capture.
"""

import logging
from typing import Callable, Optional
from PyQt6.QtCore import QByteArray, QIODevice, QObject, pyqtSignal

try:
    from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QAudioSource, QMediaDevices
    HAS_MULTIMEDIA = True
except ImportError:
    HAS_MULTIMEDIA = False
    QAudioSink = None
    QAudioSource = None
    QMediaDevices = None


logger = logging.getLogger("qt6_gui.audio_handler")


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
        self.playback_timer: Optional[QTimer] = None

        self.aac_decoder = None
        self.resampler = None
        self._init_aac_decoder()
        # Defer QAudioSink initialization to first play call or background timer to prevent blocking GUI boot
        QTimer.singleShot(200, self._init_playback)
        logger.info("🔍 [Audio Engine Trace] QtAudioEngine.__init__ completed cleanly!")



    def _init_aac_decoder(self):
        """Initialize PyAV FFmpeg AAC decoder and 48kHz Stereo AudioResampler."""
        try:
            import av
            self.aac_decoder = av.CodecContext.create("aac", "r")
            self.resampler = av.AudioResampler(format="s16", layout="stereo", rate=48000)
            logger.info("QtAudioEngine PyAV AAC decoder initialized (48kHz Int16 Stereo output)")
        except Exception as exc:
            logger.warning("Failed to initialize PyAV AAC decoder: %s", exc)

    def _init_playback(self):
        """Initialize QAudioSink for 48kHz 16-bit Stereo PCM output."""
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
            # 250ms buffer size (48000Hz * 2ch * 2B * 0.25s = 48000B)
            self.audio_sink.setBufferSize(48000 * 2 * 2 // 4)
            self.sink_io = self.audio_sink.start()

            if not self.playback_timer:
                self.playback_timer = QTimer()
                self.playback_timer.setInterval(10)
                self.playback_timer.timeout.connect(self._feed_audio_sink)
                self.playback_timer.start()

            logger.info("QtAudioEngine playback initialized successfully (48kHz Int16 Stereo, 250ms buffer)")


        except Exception as exc:
            logger.warning("Failed to initialize Qt6 QAudioSink playback: %s", exc)


    def play_pcm_frame(self, audio_bytes: bytes):
        """Decode compressed AAC or push raw PCM audio frame into jitter queue."""
        if not audio_bytes:
            return

        if not self.sink_io or not self.sink_io.isOpen():
            self._init_playback()
            if not self.sink_io or not self.sink_io.isOpen():
                return


        decoded_pcm = bytearray()
        if self.aac_decoder:
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
                pass

        if not decoded_pcm:
            decoded_pcm.extend(audio_bytes)

        # Enqueue decoded PCM into jitter queue
        self.pcm_queue.extend(decoded_pcm)

        # Prevent queue overflow (>500ms audio buffer)
        MAX_QUEUE_LEN = 48000 * 2 * 2 // 2  # 96000 bytes
        if len(self.pcm_queue) > MAX_QUEUE_LEN:
            del self.pcm_queue[: len(self.pcm_queue) - MAX_QUEUE_LEN]

    def _feed_audio_sink(self):
        """10ms QTimer ticker feeding steady PCM chunks to QAudioSink output device."""
        if not self.sink_io or not self.sink_io.isOpen() or not self.pcm_queue:
            return

        CHUNK_SIZE = 1920  # 10ms chunk at 48kHz 16-bit Stereo (480 samples * 4 bytes/sample)
        if len(self.pcm_queue) >= CHUNK_SIZE:
            chunk = bytes(self.pcm_queue[:CHUNK_SIZE])
            del self.pcm_queue[:CHUNK_SIZE]
            try:
                self.sink_io.write(chunk)
            except Exception as exc:
                logger.debug("Audio sink write exception: %s", exc)



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
        self.stop_microphone()
        if self.playback_timer:
            try:
                self.playback_timer.stop()
            except Exception:
                pass
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


