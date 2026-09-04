"""
navigation_handler.py — Android Auto Turn-by-Turn Navigation Channel Handler (ID_NAV).

Parses navigation status, turn events, distances, and road names from Android Auto navigation apps,
emitting `navigation.turn_event` onto the ZeroMQ bus for HUD/UI widgets.
"""

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent.parent.parent
proto_dir = repo_root / "protos"
if str(proto_dir) not in sys.path:
    sys.path.insert(0, str(proto_dir))

from typing import TYPE_CHECKING
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.common.StatusEnum_pb2 import Status
from protos.oaa.navigation.NavigationTurnEventMessage_pb2 import NavigationTurnEvent, NavigationNextTurnDistanceEvent
from protos.oaa.navigation.NavigationDistanceMessage_pb2 import NavigationDistance
from protos.oaa.navigation.NavigationStateMessage_pb2 import NavigationState
from protos.oaa.navigation.NavigationNotificationMessage_pb2 import NavigationNotification
from protos.oaa.navigation.ManeuverTypeEnum_pb2 import ManeuverType
from protos.oaa.navigation.InstrumentClusterMessages_pb2 import (
    InstrumentClusterStart,
    InstrumentClusterStop,
)
from shared.constants import ChannelType

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum

MANEUVER_NAME_MAP = {
    0: "unknown",
    1: "depart",
    2: "name-change",
    3: "keep-left",
    4: "keep-right",
    5: "turn-slight-left",
    6: "turn-slight-right",
    7: "turn-normal-left",
    8: "turn-normal-right",
    9: "turn-sharp-left",
    10: "turn-sharp-right",
    11: "u-turn-left",
    12: "u-turn-right",
    13: "on-ramp-slight-left",
    14: "on-ramp-slight-right",
    15: "on-ramp-normal-left",
    16: "on-ramp-normal-right",
    17: "on-ramp-sharp-left",
    18: "on-ramp-sharp-right",
    19: "on-ramp-u-turn-left",
    20: "on-ramp-u-turn-right",
    21: "off-ramp-slight-left",
    22: "off-ramp-slight-right",
    23: "off-ramp-normal-left",
    24: "off-ramp-normal-right",
    25: "fork-left",
    26: "fork-right",
    27: "merge-left",
    28: "merge-right",
    29: "merge",
    30: "roundabout-enter",
    31: "roundabout-exit",
    32: "roundabout-enter-and-exit-cw",
    33: "roundabout-enter-and-exit-cw-with-angle",
    34: "roundabout-enter-and-exit-ccw",
    35: "roundabout-enter-and-exit-ccw-with-angle",
    36: "straight",
    37: "ferry-boat",
    38: "ferry-train",
    39: "destination",
    40: "destination-straight",
    41: "destination-left",
    42: "destination-right",
}


class NavigationChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.active_road: str = ""
        self.last_maneuver_type: int = 0
        self.last_turn_side: int = 0
        self.distance_meters: float = -1.0

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        """Process incoming navigation payload frames with strict message ID routing."""
        try:
            # 1. Channel Open Request (0x0001)
            if message_id == MSG.CHANNEL_OPEN_REQUEST:
                resp = ChannelOpenResponse()
                resp.status = Status.OK
                self.log.info(f"🧭 [Navigation Channel] ChannelOpenRequest on ch={channel_id} — responding STATUS_OK")
                await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)
                return

            # 2. Instrument Cluster Start (0x8001 / 32769)
            if message_id in (0x8001, 32769):
                self.log.info(f"🧭 [Navigation Channel] Instrument Cluster Start on ch={channel_id}")
                self.active_road = ""
                self.distance_meters = -1.0
                return

            # 3. Instrument Cluster Stop (0x8002 / 32770)
            if message_id in (0x8002, 32770):
                self.log.info(f"🧭 [Navigation Channel] Instrument Cluster Stop on ch={channel_id}")
                self.clear_navigation()
                return

            # 4. Navigation Status / Cluster Status (0x8003 / 32771)
            if message_id in (0x8003, 32771):
                try:
                    nav_state = NavigationState()
                    nav_state.ParseFromString(body)
                    state_val = getattr(nav_state, "state", 0)
                    self.log.info(f"🧭 [Navigation Channel] Navigation state status={state_val}")
                    if state_val in (0, 2):  # UNAVAILABLE, INACTIVE
                        self.clear_navigation()
                except Exception as exc:
                    self.log.debug(f"🧭 [Navigation Channel] Error parsing status 0x8003: {exc}")
                return

            # 5. Legacy Turn Details (0x8004 / 32772)
            if message_id in (0x8004, 32772):
                turn_event = NavigationTurnEvent()
                turn_event.ParseFromString(body)
                road = getattr(turn_event, "road_name", "") or getattr(turn_event, "road", "") or getattr(turn_event, "name", "")
                if road:
                    self.active_road = road
                dist_val = getattr(turn_event, "distance_meters", -1)
                if dist_val >= 0:
                    self.distance_meters = float(dist_val)

                turn_icon = getattr(turn_event, "turn_icon", b"")
                turn_icon_b64 = ""
                if turn_icon:
                    import base64
                    turn_icon_b64 = f"data:image/png;base64,{base64.b64encode(turn_icon).decode('utf-8')}"

                self.last_maneuver_type = getattr(turn_event, "maneuver_type", 0)
                self.last_turn_side = getattr(turn_event, "turn_direction", getattr(turn_event, "turn_side", 0))
                maneuver_name = MANEUVER_NAME_MAP.get(self.last_maneuver_type, "unknown")

                event_data = {
                    "road": self.active_road,
                    "maneuver_type": self.last_maneuver_type,
                    "maneuver_name": maneuver_name,
                    "turn_side": self.last_turn_side,
                    "turn_icon": turn_icon_b64,
                    "event_name": getattr(turn_event, "event_name", ""),
                    "distance_meters": self.distance_meters,
                }
                self.log.info(f"🧭 [Navigation Channel] Legacy Turn step (0x8004): road='{self.active_road}', dist={self.distance_meters}m, maneuver={maneuver_name}")
                self.manager.publish("navigation.turn_event", event_data)
                self.manager._notify_status_changed()
                return

            # 6. Legacy Distance & Time (0x8005 / 32773)
            if message_id in (0x8005, 32773):
                dist_event = NavigationDistance()
                dist_event.ParseFromString(body)
                if hasattr(dist_event, "distance") and hasattr(dist_event.distance, "distance_value"):
                    self.distance_meters = float(dist_event.distance.distance_value)
                elif hasattr(dist_event, "distance_meters"):
                    self.distance_meters = float(dist_event.distance_meters)

                eta_sec = getattr(dist_event, "eta_seconds", getattr(dist_event, "time_to_turn_seconds", 0))
                eta_text = getattr(dist_event, "eta_text", "")
                maneuver_name = MANEUVER_NAME_MAP.get(self.last_maneuver_type, "unknown")

                dist_data = {
                    "distance_meters": self.distance_meters,
                    "eta_seconds": eta_sec,
                    "eta_text": eta_text,
                    "road": self.active_road,
                    "maneuver_type": self.last_maneuver_type,
                    "maneuver_name": maneuver_name,
                    "turn_side": self.last_turn_side,
                }
                self.log.debug(f"🧭 [Navigation Channel] Distance update (0x8005): dist={self.distance_meters}m")
                self.manager.publish("navigation.distance_event", dist_data)
                return

            # 7. Modern Navigation State / Steps (0x8006 / 32774)
            if message_id in (0x8006, 32774):
                notif = NavigationNotification()
                notif.ParseFromString(body)
                if notif.steps:
                    step = notif.steps[0]
                    if step.HasField("maneuver"):
                        self.last_maneuver_type = step.maneuver.type
                        # Derive turn side from maneuver enum:
                        if self.last_maneuver_type in (3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 41):
                            self.last_turn_side = 1  # LEFT
                        elif self.last_maneuver_type in (4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 42):
                            self.last_turn_side = 2  # RIGHT
                        else:
                            self.last_turn_side = 0

                    if step.road_info and step.road_info.road_names:
                        self.active_road = step.road_info.road_names[0]

                    event_name = step.instruction.text if step.HasField("instruction") else ""
                    maneuver_name = MANEUVER_NAME_MAP.get(self.last_maneuver_type, "unknown")

                    event_data = {
                        "road": self.active_road,
                        "maneuver_type": self.last_maneuver_type,
                        "maneuver_name": maneuver_name,
                        "turn_side": self.last_turn_side,
                        "turn_icon": "",
                        "event_name": event_name,
                        "distance_meters": self.distance_meters,
                    }
                    self.log.info(f"🧭 [Navigation Channel] Modern Turn step (0x8006): road='{self.active_road}', maneuver={maneuver_name} ({self.last_maneuver_type})")
                    self.manager.publish("navigation.turn_event", event_data)
                    self.manager._notify_status_changed()
                return

            # 8. Modern Navigation Current Position / Turn Distance (0x8007 / 32775)
            if message_id in (0x8007, 32775):
                dist_msg = NavigationNextTurnDistanceEvent()
                dist_msg.ParseFromString(body)
                if dist_msg.HasField("remaining_distance") and dist_msg.remaining_distance.remaining_meters >= 0:
                    self.distance_meters = float(dist_msg.remaining_distance.remaining_meters)
                elif dist_msg.HasField("step_distance"):
                    if dist_msg.step_distance.distance_meters > 0:
                        self.distance_meters = float(dist_msg.step_distance.distance_meters)
                    elif dist_msg.step_distance.HasField("distance_value"):
                        self.distance_meters = float(dist_msg.step_distance.distance_value.distance_value)

                maneuver_name = MANEUVER_NAME_MAP.get(self.last_maneuver_type, "straight")
                dist_data = {
                    "distance_meters": self.distance_meters,
                    "road": self.active_road,
                    "maneuver_type": self.last_maneuver_type,
                    "maneuver_name": maneuver_name,
                    "turn_side": self.last_turn_side,
                }
                self.log.debug(f"🧭 [Navigation Channel] Distance update (0x8007): dist={self.distance_meters}m")
                self.manager.publish("navigation.distance_event", dist_data)
                self.manager.publish("navigation.turn_event", {
                    "road": self.active_road,
                    "maneuver_type": self.last_maneuver_type,
                    "maneuver_name": maneuver_name,
                    "turn_side": self.last_turn_side,
                    "distance_meters": self.distance_meters,
                    "turn_icon": "",
                    "event_name": "",
                })
                return

            # Unhandled message ID fallback
            self.log.debug(f"🧭 [Navigation Channel] Ignored unknown msg_id=0x{message_id:04x}, len={len(body)}")

        except Exception as exc:
            self.log.warning(f"🧭 [Navigation Channel] Failed to process message 0x{message_id:04x}: {exc}")

    def clear_navigation(self) -> None:
        """Reset and clean active navigation status on route finish or native focus."""
        self.active_road = ""
        self.distance_meters = -1.0
        self.last_maneuver_type = 0
        self.last_turn_side = 0
        self._last_logged_road = None
        self._last_logged_maneuver = None
        self._last_logged_turn_side = None
        self.log.info("🧭 [Navigation Channel] Navigation cleared (route finished / native focus)")
        self.manager.publish("navigation.turn_event", {
            "road": "",
            "distance_meters": -1.0,
            "maneuver_type": 0,
            "turn_side": 0,
            "turn_icon": "",
            "event_name": "",
        })
        self.manager._notify_status_changed()
