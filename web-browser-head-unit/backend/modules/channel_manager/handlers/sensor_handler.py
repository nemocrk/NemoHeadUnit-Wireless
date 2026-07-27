from typing import TYPE_CHECKING, Callable, Dict
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.sensor.SensorChannelMessageIdsEnum_pb2 import SensorChannelMessage
from protos.oaa.sensor.SensorStartRequestMessage_pb2 import SensorStartRequestMessage
from protos.oaa.sensor.SensorStartResponseMessage_pb2 import SensorStartResponseMessage
from protos.oaa.sensor.SensorEventIndicationMessage_pb2 import SensorEventIndication
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.sensor")

MSG = ControlMessage.Enum
SENSOR_MSG = SensorChannelMessage.Enum


class SensorChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            SENSOR_MSG.SENSOR_REQUEST: self._handle_sensor_start_request,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"SensorChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_sensor_start_request(self, channel_id: int, body: bytes) -> None:
        sensor_type = 0
        try:
            req = SensorStartRequestMessage()
            req.ParseFromString(body)
            sensor_type = req.sensor_type
            self.log.info(f"SensorChannel (ch{channel_id}): SensorStartRequest received for sensor_type={sensor_type}")
        except Exception as exc:
            self.log.warning(f"SensorChannel (ch{channel_id}): SensorStartRequest parse error: {exc}")

        # Send SensorStartResponseMessage with status OK
        resp = SensorStartResponseMessage()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, SENSOR_MSG.SENSOR_START_RESPONSE, resp.SerializeToString(), encrypted=True)

        # Send SensorEventIndication payload matching requested sensor_type
        event = SensorEventIndication()
        if sensor_type == 13:  # DRIVING_STATUS
            d_status = event.driving_status.add()
            d_status.status = 0  # UNRESTRICTED
        elif sensor_type == 10:  # NIGHT_DATA
            n_mode = event.night_mode.add()
            n_mode.is_night = False

        elif sensor_type == 7:  # PARKING_BRAKE
            p_brake = event.parking_brake.add()
            p_brake.parking_brake = True
        elif sensor_type == 2:  # COMPASS
            cmp = event.compass.add()
            cmp.bearing = 0
        else:
            # Default fallback unrestricted driving status
            d_status = event.driving_status.add()
            d_status.status = 0

        event_bytes = event.SerializeToString()
        await self.manager.send_wire_frame(channel_id, SENSOR_MSG.SENSOR_EVENT_INDICATION, event_bytes, encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Sensor Message] SensorChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
