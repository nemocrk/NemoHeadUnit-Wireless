"""
video_decoder/transports/gstreamer_mjpeg.py

GStreamer transport: H.264 HW decode → JPEG per-frame encoding.

Pipeline:
    appsrc → h264parse → decodebin → videoconvert → [videoscale] → jpegenc → appsink

The frontend renders via createImageBitmap(blob, {type:'image/jpeg'}) → canvas.drawImage().
No WebCodecs dependency. Works on any browser with <canvas>.

Availability: Requires GStreamer + jpegenc element (gstreamer1.0-plugins-good).
"""

from .gstreamer_base import GStreamerBaseTransport, _gst_available, _element_exists


class GStreamerMjpegTransport(GStreamerBaseTransport):
    """
    GStreamer HW-accelerated H.264 decode → JPEG per-frame transport.

    decodebin auto-selects the best available H.264 decoder at runtime:
      - vaapih264dec  (Intel VA-API)
      - nvh264dec     (NVIDIA NVDEC)
      - d3d11h264dec  (Windows DXVA2/D3D11)
      - v4l2h264dec   (ARM/V4L2)
      - avdec_h264    (libav software fallback)
    """

    transport_name = "mjpeg"
    wire_format = "mjpeg"

    @staticmethod
    def is_available() -> bool:
        return _gst_available() and _element_exists("jpegenc")

    def _build_pipeline_string(self) -> str:
        scale_frag = self._scale_caps_fragment("I420")
        parser = self._get_parser_element()
        return (
            "appsrc name=src is-live=true format=time do-timestamp=true "
            f"! {parser} "
            "! decodebin name=dec "
            "! videoconvert "
            f"{scale_frag}"
            f"! jpegenc quality={self.jpeg_quality} "
            "! appsink name=sink emit-signals=true max-buffers=2 drop=true"
        )

