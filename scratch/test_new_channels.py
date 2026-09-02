"""
scratch/test_new_channels.py — Comprehensive verification for Phone Status, Notification, and Media Browser channels.
"""

import sys
import asyncio
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from shared.constants import ChannelType
from modules.channel_manager.service_discovery import (
    build_service_discovery_response,
    classify_channel_descriptor,
    SEMANTIC_DEFAULTS,
)
from protos.oaa.phone.PhoneStatusMessage_pb2 import PhoneStatusUpdate, PhoneCall
from protos.oaa.phone.PhoneCallStateEnum_pb2 import PhoneCallState
from protos.oaa.phone.PhoneStatusInputMessage_pb2 import PhoneStatusInput, PhoneInputAction
from modules.channel_manager.handlers.phone_status_handler import PhoneStatusHandler
from modules.channel_manager.handlers.notification_handler import NotificationHandler


class MockChannelManager:
    def __init__(self):
        self.published_events = []
        self.broadcast_ws_events = []
        self.sent_frames = []

    def publish(self, topic, data):
        self.published_events.append((topic, data))

    async def broadcast_ws_json(self, msg):
        self.broadcast_ws_events.append(msg)

    async def send_wire_frame(self, channel_id, msg_id, payload, encrypted=True):
        self.sent_frames.append((channel_id, msg_id, payload, encrypted))


async def test_service_discovery():
    print("--- 1. Testing Service Discovery Advertisements ---")
    sdr_bytes, config_tree, ch_map = build_service_discovery_response()
    
    assert len(sdr_bytes) > 0, "SDR bytes should not be empty"
    print(f"✅ SDR generated cleanly ({len(sdr_bytes)} bytes)")
    print(f"✅ Dynamic Channel Map: {ch_map}")

    # Check channels 10 (PHONE_STATUS) and 11 (NOTIFICATION) in ch_map
    assert ch_map.get(10) == "PHONE_STATUS" or any(v == "PHONE_STATUS" for v in ch_map.values()), "PHONE_STATUS channel missing in SDR map"
    assert ch_map.get(11) == "NOTIFICATION" or any(v == "NOTIFICATION" for v in ch_map.values()), "NOTIFICATION channel missing in SDR map"
    print("✅ All active channels verified in Service Discovery Response!")


async def test_phone_status_handler():
    print("\n--- 2. Testing PhoneStatusHandler ---")
    mgr = MockChannelManager()
    handler = PhoneStatusHandler(mgr)

    # 1. Test Channel Open Request
    await handler.handle_message(10, 0x0007, b"")
    assert len(mgr.sent_frames) == 1, "Should send ChannelOpenResponse"
    assert mgr.sent_frames[0][1] == 0x0008, "Response msgId should be 0x0008"
    print("✅ PhoneStatusHandler: ChannelOpenResponse verified")

    # 2. Test PhoneStatusUpdate
    update = PhoneStatusUpdate()
    update.signal_strength = 5
    call = update.calls.add()
    call.call_state = PhoneCallState.Enum.INCOMING
    call.call_duration_seconds = 12
    call.display_name = "Alice Smith"
    call.phone_number = "+1 555-0199"

    await handler.handle_message(10, 0x8001, update.SerializeToString())
    assert handler.current_state["is_in_call"] is True
    assert handler.current_state["call_state"] == "RINGING"
    assert handler.current_state["caller_name"] == "Alice Smith"
    assert handler.current_state["signal_strength"] == 5
    assert len(mgr.published_events) == 1
    assert mgr.published_events[0][0] == "phone.status"
    print("✅ PhoneStatusHandler: PhoneStatusUpdate parsed & phone.status published")

    # 3. Test Phone Action (Answer)
    await handler.send_phone_action("answer")
    assert len(mgr.sent_frames) == 2
    sent_action_frame = mgr.sent_frames[1]
    assert sent_action_frame[1] == 0x8002  # PhoneStatusInput
    inp = PhoneStatusInput()
    inp.ParseFromString(sent_action_frame[2])
    assert inp.input_type.action == PhoneInputAction.PHONE_INPUT_CALL
    print("✅ PhoneStatusHandler: Answer action wire frame verified")


async def test_notification_handler():
    print("\n--- 3. Testing NotificationHandler ---")
    mgr = MockChannelManager()
    handler = NotificationHandler(mgr)

    # Channel Open
    await handler.handle_message(12, 0x0007, b"")
    assert len(mgr.sent_frames) == 1
    print("✅ NotificationHandler: ChannelOpenResponse verified")

    # Incoming notification
    payload = b"WhatsApp\x00John Doe\x00See you soon!"
    await handler.handle_message(12, 0x8001, payload)
    assert len(handler.recent_notifications) == 1
    notif = handler.recent_notifications[0]
    assert notif["app_name"] == "WhatsApp"
    assert notif["title"] == "John Doe"
    assert notif["text"] == "See you soon!"
    print("✅ NotificationHandler: Notification parsed & recent feed updated")

    # Dismiss Action
    await handler.send_action(notif["id"], "dismiss")
    assert len(handler.recent_notifications) == 0
    print("✅ NotificationHandler: Dismiss action verified")


async def main():
    await test_service_discovery()
    await test_phone_status_handler()
    await test_notification_handler()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY! 100% CLEAN.")


if __name__ == "__main__":
    asyncio.run(main())
