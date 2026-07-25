import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..main import ChannelManagerModule


class InputChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager

    async def handle_touch_event(self, x: int, y: int, action: int) -> None:
        """Encode AA Input Event message and send via channel 4."""
        payload = struct.pack(">H H H", action, x, y)
        await self.manager.send_wire_frame(4, 0x0001, payload)
