from typing import TYPE_CHECKING
from shared.nal_utils import pack_media_frame, STREAM_TYPE_AUDIO
from shared.proto_utils import parse_media_with_timestamp

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

AV_MEDIA_WITH_TIMESTAMP_INDICATION = 0x0001
AV_MEDIA_INDICATION = 0x0002


class AudioChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager

    async def process_shm_frame(self, message_id: int, payload: bytes) -> None:
        ts_us = 0
        audio_payload = b""

        if message_id == AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            ts_us, audio_payload = parse_media_with_timestamp(payload)
        elif message_id == AV_MEDIA_INDICATION:
            audio_payload = payload
        else:
            return

        if not audio_payload:
            return

        binary_frame = pack_media_frame(STREAM_TYPE_AUDIO, ts_us, audio_payload)
        await self.manager.broadcast_ws_media(binary_frame)
