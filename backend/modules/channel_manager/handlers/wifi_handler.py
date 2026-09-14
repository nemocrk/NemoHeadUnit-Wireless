from typing import TYPE_CHECKING, Callable, Dict
from shared.logger import get_logger
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.wifi.WifiChannelMessageIdsEnum_pb2 import WifiChannelMessage
from protos.oaa.wifi.WifiCredentialsResponseMessage_pb2 import (
    WifiCredentialsResponse,
    WifiCredentialSecurityMode,
    WifiCredentialStatus,
)
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

log = get_logger("channel_manager.wifi")

MSG = ControlMessage.Enum
WIFI_MSG = WifiChannelMessage.Enum


class WifiChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

        self._handlers: Dict[int, Callable[[int, bytes], None]] = {
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            WIFI_MSG.CREDENTIALS_REQUEST: self._handle_credentials_request,
        }

    async def handle_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(channel_id, body)
        else:
            await self._handle_unhandled_message(channel_id, message_id, body)

    async def _handle_channel_open_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"WifiChannel (ch{channel_id}): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(channel_id, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_credentials_request(self, channel_id: int, body: bytes) -> None:
        self.log.info(f"WifiChannel (ch{channel_id}): Received WifiCredentialsRequest — responding WifiCredentialsResponse...")
        resp = WifiCredentialsResponse()
        resp.ssid = "AndroidAutoAP"
        resp.passphrase = "12345678"
        resp.security_mode = WifiCredentialSecurityMode.SECURITY_WPA2_PERSONAL
        resp.status = WifiCredentialStatus.CREDENTIAL_STATUS_OK
        await self.manager.send_wire_frame(channel_id, WIFI_MSG.CREDENTIALS_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_unhandled_message(self, channel_id: int, message_id: int, body: bytes) -> None:
        self.log.warning(f"⚠️ [Unhandled WiFi Message] WifiChannel (ch{channel_id}) received unknown msgId=0x{message_id:04x} len={len(body)}")
