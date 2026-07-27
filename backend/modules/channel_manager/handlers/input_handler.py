import time
from typing import TYPE_CHECKING, Callable, Dict, List, Optional
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.input.InputChannelMessageIdsEnum_pb2 import InputChannelMessage
from protos.oaa.input.InputBindingResponseMessage_pb2 import InputBindingResponse
from protos.oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
from protos.oaa.input.TouchEventData_pb2 import TouchEvent
from protos.oaa.input.TouchLocationData_pb2 import TouchLocation
from protos.oaa.input.TouchActionEnum_pb2 import TouchAction
from protos.oaa.common.StatusEnum_pb2 import Status
from shared.constants import ChannelType

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.input")

MSG = ControlMessage.Enum
INPUT_MSG = InputChannelMessage.Enum


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

    async def handle_touch_event(
        self,
        action: int,
        pointers: Optional[List[Dict[str, int]]] = None,
        x: int = 0,
        y: int = 0,
        pointer_id: int = 0,
        action_index: int = 0,
    ) -> None:
        """Encode AA Input Event message with Protobuf and send via dynamic Touch Channel."""
        msg = InputEventIndication()
        msg.timestamp = int(time.monotonic() * 1_000_000)

        touch_event = TouchEvent()
        touch_event.touch_action = action
        touch_event.action_index = action_index

        if pointers:
            for p in pointers:
                loc = touch_event.touch_location.add()
                loc.x = int(p.get("x", 0))
                loc.y = int(p.get("y", 0))
                loc.pointer_id = int(p.get("pointer_id", 0))
        else:
            loc = touch_event.touch_location.add()
            loc.x = int(x)
            loc.y = int(y)
            loc.pointer_id = int(pointer_id)

        msg.touch_event.CopyFrom(touch_event)

        input_ch_id = self.manager.get_channel_id_for_type(ChannelType.INPUT)
        payload = msg.SerializeToString()
        await self.manager.send_wire_frame(input_ch_id, INPUT_MSG.INPUT_EVENT_INDICATION, payload, encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Input Message] InputChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")

