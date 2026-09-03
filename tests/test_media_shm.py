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

    def test_channel_shm_isolation(self):
        from shared.media_shm import get_wire_channel_shm_name, get_downstream_channel_shm_name
        wire_name = get_wire_channel_shm_name(98)
        down_name = get_downstream_channel_shm_name(98)

        self.assertNotEqual(wire_name, down_name)

        wire_host = RingSharedMemoryBuffer(wire_name, size=1024*1024, create=True)
        down_host = RingSharedMemoryBuffer(down_name, size=1024*1024, create=True)

        wire_client = RingSharedMemoryBuffer(wire_name, size=1024*1024, create=False)
        down_client = RingSharedMemoryBuffer(down_name, size=1024*1024, create=False)

        # Write wire frame
        wire_offset = wire_host.write_frame(1, 111, b"raw_wire_frame")

        # Write downstream frame
        down_offset = down_host.write_frame(1, 222, b"pcm_downstream_frame")

        # Read back
        st_wire, ts_wire, data_wire = wire_client.read_frame(wire_offset)
        self.assertEqual(data_wire, b"raw_wire_frame")

        st_down, ts_down, data_down = down_client.read_frame(down_offset)
        self.assertEqual(data_down, b"pcm_downstream_frame")

        wire_host.close()
        down_host.close()
        wire_client.close()
        down_client.close()

if __name__ == "__main__":
    unittest.main()
