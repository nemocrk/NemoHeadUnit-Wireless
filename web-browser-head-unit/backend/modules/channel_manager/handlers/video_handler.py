from typing import TYPE_CHECKING, Callable, Dict
from shared.nal_utils import pack_media_frame, STREAM_TYPE_VIDEO
from shared.proto_utils import parse_media_with_timestamp
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
from protos.oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus
from protos.oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
from protos.oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication
from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum
AV_MSG = AVChannelMessage.Enum


from shared.constants import ChannelType


class VideoChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            AV_MSG.SETUP_REQUEST: self._handle_setup_request,
            AV_MSG.START_INDICATION: self._handle_start_indication,
            AV_MSG.STOP_INDICATION: self._handle_stop_indication,
            AV_MSG.VIDEO_FOCUS_REQUEST: self._handle_focus_request,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_setup_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK...")
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked = 1
        resp.configs.append(0)
        await self.manager.send_wire_frame(channel_id, AV_MSG.SETUP_RESPONSE, resp.SerializeToString(), encrypted=True)

        # Send VideoFocusIndication (PROJECTED)
        focus_ind = VideoFocusIndication()
        focus_ind.focus_mode = VideoFocusMode.VIDEO_FOCUS_PROJECTED
        focus_ind.unrequested = False
        await self.manager.send_wire_frame(channel_id, AV_MSG.VIDEO_FOCUS_INDICATION, focus_ind.SerializeToString(), encrypted=True)

    async def _handle_start_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelStartIndication — video stream ACTIVE")

    async def _handle_stop_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelStopIndication — video stream STOPPED")

    async def _handle_focus_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received VideoFocusRequest — sending VideoFocusIndication(PROJECTED)...")
        focus_ind = VideoFocusIndication()
        focus_ind.focus_mode = VideoFocusMode.VIDEO_FOCUS_PROJECTED
        focus_ind.unrequested = False
        await self.manager.send_wire_frame(channel_id, AV_MSG.VIDEO_FOCUS_INDICATION, focus_ind.SerializeToString(), encrypted=True)

    async def process_shm_frame(self, message_id: int, payload: bytes) -> None:
        ts_us = 0
        codec_payload = b""

        if message_id == AV_MSG.AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            ts_us, codec_payload = parse_media_with_timestamp(payload)
        elif message_id == AV_MSG.AV_MEDIA_INDICATION:
            codec_payload = payload
        else:
            return

        if not codec_payload:
            return

        binary_frame = pack_media_frame(STREAM_TYPE_VIDEO, ts_us, codec_payload)
        await self.manager.broadcast_ws_media(binary_frame)

        # Immediate MediaAck using AVMediaAckIndication
        ack = AVMediaAckIndication()
        ack.session = 0
        ack.value = 1
        video_ch_id = self.manager.get_channel_id_for_type(ChannelType.VIDEO)
        await self.manager.send_wire_frame(video_ch_id, AV_MSG.AV_MEDIA_ACK_INDICATION, ack.SerializeToString(), encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Video Message] VideoChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
