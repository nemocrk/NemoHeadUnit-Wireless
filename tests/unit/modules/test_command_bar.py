"""
test_command_bar.py — Unit tests for CommandBarWidget, CallStatusPill, and AudioBufferPill.
"""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
from unittest.mock import MagicMock
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.ui.command_bar import (
    CommandBarWidget,
    InCallControlPill,
    AudioBufferPill,
)


pytestmark = [pytest.mark.unit, pytest.mark.qt]


def get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_audio_buffer_pill_states_and_tooltip():
    app = get_app()
    pill = AudioBufferPill()

    # 1. Idle state
    pill.update_status({})
    assert "Idle" in pill.lbl_status.text()

    # 2. Underrun state
    metrics = {
        1: {
            "channel_id": 1,
            "is_streaming": True,
            "app_buffer": {"buffered_ms": 10, "prebuffer_ms": 150, "is_buffering": True, "underruns": 3},
            "sink_buffer": {"queued_ms": 5},
            "lag_ms": 20,
        }
    }
    pill.update_status(metrics)
    assert "UNDERRUN" in pill.lbl_status.text()

    # 3. Buffering state without underruns
    metrics[1]["app_buffer"]["underruns"] = 0
    pill.update_status(metrics)
    assert "BUF" in pill.lbl_status.text()

    # 4. Healthy sync state
    metrics[1]["app_buffer"]["is_buffering"] = False
    metrics[1]["app_buffer"]["buffered_ms"] = 150
    pill.update_status(metrics, video_metrics={"lag_ms": 30, "fps": 60.0})
    assert "60fps" in pill.lbl_status.text()
    assert "A:+20ms" in pill.lbl_status.text()

    # 5. Significant drift
    pill.update_status(metrics, video_metrics={"lag_ms": 300, "fps": 30.0})
    assert "V:+300ms" in pill.lbl_status.text()

    # 6. Tooltip mousePressEvent toggle
    from PyQt6.QtCore import QPointF
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pill.mousePressEvent(ev)
    assert pill._tooltip_visible is True
    pill.mousePressEvent(ev)
    assert pill._tooltip_visible is False


def test_call_status_pill():
    app = get_app()
    pill = InCallControlPill()
    pill.show()

    # 1. Call idle -> hides pill
    pill.update_call_state(is_in_call=False, call_state="IDLE")
    assert pill.isHidden() is True

    # 2. Incoming call ringing
    pill.update_call_state(
        is_in_call=True,
        call_state="RINGING",
        caller_name="Very Long Contact Name That Needs Truncating",
        caller_number="+15551234567",
    )
    assert pill.lbl_timer.text() == "Incoming Call..."
    assert pill.btn_answer.isHidden() is False
    assert pill.btn_mute.isHidden() is True
    assert "…" in pill.lbl_caller.text()

    # 3. Active call with timer
    pill.update_call_state(
        is_in_call=True,
        call_state="ACTIVE",
        caller_name="Alice",
        duration_seconds=125,
    )
    assert pill.lbl_timer.text() == "02:05"
    assert pill.btn_answer.isHidden() is True
    assert pill.btn_mute.isHidden() is False

    # 4. Mute toggle
    action_cb = MagicMock()
    pill.action_triggered.connect(action_cb)
    pill._on_mute_clicked()
    assert pill.is_mic_muted is True
    action_cb.assert_called_once_with("mute")

    pill.set_mic_muted(False)
    assert pill.is_mic_muted is False


def test_command_bar_widget_actions_and_media():
    app = get_app()
    bar = CommandBarWidget()

    # Test signals
    home_mock = MagicMock()
    bar.home_clicked.connect(home_mock)
    bar.btn_home.click()
    home_mock.assert_called_once()

    vol_mock = MagicMock()
    bar.volume_clicked.connect(vol_mock)
    bar.btn_volume.click()
    vol_mock.assert_called_once()

    play_mock = MagicMock()
    bar.playpause_clicked.connect(play_mock)
    bar.btn_playpause.click()
    play_mock.assert_called_once()

    # Test in_call_pill forward
    call_action_mock = MagicMock()
    bar.call_action_triggered.connect(call_action_mock)
    bar.in_call_pill.action_triggered.emit("hangup")
    call_action_mock.assert_called_once_with("hangup")

    # Update playback state and online status
    bar.update_playback_state(True)
    assert bar.btn_playpause.toolTip() == "Pause"

    bar.update_playback_state(False)
    assert bar.btn_playpause.toolTip() == "Play"

    bar.set_online_status(True)
    assert bar.status_dot.objectName() == "status-dot-online"
    bar.set_online_status(False)
    assert bar.status_dot.objectName() == "status-dot-offline"


