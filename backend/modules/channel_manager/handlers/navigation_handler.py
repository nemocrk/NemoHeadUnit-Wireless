"""
navigation_handler.py — Android Auto Turn-by-Turn Navigation Channel Handler (ID_NAV).

Parses navigation status, turn events, distances, and road names from Android Auto navigation apps,
emitting `navigation.turn_event` onto the ZeroMQ bus for HUD/UI widgets.
"""

from typing import TYPE_CHECKING
from protos.oaa.navigation.NavigationTurnEventMessage_pb2 import NavigationTurnEvent
from protos.oaa.navigation.NavigationDistanceMessage_pb2 import NavigationDistance
from protos.oaa.navigation.NavigationStateMessage_pb2 import NavigationState
from protos.oaa.navigation.InstrumentClusterMessages_pb2 import (
    InstrumentClusterStart,
    InstrumentClusterStop,
)
from shared.constants import ChannelType

if TYPE_CHECKING:
    from ..main import ChannelManagerModule


class NavigationChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.active_road: str = ""
        self.last_maneuver: str = ""
        self.distance_meters: float = -1.0

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        """Process incoming navigation payload frames."""
        try:
            # Handle Channel Open Request
            if message_id == 7:  # MSG.CHANNEL_OPEN_REQUEST
                from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
                from protos.oaa.common.StatusEnum_pb2 import Status
                resp = ChannelOpenResponse()
                resp.status = Status.OK
                self.log.info(f"🧭 [Navigation Channel] Received ChannelOpenRequest on ch={channel_id} — responding STATUS_OK")
                await self.manager.send_wire_frame(channel_id, 8, resp.SerializeToString(), encrypted=True)
                return

            # Check for NavigationTurnEvent
            turn_event = NavigationTurnEvent()
            try:
                turn_event.ParseFromString(body)
                if turn_event.road:
                    self.active_road = turn_event.road
                event_data = {
                    "road": turn_event.road or self.active_road,
                    "turn_side": getattr(turn_event, "turn_side", 0),
                    "event_name": getattr(turn_event, "event_name", ""),
                }
                self.log.info(f"🧭 [Navigation Channel] Turn event received: road='{event_data['road']}'")
                self.manager.publish("navigation.turn_event", event_data)
                return
            except Exception:
                pass

            # Check for NavigationDistance
            dist_event = NavigationDistance()
            try:
                dist_event.ParseFromString(body)
                self.distance_meters = dist_event.distance_meters
                dist_data = {
                    "distance_meters": self.distance_meters,
                    "time_to_turn_seconds": getattr(dist_event, "time_to_turn_seconds", 0),
                }
                self.log.debug(f"🧭 [Navigation Channel] Distance update: {dist_data}")
                self.manager.publish("navigation.distance_event", dist_data)
                return
            except Exception:
                pass

            # Fallback raw publish
            self.log.debug(f"🧭 [Navigation Channel] Received frame msg_id={message_id}, len={len(body)}")
            self.manager.publish("navigation.raw_event", {"msg_id": message_id, "size": len(body)})

        except Exception as exc:
            self.log.warning(f"🧭 [Navigation Channel] Failed to process message {message_id}: {exc}")
