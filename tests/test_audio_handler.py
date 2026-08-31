import unittest
from backend.modules.qt6_gui.media.audio_handler import QtAudioEngine


class TestAudioHandler(unittest.TestCase):
    def test_audio_engine_lifecycle(self):
        engine = QtAudioEngine()
        # Feed some dummy data to media and speech queues
        with engine._queue_lock:
            engine.channel_queues["media"].extend(b"\x00" * 100)
            engine.channel_queues["speech"].extend(b"\x00" * 100)
        
        # Test close
        engine.close()

        # Verify queues cleared
        self.assertEqual(len(engine.channel_queues["media"]), 0)
        self.assertEqual(len(engine.channel_queues["speech"]), 0)


if __name__ == "__main__":
    unittest.main()
