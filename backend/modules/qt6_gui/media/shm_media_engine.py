"""
shm_media_engine.py — SHM Media Transport Engine for Qt6 Frontend.

Reads downstream media frames (video RGBA/YUV420 & audio PCM) zero-copy from `nemo_media_shm_down`
and dispatches them directly to Qt6 render surfaces and audio sinks.
"""

import logging
import asyncio
from typing import Callable, Optional, Tuple

try:
    from shared.media_shm import BidirectionalMediaSHM, RingSharedMemoryBuffer
except ImportError:
    from backend.shared.media_shm import BidirectionalMediaSHM, RingSharedMemoryBuffer


logger = logging.getLogger("qt6_gui.shm_engine")


class QtSHMMediaEngine:
    """
    Shared Memory Media Dispatcher for Qt6 Frontend.
    """

    def __init__(self):
        self.shm: Optional[BidirectionalMediaSHM] = None
        self.on_video_frame: Optional[Callable[[bytes, int, int, int], None]] = None  # payload, width, height, ts_us
        self.on_audio_frame: Optional[Callable[[bytes, int, int], None]] = None  # payload, channel_id, ts_us
        self.on_stream_start: Optional[Callable[[], None]] = None
        self.on_stream_stop: Optional[Callable[[], None]] = None
        self.is_connected = False

    def connect_shm(self) -> bool:
        """Attach to existing shared memory buffers created by media_server/channel_manager."""
        logger.info("🔍 [SHM Engine Trace] Attempting BidirectionalMediaSHM(create=False)...")
        try:
            self.shm = BidirectionalMediaSHM(create=False)
            self.is_connected = True
            logger.info("QtSHMMediaEngine successfully attached to BidirectionalMediaSHM buffers")
            return True
        except Exception as exc:
            logger.warning("QtSHMMediaEngine failed to attach to SHM: %s", exc)
            self.is_connected = False
            return False


    def process_downstream_video(self, offset: int) -> None:
        """
        Reads video frame at offset from `nemo_media_shm_down` and dispatches to on_video_frame callback.
        """
        if not self.shm or offset < 0:
            return

        try:
            _, ts_low, payload = self.shm.downstream.read_frame(offset)
            if not payload:
                return

            # 1. Direct raw RGBA frame header check
            if len(payload) >= 12:
                import struct
                width, height, _ = struct.unpack(">III", payload[:12])
                if 0 < width <= 4096 and 0 < height <= 4096:
                    expected_len = width * height * 4
                    if len(payload) == 12 + expected_len:
                        rgba_pixels = payload[12:]
                        if self.on_video_frame:
                            self.on_video_frame(rgba_pixels, width, height, ts_low)
                        return

            # 2. Ignore raw H.264 NAL units from passing to QImage decoder
            if payload.startswith(b"\x00\x00\x00\x01") or payload.startswith(b"\x00\x00\x01"):
                return

            # 3. Fallback check for complete compressed image formats (JPEG / WebP)
            if (payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")) or payload.startswith(b"RIFF"):
                from PyQt6.QtGui import QImage
                qimg = QImage.fromData(payload)
                if not qimg.isNull():
                    rgba_img = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
                    w = rgba_img.width()
                    h = rgba_img.height()
                    ptr = rgba_img.bits()
                    ptr.setsize(rgba_img.sizeInBytes())
                    rgba_pixels = bytes(ptr)
                    if self.on_video_frame:
                        self.on_video_frame(rgba_pixels, w, h, ts_low)
        except Exception as exc:
            logger.debug("SHM video processing error at offset %d: %s", offset, exc)

    def process_downstream_audio(self, offset: int, channel_id: Optional[int] = None) -> None:
        """
        Reads audio frame at offset from `nemo_media_shm_down` and dispatches to on_audio_frame callback.
        """
        if not self.shm or offset < 0:
            return

        try:
            stream_type, ts_low, payload = self.shm.downstream.read_frame(offset)
            if not payload:
                return

            effective_channel_id = channel_id if channel_id is not None else stream_type
            if self.on_audio_frame:
                self.on_audio_frame(payload, effective_channel_id, ts_low)
        except Exception as exc:
            logger.debug("SHM audio processing error at offset %d: %s", offset, exc)

    def process_downstream_frame(self, offset: int) -> None:
        """Fallback dispatcher for downstream frames when stream type is read from SHM header."""
        if not self.shm or offset < 0:
            return

        try:
            stream_type, ts_low, payload = self.shm.downstream.read_frame(offset)
            if not payload:
                return
            # Treat non-video payloads as audio
            if self.on_audio_frame:
                self.on_audio_frame(payload, stream_type, ts_low)
        except Exception as exc:
            logger.debug("SHM frame processing error at offset %d: %s", offset, exc)

    def write_upstream_mic(self, pcm_data: bytes) -> int:
        """
        Writes 16kHz 16-bit Mono PCM mic frame zero-copy to `nemo_media_shm_up`.
        Returns SHM offset.
        """
        if not self.shm or not pcm_data:
            return -1
        try:
            # stream_type 1 for Speech/Mic audio
            return self.shm.upstream.write_frame(1, 0, pcm_data)
        except Exception as exc:
            logger.warning("Failed to write mic frame to SHM upstream: %s", exc)
            return -1

    def close(self):
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
        self.is_connected = False
