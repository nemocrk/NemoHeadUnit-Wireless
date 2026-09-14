"""
video_decoder/transports/base.py

Abstract base class for all video transport strategies.
Each transport implements: is_available(), start(), feed_nal(), stop().
The on_frame_ready callback is set by VideoDecoderModule before calling start().
"""

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional


class TransportUnavailableError(RuntimeError):
    """Raised when a transport's runtime dependencies are not met on the current platform."""
    pass


class BaseVideoTransport(ABC):
    """
    Abstract interface for video transport strategies.

    Subclasses implement a specific decode + wire-format pipeline.
    The owner (VideoDecoderModule) sets on_frame_ready before calling start().

    Callback signature:
        on_frame_ready(frame_bytes: bytes, timestamp_us: int, wire_format: str) -> Awaitable[None]
    """

    # Class-level constants — must be set by each subclass
    transport_name: str  # e.g. "h264", "mjpeg", "yuv420", "rgba", "webp"
    wire_format: str     # What the frontend receives — same as transport_name for most modes

    def __init__(self, jpeg_quality: int = 75, video_scale: str = "", video_codec: str = "H264") -> None:
        """
        Args:
            jpeg_quality: JPEG/WebP encode quality (50-95). Ignored for raw/passthrough modes.
            video_scale:  Target resolution for videoscale element, e.g. "960x540".
                          Empty string = native resolution. Ignored for h264/ffmpeg modes.
            video_codec:  Active video codec: 'H264', 'H265', 'VP9', 'AV1'.
        """
        self.jpeg_quality = max(50, min(95, jpeg_quality))
        self.video_scale = video_scale.strip()
        self.video_codec = video_codec.strip().upper()
        self.on_frame_ready: Optional[Callable[[bytes, int, str], Awaitable[None]]] = None

    @staticmethod
    @abstractmethod
    def is_available() -> bool:
        """
        Check if this transport's runtime dependencies are satisfied on the current platform.
        Must be a pure static check — no side effects, no I/O.
        """
        ...

    @abstractmethod
    async def start(self) -> None:
        """
        Initialize and start the transport pipeline.
        Raises TransportUnavailableError if startup fails.
        """
        ...

    @abstractmethod
    async def feed_nal(self, nal_data: bytes, timestamp_us: int) -> None:
        """
        Submit a raw H.264 Annex-B NAL unit for processing.
        The transport will eventually call on_frame_ready() with the output frame.
        This method should be non-blocking (fire and forget into the pipeline).
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """
        Tear down the transport pipeline and release all resources.
        Must be idempotent — safe to call even if not started.
        """
        ...

    def _parse_video_scale(self) -> tuple[int, int] | None:
        """
        Parse video_scale string "WxH" → (width, height) tuple, or None if empty/invalid.
        """
        if not self.video_scale:
            return None
        try:
            parts = self.video_scale.lower().replace("x", " ").replace(",", " ").split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except (ValueError, AttributeError):
            pass
        return None

    def get_diagnostics(self) -> dict:
        """
        Return diagnostic metadata about the active video decoder and hardware acceleration status.
        """
        return {
            "transport": getattr(self, "transport_name", "unknown"),
            "decoder_element": "unknown",
            "hw_accelerated": False,
            "decoder_type": "Unknown Decoder",
            "details": {},
        }

