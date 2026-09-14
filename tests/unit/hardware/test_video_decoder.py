# tests/unit/hardware/test_video_decoder.py
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from shared.hardware.video_decoder import (
    get_plugin_search_paths,
    get_decoder_candidates,
    get_available_decoders,
    get_best_hardware_decoder,
    build_video_pipeline,
    GstPipeline,
)

pytestmark = pytest.mark.unit


def test_video_decoder_candidates():
    candidates = get_decoder_candidates()
    assert len(candidates) > 0
    # Every candidate has element, description, platform, is_hardware
    for c in candidates:
        assert "element" in c
        assert "description" in c
        assert "is_hardware" in c
        assert "platform" in c


def test_video_decoder_search_paths():
    paths = get_plugin_search_paths()
    assert isinstance(paths, list)


def test_get_best_hardware_decoder_fallback():
    with patch("shared.hardware.video_decoder.get_available_decoders") as mock_avail:
        # 1. Hardware available
        mock_avail.return_value = [
            {"element": "nvh264dec", "description": "NVIDIA NVDEC", "available": True, "is_hardware": True},
            {"element": "avdec_h264", "description": "FFmpeg Software", "available": True, "is_hardware": False},
        ]
        elem, desc = get_best_hardware_decoder()
        assert elem == "nvh264dec"

        # 2. Only software available
        mock_avail.return_value = [
            {"element": "nvh264dec", "description": "NVIDIA NVDEC", "available": False, "is_hardware": True},
            {"element": "avdec_h264", "description": "FFmpeg Software", "available": True, "is_hardware": False},
        ]
        elem, desc = get_best_hardware_decoder()
        assert elem == "avdec_h264"


def test_build_video_pipeline_modes():
    # 1. Standard appsink pipeline
    pipe = build_video_pipeline(mode="appsink", sink_name="sink", src_name="src")
    assert isinstance(pipe, GstPipeline)
    pipe_str, dec_desc = pipe
    assert "appsrc" in pipe_str
    assert "appsink" in pipe_str

    # 2. zero_copy pipeline
    pipe_zc = build_video_pipeline(mode="zero_copy", sink_name="qmlsink", src_name="appsrc")
    assert "appsrc" in str(pipe_zc)

    # 3. Environment override
    with patch.dict(os.environ, {"NEMO_GST_VIDEO_PIPELINE": "appsrc ! fakesink"}):
        pipe_env = build_video_pipeline(mode="appsink")
        assert "fakesink" in str(pipe_env)

    # 4. Zero-copy environment override
    with patch.dict(os.environ, {"NEMO_GST_ZERO_COPY_PIPELINE": "vah264dec ! qml6glsink"}):
        pipe_zc_env = build_video_pipeline(mode="zero_copy")
        assert "vah264dec" in str(pipe_zc_env)

    # 5. Zero-copy with specific decoder postproc
    with patch("shared.hardware.video_decoder.get_best_hardware_decoder", return_value=("vah264dec", "VA-API")):
        pipe_va = build_video_pipeline(mode="zero_copy")
        assert "vapostproc ! glupload" in str(pipe_va)

    with patch("shared.hardware.video_decoder.get_best_hardware_decoder", return_value=("vaapih264dec", "Legacy VA-API")):
        pipe_vaapi = build_video_pipeline(mode="zero_copy")
        assert "vaapipostproc ! glupload" in str(pipe_vaapi)

    with patch("shared.hardware.video_decoder.get_best_hardware_decoder", return_value=("nvh264dec", "NVDEC")):
        pipe_nv = build_video_pipeline(mode="zero_copy")
        assert "cudaupload ! glupload" in str(pipe_nv)


def test_scan_gstreamer_plugin_paths():
    from shared.hardware.video_decoder import scan_gstreamer_plugin_paths
    mock_gst = MagicMock()
    mock_reg = MagicMock()
    mock_gst.Registry.get.return_value = mock_reg
    with patch("shared.hardware.video_decoder.get_plugin_search_paths", return_value=["/usr/lib/gstreamer-1.0"]):
        scanned = scan_gstreamer_plugin_paths(mock_gst)
        assert scanned == ["/usr/lib/gstreamer-1.0"]
        mock_reg.scan_path.assert_called_once_with("/usr/lib/gstreamer-1.0")


@pytest.mark.skipif(sys.platform == "win32", reason="VA-API is Linux-only")
def test_get_available_decoders_cli_fallback():
    with patch.dict(sys.modules, {"gi": None}), \
         patch("shutil.which", return_value="/usr/bin/vainfo"):
        decoders = get_available_decoders()
        assert len(decoders) > 0
        vah264 = [d for d in decoders if d["element"] == "vah264dec"][0]
        assert vah264["available"] is True

