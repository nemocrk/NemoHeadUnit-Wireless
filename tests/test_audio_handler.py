import unittest
import os
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
        sink = DynamicChannelAudioSink(channel_id=4, sample_rate=48000, channel_count=2)
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


if __name__ == "__main__":
    unittest.main()
