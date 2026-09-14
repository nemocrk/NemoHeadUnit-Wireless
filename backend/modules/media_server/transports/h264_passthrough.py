"""
video_decoder/transports/h264_passthrough.py

Zero-copy H.264 NAL passthrough transport.
Forwards NAL bytes directly to on_frame_ready without any decoding or conversion.
This preserves the current WebCodecs-based frontend rendering path.

Availability: Always available (no external dependencies).
"""

from shared.logger import get_logger

log = get_logger("media_server")
from .base import BaseVideoTransport



class H264PassthroughTransport(BaseVideoTransport):
    """
    Zero-copy passthrough transport.

    feed_nal() → on_frame_ready(nal_bytes, timestamp_us, "h264")

    No GStreamer, no FFmpeg, no GPU required. Always available.
    Frontend renders via WebCodecs VideoDecoder.
    """

    transport_name = "h264"
    wire_format = "h264"

    @staticmethod
    def is_available() -> bool:
        return True

    async def start(self) -> None:
        log.info(
            "🎬 [Video Decoder] Mode: 'h264' (Passthrough) | Decoder: 'browser_webcodecs' | "
            "Acceleration: Frontend WebCodecs GPU Offload (Hardware Accelerated)"
        )

    async def feed_nal(self, nal_data: bytes, timestamp_us: int) -> None:
        if self.on_frame_ready and nal_data:
            await self.on_frame_ready(nal_data, timestamp_us, self.wire_format)

    async def stop(self) -> None:
        pass  # Nothing to tear down

    def get_diagnostics(self) -> dict:
        return {
            "transport": self.transport_name,
            "decoder_element": "browser_webcodecs",
            "hw_accelerated": True,
            "decoder_type": "Frontend WebCodecs GPU Offload",
            "details": {
                "description": "Zero-copy H.264 NAL passthrough to browser WebCodecs VideoDecoder",
            },
        }

