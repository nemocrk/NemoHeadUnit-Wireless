# tests/integration/harness/packet_fixture.py
import struct
from typing import List

class PacketFixture:
    """Records, replays, and injects malformed wire frames for protocol resilience tests."""
    def __init__(self):
        self.captured: List[bytes] = []

    def build_frame(self, channel_id: int, flags: int, payload: bytes) -> bytes:
        return struct.pack(">BBH", channel_id, flags, len(payload)) + payload

    def record_frame(self, frame_bytes: bytes):
        self.captured.append(frame_bytes)

    def corrupt_header(self, frame_bytes: bytes) -> bytes:
        if len(frame_bytes) < 4:
            return frame_bytes
        # Invert channel ID byte
        return bytes([frame_bytes[0] ^ 0xFF]) + frame_bytes[1:]

    def corrupt_declared_length(self, frame_bytes: bytes, declared_length: int) -> bytes:
        if len(frame_bytes) < 4:
            return frame_bytes
        return frame_bytes[:2] + struct.pack(">H", declared_length) + frame_bytes[4:]

    def corrupt_payload(self, frame_bytes: bytes) -> bytes:
        if len(frame_bytes) <= 4:
            return frame_bytes
        return frame_bytes[:4] + b"\x00" * (len(frame_bytes) - 4)
