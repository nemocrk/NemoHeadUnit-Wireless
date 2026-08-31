import base64
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

    async def send_focus_indication(self, focus_mode: int) -> None:
        """Send explicit VideoFocusIndication message to phone."""
        from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
        mode_name = VideoFocusMode.Enum.Name(focus_mode) if hasattr(VideoFocusMode.Enum, "Name") else focus_mode
        self.log.info(f"📹 VideoChannel: Sending explicit VideoFocusIndication({mode_name}) to phone")

        focus_ind = VideoFocusIndication()
        focus_ind.focus_mode = focus_mode
        focus_ind.unrequested = False

        video_ch_id = self.manager.get_channel_id_for_type(ChannelType.VIDEO)
        await self.manager.send_wire_frame(video_ch_id, AV_MSG.VIDEO_FOCUS_INDICATION, focus_ind.SerializeToString(), encrypted=True)

    async def update_video_focus(self) -> None:
        """Send default VideoFocusIndication(PROJECTED) on channel setup completion."""
        if not self.setup_completed:
            return
        from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
        await self.send_focus_indication(VideoFocusMode.Enum.PROJECTED)



    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_setup_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelSetupRequest — responding OK (max_unacked=10)...")
        if body:
            try:
                from protos.oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
                from protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType
                setup_req = AVChannelSetupRequest()
                setup_req.ParseFromString(body)
                self.codec_enum = setup_req.media_codec_type
                self.codec_name = MediaCodecType.Enum.Name(setup_req.media_codec_type) if hasattr(MediaCodecType.Enum, "Name") else str(setup_req.media_codec_type)
                self.log.info(f"📹 VideoChannel (ch{channel_id}): AVChannelSetupRequest codec={self.codec_name} ({self.codec_enum})")
                if channel_id in self.manager.active_channels:
                    av_conf = self.manager.active_channels[channel_id].setdefault("av_channel", {})
                    av_conf["codec"] = self.codec_name
                await self.manager.broadcast_ws_json(self.manager.get_stream_config_dict())
            except Exception as exc:
                self.log.warning(f"VideoChannel (ch{channel_id}): Setup request parse warning: {exc}")

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
        self.manager.publish("video.stream_start", {
            "session_id": self.session_id,
            "codec": getattr(self, "codec_name", "MEDIA_CODEC_VIDEO_H264_BP"),
            "codec_enum": getattr(self, "codec_enum", 3),
        })

    async def _handle_stop_indication(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"VideoChannel (ch{channel_id}): Received AVChannelStopIndication — video stream STOPPED")
        self.manager.publish("video.stream_stop", {
            "session_id": self.session_id,
            "codec": getattr(self, "codec_name", "MEDIA_CODEC_VIDEO_H264_BP"),
        })


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

        if self.frame_count % UNACKED_FRAMES_THRESHOLD == 0:
            self.log.debug(
                f"📹 [Video Stream Flow] Processed video frame {self.frame_count}/{UNACKED_FRAMES_THRESHOLD} "
                f"(ch{video_ch_id}): msgId=0x{message_id:04x}, payload_len={len(codec_payload)}, ts={ts_us} µs "
                f"-> Publishing to video.raw_nal (transport: {self.manager.active_video_transport or 'h264'})"
            )

        # Write raw H.264 NAL bytes directly to SHM (nemo_video_transcode_in) zero-copy
        shm_offset = self.manager.shm.transcode_in.write_frame(4, ts_us, codec_payload)
        self.manager.publish("media.video.raw_nal_shm", {
            "shm_offset": shm_offset,
            "len": len(codec_payload),
            "channel_id": video_ch_id,
            "timestamp_us": ts_us,
        })

        # Batch MediaAck every UNACKED_FRAMES_THRESHOLD frames
        self.frame_count += 1
        if self.frame_count % UNACKED_FRAMES_THRESHOLD == 0:
            ack = AVMediaAckIndication()
            ack.session_id = self.session_id
            ack.ack_count = UNACKED_FRAMES_THRESHOLD
            self.log.debug(
                f"📹 VideoChannel (ch{video_ch_id}): Sending batch AVMediaAckIndication "
                f"(session_id={self.session_id}, ack_count={UNACKED_FRAMES_THRESHOLD}, total_frames={self.frame_count})"
            )
            await self.manager.send_wire_frame(
                video_ch_id, AV_MSG.AV_MEDIA_ACK_INDICATION,
                ack.SerializeToString(), encrypted=True, log_level='debug'
            )






    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Video Message] VideoChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
