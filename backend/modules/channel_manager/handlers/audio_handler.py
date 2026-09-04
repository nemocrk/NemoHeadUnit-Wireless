import base64
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

UNACKED_FRAMES_THRESHOLD = 10


class AudioChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.frame_counts: Dict[int, int] = {}
        self.unacked_counts: Dict[int, int] = {}
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
        self.log.info(f"🔊 AudioChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK (max_unacked={UNACKED_FRAMES_THRESHOLD})...")
        if body:
            try:
                from protos.oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
                from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
                setup_req = AVChannelSetupRequest()
                setup_req.ParseFromString(body)
                codec_name = MediaCodecType.Enum.Name(setup_req.media_codec_type) if hasattr(MediaCodecType.Enum, "Name") else str(setup_req.media_codec_type)

                if channel_id in self.manager.active_channels:
                    av_conf = self.manager.active_channels[channel_id].setdefault("av_channel", {})
                    av_conf["codec"] = codec_name
                av_desc = self.manager.active_channels.get(channel_id, {}).get("av_channel", {})
                audio_configs = av_desc.get("audio_configs", [])
                sample_rate = 48000
                channel_count = 2
                bit_depth = 16
                if audio_configs:
                    cfg0 = audio_configs[0]
                    sample_rate = cfg0.get("sample_rate", 48000)
                    channel_count = cfg0.get("channel_count", 2)
                    bit_depth = cfg0.get("bit_depth", 16)
                audio_type = av_desc.get("audio_type", "MEDIA")

                self.log.info(
                    f"🔊 [Audio Setup Ch{channel_id}] Stream Config: "
                    f"type={audio_type} | codec={codec_name} ({setup_req.media_codec_type}) | "
                    f"{sample_rate}Hz {channel_count}ch {bit_depth}-bit Int16"
                )

                self.manager.publish("media.audio.channel_configured", {
                    "channel_id": channel_id,
                    "codec": codec_name,
                    "codec_enum": setup_req.media_codec_type,
                    "sample_rate": sample_rate,
                    "channel_count": channel_count,
                    "bit_depth": bit_depth,
                    "audio_type": audio_type,
                })
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
        # Flush any pending unacked frames so phone doesn't freeze on restart
        unacked = self.unacked_counts.get(channel_id, 0)
        if unacked > 0:
            session_id = self.sessions.get(channel_id, 0)
            ack = AVMediaAckIndication()
            ack.session_id = session_id
            ack.ack_count = unacked
            self.unacked_counts[channel_id] = 0
            self.log.debug(f"🔊 AudioChannel (ch{channel_id}): Flushed pending AVMediaAckIndication (ack_count={unacked})")
            await self.manager.send_wire_frame(channel_id, AV_MSG.AV_MEDIA_ACK_INDICATION, ack.SerializeToString(), encrypted=True, log_level='debug')

    async def _handle_focus_request(self, channel_id: int, body: bytes) -> None:
        focus_type = AudioFocusType.Enum.GAIN
        try:
            req = AudioFocusRequest()
            req.ParseFromString(body)
            focus_type = req.audio_focus_type
        except Exception as exc:
            self.log.warning(f"AudioChannel (ch{channel_id}): AudioFocusRequest parse warning: {exc}")

        focus_resp = AudioFocusResponse()
        if focus_type == AudioFocusType.Enum.GAIN:
            focus_resp.audio_focus_state = AudioFocusState.GAIN
        elif focus_type == AudioFocusType.Enum.GAIN_TRANSIENT:
            focus_resp.audio_focus_state = AudioFocusState.GAIN_TRANSIENT
        elif focus_type == AudioFocusType.Enum.GAIN_TRANSIENT_MAY_DUCK:
            focus_resp.audio_focus_state = AudioFocusState.GAIN_TRANSIENT_GUIDANCE_ONLY
        elif focus_type == AudioFocusType.Enum.RELEASE:
            focus_resp.audio_focus_state = AudioFocusState.LOSS
        else:
            focus_resp.audio_focus_state = AudioFocusState.INVALID
        focus_resp.granted = True
        self.log.info(f"AudioChannel (ch{channel_id}): Responding AudioFocusResponse(state={focus_resp.audio_focus_state}, granted=True)")
        await self.manager.send_wire_frame(channel_id, MSG.AUDIO_FOCUS_RESPONSE, focus_resp.SerializeToString(), encrypted=True)
        is_paused = (focus_resp.audio_focus_state == AudioFocusState.LOSS)
        self.manager.publish("media.audio.focus", {
            "channel_id": channel_id,
            "focus_type": focus_type,
            "focus_state": focus_resp.audio_focus_state,
            "is_paused": is_paused,
        })

    async def process_shm_frame(self, channel_id: int, message_id: int, offset: int, ts_us: int, payload_len: int) -> None:
        # Re-transmit pointer directly to GUI zero-copy
        self.manager.publish("media.audio.frame_shm", {
            "shm_offset": offset,
            "len": payload_len,
            "timestamp_us": ts_us,
            "channel_id": channel_id,
        })

        # Broadcast binary frame to web browser clients only if clients connected
        if self.manager.ws_clients:
            shm_buf = self.manager.shm.get_downstream_channel(channel_id)
            _, _, audio_payload = shm_buf.read_frame(offset)
            if audio_payload:
                binary_frame = pack_media_frame(channel_id, ts_us, audio_payload)
                await self.manager.broadcast_ws_media(binary_frame)

        # Per-channel frame counting and batch MediaAck
        ch_frames = self.frame_counts.get(channel_id, 0) + 1
        self.frame_counts[channel_id] = ch_frames

        unacked = self.unacked_counts.get(channel_id, 0) + 1
        self.unacked_counts[channel_id] = unacked

        if unacked >= UNACKED_FRAMES_THRESHOLD:
            session_id = self.sessions.get(channel_id, 0)
            ack = AVMediaAckIndication()
            ack.session_id = session_id
            ack.ack_count = unacked
            self.unacked_counts[channel_id] = 0
            self.log.debug(f"🔊 AudioChannel (ch{channel_id}): Sending batch AVMediaAckIndication (session_id={session_id}, ack_count={unacked}, total_frames={ch_frames})")
            await self.manager.send_wire_frame(channel_id, AV_MSG.AV_MEDIA_ACK_INDICATION, ack.SerializeToString(), encrypted=True, log_level='debug')





    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Audio Message] AudioChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")

