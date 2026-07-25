from typing import TYPE_CHECKING, Callable, Dict
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
from protos.oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus
from protos.oaa.av.AVInputOpenResponseMessage_pb2 import AVInputOpenResponse
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.av_input")

MSG = ControlMessage.Enum
AV_MSG = AVChannelMessage.Enum


class AVInputChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            AV_MSG.SETUP_REQUEST: self._handle_setup_request,
            AV_MSG.AV_INPUT_OPEN_REQUEST: self._handle_av_input_open_request,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AVInputChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_setup_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AVInputChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK...")
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked = 1
        resp.configs.append(0)
        await self.manager.send_wire_frame(channel_id, AV_MSG.SETUP_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_av_input_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AVInputChannel (ch{channel_id}): Received AVInputOpenRequest — responding AVInputOpenResponse(STATUS_OK)...")
        resp = AVInputOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, AV_MSG.AV_INPUT_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled AVInput Message] AVInputChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
