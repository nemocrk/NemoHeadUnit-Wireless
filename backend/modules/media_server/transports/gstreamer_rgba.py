"""
video_decoder/transports/gstreamer_rgba.py

GStreamer transport: H.264 HW decode → raw RGBA pixel frames.

Pipeline:
    appsrc → h264parse → decodebin → videoconvert → [videoscale] → appsink (RGBA)

Wire format per frame:
    [9-byte standard media header: ChannelID(1) + Timestamp(8)]  ← added by VideoDecoderModule
    [Width:  4 bytes uint32 BE]
    [Height: 4 bytes uint32 BE]
    [Flags:  4 bytes uint32 BE, reserved=0]
    [RGBA data: Width × Height × 4 bytes]

Frontend renders via:
    new ImageData(new Uint8ClampedArray(rgbaData), width, height)
    ctx.putImageData(imageData, 0, 0)

Zero dependencies, zero shaders, zero decode — works on ANY browser with <canvas>.
Also useful as a debugging/correctness baseline.

Bandwidth: ~110 MB/s at 720p@30fps. Suitable for loopback only.

Availability: Requires GStreamer (no additional plugins needed beyond -plugins-base).
"""

import struct
from .gstreamer_base import GStreamerBaseTransport, _gst_available, _element_exists


class GStreamerRgbaTransport(GStreamerBaseTransport):
    """
    GStreamer HW-accelerated H.264 decode → raw RGBA transport.

    Simplest possible frontend rendering: putImageData() with zero decoding.
    Suitable only for loopback. The auto-negotiation prevents use over networks.
    """

    transport_name = "rgba"
    wire_format = "rgba"

    _frame_width: int = 0
    _frame_height: int = 0

    @staticmethod
    def is_available() -> bool:
        return _gst_available() and _element_exists("videoconvert")

    def _build_pipeline_string(self) -> str:
        scale_frag = self._scale_caps_fragment("RGBA")
        return (
            "appsrc name=src is-live=true format=time do-timestamp=true "
            "! h264parse "
            "! decodebin "
            "! videoconvert "
            f"{scale_frag}"
            "! appsink name=sink emit-signals=true max-buffers=2 drop=true"
        )

    def _extract_frame(self, sample) -> bytes:
        """
        Extract RGBA buffer and prepend the 12-byte dimension header.
        """
        caps = sample.get_caps()
        if caps:
            structure = caps.get_structure(0)
            ok_w, w = structure.get_int("width")
            ok_h, h = structure.get_int("height")
            if ok_w and ok_h:
                self._frame_width = w
                self._frame_height = h

        buf = sample.get_buffer()
        success, map_info = buf.map(self._Gst.MapFlags.READ)
        if not success:
            return b""
        try:
            rgba_bytes = bytes(map_info.data)
        finally:
            buf.unmap(map_info)

        # Prepend 12-byte header: width(4) + height(4) + flags(4)
        header = struct.pack(">III", self._frame_width, self._frame_height, 0)
        return header + rgba_bytes
