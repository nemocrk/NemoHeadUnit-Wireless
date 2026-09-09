import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import unittest
from shared.nal_utils import (
    pack_media_frame,
    unpack_media_frame,
    get_nal_type,
    is_keyframe,
    is_header_nal,
    STREAM_TYPE_VIDEO,
    STREAM_TYPE_AUDIO,
    NAL_TYPE_IDR,
    NAL_TYPE_SPS,
    NAL_TYPE_PPS,
)

class TestNalUtils(unittest.TestCase):
    def test_pack_unpack_frame(self):
        ts = 1689000000123456
        payload = b"\x00\x00\x00\x01\x65\x01\x02\x03"
        packed = pack_media_frame(STREAM_TYPE_VIDEO, ts, payload)

        self.assertEqual(len(packed), 9 + len(payload))
        s_type, out_ts, out_payload = unpack_media_frame(packed)
        self.assertEqual(s_type, STREAM_TYPE_VIDEO)
        self.assertEqual(out_ts, ts)
        self.assertEqual(out_payload, payload)

    def test_nal_type_parsing(self):
        # Keyframe (0x65 -> NAL 5)
        raw_keyframe = b"\x00\x00\x00\x01\x65\x90\x01"
        self.assertEqual(get_nal_type(raw_keyframe), 5)
        self.assertTrue(is_keyframe(raw_keyframe))
        self.assertFalse(is_header_nal(raw_keyframe))

        # SPS (0x67 -> NAL 7)
        raw_sps = b"\x00\x00\x01\x67\x42\x00"
        self.assertEqual(get_nal_type(raw_sps), 7)
        self.assertTrue(is_header_nal(raw_sps))

    def test_empty_and_short_payloads(self):
        self.assertEqual(get_nal_type(b""), 0)
        self.assertFalse(is_keyframe(b""))
        self.assertFalse(is_header_nal(b""))
        with self.assertRaises(ValueError):
            unpack_media_frame(b"short")

    def test_hevc_codec_parsing(self):
        # HEVC IDR (NAL 19 << 1 = 38 = 0x26)
        hevc_idr = b"\x00\x00\x00\x01" + bytes([19 << 1, 0x01])
        self.assertTrue(is_keyframe(hevc_idr, codec="H265"))
        self.assertTrue(is_keyframe(hevc_idr, codec="HEVC"))
        self.assertFalse(is_header_nal(hevc_idr, codec="H265"))

        # HEVC SPS (NAL 33 << 1 = 66 = 0x42)
        hevc_sps = b"\x00\x00\x00\x01" + bytes([33 << 1, 0x01])
        self.assertTrue(is_header_nal(hevc_sps, codec="HEVC"))

    def test_vp9_and_av1_codec_parsing(self):
        # VP9 frame marker (binary 10 = 0x80) and keyframe (type 0 = 0x00)
        vp9_keyframe = bytes([0x80])  # marker=10, type=0
        self.assertTrue(is_keyframe(vp9_keyframe, codec="VP9"))
        self.assertFalse(is_header_nal(vp9_keyframe, codec="VP9"))

        vp9_non_key = bytes([0x84])  # marker=10, type=1 (bit 2 = 1)
        self.assertFalse(is_keyframe(vp9_non_key, codec="VP9"))

        # AV1 OBU Sequence Header (OBU 1 << 3 = 8)
        av1_seq = bytes([1 << 3])
        self.assertTrue(is_keyframe(av1_seq, codec="AV1"))
        self.assertTrue(is_header_nal(av1_seq, codec="AV1"))


if __name__ == "__main__":
    unittest.main()

