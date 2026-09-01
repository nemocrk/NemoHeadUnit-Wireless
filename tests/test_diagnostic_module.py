import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from backend.modules.diagnostic.synthetic_media import generate_pcm_tone, calculate_audio_rms, generate_synthetic_h264_frame
from backend.modules.diagnostic.main import DiagnosticModule


class TestDiagnosticModule(unittest.IsolatedAsyncioTestCase):
    def test_synthetic_pcm_generation(self):
        pcm = generate_pcm_tone(freq_hz=1000.0, duration_ms=50, sample_rate=48000, channels=2)
        self.assertGreater(len(pcm), 0)
        rms_db, peak_db = calculate_audio_rms(pcm, channels=2)
        self.assertLess(rms_db, 0.0)
        self.assertGreater(rms_db, -60.0)

    def test_synthetic_h264_frame(self):
        nal = generate_synthetic_h264_frame(width=1280, height=720, is_idr=True)
        self.assertGreater(len(nal), 4)
        self.assertTrue(nal.startswith(b"\x00\x00\x00\x01") or nal.startswith(b"\x00\x00\x01"))

    async def test_diagnostic_module_lifecycle(self):
        module = DiagnosticModule()
        self.assertEqual(module.name, "diagnostic")
        self.assertEqual(module.priority, 5)
        self.assertEqual(module.path_prefix, "/api/diagnostic")


if __name__ == "__main__":
    unittest.main()
