"""Tests for video pipeline stall detection, watchdog auto-recovery, and viewport fallback switching."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import time
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.ui.video_viewport import VideoViewportWidget
from backend.modules.qt6_gui.media.shm_media_engine import QtSHMMediaEngine


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_video_viewport_switches_to_fallback_image_on_software_frame():
    app = get_app()
    viewport = VideoViewportWidget()
    viewport._attached = True
    if viewport._video_item:
        viewport._video_item.setProperty("visible", True)
    if viewport._fallback_image:
        viewport._fallback_image.setProperty("visible", False)

    dummy_rgba = bytes([255, 0, 0, 255] * (1280 * 720))
    viewport.update_frame(dummy_rgba, 1280, 720)

    # Fallback image must be made visible, and videoItem hidden
    if viewport._fallback_image:
        assert viewport._fallback_image.property("visible") is True
    if viewport._video_item:
        assert viewport._video_item.property("visible") is False
    assert viewport.current_frame_data == dummy_rgba


def test_shm_engine_watchdog_triggers_on_stalled_frames():
    engine = QtSHMMediaEngine()
    engine.on_video_frame = MagicMock()
    keyframe_mock = MagicMock()
    engine.request_keyframe = keyframe_mock

    # Simulate NAL arrival with no successful decode for > 2.0s
    engine._last_rendered_time = time.time() - 3.0
    engine._last_watchdog_recover_time = 0.0

    # Mock shm read_frame returning a valid NAL
    mock_shm = MagicMock()
    mock_shm_channel = MagicMock()
    nal_packet = b"\x00\x00\x00\x01\x67\x42\x00\x1f"  # dummy NAL
    mock_shm_channel.read_frame.return_value = (0, 1000, nal_packet)
    mock_shm.get_downstream_channel.return_value = mock_shm_channel
    engine.shm = mock_shm

    engine.process_downstream_video(offset=0, channel_id=3)

    # Watchdog should detect stall, request keyframe, and reset decoder
    assert keyframe_mock.called
