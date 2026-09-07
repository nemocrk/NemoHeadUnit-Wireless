# tests/integration/harness/mock_phone.py
import asyncio
import struct
from typing import Optional, Tuple

class MockPhoneClient:
    """Simulates an Android Auto mobile device connecting over TCP wire protocol."""
    def __init__(self):
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.channel_map: dict[str, int] = {
            "control": 0,
            "video": 1,
            "media_audio": 2,
            "speech_audio": 3,
            "input": 4,
            "sensor": 5
        }

    async def connect(self, host: str, port: int):
        self.reader, self.writer = await asyncio.open_connection(host, port)

    async def send_frame(self, channel_id: int, flags: int, payload: bytes):
        if not self.writer:
            raise ConnectionError("Not connected")
        header = struct.pack(">BBH", channel_id, flags, len(payload))
        self.writer.write(header + payload)
        await self.writer.drain()

    async def read_frame(self) -> Tuple[int, int, bytes]:
        if not self.reader:
            raise ConnectionError("Not connected")
        header = await self.reader.readexactly(4)
        channel_id, flags, length = struct.unpack(">BBH", header)
        payload = await self.reader.readexactly(length) if length > 0 else b""
        return channel_id, flags, payload

    async def send_video_nal(self, channel_id: int, nal_bytes: bytes):
        # 0x03 = FIRST_FRAME | LAST_FRAME
        await self.send_frame(channel_id, 0x03, nal_bytes)

    async def send_audio_pcm(self, channel_id: int, pcm_bytes: bytes):
        await self.send_frame(channel_id, 0x03, pcm_bytes)

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
