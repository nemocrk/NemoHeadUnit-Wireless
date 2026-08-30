"""
media_playback_handler.py — Android Auto Media Playback Channel Handler (ID_MPB).

Handles playback status events and metadata (Track Title, Artist, Album, Album Art),
publishing `media.metadata` and `media.playback_status` to the ZeroMQ bus.
"""

from typing import TYPE_CHECKING
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.common.StatusEnum_pb2 import Status
from protos.oaa.media.MediaPlaybackStatusMessage_pb2 import MediaPlaybackStatus
from protos.oaa.media.MediaPlaybackMetadataMessage_pb2 import MediaPlaybackMetadata
from protos.oaa.media.CarLocalMediaPlaybackStatusMessage_pb2 import CarLocalMediaPlaybackStatus
from shared.constants import ChannelType

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum


class MediaPlaybackChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.track_title: str = ""
        self.artist: str = ""
        self.album: str = ""
        self.playback_state: str = "STOPPED"
        self._fragment_buffer = bytearray()

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        """Process incoming media playback and metadata frames."""
        try:
            # Handle Channel Open Request
            if message_id == MSG.CHANNEL_OPEN_REQUEST:
                resp = ChannelOpenResponse()
                resp.status = Status.OK
                self.log.info(f"🎵 [Media Playback Channel] Received ChannelOpenRequest on ch={channel_id} — responding STATUS_OK")
                await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)
                return

            # 1. Try parsing MediaPlaybackStatus
            status = MediaPlaybackStatus()
            try:
                status.ParseFromString(body)
                status_dict = {
                    "playback_state": getattr(status, "playback_state", 0),
                    "playback_position_ms": getattr(status, "playback_position_ms", 0),
                    "media_source": getattr(status, "media_source", ""),
                }
                self.log.info(f"🎵 [Media Playback Channel] Status update: source='{status_dict['media_source']}' state={status_dict['playback_state']}")
                self.manager.publish("media.playback_status", status_dict)
                return
            except Exception:
                pass

            # 2. Try parsing MediaPlaybackMetadata
            meta = MediaPlaybackMetadata()
            try:
                meta.ParseFromString(body)
                self.track_title = getattr(meta, "song_title", "") or getattr(meta, "title", "")
                self.artist = getattr(meta, "artist", "")
                self.album = getattr(meta, "album", "")
                meta_dict = {
                    "title": self.track_title,
                    "artist": self.artist,
                    "album": self.album,
                }
                self.log.info(f"🎵 [Media Playback Channel] Metadata received: title='{self.track_title}', artist='{self.artist}'")
                self.manager.publish("media.metadata", meta_dict)
                return
            except Exception:
                pass

            self.log.debug(f"🎵 [Media Playback Channel] Received payload msg_id={message_id}, len={len(body)}")

        except Exception as exc:
            self.log.warning(f"🎵 [Media Playback Channel] Failed to process message {message_id}: {exc}")
