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
from protos.oaa.video.VideoFocusRequestMessage_pb2 import VideoFocusRequest
from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
from protos.oaa.video.VideoFocusReasonEnum_pb2 import VideoFocusReason
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum
AV_MSG = AVChannelMessage.Enum
UNACKED_FRAMES_THRESHOLD = 30

from shared.constants import ChannelType


class VideoChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.setup_completed = False
        self.frame_count = 0
        self.session_id = 0

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            AV_MSG.SETUP_REQUEST: self._handle_setup_request,
            AV_MSG.START_INDICATION: self._handle_start_indication,
            AV_MSG.STOP_INDICATION: self._handle_stop_indication,
            AV_MSG.VIDEO_FOCUS_REQUEST: self._handle_focus_request,
            AV_MSG.VIDEO_FOCUS_INDICATION: self._handle_focus_indication,
        }


    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            try:
                await handler(channel_id, body)
            except Exception as exc:
                self.log.error(f"VideoChannel (ch{channel_id}): Error in msgId 0x{message_id:04x} handler — {exc}", exc_info=True)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def update_video_focus(self) -> None:
        """Gates VideoFocusIndication (0x8008) based on active frontend WebSocket clients."""
        if not self.setup_completed:
            return

        has_clients = len(self.manager.ws_clients) > 0
        focus_enum = VideoFocusMode.Enum.PROJECTED if has_clients else VideoFocusMode.Enum.NATIVE
        mode_name = "PROJECTED" if has_clients else "NATIVE"

        self.log.info(f"📹 VideoChannel: Sending VideoFocusIndication({mode_name}) to phone (Active WS clients: {len(self.manager.ws_clients)})")

        focus_ind = VideoFocusIndication()
        focus_ind.focus_mode = focus_enum
        focus_ind.unrequested = False

        video_ch_id = self.manager.get_channel_id_for_type(ChannelType.VIDEO)
        await self.manager.send_wire_frame(video_ch_id, AV_MSG.VIDEO_FOCUS_INDICATION, focus_ind.SerializeToString(), encrypted=True)



    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_setup_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK (max_unacked=10)...")
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked = UNACKED_FRAMES_THRESHOLD
        resp.configs.append(0)
        await self.manager.send_wire_frame(channel_id, AV_MSG.SETUP_RESPONSE, resp.SerializeToString(), encrypted=True)

        self.setup_completed = True
        await self.update_video_focus()

    async def _handle_start_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelStartIndication — video stream ACTIVE")
        if body:
            try:
                from protos.oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
                start_ind = AVChannelStartIndication()
                start_ind.ParseFromString(body)
                self.session_id = start_ind.session
                self.log.info(f"📹 VideoChannel (ch{channel_id}): Extracted start session_id={self.session_id}")
            except Exception as exc:
                self.log.warning(f"VideoChannel (ch{channel_id}): Failed to parse start indication session_id: {exc}")

    async def _handle_stop_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelStopIndication — video stream STOPPED")


    async def _handle_focus_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received VideoFocusRequest from phone — updating focus state...")
        await self.update_video_focus()


    async def _handle_focus_indication(self, channel_id: int, body: bytes) -> None:

        try:
            ind = VideoFocusIndication()
            ind.ParseFromString(body)
            mode_name = VideoFocusMode.Enum.Name(ind.focus_mode) if hasattr(VideoFocusMode.Enum, "Name") else ind.focus_mode
            self.log.info(f"VideoChannel (ch{channel_id}): Received VideoFocusIndication from phone: focus_mode={mode_name}, unrequested={ind.unrequested}")
        except Exception as exc:
            self.log.warning(f"VideoChannel (ch{channel_id}): VideoFocusIndication parse warning: {exc}")



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

        video_ch_id = self.manager.get_channel_id_for_type(ChannelType.VIDEO)
        binary_frame = pack_media_frame(video_ch_id, ts_us, codec_payload)
        await self.manager.broadcast_ws_media(binary_frame)
        if self.frame_count % UNACKED_FRAMES_THRESHOLD == 0:
            self.log.debug(f"📹 [Video Stream Flow] Processed video frame {self.frame_count}/{UNACKED_FRAMES_THRESHOLD} (ch{video_ch_id}): msgId=0x{message_id:04x}, payload_len={len(codec_payload)}, ts={ts_us} µs -> Broadcasting to {len(self.manager.ws_clients)} WS client(s)")

        # Batch MediaAck every 10 frames using AVMediaAckIndication(ack_count=10)
        self.frame_count += 1
        if self.frame_count % UNACKED_FRAMES_THRESHOLD == 0:
            ack = AVMediaAckIndication()
            ack.session_id = self.session_id
            ack.ack_count = UNACKED_FRAMES_THRESHOLD
            self.log.debug(f"📹 VideoChannel (ch{video_ch_id}): Sending batch AVMediaAckIndication (session_id={self.session_id}, ack_count=10, total_frames={self.frame_count})")
            await self.manager.send_wire_frame(video_ch_id, AV_MSG.AV_MEDIA_ACK_INDICATION, ack.SerializeToString(), encrypted=True, log_level='debug')






    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Video Message] VideoChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
