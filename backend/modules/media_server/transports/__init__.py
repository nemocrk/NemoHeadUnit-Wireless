"""
video_decoder transports — Strategy pattern for video decode + wire-format conversion.

Transport classes:
  H264PassthroughTransport   — zero-copy NAL passthrough (h264 mode)
  GStreamerMjpegTransport    — GStreamer HW decode → JPEG (mjpeg mode)
  GStreamerWebpTransport     — GStreamer HW decode → WebP (webp mode)
  FFmpegMjpegTransport       — FFmpeg subprocess → JPEG (mjpeg-ffmpeg mode)
  GStreamerYuv420Transport   — GStreamer HW decode → raw YUV420p (yuv420 mode)
  GStreamerRgbaTransport     — GStreamer HW decode → raw RGBA (rgba mode)
"""

from .base import BaseVideoTransport, TransportUnavailableError

try:
    from .h264_passthrough import H264PassthroughTransport
except ImportError:
    H264PassthroughTransport = None  # type: ignore

try:
    from .gstreamer_mjpeg import GStreamerMjpegTransport
except ImportError:
    GStreamerMjpegTransport = None  # type: ignore

try:
    from .gstreamer_webp import GStreamerWebpTransport
except ImportError:
    GStreamerWebpTransport = None  # type: ignore

try:
    from .ffmpeg_mjpeg import FFmpegMjpegTransport
except ImportError:
    FFmpegMjpegTransport = None  # type: ignore

try:
    from .gstreamer_yuv420 import GStreamerYuv420Transport
except ImportError:
    GStreamerYuv420Transport = None  # type: ignore

try:
    from .gstreamer_rgba import GStreamerRgbaTransport
except ImportError:
    GStreamerRgbaTransport = None  # type: ignore


# Registry: transport_mode name → transport class
_TRANSPORT_REGISTRY: dict[str, type[BaseVideoTransport]] = {}

for _cls in [
    H264PassthroughTransport,
    GStreamerMjpegTransport,
    GStreamerWebpTransport,
    FFmpegMjpegTransport,
    GStreamerYuv420Transport,
    GStreamerRgbaTransport,
]:
    if _cls is not None:
        _TRANSPORT_REGISTRY[_cls.transport_name] = _cls


def get_transport_class(mode: str) -> type[BaseVideoTransport]:
    """
    Return the transport class for the given mode name.
    Raises TransportUnavailableError if the mode is unknown or not importable.
    """
    cls = _TRANSPORT_REGISTRY.get(mode)
    if cls is None:
        raise TransportUnavailableError(
            f"Transport mode '{mode}' is unknown or its dependencies are not installed. "
            f"Available modes: {list(_TRANSPORT_REGISTRY.keys())}"
        )
    return cls


def get_available_modes() -> list[str]:
    """Return the list of transport mode names whose dependencies are satisfied."""
    available = []
    for name, cls in _TRANSPORT_REGISTRY.items():
        try:
            if cls.is_available():
                available.append(name)
        except Exception:
            pass
    return available


__all__ = [
    "BaseVideoTransport",
    "TransportUnavailableError",
    "H264PassthroughTransport",
    "GStreamerMjpegTransport",
    "GStreamerWebpTransport",
    "FFmpegMjpegTransport",
    "GStreamerYuv420Transport",
    "GStreamerRgbaTransport",
    "get_transport_class",
    "get_available_modes",
]
