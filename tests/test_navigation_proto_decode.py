"""Tests for Android Auto Navigation message ID routing and protobuf decoding."""

import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock, AsyncMock

repo_root = Path(__file__).resolve().parent.parent
proto_dir = repo_root / "protos"
if str(proto_dir) not in sys.path:
    sys.path.insert(0, str(proto_dir))

from protos.oaa.navigation.NavigationNotificationMessage_pb2 import NavigationNotification
from protos.oaa.navigation.NavigationTurnEventMessage_pb2 import NavigationTurnEvent, NavigationNextTurnDistanceEvent
from protos.oaa.navigation.ManeuverTypeEnum_pb2 import ManeuverType
from backend.modules.channel_manager.handlers.navigation_handler import NavigationChannelHandler


import asyncio

def test_navigation_notification_msg_0x8006():
    manager = MagicMock()
    manager.publish = MagicMock()
    manager._notify_status_changed = MagicMock()
    handler = NavigationChannelHandler(manager)

    # Build NavigationNotification proto (message ID 0x8006)
    notif = NavigationNotification()
    step = notif.steps.add()
    step.maneuver.type = ManeuverType.Enum.TURN_NORMAL_LEFT  # 7
    step.road_info.road_names.append("Oak Street")

    body = notif.SerializeToString()
    asyncio.run(handler.handle_frame(channel_id=5, message_id=0x8006, body=body))

    # Verify navigation.turn_event published with correct maneuver and road
    assert manager.publish.called
    events = [call.args for call in manager.publish.call_args_list if call.args[0] == "navigation.turn_event"]
    assert len(events) >= 1
    event_payload = events[0][1]
    assert event_payload["road"] == "Oak Street"
    assert event_payload["maneuver_type"] == ManeuverType.Enum.TURN_NORMAL_LEFT
    assert event_payload["maneuver_name"] == "turn-normal-left"


def test_navigation_distance_msg_0x8007():
    manager = MagicMock()
    manager.publish = MagicMock()
    manager._notify_status_changed = MagicMock()
    handler = NavigationChannelHandler(manager)

    dist_msg = NavigationNextTurnDistanceEvent()
    dist_msg.remaining_distance.remaining_meters = 450
    body = dist_msg.SerializeToString()

    asyncio.run(handler.handle_frame(channel_id=5, message_id=0x8007, body=body))

    events = [call.args for call in manager.publish.call_args_list if call.args[0] in ("navigation.distance_event", "navigation.turn_event")]
    assert len(events) >= 1
    event_payload = events[0][1]
    assert event_payload["distance_meters"] == 450.0
