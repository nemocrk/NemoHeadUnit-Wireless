"""
test_video_decoder.py — Unit tests for cross-platform video decoder probing and pipeline building.
"""

import os
import sys
import pytest

from shared.hardware.video_decoder import (
    get_plugin_search_paths,
    get_decoder_candidates,
    get_available_decoders,
    get_best_hardware_decoder,
    build_video_pipeline,
)


def test_plugin_search_paths():
    paths = get_plugin_search_paths()
    assert isinstance(paths, list)
    assert len(paths) > 0
    if sys.platform.startswith("linux"):
        assert any("gstreamer-1.0" in p for p in paths)


def test_decoder_candidates():
    candidates = get_decoder_candidates()
    assert isinstance(candidates, list)
    elements = [c["element"] for c in candidates]
    # Verify presence of major decoders in candidate list
    assert "avdec_h264" in elements
    assert "nvh264dec" in elements
    assert "vah264dec" in elements
    assert "d3d11h264dec" in elements


def test_get_available_decoders():
    decoders = get_available_decoders()
    assert isinstance(decoders, list)
    assert len(decoders) > 0
    for d in decoders:
        assert "element" in d
        assert "description" in d
        assert "available" in d
        assert "is_hardware" in d


def test_get_best_hardware_decoder():
    best_elem, desc = get_best_hardware_decoder()
    assert isinstance(best_elem, str)
    assert isinstance(desc, str)
    assert len(best_elem) > 0


def test_build_video_pipeline_standard():
    pipe = build_video_pipeline(mode="rgba", width=1280, height=720)
    assert "appsrc" in pipe
    assert "appsink" in pipe
    assert "name=src" in pipe
    assert "name=sink" in pipe


def test_build_video_pipeline_env_override(monkeypatch):
    custom_pipe = "appsrc name=src ! custom_test_elem ! appsink name=sink"
    monkeypatch.setenv("NEMO_GST_VIDEO_PIPELINE", custom_pipe)
    pipe = build_video_pipeline(mode="rgba")
    assert pipe == custom_pipe


def test_build_zero_copy_pipeline_env_override(monkeypatch):
    custom_zero_copy = "appsrc name=src ! custom_zero ! qml6glsink name=qml_sink"
    monkeypatch.setenv("NEMO_GST_ZERO_COPY_PIPELINE", custom_zero_copy)
    pipe = build_video_pipeline(mode="zero_copy")
    assert pipe == custom_zero_copy
