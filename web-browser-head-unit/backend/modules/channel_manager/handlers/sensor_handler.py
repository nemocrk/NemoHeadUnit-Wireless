import struct
from typing import TYPE_CHECKING
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.sensor")

MSG = ControlMessage.Enum
_MSG_SENSOR_START_REQUEST  = 0x8001
_MSG_SENSOR_START_RESPONSE = 0x8002
_MSG_SENSOR_EVENT          = 0x8003


class SensorChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.info(f"SensorChannel (ch{channel_id}) msgId=0x{message_id:04x} len={len(body)}")
        if message_id == MSG.CHANNEL_OPEN_REQUEST:
            status_ok = b"\x08\x00"
            await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, status_ok, encrypted=True)
        elif message_id == _MSG_SENSOR_START_REQUEST:
            status_ok = b"\x08\x00"
            await self.manager.send_wire_frame(channel_id, _MSG_SENSOR_START_RESPONSE, status_ok, encrypted=True)
            # Send valid driving status unrestricted event payload (field 13 driving_status)
            try:
                from protos.oaa.sensor.SensorEventIndicationMessage_pb2 import SensorEventIndication
                event = SensorEventIndication()
                d_status = event.driving_status.add()
                d_status.status = 0  # UNRESTRICTED
                event_bytes = event.SerializeToString()
            except Exception:
                event_bytes = b"\x6a\x02\x08\x00"  # Valid driving_status field 13 wire bytes
            await self.manager.send_wire_frame(channel_id, _MSG_SENSOR_EVENT, event_bytes, encrypted=True)
