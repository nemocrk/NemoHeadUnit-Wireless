import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict
from shared.logger import get_logger
from shared.proto_utils import build_media_with_timestamp
from shared.constants import ChannelType
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
from protos.oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus
from protos.oaa.av.AVInputOpenRequestMessage_pb2 import AVInputOpenRequest
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
        # self.debug_capture_path = Path(__file__).resolve().parents[4] / "mic_debug_capture.raw"
        # self.total_captured_bytes = 0

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            AV_MSG.SETUP_REQUEST: self._handle_setup_request,
            AV_MSG.AV_INPUT_OPEN_REQUEST: self._handle_av_input_open_request,
            AV_MSG.START_INDICATION: self._handle_start_indication,
            AV_MSG.STOP_INDICATION: self._handle_stop_indication,
            AV_MSG.AV_MEDIA_ACK_INDICATION: self._handle_media_ack_indication,
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

    async def _handle_start_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AVInputChannel (ch{channel_id}): Received AVChannelStartIndication — microphone stream ACTIVE. Notifying media_server...")
        self.manager.publish("media.audio.mic_control", {"enabled": True})

    async def _handle_stop_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AVInputChannel (ch{channel_id}): Received AVChannelStopIndication — microphone stream STOPPED. Notifying media_server...")
        self.manager.publish("media.audio.mic_control", {"enabled": False})

    async def _handle_av_input_open_request(self, channel_id: int, body: bytes) -> None:
        open_stream = True
        try:
            req = AVInputOpenRequest()
            req.ParseFromString(body)
            open_stream = req.open
        except Exception as exc:
            self.log.warning(f"AVInputChannel (ch{channel_id}): Could not parse AVInputOpenRequest body: {exc}")

        self.log.info(
            f"AVInputChannel (ch{channel_id}): Received AVInputOpenRequest(open={open_stream}) — "
            f"responding AVInputOpenResponse(session=0, value=0) & notifying media_server..."
        )
        resp = AVInputOpenResponse()
        resp.session = 0
        resp.value = 0
        await self.manager.send_wire_frame(channel_id, AV_MSG.AV_INPUT_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)
        self.manager.publish("media.audio.mic_control", {"enabled": open_stream})

    async def send_mic_data(self, pcm_data: bytes) -> None:
        """Pack and transmit upstream microphone audio chunk to the phone on AUDIO_MIC channel."""
        mic_ch_id = self.manager.get_channel_id_for_type(ChannelType.AUDIO_MIC)
        ts_us = int(time.monotonic() * 1_000_000)
        payload = build_media_with_timestamp(ts_us, pcm_data)
        await self.manager.send_wire_frame(mic_ch_id, AV_MSG.AV_MEDIA_WITH_TIMESTAMP_INDICATION, payload, encrypted=True)

    async def _handle_media_ack_indication(self, channel_id: int, body: bytes) -> None:
        self.log.debug(f"AVInputChannel (ch{channel_id}): Received AVMediaAckIndication from phone (len={len(body)})")

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled AVInput Message] AVInputChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
