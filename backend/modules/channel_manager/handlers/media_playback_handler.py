"""
media_playback_handler.py — Android Auto Media Playback Channel Handler (ID_MPB).

Handles playback status events and metadata (Track Title, Artist, Album, Album Art),
publishing `media.metadata` and `media.playback_status` to the ZeroMQ bus.
"""

import base64
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

# Wire Message IDs for Media Playback Channel
MSG_PLAYBACK_STATUS = 0x8001
MSG_PLAYBACK_STATUS_EVENT = 0x8002
MSG_PLAYBACK_METADATA = 0x8003
MSG_PLAYBACK_COMMAND = 0x8004


class MediaPlaybackChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.track_title: str = ""
        self.artist: str = ""
        self.album: str = ""
        self.album_art_b64: str = ""
        self.playback_state: int = 0
        self.position_seconds: int = 0
        self.media_source: str = ""

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

            # 1. Media Metadata (0x8003) — Track Title, Artist, Album, Artwork
            if message_id == MSG_PLAYBACK_METADATA or len(body) > 512:
                try:
                    meta = MediaPlaybackMetadata()
                    meta.ParseFromString(body)
                    self.track_title = getattr(meta, "title", "") or getattr(meta, "song_title", "") or getattr(meta, "song", "")
                    self.artist = getattr(meta, "artist", "")
                    self.album = getattr(meta, "album", "")
                    album_art = getattr(meta, "album_art", b"")
                    
                    if album_art:
                        self.album_art_b64 = f"data:image/jpeg;base64,{base64.b64encode(album_art).decode('utf-8')}"
                    else:
                        self.album_art_b64 = ""

                    meta_dict = {
                        "title": self.track_title,
                        "artist": self.artist,
                        "album": self.album,
                        "has_album_art": bool(album_art),
                        "album_art": self.album_art_b64,
                    }
                    self.log.info(
                        f"🎵 [Media Playback Channel] Metadata received: title='{self.track_title}', "
                        f"artist='{self.artist}', album='{self.album}', art={len(album_art)}B"
                    )
                    self.manager.publish("media.metadata", meta_dict)
                    self.manager._notify_status_changed()
                    return
                except Exception as exc:
                    self.log.warning(f"Failed to parse MediaPlaybackMetadata (0x{message_id:04x}): {exc}")

            # 2. Media Playback Status (0x8001) — State, Position, Source App
            if message_id == MSG_PLAYBACK_STATUS or message_id == MSG_PLAYBACK_STATUS_EVENT:
                try:
                    status = MediaPlaybackStatus()
                    status.ParseFromString(body)
                    source_name = getattr(status, "source_app", "") or getattr(status, "media_source", "")
                    if source_name:
                        self.media_source = source_name
                    pos_sec = getattr(status, "position_seconds", 0) or (getattr(status, "playback_position_ms", 0) // 1000)
                    self.playback_state = getattr(status, "playback_state", 0)
                    self.position_seconds = pos_sec

                    status_dict = {
                        "playback_state": self.playback_state,
                        "position_seconds": self.position_seconds,
                        "media_source": self.media_source,
                    }
                    self.log.info(
                        f"🎵 [Media Playback Channel] Status update: source='{self.media_source}' "
                        f"state={self.playback_state}, pos={self.position_seconds}s"
                    )
                    self.manager.publish("media.playback_status", status_dict)
                    self.manager._notify_status_changed()
                    return
                except Exception as exc:
                    self.log.warning(f"Failed to parse MediaPlaybackStatus (0x{message_id:04x}): {exc}")

            # 3. Fallback generic inspection
            self.log.debug(f"🎵 [Media Playback Channel] Received unhandled msg_id=0x{message_id:04x}, len={len(body)}")

        except Exception as exc:
            self.log.warning(f"🎵 [Media Playback Channel] Error processing message 0x{message_id:04x}: {exc}")
