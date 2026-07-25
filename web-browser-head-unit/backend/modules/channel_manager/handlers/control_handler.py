import struct
from typing import TYPE_CHECKING

from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum


class ControlChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.tls_started = False

    async def handle_frame(self, message_id: int, body: bytes) -> None:
        self.log.info(f"ControlChannel (ch0) msgId=0x{message_id:04x} len={len(body)}")
        if message_id == MSG.VERSION_REQUEST:
            resp = struct.pack(">H H H", MSG.VERSION_RESPONSE, 1, 1)  # Version 1.1 OK
            await self.manager.send_wire_frame(0, MSG.VERSION_RESPONSE, resp)
        elif message_id == MSG.VERSION_RESPONSE:
            self.log.info("Received Version Response (VERSION_RESPONSE) from phone — version exchange complete!")
            if not self.tls_started:
                self.tls_started = True
                self.manager.publish("aa.handshake.start_tls", {})
            else:
                self.log.info("Post-TLS Version exchange confirmed — awaiting Service Discovery Request...")
        elif message_id == MSG.SSL_HANDSHAKE:
            self.log.info(f"Received SSL Handshake (SSL_HANDSHAKE) from phone ({len(body)} bytes) — feeding to TLS engine...")
            await self.manager.publish("aa.handshake.feed_input", {"payload_hex": body.hex()})
        elif message_id == MSG.SERVICE_DISCOVERY_REQUEST:
            self.log.info("Received Service Discovery Request (SERVICE_DISCOVERY_REQUEST) from phone — building Service Discovery Response...")
            try:
                try:
                    from modules.channel_manager.service_discovery import build_service_discovery_response
                except ImportError:
                    from service_discovery import build_service_discovery_response
                sdr_bytes, sdr_dict = build_service_discovery_response(self.manager.config, bt_mac="00:00:00:00:00:00", wifi_bssid="")
                self.log.info(f"📋 [SDR Configuration] Transmitting Service Discovery Response ({len(sdr_bytes)} bytes) to phone:\n{sdr_dict}")
            except Exception as exc:
                self.log.warning(f"Using fallback minimal SDR payload: {exc}")
                sdr_bytes = b"\x0a\x0bAndroidAuto\x12\x041.0.0"
            await self.manager.send_wire_frame(0, MSG.SERVICE_DISCOVERY_RESPONSE, sdr_bytes, encrypted=True)
        elif message_id == MSG.CHANNEL_OPEN_REQUEST:
            self.log.info("Received Channel Open Request (CHANNEL_OPEN_REQUEST) — responding STATUS_OK...")
            status_ok = b"\x08\x00"  # STATUS_OK
            await self.manager.send_wire_frame(0, MSG.CHANNEL_OPEN_RESPONSE, status_ok, encrypted=True)
        elif message_id == MSG.PING_REQUEST:
            self.log.debug("Received Ping Request (PING_REQUEST) — sending Ping Response...")
            await self.manager.send_wire_frame(0, MSG.PING_RESPONSE, b"")
