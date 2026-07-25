from typing import TYPE_CHECKING, Callable, Dict
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2 import BluetoothChannelMessage
from protos.oaa.bluetooth.BluetoothPairingResponseMessage_pb2 import BluetoothPairingResponse
from protos.oaa.bluetooth.BluetoothAuthenticationResultMessage_pb2 import BluetoothAuthenticationResult
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.bluetooth")

MSG = ControlMessage.Enum
BT_MSG = BluetoothChannelMessage.Enum


class BluetoothChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            BT_MSG.PAIRING_REQUEST: self._handle_pairing_request,
            BT_MSG.AUTH_DATA: self._handle_auth_data,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"BluetoothChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_pairing_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"BluetoothChannel (ch{channel_id}): Received PairingRequest — responding PairingResponse(paired=True)...")
        resp = BluetoothPairingResponse()
        resp.status = Status.OK
        resp.already_paired = True
        await self.manager.send_wire_frame(channel_id, BT_MSG.PAIRING_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_auth_data(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"BluetoothChannel (ch{channel_id}): Received AuthData — responding AuthResult(STATUS_OK)...")
        resp = BluetoothAuthenticationResult()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, BT_MSG.AUTH_RESULT, resp.SerializeToString(), encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled Bluetooth Message] BluetoothChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
