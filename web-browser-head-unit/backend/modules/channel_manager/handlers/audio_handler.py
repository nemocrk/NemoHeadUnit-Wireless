from typing import TYPE_CHECKING, Callable, Dict
from shared.nal_utils import pack_media_frame, STREAM_TYPE_AUDIO
from shared.proto_utils import parse_media_with_timestamp
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
from protos.oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus
from protos.oaa.audio.AudioFocusResponseMessage_pb2 import AudioFocusResponse
from protos.oaa.audio.AudioFocusStateEnum_pb2 import AudioFocusState
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum
AV_MSG = AVChannelMessage.Enum


class AudioChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            AV_MSG.SETUP_REQUEST: self._handle_setup_request,
            AV_MSG.START_INDICATION: self._handle_start_indication,
            AV_MSG.STOP_INDICATION: self._handle_stop_indication,
            MSG.AUDIO_FOCUS_REQUEST: self._handle_focus_request,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_setup_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK...")
        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked = 1
        resp.configs.append(0)
        await self.manager.send_wire_frame(channel_id, AV_MSG.SETUP_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_start_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AVChannelStartIndication — audio stream ACTIVE")

    async def _handle_stop_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AVChannelStopIndication — audio stream STOPPED")

    async def _handle_focus_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AudioFocusRequest — responding AudioFocusResponse(GAIN)...")
        focus_resp = AudioFocusResponse()
        focus_resp.audio_focus_state = AudioFocusState.GAIN
        focus_resp.granted = False
        await self.manager.send_wire_frame(channel_id, MSG.AUDIO_FOCUS_RESPONSE, focus_resp.SerializeToString(), encrypted=True)

    async def process_shm_frame(self, message_id: int, payload: bytes) -> None:
        ts_us = 0
        audio_payload = b""

        if message_id == AV_MSG.AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            ts_us, audio_payload = parse_media_with_timestamp(payload)
        elif message_id == AV_MSG.AV_MEDIA_INDICATION:
            audio_payload = payload
        else:
            return

        if not audio_payload:
            return

        binary_frame = pack_media_frame(STREAM_TYPE_AUDIO, ts_us, audio_payload)
        await self.manager.broadcast_ws_media(binary_frame)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Audio Message] AudioChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
