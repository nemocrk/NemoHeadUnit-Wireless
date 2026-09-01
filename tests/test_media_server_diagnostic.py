import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from backend.modules.media_server.diagnostic_routes import register_diagnostic_routes


class DummyAudioAdapter:
    async def get_available_sinks(self):
        return ["default", "hw:0,0", "pulse_out"]

    async def get_available_sources(self):
        return ["default", "hw:0,1", "pulse_in"]

    async def set_active_sink(self, name):
        return True

    async def set_active_source(self, name):
        return True


class DummyMediaServer:
    def __init__(self):
        self.routes = {}
        self.audio_adapter = DummyAudioAdapter()
        self.config = {"audio_output_sink": "default", "audio_input_source": "default"}
        self.shm = MagicMock()
        self.published = []
        self._active_transport_name = "h264"
        self._active_video_codec = "H264"
        self._transport = MagicMock()
        self._transport.get_diagnostics.return_value = {
            "decoder_element": "v4l2slh264dec",
            "decoder_type": "Hardware (V4L2)",
            "frames_decoded": 10,
        }
        self.log = MagicMock()

    def add_http_route(self, method, path, handler):
        self.routes[(method, path)] = handler

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    async def _switch_transport(self, mode):
        self._active_transport_name = mode
        return True


class TestMediaServerDiagnostic(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.media_server = DummyMediaServer()
        register_diagnostic_routes(self.media_server)

    async def test_capabilities_endpoint(self):
        handler = self.media_server.routes.get(("GET", "/diagnostic/capabilities"))
        self.assertIsNotNone(handler)

        req = MagicMock(spec=web.Request)
        resp = await handler(req)
        self.assertEqual(resp.status, 200)

    async def test_audio_inject_endpoint(self):
        handler = self.media_server.routes.get(("POST", "/diagnostic/audio/inject"))
        self.assertIsNotNone(handler)

        req = MagicMock(spec=web.Request)
        req.can_read_body = True
        req.json = AsyncMock(return_value={"format": "pcm", "tone_hz": 440, "duration_ms": 100})

        resp = await handler(req)
        self.assertEqual(resp.status, 200)
        self.assertTrue(len(self.media_server.published) > 0)

    async def test_audio_set_device_endpoint(self):
        handler = self.media_server.routes.get(("POST", "/diagnostic/audio/set_device"))
        self.assertIsNotNone(handler)

        req = MagicMock(spec=web.Request)
        req.can_read_body = True
        req.json = AsyncMock(return_value={"sink": "hw:0,0", "source": "hw:0,1"})

        resp = await handler(req)
        self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
