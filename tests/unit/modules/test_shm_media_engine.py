"""
test_shm_media_engine.py — Comprehensive Unit tests for QtSHMMediaEngine and Decoders.
"""

import struct
import time
from unittest.mock import MagicMock, patch
import pytest

from backend.modules.qt6_gui.media.shm_media_engine import (
    GStreamerHwDecoder,
    Qml6ZeroCopyDecoder,
    QtSHMMediaEngine,
)

pytestmark = pytest.mark.unit


def test_gstreamer_hw_decoder_lifecycle_and_decode():
    cb = MagicMock()
    dec = GStreamerHwDecoder(on_frame_callback=cb)
    dec.is_available = True
    dec._appsrc = MagicMock()
    mock_gst = MagicMock()
    dec._Gst = mock_gst

    # Test decode_nal
    buf = MagicMock()
    mock_gst.Buffer.new_wrapped.return_value = buf
    assert dec.decode_nal(b"\x00\x00\x00\x01\x65", ts_us=5000) is True
    assert buf.pts == 5000 * 1000
    dec._appsrc.emit.assert_called_with("push-buffer", buf)

    # Test _on_new_sample
    sink = MagicMock()
    sample = MagicMock()
    caps = MagicMock()
    structure = MagicMock()
    structure.get_int.side_effect = [(True, 1280), (True, 720)]
    caps.get_structure.return_value = structure
    sample.get_caps.return_value = caps

    gst_buf = MagicMock()
    gst_buf.pts = 10000000
    map_info = MagicMock()
    map_info.data = bytes([100] * (1280 * 720 * 4))
    gst_buf.map.return_value = (True, map_info)
    sample.get_buffer.return_value = gst_buf

    sink.emit.return_value = sample
    mock_gst.MapFlags.READ = 1
    mock_gst.CLOCK_TIME_NONE = -1
    mock_gst.FlowReturn.OK = 0

    assert dec._on_new_sample(sink) == 0
    cb.assert_called_once()
    assert dec.frames_decoded == 1

    # Test close
    pipeline = MagicMock()
    dec._pipeline = pipeline
    dec.close()
    assert dec.is_available is False
    pipeline.set_state.assert_called_once()


def test_qml6_zero_copy_decoder():
    cb = MagicMock()
    dec = Qml6ZeroCopyDecoder(on_frame_callback=cb)
    dec.is_available = True
    dec._is_focused = True
    dec._appsrc = MagicMock()
    dec._pipeline = MagicMock()
    dec._is_sink_bound = True
    dec._is_playing = False
    mock_gst = MagicMock()
    dec._Gst = mock_gst

    buf = MagicMock()
    mock_gst.Buffer.new_wrapped.return_value = buf
    assert dec.decode_nal(b"\x00\x00\x00\x01\x65", ts_us=2000) is True
    assert dec._is_playing is True
    assert dec.frames_decoded == 1

    # drops frame when not focused
    dec.set_focused(False)
    assert dec.decode_nal(b"\x00\x00\x00\x01\x65") is False

    # close
    dec.close()
    assert dec.is_available is False


def test_shm_engine_properties_and_focus():
    engine = QtSHMMediaEngine()
    hw_mock = MagicMock()
    engine._hw_decoder = hw_mock

    engine.set_video_focused(False)
    assert engine.is_video_focused is False
    hw_mock.set_focused.assert_called_with(False)

    frame_cb = MagicMock()
    engine.on_video_frame = frame_cb
    engine.set_video_focused(True)
    engine._on_hw_decoded_frame(b"rgba", 1280, 720, 100)
    frame_cb.assert_called_once_with(b"rgba", 1280, 720, 100)


def test_shm_engine_recover_pipeline():
    engine = QtSHMMediaEngine()
    hw_mock = MagicMock()
    hw_mock.is_available = True
    engine._hw_decoder = hw_mock

    keyframe_mock = MagicMock()
    engine.request_keyframe = keyframe_mock

    engine._recover_pipeline()
    hw_mock.close.assert_called_once()
    assert hw_mock.is_available is False
    keyframe_mock.assert_called_once()


def test_shm_engine_connect_and_close():
    engine = QtSHMMediaEngine()
    with patch("backend.modules.qt6_gui.media.shm_media_engine.BidirectionalMediaSHM") as mock_shm_cls:
        mock_shm = MagicMock()
        mock_shm_cls.return_value = mock_shm

        assert engine.connect_shm() is True
        assert engine.is_connected is True

        engine.close()
        assert engine.is_connected is False
        mock_shm.close.assert_called_once()


def test_shm_engine_process_downstream_video_raw_rgba():
    engine = QtSHMMediaEngine()
    frame_cb = MagicMock()
    engine.on_video_frame = frame_cb
    engine.is_video_focused = True

    mock_shm = MagicMock()
    engine.shm = mock_shm

    width, height = 10, 10
    rgba_data = bytes([255, 0, 0, 255] * (width * height))
    header = struct.pack(">III", width, height, 0)
    payload = header + rgba_data

    mock_channel = MagicMock()
    mock_channel.read_frame.return_value = (0, 12345, payload)
    mock_shm.get_downstream_channel.return_value = mock_channel

    engine.process_downstream_video(offset=0, channel_id=3)
    frame_cb.assert_called_once_with(rgba_data, width, height, 12345)


def test_shm_engine_process_downstream_video_nal_and_jpeg():
    engine = QtSHMMediaEngine()
    frame_cb = MagicMock()
    engine.on_video_frame = frame_cb
    engine.is_video_focused = True

    mock_shm = MagicMock()
    engine.shm = mock_shm
    mock_channel = MagicMock()
    mock_shm.get_downstream_channel.return_value = mock_channel

    # 1. H.264 NAL with HW decoder
    hw_mock = MagicMock()
    hw_mock.is_available = True
    hw_mock.frames_decoded = 1
    engine._hw_decoder = hw_mock

    nal_payload = b"\x00\x00\x00\x01\x67\x42\x00\x1f"
    mock_channel.read_frame.return_value = (0, 9999, nal_payload)

    engine.process_downstream_video(offset=0, channel_id=3)
    hw_mock.decode_nal.assert_called_with(nal_payload, 9999)

    # 2. JPEG payload fallback
    hw_mock.is_available = False
    jpeg_payload = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9"
    mock_channel.read_frame.return_value = (0, 8888, jpeg_payload)

    with patch("PyQt6.QtGui.QImage.fromData") as mock_from_data:
        mock_qimg = MagicMock()
        mock_qimg.isNull.return_value = False
        mock_rgba = MagicMock()
        mock_rgba.width.return_value = 100
        mock_rgba.height.return_value = 100
        mock_rgba.sizeInBytes.return_value = 40000
        mock_rgba.bits.return_value = MagicMock()
        mock_qimg.convertToFormat.return_value = mock_rgba
        mock_from_data.return_value = mock_qimg

        with patch("builtins.bytes", return_value=b"mock_rgba_bytes"):
            engine.process_downstream_video(offset=10, channel_id=3)
            frame_cb.assert_called_with(b"mock_rgba_bytes", 100, 100, 8888)


def test_shm_engine_process_downstream_audio():
    engine = QtSHMMediaEngine()
    audio_cb = MagicMock()
    engine.on_audio_frame = audio_cb

    mock_shm = MagicMock()
    engine.shm = mock_shm
    mock_channel = MagicMock()
    audio_pcm = b"\x00\x01" * 160
    mock_channel.read_frame.return_value = (1, 5555, audio_pcm)
    mock_shm.get_downstream_channel.return_value = mock_channel

    # Per-channel read
    engine.process_downstream_audio(offset=10, channel_id=1)
    audio_cb.assert_called_once_with(audio_pcm, 1, 5555)

    # Legacy frame dispatcher
    audio_cb.reset_mock()
    mock_shm.downstream.read_frame.return_value = (2, 6666, audio_pcm)
    engine.process_downstream_frame(offset=20)
    audio_cb.assert_called_once_with(audio_pcm, 2, 6666)


def test_shm_engine_write_upstream_mic():
    engine = QtSHMMediaEngine()
    assert engine.write_upstream_mic(b"") == -1

    mock_shm = MagicMock()
    mock_upstream = MagicMock()
    mock_upstream.write_frame.return_value = 512
    mock_shm.upstream = mock_upstream
    engine.shm = mock_shm

    offset = engine.write_upstream_mic(b"\x12\x34" * 160)
    assert offset == 512
    mock_upstream.write_frame.assert_called_once_with(1, 0, b"\x12\x34" * 160)


