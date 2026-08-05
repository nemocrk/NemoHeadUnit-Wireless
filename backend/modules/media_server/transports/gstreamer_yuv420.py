"""
video_decoder/transports/gstreamer_yuv420.py

GStreamer transport: H.264 HW decode → raw YUV420p planar frames.

Pipeline:
    appsrc → h264parse → decodebin → videoconvert → [videoscale] → appsink (I420)

Wire format per frame:
    [9-byte standard media header: ChannelID(1) + Timestamp(8)]  ← added by VideoDecoderModule
    [Width:  4 bytes uint32 BE]
    [Height: 4 bytes uint32 BE]
    [Flags:  4 bytes uint32 BE, reserved=0]
    [Y plane: Width × Height bytes]
    [U plane: (Width/2) × (Height/2) bytes]
    [V plane: (Width/2) × (Height/2) bytes]

Frontend renders via a WebGL YUV→RGB BT.601 fragment shader.
Zero encode latency — raw GPU decoder output straight to the wire.

Bandwidth: ~41 MB/s at 720p@30fps. Suitable for loopback only.

Availability: Requires GStreamer (no additional plugins needed beyond -plugins-base).
"""

import struct
from .gstreamer_base import GStreamerBaseTransport, _gst_available, _element_exists


class GStreamerYuv420Transport(GStreamerBaseTransport):
    """
    GStreamer HW-accelerated H.264 decode → raw YUV420p planar transport.

    Zero encode latency path. decodebin auto-selects the best HW decoder.
    The frontend uses a WebGL shader for GPU-accelerated YUV→RGB conversion.
    """

    transport_name = "yuv420"
    wire_format = "yuv420"

    # Cached frame dimensions (updated from GStreamer caps on first frame)
    _frame_width: int = 0
    _frame_height: int = 0

    @staticmethod
    def is_available() -> bool:
        return _gst_available() and _element_exists("videoconvert")

    def _build_pipeline_string(self) -> str:
        scale_frag = self._scale_caps_fragment("I420")
        return (
            "appsrc name=src is-live=true format=time do-timestamp=true "
            "! h264parse "
            "! decodebin name=dec "
            "! videoconvert "
            f"{scale_frag}"
            "! appsink name=sink emit-signals=true max-buffers=2 drop=true"
        )


    def _extract_frame(self, sample) -> bytes:
        """
        Extract I420 (YUV420p) buffer and prepend the 12-byte dimension header.
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
            yuv_bytes = bytes(map_info.data)
        finally:
            buf.unmap(map_info)

        # Prepend 12-byte header: width(4) + height(4) + flags(4)
        header = struct.pack(">III", self._frame_width, self._frame_height, 0)
        return header + yuv_bytes
