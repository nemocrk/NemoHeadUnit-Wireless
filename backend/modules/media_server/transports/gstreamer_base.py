"""
video_decoder/transports/gstreamer_base.py

Shared GStreamer pipeline infrastructure for all GST-based transports.
Handles appsrc push, pipeline lifecycle, appsink callback dispatch,
and graceful fallback to TransportUnavailableError on startup failure.
"""

import asyncio
import threading
from typing import Optional

from .base import BaseVideoTransport, TransportUnavailableError


import os


def _scan_system_plugin_paths(Gst) -> None:
    """Ensure GStreamer system plugin directories are registered in Conda/Micromamba env."""
    for path in [
        "/usr/lib/x86_64-linux-gnu/gstreamer-1.0",
        "/usr/lib/gstreamer-1.0",
        "/usr/lib64/gstreamer-1.0",
        "/usr/lib/i386-linux-gnu/gstreamer-1.0",
    ]:
        if os.path.isdir(path):
            try:
                Gst.Registry.get().scan_path(path)
            except Exception:
                pass


def _gst_available() -> bool:
    """Check if GStreamer Python bindings are importable."""
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: F401
        return True
    except Exception:
        return False


def _element_exists(element_name: str) -> bool:
    """Check if a GStreamer element is registered in the current plugin registry."""
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        _scan_system_plugin_paths(Gst)
        return Gst.Registry.get().find_feature(element_name, Gst.ElementFactory.__gtype__) is not None
    except Exception:
        return False



from shared.logger import get_logger

log = get_logger("media_server")

_HW_DECODER_KEYWORDS = ("vaapi", "nv", "v4l2", "d3d11", "dxva2", "qsv", "omx", "mali", "vpu", "cuda", "videotoolbox")



class GStreamerBaseTransport(BaseVideoTransport):
    """
    Shared GStreamer pipeline base for all GST-based video transports.

    Subclasses override:
        _build_pipeline_string() -> str   — returns gst-launch-style pipeline string
        _extract_frame(sample) -> bytes   — extracts the output bytes from a Gst.Sample

    The pipeline must contain:
        - An appsrc element named "src" (for pushing H.264 NAL bytes)
        - An appsink element named "sink" (for pulling decoded/encoded frames)
    """

    def __init__(self, jpeg_quality: int = 75, video_scale: str = "", video_codec: str = "H264") -> None:
        super().__init__(jpeg_quality, video_scale, video_codec)
        self._pipeline = None
        self._appsrc = None
        self._appsink = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._gst_thread: Optional[threading.Thread] = None
        self._bus_watch_id: Optional[int] = None
        self._detected_decoder: str = "decodebin (dynamic)"
        self._is_hw_accelerated: bool = True
        self._decoder_type_desc: str = "GStreamer dynamic decodebin"

    def _get_parser_element(self) -> str:
        """Return the appropriate GStreamer parser element for the active video codec."""
        c = self.video_codec.upper()
        if "H265" in c or "HEVC" in c:
            return "h265parse"
        elif "VP9" in c:
            return "vp9parse"
        elif "AV1" in c:
            return "av1parse"
        else:
            return "h264parse"

    def _build_pipeline_string(self) -> str:
        """Return the GStreamer pipeline description string. Must be overridden by subclasses."""
        raise NotImplementedError

    def _extract_frame(self, sample) -> bytes:
        """
        Extract output bytes from a Gst.Sample.
        Default implementation reads the raw buffer bytes.
        Subclasses may override (e.g. for structured frame headers).
        """
        buf = sample.get_buffer()
        success, map_info = buf.map(self._Gst.MapFlags.READ)
        if not success:
            return b""
        try:
            return bytes(map_info.data)
        finally:
            buf.unmap(map_info)

    def _on_decodebin_element_added(self, bin_obj, element) -> None:
        try:
            factory = element.get_factory()
            if not factory:
                return
            fname = factory.get_name()
            klass = factory.get_klass() or ""
            if "Decoder" in klass and ("Video" in klass or "h264" in fname.lower()):
                self._detected_decoder = fname
                self._is_hw_accelerated = any(kw in fname.lower() for kw in _HW_DECODER_KEYWORDS)
                self._decoder_type_desc = (
                    "Hardware Accelerated" if self._is_hw_accelerated else "Software Fallback (CPU)"
                )
                log.info(
                    f"🎬 [Video Decoder] Mode: '{self.transport_name}' | GStreamer Element: '{fname}' | "
                    f"Acceleration: {self._decoder_type_desc}"
                )
        except Exception:
            pass

    async def start(self) -> None:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self._Gst = Gst
        except Exception as exc:
            raise TransportUnavailableError(f"GStreamer Python bindings not available: {exc}") from exc

        Gst.init(None)
        _scan_system_plugin_paths(Gst)
        self._loop = asyncio.get_running_loop()


        pipeline_str = self._build_pipeline_string()
        try:
            self._pipeline = Gst.parse_launch(pipeline_str)
        except Exception as exc:
            raise TransportUnavailableError(f"Failed to parse GStreamer pipeline: {exc}\nPipeline: {pipeline_str}") from exc

        self._appsrc = self._pipeline.get_by_name("src")
        self._appsink = self._pipeline.get_by_name("sink")
        dec = self._pipeline.get_by_name("dec")

        if not self._appsrc or not self._appsink:
            raise TransportUnavailableError("GStreamer pipeline missing 'src' appsrc or 'sink' appsink element")

        if dec:
            try:
                dec.connect("element-added", self._on_decodebin_element_added)
            except Exception:
                pass

        # Connect appsink new-sample signal
        self._appsink.set_property("emit-signals", True)
        self._appsink.connect("new-sample", self._on_new_sample)

        # Configure appsrc
        self._appsrc.set_property("is-live", True)
        self._appsrc.set_property("do-timestamp", True)
        self._appsrc.set_property("format", Gst.Format.TIME)

        # Start pipeline
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._pipeline.set_state(Gst.State.NULL)
            raise TransportUnavailableError(
                f"GStreamer pipeline failed to reach PLAYING state for transport '{self.transport_name}'. "
                "Check that the required decoder plugin is installed (vaapih264dec, nvh264dec, or avdec_h264)."
            )

        log.info(
            f"🎬 [Video Decoder] Started GStreamer transport '{self.transport_name}' "
            f"(decodebin attached, awaiting dynamic decoder binding)"
        )

    def get_diagnostics(self) -> dict:
        return {
            "transport": self.transport_name,
            "decoder_element": self._detected_decoder,
            "hw_accelerated": self._is_hw_accelerated,
            "decoder_type": self._decoder_type_desc,
            "details": {
                "gstreamer_pipeline": self._build_pipeline_string(),
            },
        }


    def _on_new_sample(self, sink) -> int:
        """GStreamer appsink callback — called from GST thread."""
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstApp", "1.0")
            from gi.repository import Gst, GstApp  # noqa: F401
            sample = sink.emit("pull-sample")
            if sample is None:
                return self._Gst.FlowReturn.ERROR

            frame_bytes = self._extract_frame(sample)
            if frame_bytes and self.on_frame_ready and self._loop:
                # Dispatch to asyncio event loop from GST thread
                asyncio.run_coroutine_threadsafe(
                    self.on_frame_ready(frame_bytes, 0, self.wire_format),
                    self._loop,
                )
            return self._Gst.FlowReturn.OK
        except Exception:
            return self._Gst.FlowReturn.ERROR

    async def feed_nal(self, nal_data: bytes, timestamp_us: int) -> None:
        if not self._appsrc or not nal_data:
            return
        try:
            buf = self._Gst.Buffer.new_wrapped(nal_data)
            buf.pts = timestamp_us * 1000  # microseconds → nanoseconds
            buf.dts = self._Gst.CLOCK_TIME_NONE
            self._appsrc.emit("push-buffer", buf)
        except Exception:
            pass  # Best-effort; pipeline may not be ready yet

    async def stop(self) -> None:
        if self._pipeline:
            try:
                self._appsrc.emit("end-of-stream")
            except Exception:
                pass
            try:
                self._pipeline.set_state(self._Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None
            self._appsrc = None
            self._appsink = None

    def _scale_caps_fragment(self, pixel_format: str) -> str:
        """
        Build the videoscale + caps filter fragment for the pipeline string.
        Returns empty string if no scaling is configured.
        """
        scale = self._parse_video_scale()
        if scale:
            w, h = scale
            return f"! videoscale ! video/x-raw,format={pixel_format},width={w},height={h} "
        return f"! video/x-raw,format={pixel_format} "
