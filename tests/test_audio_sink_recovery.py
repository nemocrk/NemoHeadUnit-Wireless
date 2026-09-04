"""Tests for audio sink error recovery and device fallback on disconnect."""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtMultimedia import QAudio
from backend.modules.qt6_gui.media.audio_handler import DynamicChannelAudioSink


def test_audio_sink_recovers_on_io_error():
    sink = DynamicChannelAudioSink(channel_id=5, target_device="USB Audio DAC")
    assert hasattr(sink, "_handle_sink_error")

    # Mock recovery scheduler
    sink._schedule_recovery = MagicMock()
    sink._handle_sink_error(QAudio.Error.IOError)

    assert sink._schedule_recovery.called


def test_audio_sink_fallback_to_default_on_missing_target():
    sink = DynamicChannelAudioSink(channel_id=5, target_device="NonExistentUSBDAC")
    # Attempting re-init with missing target device should fallback to default without raising
    sink._init_playback(48000, 2)
    assert sink.audio_sink is not None
