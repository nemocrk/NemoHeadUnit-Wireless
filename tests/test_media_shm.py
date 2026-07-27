import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import unittest
from shared.media_shm import BidirectionalMediaSHM, RingSharedMemoryBuffer

class TestMediaSHM(unittest.TestCase):
    def test_ring_shm_write_read(self):
        writer = RingSharedMemoryBuffer("nemo_test_shm", size=1024*1024, create=True)
        reader = RingSharedMemoryBuffer("nemo_test_shm", size=1024*1024, create=False)

        ts = 12345678
        payload = b"\x00\x00\x00\x01\x65\x90\x01\x02\x03\x04"
        offset = writer.write_frame(0, ts, payload)

        self.assertGreaterEqual(offset, 0)

        s_type, out_ts, out_payload = reader.read_frame(offset)
        self.assertEqual(s_type, 0)
        self.assertEqual(out_ts, ts & 0xFFFFFFFF)
        self.assertEqual(out_payload, payload)

        writer.close()
        reader.close()

    def test_bidirectional_shm(self):
        shm_host = BidirectionalMediaSHM(create=True)
        shm_client = BidirectionalMediaSHM(create=False)

        down_offset = shm_host.downstream.write_frame(0, 999, b"downstream_data")
        st, ts, data = shm_client.downstream.read_frame(down_offset)
        self.assertEqual(data, b"downstream_data")

        up_offset = shm_client.upstream.write_frame(1, 888, b"upstream_mic_data")
        st, ts, data = shm_host.upstream.read_frame(up_offset)
        self.assertEqual(data, b"upstream_mic_data")

        shm_host.close()
        shm_client.close()

if __name__ == "__main__":
    unittest.main()
