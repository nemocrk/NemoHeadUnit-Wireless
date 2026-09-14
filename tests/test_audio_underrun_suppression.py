"""Tests for audio underrun suppression when an audio channel is paused."""

import pytest
from unittest.mock import MagicMock
from backend.modules.qt6_gui.media.audio_handler import AudioPcmStream, DynamicChannelAudioSink


def test_audio_pcm_stream_suppresses_underruns_when_paused():
    stream = AudioPcmStream(sample_rate=48000, channels=2, prebuffer_ms=0)
    assert hasattr(stream, "set_paused")

    # 1. Fill buffer, pause, and drain it completely -> should NOT trigger underrun
    stream.write_pcm(b"\x00" * 2000)
    stream.set_paused(True)
    chunk = stream.readData(2000)
    assert len(chunk) == 2000
    metrics = stream.get_buffer_metrics()
    assert metrics["underruns"] == 0
    assert metrics.get("is_paused") is True

    # 2. Unpause, fill buffer and drain it completely -> should trigger underrun
    stream.set_paused(False)
    stream.write_pcm(b"\x00" * 2000)
    chunk = stream.readData(2000)
    assert len(chunk) == 2000
    metrics = stream.get_buffer_metrics()
    assert metrics["underruns"] == 1
    assert metrics.get("is_paused") is False


def test_dynamic_channel_audio_sink_tracks_pause_state():
    sink = DynamicChannelAudioSink(channel_id=5, target_device="default")
    assert hasattr(sink, "is_paused")
    assert sink.is_paused is False

    sink.set_paused(True)
    assert sink.is_paused is True
    diag = sink.get_diagnostics()
    assert diag["app_buffer"]["is_paused"] is True
    assert diag["app_buffer"]["underruns"] == 0

    sink.set_paused(False)
    assert sink.is_paused is False
