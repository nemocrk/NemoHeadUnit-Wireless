import struct
from typing import TYPE_CHECKING, Callable, Dict
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.input.InputChannelMessageIdsEnum_pb2 import InputChannelMessage
from protos.oaa.input.InputBindingResponseMessage_pb2 import InputBindingResponse
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.input")

MSG = ControlMessage.Enum
INPUT_MSG = InputChannelMessage.Enum


from shared.constants import ChannelType


class InputChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            INPUT_MSG.BINDING_REQUEST: self._handle_binding_request,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"InputChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_binding_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"InputChannel (ch{channel_id}): Received KeyBindingRequest — responding KeyBindingResponse(STATUS_OK)...")
        resp = InputBindingResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, INPUT_MSG.BINDING_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def handle_touch_event(self, x: int, y: int, action: int) -> None:
        """Encode AA Input Event message and send via dynamic Touch Channel."""
        payload = struct.pack(">H H H", action, x, y)
        input_ch_id = self.manager.get_channel_id_for_type(ChannelType.INPUT)
        await self.manager.send_wire_frame(input_ch_id, INPUT_MSG.INPUT_EVENT_INDICATION, payload, encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Input Message] InputChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
