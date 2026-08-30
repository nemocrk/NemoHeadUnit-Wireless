"""
navigation_handler.py — Android Auto Turn-by-Turn Navigation Channel Handler (ID_NAV).

Parses navigation status, turn events, distances, and road names from Android Auto navigation apps,
emitting `navigation.turn_event` onto the ZeroMQ bus for HUD/UI widgets.
"""

from typing import TYPE_CHECKING
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.common.StatusEnum_pb2 import Status
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

MSG = ControlMessage.Enum


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
            if message_id == MSG.CHANNEL_OPEN_REQUEST:
                resp = ChannelOpenResponse()
                resp.status = Status.OK
                self.log.info(f"🧭 [Navigation Channel] Received ChannelOpenRequest on ch={channel_id} — responding STATUS_OK")
                await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)
                return

            # Handle Instrument Cluster Start (0x8001 / 32769)
            if message_id in (0x8001, 32769):
                self.log.info(f"🧭 [Navigation Channel] Instrument Cluster Start on ch={channel_id}")
                self.active_road = ""
                self.distance_meters = -1.0
                return

            # Handle Instrument Cluster Stop (0x8002 / 32770)
            if message_id in (0x8002, 32770):
                self.log.info(f"🧭 [Navigation Channel] Instrument Cluster Stop on ch={channel_id}")
                self.active_road = ""
                self.distance_meters = -1.0
                return

            # Check for NavigationTurnEvent / NextTurnDetail
            turn_event = NavigationTurnEvent()
            try:
                turn_event.ParseFromString(body)
                road = getattr(turn_event, "road_name", "") or getattr(turn_event, "road", "") or getattr(turn_event, "name", "")
                if road:
                    self.active_road = road
                dist_val = getattr(turn_event, "distance_meters", -1)
                if dist_val >= 0:
                    self.distance_meters = float(dist_val)

                event_data = {
                    "road": self.active_road,
                    "turn_side": getattr(turn_event, "turn_direction", getattr(turn_event, "turn_side", 0)),
                    "event_name": getattr(turn_event, "event_name", ""),
                    "distance_meters": self.distance_meters,
                }
                self.log.info(f"🧭 [Navigation Channel] Turn event received: road='{event_data['road']}', dist={event_data['distance_meters']}m")
                self.manager.publish("navigation.turn_event", event_data)
                self.manager._notify_status_changed()
                return
            except Exception:
                pass

            # Check for NavigationDistance
            dist_event = NavigationDistance()
            try:
                dist_event.ParseFromString(body)
                if hasattr(dist_event, "distance") and hasattr(dist_event.distance, "distance_value"):
                    self.distance_meters = float(dist_event.distance.distance_value)
                elif hasattr(dist_event, "distance_meters"):
                    self.distance_meters = float(dist_event.distance_meters)

                dist_data = {
                    "distance_meters": self.distance_meters,
                    "time_to_turn_seconds": getattr(dist_event, "time_to_turn_seconds", getattr(dist_event, "eta_seconds", 0)),
                }
                self.log.debug(f"🧭 [Navigation Channel] Distance update: {dist_data}")
                self.manager.publish("navigation.distance_event", dist_data)
                self.manager._notify_status_changed()
                return
            except Exception:
                pass

            # Fallback raw publish
            self.log.debug(f"🧭 [Navigation Channel] Received frame msg_id=0x{message_id:04x}, len={len(body)}")
            self.manager.publish("navigation.raw_event", {"msg_id": message_id, "size": len(body)})

        except Exception as exc:
            self.log.warning(f"🧭 [Navigation Channel] Failed to process message 0x{message_id:04x}: {exc}")
