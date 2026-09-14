import os
import time
import pytest
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
from backend.modules.qt6_gui.media.audio_handler import DynamicChannelAudioSink, QtAudioEngine
from backend.modules.qt6_gui.ui.command_bar import AudioBufferPill

# Ensure single QApplication instance
app = QApplication.instance() or QApplication([])


def test_dynamic_channel_audio_sink_streaming_lifecycle():
    sink = DynamicChannelAudioSink(channel_id=4, target_device="default")
    assert sink.is_streaming is False
    assert sink.is_stopped is True

    # Push a frame -> stream becomes active
    sink.push_frame(b"\x00\x00" * 480)
    assert sink.is_streaming is True
    assert sink.is_stopped is False

    # Mark stream stopped -> stream becomes inactive
    sink.set_stream_status("STOPPED")
    assert sink.is_streaming is False
    assert sink.is_stopped is True

    sink.close()


def test_audio_sink_does_not_count_underrun_on_stream_stop():
    sink = DynamicChannelAudioSink(channel_id=4, target_device="default", prebuffer_ms=0)
    sink.push_frame(b"\x00\x00" * 480)
    assert sink.get_metrics()["app_buffer"]["underruns"] == 0

    # Explicitly stop stream
    sink.set_stream_status("STOPPED")

    # Draining empty buffer after stop must NOT increment underruns
    sink._flush_to_sink()
    metrics = sink.get_metrics()
    assert metrics["app_buffer"]["underruns"] == 0

    sink.close()


def test_audio_buffer_pill_reflects_audio_stream_status():
    pill = AudioBufferPill()

    # 1. Idle state: no audio, no video
    pill.update_status({}, video_metrics={"lag_ms": 0, "fps": 0.0})
    assert "Idle" in pill.lbl_status.text()
    assert pill.lbl_status.styleSheet().find("#8b949e") != -1

    # 2. Video only active, audio idle
    pill.update_status({}, video_metrics={"lag_ms": 15, "fps": 60.0})
    assert "A:+0ms" not in pill.lbl_status.text()
    assert "60fps" in pill.lbl_status.text()

    # 3. Audio paused
    metrics_paused = {
        4: {
            "channel_id": 4,
            "is_started": True,
            "is_streaming": False,
            "is_stopped": False,
            "app_buffer": {"is_paused": True, "buffered_ms": 0, "prebuffer_ms": 150, "underruns": 0},
            "sink_buffer": {},
            "lag_ms": 0,
        }
    }
    pill.update_status(metrics_paused, video_metrics={"lag_ms": 0, "fps": 0.0})
    assert "PAUSED" in pill.lbl_status.text()
    assert pill.lbl_status.styleSheet().find("#8b949e") != -1

    # 4. Audio actively streaming healthy
    metrics_streaming = {
        4: {
            "channel_id": 4,
            "is_started": True,
            "is_streaming": True,
            "is_stopped": False,
            "app_buffer": {"is_paused": False, "is_buffering": False, "buffered_ms": 120, "prebuffer_ms": 150, "underruns": 0},
            "sink_buffer": {"queued_ms": 40},
            "lag_ms": 12,
        }
    }
    pill.update_status(metrics_streaming, video_metrics={"lag_ms": 15, "fps": 60.0})
    assert "60fps" in pill.lbl_status.text()
    assert "A:+12ms" in pill.lbl_status.text()
    assert pill.lbl_status.styleSheet().find("#3fb950") != -1  # Green

    # 5. Audio stopped after playing: must NOT say UNDERRUN
    metrics_stopped = {
        4: {
            "channel_id": 4,
            "is_started": True,
            "is_streaming": False,
            "is_stopped": True,
            "total_bytes_in": 50000,
            "app_buffer": {"is_paused": False, "is_buffering": True, "buffered_ms": 0, "prebuffer_ms": 150, "underruns": 0},
            "sink_buffer": {"queued_ms": 0},
            "lag_ms": 0,
        }
    }
    pill.update_status(metrics_stopped, video_metrics={"lag_ms": 0, "fps": 0.0})
    assert "UNDERRUN" not in pill.lbl_status.text()
    assert "Idle" in pill.lbl_status.text()
