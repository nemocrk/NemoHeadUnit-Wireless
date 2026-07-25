from typing import TYPE_CHECKING
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.wifi")

MSG = ControlMessage.Enum
_MSG_CREDENTIALS_REQUEST   = 0x8001
_MSG_CREDENTIALS_RESPONSE  = 0x8002


class WifiChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.info(f"WifiChannel (ch{channel_id}) msgId=0x{message_id:04x} len={len(body)}")
        if message_id == MSG.CHANNEL_OPEN_REQUEST:
            status_ok = b"\x08\x00"
            await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, status_ok, encrypted=True)
        elif message_id == _MSG_CREDENTIALS_REQUEST:
            resp = b"\x0a\x0dAndroidAutoAP\x12\x0812345678\x20\x08\x28\x01"
            await self.manager.send_wire_frame(channel_id, _MSG_CREDENTIALS_RESPONSE, resp, encrypted=True)
