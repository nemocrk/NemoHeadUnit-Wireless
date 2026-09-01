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
        
        # Test close
        engine.close()


if __name__ == "__main__":
    unittest.main()
