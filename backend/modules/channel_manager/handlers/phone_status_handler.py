"""
phone_status_handler.py — Android Auto Phone Status Channel Handler (GAL type 13 / Field 10).

Processes PhoneStatusUpdate (0x8001) from phone (call state, duration, signal, battery)
and sends PhoneStatusInput (0x8002) for answering/rejecting/ending calls.
"""

from __future__ import annotations
import base64
from typing import TYPE_CHECKING, Optional, Any

from shared.logger import get_logger
from shared.constants import ChannelType
from shared.proto_utils import encode_proto

from protos.oaa.common.StatusEnum_pb2 import Status
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.phone.PhoneStatusMessage_pb2 import PhoneStatusUpdate, PhoneCall
from protos.oaa.phone.PhoneStatusInputMessage_pb2 import PhoneStatusInput, PhoneInputType, PhoneInputAction
from protos.oaa.phone.PhoneCallStateEnum_pb2 import PhoneCallState

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG_CHANNEL_OPEN_REQUEST = 0x0007
MSG_CHANNEL_OPEN_RESPONSE = 0x0008
MSG_PHONE_STATUS_UPDATE = 0x8001
MSG_PHONE_STATUS_INPUT = 0x8002

CALL_STATE_MAP = {
    0: "UNKNOWN",
    1: "IN_CALL",
    2: "ON_HOLD",
    3: "INACTIVE",
    4: "INCOMING",
    5: "CONFERENCED",
    6: "MUTED",
}


class PhoneStatusHandler:
    """Handles Android Auto Phone Status Channel (Channel 10)."""

    def __init__(self, manager: ChannelManagerModule) -> None:
        self.manager = manager
        self.log = get_logger("channel_manager.phone_status")
        self.active_channel_id: Optional[int] = None
        self.current_state: dict[str, Any] = {
            "is_in_call": False,
            "call_state": "IDLE",
            "caller_name": "",
            "caller_number": "",
            "call_duration_seconds": 0,
            "signal_strength": -1,  # 0 to 5, -1 if unknown
            "battery_level": -1,   # 0 to 100, -1 if unknown
            "is_charging": False,
            "has_photo": False,
            "contact_photo_b64": "",
            "operator_name": "",
            "is_roaming": False,
        }

    async def handle_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        """Route incoming wire frames for the Phone Status channel."""
        self.active_channel_id = channel_id

        if message_id == MSG_CHANNEL_OPEN_REQUEST:
            await self._handle_channel_open_request(channel_id, body)
        elif message_id == MSG_PHONE_STATUS_UPDATE:
            await self._handle_phone_status_update(channel_id, body)
        else:
            self.log.debug(f"PhoneStatusHandler (ch{channel_id}): Unhandled msg 0x{message_id:04x} ({len(body)}B)")

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"📞 PhoneStatusChannel (ch{channel_id}): Received Channel Open Request — responding OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_phone_status_update(self, channel_id: int, body: bytes) -> None:
        try:
            self.log.info(f"📞 PhoneStatus raw wire bytes ({len(body)}B): {body.hex()}")
            update = PhoneStatusUpdate()
            update.ParseFromString(body)
            self.log.info(f"📞 PhoneStatus parsed proto:\n{update}")

            if update.HasField("signal_strength"):
                self.current_state["signal_strength"] = min(5, max(0, update.signal_strength))

            if len(update.calls) > 0:
                call: PhoneCall = update.calls[0]
                call_state_enum = call.call_state
                state_str = CALL_STATE_MAP.get(call_state_enum, "UNKNOWN")
                duration = call.call_duration_seconds
                number = call.phone_number if call.HasField("phone_number") else ""
                name = call.display_name if call.HasField("display_name") else number

                photo_b64 = ""
                if call.HasField("contact_photo") and len(call.contact_photo) > 0:
                    photo_b64 = base64.b64encode(call.contact_photo).decode("ascii")

                self.current_state.update({
                    "is_in_call": state_str in ("IN_CALL", "ON_HOLD", "INCOMING", "CONFERENCED", "MUTED", "ACTIVE", "HOLD", "RINGING"),
                    "call_state": state_str,
                    "caller_name": name,
                    "caller_number": number,
                    "call_duration_seconds": duration,
                    "has_photo": bool(photo_b64),
                    "contact_photo_b64": photo_b64,
                })
            else:
                self.current_state.update({
                    "is_in_call": False,
                    "call_state": "IDLE",
                    "caller_name": "",
                    "caller_number": "",
                    "call_duration_seconds": 0,
                    "has_photo": False,
                    "contact_photo_b64": "",
                })

            self.log.info(
                f"📞 PhoneStatusUpdate: state={self.current_state['call_state']}, "
                f"caller='{self.current_state['caller_name']}', signal={self.current_state['signal_strength']}/5"
            )

            # Publish event over ZMQ bus
            self.manager.publish("phone.status", self.current_state)
            # Broadcast over WebSocket for Frontend and Qt6
            await self.manager.broadcast_ws_json({
                "type": "phone_status",
                "data": self.current_state,
            })

        except Exception as exc:
            self.log.warning(f"PhoneStatusHandler (ch{channel_id}): Failed to parse PhoneStatusUpdate: {exc}")

    async def update_telemetry(self, data: dict) -> None:
        """Merge incoming Bluetooth telemetry (battery, RSSI, operator, roaming) from connectivity_manager."""
        if not isinstance(data, dict):
            return
        updated = False
        if "battery_level" in data and data["battery_level"] is not None and data["battery_level"] >= 0:
            self.current_state["battery_level"] = max(0, min(100, int(data["battery_level"])))
            updated = True
        if "signal_strength" in data and data["signal_strength"] is not None and data["signal_strength"] >= 0:
            self.current_state["signal_strength"] = max(0, min(5, int(data["signal_strength"])))
            updated = True
        if "operator_name" in data and data["operator_name"]:
            self.current_state["operator_name"] = str(data["operator_name"])
            updated = True
        if "is_roaming" in data and data["is_roaming"] is not None:
            self.current_state["is_roaming"] = bool(data["is_roaming"])
            updated = True

        if updated:
            self.log.info(
                f"📱 PhoneStatus merged telemetry: battery={self.current_state['battery_level']}%, "
                f"signal={self.current_state['signal_strength']}/5, operator='{self.current_state['operator_name']}'"
            )
            await self.manager.broadcast_ws_json({
                "type": "phone_status",
                "data": self.current_state,
            })

    async def update_battery_status(self, battery_level: int, is_charging: bool = False) -> None:
        """Update battery status and broadcast change."""
        self.current_state["battery_level"] = max(0, min(100, battery_level))
        self.current_state["is_charging"] = is_charging
        self.manager.publish("phone.status", self.current_state)
        await self.manager.broadcast_ws_json({
            "type": "phone_status",
            "data": self.current_state,
        })

    async def send_phone_action(self, action_name: str) -> bool:
        """
        Send phone action (ANSWER, REJECT, HANGUP, MUTE, CALL) to phone over wire.
        """
        ch_id = self.active_channel_id or self.manager.get_channel_id_for_type(ChannelType.PHONE_STATUS)
        if ch_id is None:
            self.log.warning("Cannot send phone action: Phone status channel not active")
            return False

        action_enum = PhoneInputAction.PHONE_INPUT_UNKNOWN
        act = action_name.upper().strip()

        if act in ("ANSWER", "ACCEPT", "CALL"):
            action_enum = PhoneInputAction.PHONE_INPUT_CALL
        elif act in ("REJECT", "DECLINE", "HANGUP", "END", "BACK"):
            action_enum = PhoneInputAction.PHONE_INPUT_BACK
        elif act == "ENTER":
            action_enum = PhoneInputAction.PHONE_INPUT_ENTER
        elif act == "UP":
            action_enum = PhoneInputAction.PHONE_INPUT_UP
        elif act == "DOWN":
            action_enum = PhoneInputAction.PHONE_INPUT_DOWN

        req = PhoneStatusInput()
        req.input_type.action = action_enum
        if self.current_state.get("caller_number"):
            req.caller_id = self.current_state["caller_number"]
        if self.current_state.get("caller_name"):
            req.display_name = self.current_state["caller_name"]

        self.log.info(f"📞 Sending PhoneStatusInput: action={act} ({action_enum}) on ch{ch_id}")
        await self.manager.send_wire_frame(
            ch_id,
            MSG_PHONE_STATUS_INPUT,
            req.SerializeToString(),
            encrypted=True,
        )
        return True
