"""
notification_handler.py — Android Auto Notification Channel Handler (GAL type 14 / Field 13).

Processes incoming notifications, alerts, and messaging events from phone
and allows sending action responses (e.g. Dismiss, Play, Reply) back to phone.
"""

from __future__ import annotations
import base64
import time
from typing import TYPE_CHECKING, Optional, Any

from shared.logger import get_logger
from shared.constants import ChannelType
from shared.proto_utils import encode_proto

from protos.oaa.common.StatusEnum_pb2 import Status
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG_CHANNEL_OPEN_REQUEST = 0x0007
MSG_CHANNEL_OPEN_RESPONSE = 0x0008
MSG_NOTIFICATION_EVENT = 0x8001
MSG_NOTIFICATION_ACTION = 0x8002


class NotificationHandler:
    """Handles Android Auto Notification & Alert Channel (Channel 12)."""

    def __init__(self, manager: ChannelManagerModule) -> None:
        self.manager = manager
        self.log = get_logger("channel_manager.notification")
        self.active_channel_id: Optional[int] = None
        self.recent_notifications: list[dict[str, Any]] = []

    async def handle_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.active_channel_id = channel_id

        if message_id == MSG_CHANNEL_OPEN_REQUEST:
            await self._handle_channel_open_request(channel_id, body)
        elif message_id == MSG_NOTIFICATION_EVENT:
            await self._handle_notification_event(channel_id, body)
        else:
            self.log.debug(f"NotificationHandler (ch{channel_id}): Received msg 0x{message_id:04x} ({len(body)}B)")

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"🔔 NotificationChannel (ch{channel_id}): Received Channel Open Request — responding OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_notification_event(self, channel_id: int, body: bytes) -> None:
        try:
            # Parse notification payload
            notif_id = f"notif_{int(time.time() * 1000)}"
            app_name = "Android Auto"
            title = "Notification"
            text = ""

            # Extract text/strings if available in binary or protobuf payload
            try:
                # If body contains readable UTF-8 strings or protobuf fields
                text_content = body.decode("utf-8", errors="ignore")
                parts = [p for p in text_content.split("\x00") if len(p.strip()) > 1]
                if len(parts) >= 2:
                    app_name = parts[0]
                    title = parts[1]
                    text = " ".join(parts[2:]) if len(parts) > 2 else ""
                elif len(parts) == 1:
                    title = parts[0]
            except Exception:
                pass

            notif_entry = {
                "id": notif_id,
                "app_name": app_name,
                "title": title or "Alert",
                "text": text,
                "timestamp_ms": int(time.time() * 1000),
                "channel_id": channel_id,
            }

            self.recent_notifications.insert(0, notif_entry)
            self.recent_notifications = self.recent_notifications[:20]  # Keep last 20

            self.log.info(f"🔔 New Notification: [{app_name}] {title} — {text}")

            # Publish ZMQ topic
            self.manager.publish("notification.post", notif_entry)

            # Broadcast over WebSocket
            await self.manager.broadcast_ws_json({
                "type": "notification_post",
                "data": notif_entry,
            })

        except Exception as exc:
            self.log.warning(f"NotificationHandler (ch{channel_id}): Failed to parse notification: {exc}")

    async def send_action(self, notif_id: str, action_id: str = "dismiss") -> bool:
        """Send notification dismiss or action trigger back to phone."""
        if self.active_channel_id is None:
            return False

        self.log.info(f"🔔 Sending notification action '{action_id}' for {notif_id}")
        payload = action_id.encode("utf-8")
        await self.manager.send_wire_frame(
            self.active_channel_id,
            MSG_NOTIFICATION_ACTION,
            payload,
            encrypted=True,
        )

        # Remove from local list if dismissed
        if action_id == "dismiss":
            self.recent_notifications = [n for n in self.recent_notifications if n["id"] != notif_id]
            self.manager.publish("notification.dismiss", {"id": notif_id})
            await self.manager.broadcast_ws_json({
                "type": "notification_dismiss",
                "data": {"id": notif_id},
            })

        return True
