import unittest
import os
from unittest.mock import MagicMock, patch
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from backend.modules.qt6_gui.media.audio_handler import QtAudioEngine, AudioPcmStream, DynamicChannelAudioSink


class TestAudioHandler(unittest.TestCase):
    def test_audio_pcm_stream_io(self):
        stream = AudioPcmStream(prebuffer_ms=0)
        # Verify pure virtual methods implemented
        self.assertTrue(stream.isSequential())
        self.assertEqual(stream.writeData(b"123"), -1)
        
        # Test write and read
        stream.write_pcm(b"\x01\x02\x03\x04")
        chunk = stream.readData(4)
        self.assertEqual(chunk, b"\x01\x02\x03\x04")

        # Test clear
        stream.clear()
        self.assertEqual(stream.bytesAvailable(), 0)

    def test_audio_engine_lifecycle(self):
        engine = QtAudioEngine()
        self.assertIsNotNone(engine)
        
        # Push PCM frame
        engine.play_pcm_frame(b"\x00\x00" * 200, channel_id=4)
        self.assertIn(4, engine.sinks)

        # Verify get_metrics telemetry
        metrics = engine.get_metrics()
        self.assertIn(4, metrics)
        ch_metric = metrics[4]
        self.assertEqual(ch_metric["channel_id"], 4)
        self.assertIn("app_buffer", ch_metric)
        self.assertIn("sink_buffer", ch_metric)
        self.assertGreaterEqual(ch_metric["app_buffer"]["buffered_bytes"], 0)
        self.assertIn("underruns", ch_metric["app_buffer"])
        
        # Test close
        engine.close()

    def test_audio_buffer_metrics_and_underrun(self):
        # 48000Hz, 2ch, 16-bit = 192000 bytes/sec -> 150ms = 28800 bytes prebuffer
        stream = AudioPcmStream(sample_rate=48000, channels=2, prebuffer_ms=150)
        metrics = stream.get_buffer_metrics()
        self.assertTrue(metrics["is_buffering"])
        self.assertEqual(metrics["buffered_bytes"], 0)
        self.assertEqual(metrics["underruns"], 0)

        # Write data below prebuffer threshold (still buffering)
        stream.write_pcm(b"\x00" * 10000)
        self.assertTrue(stream.get_buffer_metrics()["is_buffering"])

        # Write remaining data to exceed prebuffer (28800 bytes)
        stream.write_pcm(b"\x00" * 20000)
        self.assertFalse(stream.get_buffer_metrics()["is_buffering"])
        self.assertGreaterEqual(stream.get_buffer_metrics()["buffered_ms"], 150)

        # Read entire buffer until drained -> should enter buffering and increment underrun
        drained_chunk = stream.readData(30000)
        self.assertEqual(len(drained_chunk), 30000)
        self.assertTrue(stream.get_buffer_metrics()["is_buffering"])
        self.assertEqual(stream.get_buffer_metrics()["underruns"], 1)

    def test_dynamic_channel_native_format(self):
        # 16000Hz mono Speech channel (Ch 5) configured natively
        sink = DynamicChannelAudioSink(channel_id=5, sample_rate=16000, channel_count=1)
        sink.configure_codec(codec="MEDIA_CODEC_AUDIO_PCM", sample_rate=16000, channel_count=1)
        self.assertEqual(sink.sample_rate, 16000)
        self.assertEqual(sink.channel_count, 1)
        
        # Push 320 samples (20ms @ 16kHz mono = 640 bytes)
        raw_mono_16k = b"\x10\x00" * 320
        sink.push_frame(raw_mono_16k)
        self.assertEqual(sink.total_bytes_in, 640)
        self.assertEqual(sink.get_metrics()["app_buffer"]["buffered_bytes"], 640)
        sink.close()

    def test_two_stage_prebuffer_and_priming(self):
        # 48000Hz 2ch Int16 = 192000 B/s.
        # 150ms prebuffer = 28800 bytes.
        sink = DynamicChannelAudioSink(channel_id=4, sample_rate=48000, channel_count=2, prebuffer_ms=150)
        sink.configure_codec(codec="MEDIA_CODEC_AUDIO_PCM", sample_rate=48000, channel_count=2)
        
        # Initially buffering
        m0 = sink.get_metrics()["app_buffer"]
        self.assertTrue(m0["is_buffering"])
        self.assertEqual(m0["buffered_bytes"], 0)
        
        # Push 10000 bytes (< 28800 bytes -> still buffering)
        sink.push_frame(b"\x00" * 10000)
        m1 = sink.get_metrics()["app_buffer"]
        self.assertTrue(m1["is_buffering"])
        self.assertEqual(m1["buffered_bytes"], 10000)
        
        # Push another 25000 bytes (total 35000 >= 28800 -> prebuffer filled)
        sink.push_frame(b"\x00" * 25000)
        m2 = sink.get_metrics()["app_buffer"]
        self.assertFalse(m2["is_buffering"])
        sink.close()

    def test_audio_dump_wav(self):
        import os
        import tempfile
        os.environ["NEMO_AUDIO_DUMP"] = "1"
        try:
            sink = DynamicChannelAudioSink(channel_id=99, sample_rate=48000, channel_count=2)
            sink.configure_codec(codec="MEDIA_CODEC_AUDIO_PCM", sample_rate=48000, channel_count=2)
            sink.push_frame(b"\x00\x01" * 480)
            sink.close()
            expected_path = os.path.join(tempfile.gettempdir(), "nemo_audio_ch99_48000hz.wav")
            self.assertTrue(os.path.exists(expected_path))
            self.assertGreater(os.path.getsize(expected_path), 0)
            os.remove(expected_path)
        finally:
            os.environ.pop("NEMO_AUDIO_DUMP", None)

    def test_parse_media_with_timestamp_raw_pcm(self):
        import struct
        from shared.proto_utils import parse_media_with_timestamp
        fake_ts = 1234567890
        ts_bytes = struct.pack(">Q", fake_ts)
        # 960 bytes of PCM audio containing byte 0x12 (would previously trigger false protobuf match)
        pcm_bytes = bytearray(b"\x00\x00" * 480)
        pcm_bytes[100] = 0x12
        pcm_bytes[101] = 50
        raw_frame = bytes(ts_bytes + pcm_bytes)

        ts, parsed = parse_media_with_timestamp(raw_frame)
        self.assertEqual(ts, fake_ts)
        self.assertEqual(parsed, bytes(pcm_bytes))
        self.assertEqual(len(parsed), 960)

    def test_find_audio_devices(self):
        from unittest.mock import MagicMock, patch
        from backend.modules.qt6_gui.media.audio_handler import find_audio_output_device, find_audio_input_device

        mock_dev1 = MagicMock()
        mock_dev1.description.return_value = "USB DAC Output"
        mock_dev1.id.return_value = b"usb_dac_out"

        mock_dev2 = MagicMock()
        mock_dev2.description.return_value = "USB Microphone Input"
        mock_dev2.id.return_value = b"usb_mic_in"

        with patch("backend.modules.qt6_gui.media.audio_handler.QMediaDevices") as mock_qmd:
            mock_qmd.audioOutputs.return_value = [mock_dev1]
            mock_qmd.defaultAudioOutput.return_value = mock_dev1
            mock_qmd.audioInputs.return_value = [mock_dev2]
            mock_qmd.defaultAudioInput.return_value = mock_dev2

            # Match output device
            self.assertEqual(find_audio_output_device("USB DAC"), mock_dev1)
            # Default output device
            self.assertEqual(find_audio_output_device("default"), mock_dev1)
            # Fallback output device
            self.assertEqual(find_audio_output_device("Nonexistent"), mock_dev1)

            # Match input device
            self.assertEqual(find_audio_input_device("USB Mic"), mock_dev2)
            # Default input device
            self.assertEqual(find_audio_input_device("default"), mock_dev2)
            # Fallback input device
            self.assertEqual(find_audio_input_device("Nonexistent"), mock_dev2)

    def test_audio_pcm_stream_format_and_pause(self):
        stream = AudioPcmStream(sample_rate=44100, channels=2, prebuffer_ms=100)
        stream.configure_format(sample_rate=48000, channels=2, prebuffer_ms=150)
        self.assertEqual(stream._sample_rate, 48000)
        self.assertTrue(stream._is_buffering)

        # Buffer overflow test
        stream.write_pcm(b"\x00" * 300000, max_buffer_bytes=100000)
        self.assertLessEqual(len(stream._buffer), 100000)

        # Pause and resume
        stream.set_paused(True)
        self.assertTrue(stream._is_paused)
        stream.set_paused(False)
        self.assertFalse(stream._is_paused)

    def test_dynamic_channel_audio_sink_modes_and_pts(self):
        import time
        sink = DynamicChannelAudioSink(channel_id=3, sample_rate=48000, channel_count=2)
        
        # Test status transitions
        sink.set_stream_status("STOPPED")
        self.assertTrue(sink._is_stopped)
        sink.set_stream_status("START")
        self.assertFalse(sink._is_stopped)

        # Test pause
        sink.set_paused(True)
        self.assertTrue(sink._is_paused)
        sink.set_paused(False)
        self.assertFalse(sink._is_paused)

        # Test push frame with timestamp baseline and forward jump
        t0 = time.time()
        sink.push_frame(b"\x00\x00" * 480, ts_us=1_000_000)
        self.assertEqual(sink.last_ts_us, 1_000_000)
        self.assertEqual(sink.current_lag_ms, 0.0)

        # Push frame with huge PTS jump (> 1.5s)
        sink.push_frame(b"\x00\x00" * 480, ts_us=10_000_000)
        self.assertEqual(sink.current_lag_ms, 0.0)

        # Test is_streaming logic
        sink.last_frame_time = t0
        self.assertTrue(sink.is_streaming)
        sink.last_frame_time = t0 - 1.0
        with sink._app_lock:
            sink._app_buffer.clear()
        self.assertFalse(sink.is_streaming)

        sink.close()

    def test_dynamic_channel_audio_sink_aac_decoding(self):
        import numpy as np
        sink = DynamicChannelAudioSink(channel_id=2, sample_rate=48000, channel_count=2)
        sink._is_aac = True
        mock_decoder = MagicMock()
        mock_frame = MagicMock()
        mock_arr = np.zeros(480, dtype=np.int16)
        mock_frame.to_ndarray.return_value = mock_arr
        mock_decoder.decode.return_value = [mock_frame]
        sink.aac_decoder = mock_decoder

        # Test with resampler
        mock_resampler = MagicMock()
        mock_resampler.resample.return_value = [mock_frame]
        sink.resampler = mock_resampler

        sink.push_frame(b"\x01\x02\x03\x04")
        self.assertGreater(sink.total_bytes_in, 0)
        
        # Test direct init aac decoder
        sink._init_aac_decoder()
        
        sink.close()

    def test_dynamic_channel_audio_sink_error_handling(self):
        sink = DynamicChannelAudioSink(channel_id=1, sample_rate=48000, channel_count=2)
        sink._handle_sink_error(1)
        sink.close()

    def test_dynamic_channel_audio_sink_do_start(self):
        sink = DynamicChannelAudioSink(channel_id=1, sample_rate=48000, channel_count=2)
        mock_sink = MagicMock()
        mock_io = MagicMock()
        mock_sink.start.return_value = mock_io

        with patch("backend.modules.qt6_gui.media.audio_handler.QAudioSink", return_value=mock_sink):
            sink._do_start()
            self.assertTrue(sink._is_started)
            self.assertEqual(sink.audio_sink, mock_sink)
            self.assertEqual(sink.audio_io, mock_io)

        sink.close()

    def test_dynamic_channel_audio_sink_flush(self):
        from unittest.mock import MagicMock
        sink = DynamicChannelAudioSink(channel_id=1, sample_rate=48000, channel_count=2, prebuffer_ms=0)
        mock_sink = MagicMock()
        mock_io = MagicMock()
        mock_sink.bytesFree.return_value = 1000
        mock_sink.bufferSize.return_value = 4000
        mock_io.write.return_value = 400

        sink.audio_sink = mock_sink
        sink.audio_io = mock_io
        sink._is_buffering = False
        sink._is_started = True

        with sink._app_lock:
            sink._app_buffer.extend(b"\x00" * 800)

        sink._flush_to_sink()
        mock_io.write.assert_called_once()
        self.assertEqual(sink.total_bytes_out, 400)
        sink.close()

    def test_dynamic_channel_audio_sink_flush_reentrancy_guard(self):
        sink = DynamicChannelAudioSink(channel_id=1, sample_rate=48000, channel_count=2, prebuffer_ms=0)
        mock_sink = MagicMock()
        mock_io = MagicMock()
        mock_sink.bytesFree.return_value = 1000
        mock_sink.bufferSize.return_value = 4000

        # Simulate re-entrant call during write()
        reentrant_calls = [0]
        def write_side_effect(data):
            reentrant_calls[0] += 1
            if reentrant_calls[0] < 5:
                sink._flush_to_sink()
            return len(data)

        mock_io.write.side_effect = write_side_effect
        sink.audio_sink = mock_sink
        sink.audio_io = mock_io
        sink._is_buffering = False
        sink._is_started = True

        with sink._app_lock:
            sink._app_buffer.extend(b"\x00" * 800)

        sink._flush_to_sink()
        self.assertEqual(reentrant_calls[0], 1)
        sink.close()

    def test_dynamic_channel_audio_sink_flush_chunking(self):
        sink = DynamicChannelAudioSink(channel_id=1, sample_rate=48000, channel_count=2, prebuffer_ms=0)
        mock_sink = MagicMock()
        mock_io = MagicMock()
        mock_sink.bytesFree.return_value = 20000
        mock_sink.bufferSize.return_value = 48000

        written_chunks = []
        def write_side_effect(data):
            written_chunks.append(len(data))
            return len(data)

        mock_io.write.side_effect = write_side_effect
        sink.audio_sink = mock_sink
        sink.audio_io = mock_io
        sink._is_buffering = False
        sink._is_started = True

        # Buffer 10000 bytes (> 4096B chunk limit)
        with sink._app_lock:
            sink._app_buffer.extend(b"\x00" * 10000)

        sink._flush_to_sink()
        # All writes must be <= 4096 bytes
        self.assertTrue(all(c <= 4096 for c in written_chunks))
        self.assertEqual(sum(written_chunks), 10000)
        sink.close()

    def test_qt_audio_engine_multi_channel_and_routes(self):
        engine = QtAudioEngine()
        engine.set_output_sink("CustomSink")
        self.assertEqual(engine.target_output_sink, "CustomSink")

        # Explicit channel codec config
        engine.configure_channel_codec(1, "MEDIA_CODEC_AUDIO_PCM", sample_rate=44100, channel_count=2)
        self.assertIn(1, engine.sinks)

        # Channel 5 speech fallback
        engine.play_pcm_frame(b"\x00\x00" * 160, channel_id=5)
        self.assertIn(5, engine.sinks)
        self.assertEqual(engine.sinks[5].sample_rate, 16000)

        # Broadcast paused and stream status
        engine.set_paused(True)
        engine.set_stream_status("STOPPED")
        engine.set_paused(False, channel_id=1)
        engine.set_stream_status("ACTIVE", channel_id=1)

        # Reconfigure output sink when sinks are active
        engine.set_output_sink("UpdatedDevice")

        engine.close()

    def test_qt_audio_engine_microphone(self):
        from unittest.mock import MagicMock, patch
        engine = QtAudioEngine()

        # Test no input device
        with patch("backend.modules.qt6_gui.media.audio_handler.find_audio_input_device", return_value=None):
            self.assertFalse(engine.start_microphone())

        mock_input = MagicMock()
        mock_input.isNull.return_value = False
        mock_input.description.return_value = "Test Mic"
        mock_input.isFormatSupported.return_value = False
        mock_pref = MagicMock()
        mock_pref.sampleFormat.return_value = "Int16"
        mock_pref.sampleRate.return_value = 16000
        mock_pref.channelCount.return_value = 1
        mock_input.preferredFormat.return_value = mock_pref

        mock_source = MagicMock()
        mock_io = MagicMock()
        mock_source.start.return_value = mock_io
        mock_io.bytesAvailable.return_value = 1280
        mock_io.readAll.return_value.data.return_value = b"\x00" * 1280

        captured_chunks = []
        engine.mic_data_captured.connect(lambda chunk: captured_chunks.append(chunk))

        with patch("backend.modules.qt6_gui.media.audio_handler.find_audio_input_device", return_value=mock_input), \
             patch("backend.modules.qt6_gui.media.audio_handler.QAudioSource", return_value=mock_source):
            started = engine.start_microphone()
            self.assertTrue(started)
            engine._poll_mic()
            # 1280 bytes = exactly two 640-byte chunks
            self.assertEqual(len(captured_chunks), 2)
            self.assertEqual(len(captured_chunks[0]), 640)

            # Change input source while running
            engine.set_input_source("NewMic")

            # Stop mic
            engine.stop_microphone()
            self.assertIsNone(engine.audio_source)

        engine.close()


if __name__ == "__main__":
    unittest.main()
