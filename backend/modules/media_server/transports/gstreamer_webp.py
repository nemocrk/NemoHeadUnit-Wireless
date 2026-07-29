"""
video_decoder/transports/gstreamer_webp.py

GStreamer transport: H.264 HW decode → WebP per-frame encoding.

Pipeline:
    appsrc → h264parse → decodebin → videoconvert → [videoscale] → webpenc → appsink

WebP produces ~30-40% smaller frames than JPEG at equivalent visual quality.
The frontend renders via createImageBitmap(blob, {type:'image/webp'}) → canvas.drawImage().

Availability: Requires GStreamer + webpenc element (gstreamer1.0-plugins-bad).
"""

from .gstreamer_base import GStreamerBaseTransport, _gst_available, _element_exists


class GStreamerWebpTransport(GStreamerBaseTransport):
    """
    GStreamer HW-accelerated H.264 decode → WebP per-frame transport.

    Uses webpenc speed=4 for a good balance of encode speed vs. compression ratio.
    speed range: 0 (slowest/best compression) to 6 (fastest/worst compression).
    """

    transport_name = "webp"
    wire_format = "webp"

    @staticmethod
    def is_available() -> bool:
        return _gst_available() and _element_exists("webpenc")

    def _build_pipeline_string(self) -> str:
        scale_frag = self._scale_caps_fragment("I420")
        return (
            "appsrc name=src is-live=true format=time do-timestamp=true "
            "! h264parse "
            "! decodebin "
            "! videoconvert "
            f"{scale_frag}"
            f"! webpenc quality={self.jpeg_quality} speed=4 "
            "! appsink name=sink emit-signals=true max-buffers=2 drop=true"
        )
