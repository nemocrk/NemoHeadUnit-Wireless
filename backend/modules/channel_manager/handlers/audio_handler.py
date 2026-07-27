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

from protos.oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
from shared.constants import ChannelType

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum
AV_MSG = AVChannelMessage.Enum

UNACKED_FRAMES_THRESHOLD = 50


class AudioChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.frame_count = 0
        self.sessions: Dict[int, int] = {}

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
        self.log.info(f"AudioChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK (max_unacked=10)...")
        if body:
            try:
                from protos.oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
                from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
                setup_req = AVChannelSetupRequest()
                setup_req.ParseFromString(body)
                codec_name = MediaCodecType.Enum.Name(setup_req.media_codec_type) if hasattr(MediaCodecType.Enum, "Name") else setup_req.media_codec_type
                self.log.info(f"🔊 AudioChannel (ch{channel_id}): AVChannelSetupRequest codec={codec_name}")
                if channel_id in self.manager.active_channels:
                    av_conf = self.manager.active_channels[channel_id].setdefault("av_channel", {})
                    av_conf["codec"] = codec_name
                await self.manager.broadcast_ws_json(self.manager.get_stream_config_dict())
            except Exception as exc:
                self.log.warning(f"AudioChannel (ch{channel_id}): Setup request parse warning: {exc}")

        resp = AVChannelSetupResponse()
        resp.media_status = AVChannelSetupStatus.Enum.OK
        resp.max_unacked = UNACKED_FRAMES_THRESHOLD
        resp.configs.append(0)
        await self.manager.send_wire_frame(channel_id, AV_MSG.SETUP_RESPONSE, resp.SerializeToString(), encrypted=True)


    async def _handle_start_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AVChannelStartIndication — audio stream ACTIVE")
        if body:
            try:
                from protos.oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
                start_ind = AVChannelStartIndication()
                start_ind.ParseFromString(body)
                self.sessions[channel_id] = start_ind.session
                self.log.info(f"🔊 AudioChannel (ch{channel_id}): Extracted start session_id={start_ind.session}")
            except Exception as exc:
                self.log.warning(f"AudioChannel (ch{channel_id}): Failed to parse start indication session_id: {exc}")

    async def _handle_stop_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AVChannelStopIndication — audio stream STOPPED")

    async def _handle_focus_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"AudioChannel (ch{channel_id}): Received AudioFocusRequest — responding AudioFocusResponse(GAIN)...")
        focus_resp = AudioFocusResponse()
        focus_resp.audio_focus_state = AudioFocusState.GAIN
        focus_resp.granted = False
        await self.manager.send_wire_frame(channel_id, MSG.AUDIO_FOCUS_RESPONSE, focus_resp.SerializeToString(), encrypted=True)

    async def process_shm_frame(self, channel_id: int, message_id: int, payload: bytes) -> None:
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

        binary_frame = pack_media_frame(channel_id, ts_us, audio_payload)
        await self.manager.broadcast_ws_media(binary_frame)

        if self.frame_count % UNACKED_FRAMES_THRESHOLD == 0:
            self.log.debug(f"🔊 [Audio Stream Flow] Processed audio frame {self.frame_count}/{UNACKED_FRAMES_THRESHOLD} (ch{channel_id}): msgId=0x{message_id:04x}, payload_len={len(audio_payload)}, ts={ts_us} µs -> Broadcasting to {len(self.manager.ws_clients)} WS client(s)")

        # Batch MediaAck every 10 frames using AVMediaAckIndication(session_id, ack_count=10)
        session_id = self.sessions.get(channel_id, 0)
        self.frame_count += 1
        if self.frame_count % UNACKED_FRAMES_THRESHOLD == 0:
            ack = AVMediaAckIndication()
            ack.session_id = session_id
            ack.ack_count = UNACKED_FRAMES_THRESHOLD
            self.log.debug(f"🔊 AudioChannel (ch{channel_id}): Sending batch AVMediaAckIndication (session_id={session_id}, ack_count={UNACKED_FRAMES_THRESHOLD}, total_frames={self.frame_count})")
            await self.manager.send_wire_frame(channel_id, AV_MSG.AV_MEDIA_ACK_INDICATION, ack.SerializeToString(), encrypted=True, log_level='debug')





    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Audio Message] AudioChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")

