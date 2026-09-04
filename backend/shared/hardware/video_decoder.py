"""
video_decoder.py — Cross-Platform Video Decoder Factory & Hardware Acceleration Matrix.

Provides dynamic detection of hardware decoders across:
  - NVIDIA CUDA / NVDEC: nvh264dec, nvh264sldec
  - Intel VA-API / QSV: vah264dec, vaapih264dec, qsvh264dec
  - AMD Mesa VA-API: vah264dec, vaapih264dec
  - Windows Direct3D 11 / MediaFoundation: d3d11h264dec, mfh264dec
  - ARM / Linux V4L2: v4l2slh264dec, v4l2h264dec, omxh264dec
  - Software Fallback: avdec_h264, openh264dec

Supports global environment overrides for hardware-quirk configurations:
  - NEMO_GST_VIDEO_PIPELINE: Overrides standard appsink pipeline.
  - NEMO_GST_ZERO_COPY_PIPELINE: Overrides zero-copy QML pipeline.
"""

import os
import sys
import shutil
from typing import List, Dict, Tuple, Optional, Any, Union
try:
    from shared.logger import get_logger
except ImportError:
    from backend.shared.logger import get_logger

log = get_logger("hardware.video_decoder")


def get_plugin_search_paths() -> List[str]:
    """
    Return candidate filesystem directories containing GStreamer plugins
    across Debian/Ubuntu multiarch, Arch Linux, Conda/Micromamba, and Windows.
    """
    candidates = []

    # 1. Conda / Micromamba environment paths
    if sys.prefix:
        candidates.extend([
            os.path.join(sys.prefix, "lib", "gstreamer-1.0"),
            os.path.join(sys.prefix, "Library", "lib", "gstreamer-1.0"),
            os.path.join(sys.prefix, "Library", "bin", "gstreamer-1.0"),
        ])

    # 2. Linux multiarch and standard system paths
    if sys.platform.startswith("linux"):
        candidates.extend([
            "/usr/lib/x86_64-linux-gnu/gstreamer-1.0",
            "/usr/lib/aarch64-linux-gnu/gstreamer-1.0",
            "/usr/lib/arm-linux-gnueabihf/gstreamer-1.0",
            "/usr/lib/i386-linux-gnu/gstreamer-1.0",
            "/usr/lib/gstreamer-1.0",
            "/usr/lib64/gstreamer-1.0",
            "/usr/local/lib/gstreamer-1.0",
        ])

    # 3. Windows standard GStreamer SDK install paths
    elif sys.platform == "win32":
        for env_var in ("GSTREAMER_1_0_ROOT_MSVC_X86_64", "GSTREAMER_1_0_ROOT_X86_64", "GSTREAMER_1_0_ROOT_MINGW_W64"):
            root = os.environ.get(env_var)
            if root:
                candidates.append(os.path.join(root, "lib", "gstreamer-1.0"))
        candidates.extend([
            r"C:\gstreamer\1.0\msvc_x86_64\lib\gstreamer-1.0",
            r"C:\gstreamer\1.0\x86_64\lib\gstreamer-1.0",
        ])

    return [p for p in candidates if os.path.isdir(p)]


def scan_gstreamer_plugin_paths(gst_instance=None) -> List[str]:
    """Scan all detected system plugin paths into GStreamer's default registry."""
    scanned = []
    try:
        if gst_instance is None:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            gst_instance = Gst

        registry = gst_instance.Registry.get()
        for path in get_plugin_search_paths():
            try:
                registry.scan_path(path)
                scanned.append(path)
            except Exception:
                pass
    except Exception as exc:
        log.debug("scan_gstreamer_plugin_paths notice: %s", exc)
    return scanned


def get_decoder_candidates() -> List[Dict[str, any]]:
    """
    Return the prioritized matrix of supported H.264 video decoder elements
    across GPU vendors and OS platforms.
    """
    return [
        # 1. NVIDIA CUDA / NVDEC (Linux & Windows)
        {
            "element": "nvh264dec",
            "description": "NVIDIA CUDA Hardware NVDEC (nvh264dec)",
            "platform": "all",
            "is_hardware": True,
            "postproc": "cudaupload ! glupload",
        },
        {
            "element": "nvh264sldec",
            "description": "NVIDIA CUDA Stateless NVDEC (nvh264sldec)",
            "platform": "all",
            "is_hardware": True,
            "postproc": "glupload",
        },
        # 2. Intel / AMD VA-API (Linux)
        {
            "element": "vah264dec",
            "description": "Intel/AMD Modern VA-API Hardware VPU (vah264dec)",
            "platform": "linux",
            "is_hardware": True,
            "postproc": "vapostproc",
        },
        {
            "element": "vaapih264dec",
            "description": "Intel/AMD Legacy VA-API (vaapih264dec)",
            "platform": "linux",
            "is_hardware": True,
            "postproc": "vaapipostproc",
        },
        # 3. Intel Quick Sync Video (Linux & Windows)
        {
            "element": "qsvh264dec",
            "description": "Intel Quick Sync Video (qsvh264dec)",
            "platform": "all",
            "is_hardware": True,
            "postproc": "videoconvert",
        },
        # 4. Windows Direct3D 11 & Media Foundation (Windows)
        {
            "element": "d3d11h264dec",
            "description": "Windows Direct3D 11 Hardware Decoder (d3d11h264dec)",
            "platform": "windows",
            "is_hardware": True,
            "postproc": "d3d11convert",
        },
        {
            "element": "mfh264dec",
            "description": "Windows Media Foundation (mfh264dec)",
            "platform": "windows",
            "is_hardware": True,
            "postproc": "videoconvert",
        },
        # 5. ARM V4L2 VPU (Linux)
        {
            "element": "v4l2slh264dec",
            "description": "ARM Linux V4L2 Stateless VPU (v4l2slh264dec)",
            "platform": "linux",
            "is_hardware": True,
            "postproc": "videoconvert",
        },
        {
            "element": "v4l2h264dec",
            "description": "ARM Linux V4L2 Stateful VPU (v4l2h264dec)",
            "platform": "linux",
            "is_hardware": True,
            "postproc": "videoconvert",
        },
        # 6. Software Fallbacks (CPU)
        {
            "element": "avdec_h264",
            "description": "FFmpeg libavcodec Software Decoder (avdec_h264)",
            "platform": "all",
            "is_hardware": False,
            "postproc": "videoconvert",
        },
        {
            "element": "openh264dec",
            "description": "Cisco OpenH264 Software Decoder (openh264dec)",
            "platform": "all",
            "is_hardware": False,
            "postproc": "videoconvert",
        },
    ]


def get_available_decoders() -> List[Dict[str, any]]:
    """Probe the GStreamer registry and return available decoders for active host."""
    results = []
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        scan_gstreamer_plugin_paths(Gst)

        candidates = get_decoder_candidates()
        is_linux = sys.platform.startswith("linux")
        is_win = sys.platform == "win32"

        for cand in candidates:
            plat = cand["platform"]
            if plat == "linux" and not is_linux:
                continue
            if plat == "windows" and not is_win:
                continue

            factory = Gst.ElementFactory.find(cand["element"])
            available = factory is not None
            results.append({
                "element": cand["element"],
                "description": cand["description"],
                "available": available,
                "is_hardware": cand["is_hardware"],
            })
    except Exception as exc:
        log.debug("get_available_decoders Gst error (%s) — falling back to CLI heuristic", exc)
        is_linux = sys.platform.startswith("linux")
        is_win = sys.platform == "win32"
        if is_linux:
            results.append({"element": "vah264dec", "description": "VA-API Hardware (vah264dec)", "available": shutil.which("vainfo") is not None, "is_hardware": True})
            results.append({"element": "vaapih264dec", "description": "VA-API Legacy (vaapih264dec)", "available": shutil.which("vainfo") is not None, "is_hardware": True})
            results.append({"element": "v4l2slh264dec", "description": "V4L2 Hardware (v4l2slh264dec)", "available": shutil.which("v4l2-ctl") is not None, "is_hardware": True})
        elif is_win:
            results.append({"element": "d3d11h264dec", "description": "Direct3D 11 Hardware (d3d11h264dec)", "available": True, "is_hardware": True})
        results.append({"element": "avdec_h264", "description": "FFmpeg Software Decoder", "available": shutil.which("ffmpeg") is not None, "is_hardware": False})

    return results


def get_best_hardware_decoder() -> Tuple[str, str]:
    """
    Select the highest-priority hardware decoder available on the current system.
    Returns (element_name, description).
    """
    decoders = get_available_decoders()
    for dec in decoders:
        if dec["available"] and dec["is_hardware"]:
            return dec["element"], dec["description"]

    # Fallback to software decoder
    for dec in decoders:
        if dec["available"] and not dec["is_hardware"]:
            return dec["element"], dec["description"]

    return "avdec_h264", "FFmpeg Software Fallback (avdec_h264)"


class GstPipeline(str):
    """
    Dual-compatible return type for build_video_pipeline.
    Behaves as a regular pipeline string, but supports tuple unpacking
    (pipeline_str, decoder_desc) for callers expecting (pipe_str, dec_name).
    """
    def __new__(cls, pipeline_str: str, decoder_desc: str = ""):
        obj = super().__new__(cls, pipeline_str)
        obj.decoder_desc = decoder_desc
        return obj

    def __iter__(self):
        yield str(self)
        yield self.decoder_desc


def build_video_pipeline(
    mode: str = "rgba",
    width: int = 1280,
    height: int = 720,
    parser: str = "h264parse",
    sink_name: Optional[str] = None,
    src_name: str = "src",
    Gst: Any = None,
    **kwargs,
) -> GstPipeline:
    """
    Construct a GStreamer pipeline string honoring environment overrides
    or dynamically assembling the optimal element chain.

    Modes:
      - 'rgba' / 'appsink': Standard appsink pipeline pushing RGBA buffers to callbacks.
      - 'zero_copy': Direct GPU DMABuf / GL pipeline targeting qml6glsink.
    """
    is_zero_copy = mode == "zero_copy"
    resolved_sink = sink_name or ("qml_sink" if is_zero_copy else "sink")
    resolved_src = src_name or "src"

    # 1. Check environment overrides first (allows hardware-quirk scripts to inject tuning)
    if is_zero_copy:
        env_override = os.environ.get("NEMO_GST_ZERO_COPY_PIPELINE")
        if env_override:
            log.info("🎬 [Video Decoder] Using NEMO_GST_ZERO_COPY_PIPELINE env override: %s", env_override)
            if not env_override.strip().startswith("appsrc"):
                pipeline_str = f"appsrc name={resolved_src} is-live=true format=bytes ! {parser} config-interval=-1 ! {env_override}"
            else:
                pipeline_str = env_override
            log.info("🎬 [Video Decoder] Computed GStreamer pipeline (%s): %s", mode, pipeline_str)
            return GstPipeline(pipeline_str, "Environment Override (NEMO_GST_ZERO_COPY_PIPELINE)")

    env_pipe = os.environ.get("NEMO_GST_VIDEO_PIPELINE")
    if env_pipe:
        log.info("🎬 [Video Decoder] Using NEMO_GST_VIDEO_PIPELINE env override: %s", env_pipe)
        log.info("🎬 [Video Decoder] Computed GStreamer pipeline (%s): %s", mode, env_pipe)
        return GstPipeline(env_pipe, "Environment Override (NEMO_GST_VIDEO_PIPELINE)")

    # 2. Dynamic hardware decoder resolution
    best_elem, desc = get_best_hardware_decoder()
    log.info("🎬 [Video Decoder] Selected decoder: '%s' (%s)", best_elem, desc)

    if is_zero_copy:
        # Build zero-copy pipeline if supported by element
        if best_elem == "vah264dec":
            postproc = "vapostproc ! glupload"
        elif best_elem == "vaapih264dec":
            postproc = "vaapipostproc ! glupload"
        elif best_elem == "nvh264dec":
            postproc = "cudaupload ! glupload"
        else:
            postproc = "videoconvert ! glupload"

        pipeline_str = (
            f"appsrc name={resolved_src} is-live=true format=bytes "
            f"! {parser} config-interval=-1 "
            f"! {best_elem} "
            f"! {postproc} "
            f"! qml6glsink name={resolved_sink} sync=false"
        )
    else:
        # Standard appsink mode (RGBA output for Qt viewport or SHM)
        pipeline_str = (
            f"appsrc name={resolved_src} is-live=true format=bytes "
            f"! {parser} config-interval=-1 "
            f"! {best_elem} "
            f"! videoconvert "
            f"! video/x-raw,format=RGBA "
            f"! appsink name={resolved_sink} emit-signals=true max-buffers=2 drop=true sync=false"
        )

    log.info("🎬 [Video Decoder] Computed GStreamer pipeline (%s): %s", mode, pipeline_str)
    return GstPipeline(pipeline_str, desc)
