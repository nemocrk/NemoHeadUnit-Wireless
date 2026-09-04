import time
import struct
from typing import TYPE_CHECKING, Callable, Dict

from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import ServiceDiscoveryResponse
from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
from protos.oaa.control.PingResponseMessage_pb2 import PingResponse
from protos.oaa.control.VoiceSessionRequestMessage_pb2 import VoiceSessionRequest
from protos.oaa.control.BatteryStatusMessage_pb2 import BatteryStatusNotification
from protos.oaa.audio.AudioFocusRequestMessage_pb2 import AudioFocusRequest
from protos.oaa.audio.AudioFocusResponseMessage_pb2 import AudioFocusResponse
from protos.oaa.audio.AudioFocusStateEnum_pb2 import AudioFocusState
from protos.oaa.audio.AudioFocusTypeEnum_pb2 import AudioFocusType
from protos.oaa.navigation.NavigationFocusRequestMessage_pb2 import NavigationFocusRequest, NavigationFocusType
from protos.oaa.navigation.NavigationFocusResponseMessage_pb2 import NavigationFocusResponse
from protos.oaa.common.StatusEnum_pb2 import Status

if TYPE_CHECKING:
    from ..main import ChannelManagerModule

MSG = ControlMessage.Enum


class ControlChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log
        self.tls_started = False

        # Dedicated per-message dispatch mapping
        self._handlers: Dict[int, Callable[[bytes], None]] = {
            MSG.VERSION_REQUEST: self._handle_version_request,
            MSG.VERSION_RESPONSE: self._handle_version_response,
            MSG.SSL_HANDSHAKE: self._handle_ssl_handshake,
            MSG.SERVICE_DISCOVERY_REQUEST: self._handle_service_discovery_request,
            MSG.CHANNEL_OPEN_REQUEST: self._handle_channel_open_request,
            MSG.PING_REQUEST: self._handle_ping_request,
            MSG.AUDIO_FOCUS_REQUEST: self._handle_audio_focus_request,
            MSG.NAVIGATION_FOCUS_REQUEST: self._handle_navigation_focus_request,
            MSG.VOICE_SESSION_REQUEST: self._handle_voice_session_request,
            MSG.BATTERY_STATUS_NOTIFICATION: self._handle_battery_status_notification,
            MSG.SHUTDOWN_REQUEST: self._handle_shutdown_request,
        }

    async def handle_frame(self, message_id: int, body: bytes) -> None:
        handler = self._handlers.get(message_id)
        if handler:
            await handler(body)
        else:
            await self._handle_unhandled_message(message_id, body)

    async def _handle_version_request(self, body: bytes) -> None:
        self.log.info("ControlChannel (ch0): Handling VERSION_REQUEST...")
        resp = struct.pack(">H H H", MSG.VERSION_RESPONSE, 1, 1)  # Version 1.1 OK
        await self.manager.send_wire_frame(0, MSG.VERSION_RESPONSE, resp)

    async def _handle_version_response(self, body: bytes) -> None:
        self.log.info("ControlChannel (ch0): Received Version Response from phone — version exchange complete!")
        if not self.tls_started:
            self.tls_started = True
            self.manager.publish("aa.handshake.start_tls", {})
        else:
            self.log.info("ControlChannel (ch0): Post-TLS Version exchange confirmed — awaiting Service Discovery Request...")

    async def _handle_ssl_handshake(self, body: bytes) -> None:
        self.log.info(f"ControlChannel (ch0): Received SSL Handshake from phone ({len(body)} bytes) — feeding to TLS engine...")
        self.manager.publish("aa.handshake.feed_input", {"payload_hex": body.hex()})

    async def _handle_service_discovery_request(self, body: bytes) -> None:
        self.log.info("ControlChannel (ch0): Received Service Discovery Request from phone — building Service Discovery Response...")
        try:
            try:
                from modules.channel_manager.service_discovery import build_service_discovery_response
            except ImportError:
                from service_discovery import build_service_discovery_response
            sdr_bytes, sdr_dict, type_map = build_service_discovery_response(self.manager.config, bt_mac="00:00:00:00:00:00", wifi_bssid="")
            self.manager.set_channel_type_map(type_map)
            self.manager.publish("aa.sdr.channels", {"type_map": type_map})
            self.log.info(f"📋 [SDR Configuration] Transmitting Service Discovery Response ({len(sdr_bytes)} bytes) to phone (Dynamic Channel Map: {type_map}):\n{sdr_dict}")
        except Exception as exc:
            self.log.warning(f"ControlChannel (ch0): Using fallback ServiceDiscoveryResponse protobuf: {exc}")
            sdr = ServiceDiscoveryResponse()
            sdr.name = "AndroidAuto"
            sdr.car_model = "1.0.0"
            sdr_bytes = sdr.SerializeToString()
        await self.manager.send_wire_frame(0, MSG.SERVICE_DISCOVERY_RESPONSE, sdr_bytes, encrypted=True)

    async def _handle_channel_open_request(self, body: bytes) -> None:
        self.log.info("ControlChannel (ch0): Received Channel Open Request — responding STATUS_OK...")
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        await self.manager.send_wire_frame(0, MSG.CHANNEL_OPEN_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_ping_request(self, body: bytes) -> None:
        timestamp = 0
        try:
            req = PingRequest()
            req.ParseFromString(body)
            timestamp = req.timestamp
            self.log.debug(f"ControlChannel (ch0): PING ts={timestamp} → PONG")
        except Exception:
            timestamp = int(time.time() * 1_000_000)
            self.log.debug("ControlChannel (ch0): PING (raw) → PONG")

        resp = PingResponse()
        resp.timestamp = timestamp
        await self.manager.send_wire_frame(0, MSG.PING_RESPONSE, resp.SerializeToString(), encrypted=True, log_level='debug')

    async def _handle_audio_focus_request(self, body: bytes) -> None:
        focus_type = AudioFocusType.Enum.GAIN
        try:
            req = AudioFocusRequest()
            req.ParseFromString(body)
            focus_type = req.audio_focus_type
            self.log.info(f"ControlChannel (ch0): Received AudioFocusRequest (focus_type={focus_type})")
        except Exception as exc:
            self.log.warning(f"ControlChannel (ch0): AudioFocusRequest parse error: {exc}")

        resp = AudioFocusResponse()
        if focus_type == AudioFocusType.Enum.GAIN:
            resp.audio_focus_state = AudioFocusState.GAIN
        elif focus_type == AudioFocusType.Enum.GAIN_TRANSIENT:
            resp.audio_focus_state = AudioFocusState.GAIN_TRANSIENT
        elif focus_type == AudioFocusType.Enum.GAIN_TRANSIENT_MAY_DUCK:
            resp.audio_focus_state = AudioFocusState.GAIN_TRANSIENT_GUIDANCE_ONLY
        elif focus_type == AudioFocusType.Enum.RELEASE:
            resp.audio_focus_state = AudioFocusState.LOSS
        else:
            resp.audio_focus_state = AudioFocusState.INVALID
        resp.granted = True

        self.log.info(f"ControlChannel (ch0): Responding AudioFocusResponse (state={resp.audio_focus_state}, granted=True)")
        await self.manager.send_wire_frame(0, MSG.AUDIO_FOCUS_RESPONSE, resp.SerializeToString(), encrypted=True)
        is_paused = (resp.audio_focus_state == AudioFocusState.LOSS)
        self.manager.publish("media.audio.focus", {
            "channel_id": 0,
            "focus_type": focus_type,
            "focus_state": resp.audio_focus_state,
            "is_paused": is_paused,
        })

    async def _handle_navigation_focus_request(self, body: bytes) -> None:
        req_type = NavigationFocusType.NAV_FOCUS_PROJECTED
        try:
            req = NavigationFocusRequest()
            req.ParseFromString(body)
            req_type = req.type
            self.log.info(f"ControlChannel (ch0): Received NavigationFocusRequest (type={req_type})")
        except Exception as exc:
            self.log.warning(f"ControlChannel (ch0): NavigationFocusRequest parse error: {exc}")

        if req_type == NavigationFocusType.NAV_FOCUS_NATIVE:
            if hasattr(self.manager, "navigation_handler") and self.manager.navigation_handler:
                self.manager.navigation_handler.clear_navigation()

        resp = NavigationFocusResponse()
        resp.type = NavigationFocusType.NAV_FOCUS_PROJECTED
        await self.manager.send_wire_frame(0, MSG.NAVIGATION_FOCUS_RESPONSE, resp.SerializeToString(), encrypted=True)

    async def _handle_voice_session_request(self, body: bytes) -> None:
        try:
            req = VoiceSessionRequest()
            req.ParseFromString(body)
            self.log.info(f"ControlChannel (ch0): Received VoiceSessionRequest (session_type={req.session_type})")
        except Exception:
            self.log.info("ControlChannel (ch0): Received VoiceSessionRequest")

    async def _handle_battery_status_notification(self, body: bytes) -> None:
        try:
            req = BatteryStatusNotification()
            req.ParseFromString(body)
            self.log.info(f"🔋 ControlChannel (ch0): BatteryStatus level={req.battery_level}% remaining={req.time_remaining_s}s critical={req.critical_battery}")
            if hasattr(self.manager, "phone_status_handler") and self.manager.phone_status_handler:
                await self.manager.phone_status_handler.update_battery_status(req.battery_level)
        except Exception as exc:
            self.log.warning(f"ControlChannel (ch0): Failed to parse BatteryStatusNotification: {exc}")

    async def _handle_shutdown_request(self, body: bytes) -> None:
        self.log.info("ControlChannel (ch0): Received ShutdownRequest — responding ShutdownResponse")
        await self.manager.send_wire_frame(0, MSG.SHUTDOWN_RESPONSE, b"", encrypted=True)

    async def _handle_unhandled_message(self, message_id: int, body: bytes) -> None:
        if message_id == 0xffff:
            self.log.debug(f"ControlChannel (ch0): Received dummy/keepalive msgId=0xffff ({len(body)} bytes)")
            return
        self.log.warning(f"⚠️ [Unhandled Control Message] ControlChannel (ch0) received unknown msgId=0x{message_id:04x} len={len(body)}")
