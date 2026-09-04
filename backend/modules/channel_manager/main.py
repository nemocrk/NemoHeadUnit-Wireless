#!/usr/bin/env python3
"""
Web Browser Head Unit — Consolidated Channel Manager Module

Cross-platform Priority 3 Microservice extending `BaseBackendModule`.

Module Responsibilities:
  1. Hosts all AA sub-channel handlers in-process via asyncio tasks (Control, Video, Audio PCM/AAC, Mic, Input, Sensor, BT, WiFi).
  2. Reads downstream media frames zero-copy from `BidirectionalMediaSHM.downstream`.
  3. Writes upstream microphone audio frames zero-copy to `BidirectionalMediaSHM.upstream`.
  4. Exposes unified binary WebSocket stream (`ws://127.0.0.1:8000/api/stream`) for WebCodecs frontend.
"""

import asyncio
import json
import struct

from typing import Any, Dict, List, Optional, Set
import aiohttp
from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.media_shm import BidirectionalMediaSHM
from shared.nal_utils import pack_media_frame, STREAM_TYPE_VIDEO, STREAM_TYPE_AUDIO
from shared.proto_utils import channels_from_sdr_bytes, parse_media_with_timestamp

try:
    from modules.channel_manager.handlers import (
        ControlChannelHandler,
        VideoChannelHandler,
        AudioChannelHandler,
        AudioChannelHandler,
        AVInputChannelHandler,
        InputChannelHandler,
        SensorChannelHandler,
        BluetoothChannelHandler,
        WifiChannelHandler,
        NavigationChannelHandler,
        MediaPlaybackChannelHandler,
        PhoneStatusHandler,
        NotificationHandler,
    )
except ImportError:
    from handlers import (
        ControlChannelHandler,
        VideoChannelHandler,
        AudioChannelHandler,
        AVInputChannelHandler,
        InputChannelHandler,
        SensorChannelHandler,
        BluetoothChannelHandler,
        WifiChannelHandler,
        NavigationChannelHandler,
        MediaPlaybackChannelHandler,
        PhoneStatusHandler,
        NotificationHandler,
    )

from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage

MSG = ControlMessage.Enum

from shared.constants import ChannelType


class ChannelManagerModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="channel_manager",
            priority=3,
            path_prefix="/api/channels",
        )
        self.shm = BidirectionalMediaSHM(create=False)
        self.ws_clients: Set[web.WebSocketResponse] = set()
        self.active_channels: Dict[int, dict] = {}
        self.channel_type_map: Dict[int, ChannelType] = {}

        # Sub-channel handlers
        self.control_handler = ControlChannelHandler(self)
        self.video_handler = VideoChannelHandler(self)
        self.audio_handler = AudioChannelHandler(self)
        self.av_input_handler = AVInputChannelHandler(self)
        self.input_handler = InputChannelHandler(self)
        self.sensor_handler = SensorChannelHandler(self)
        self.bluetooth_handler = BluetoothChannelHandler(self)
        self.wifi_handler = WifiChannelHandler(self)
        self.navigation_handler = NavigationChannelHandler(self)
        self.media_playback_handler = MediaPlaybackChannelHandler(self)
        self.phone_status_handler = PhoneStatusHandler(self)
        self.notification_handler = NotificationHandler(self)

        # Active video transport name — set by video_decoder module via video.transport_active
        self.active_video_transport: str = "h264"
        self._status_changed_evt = asyncio.Event()

    def set_channel_type_map(self, type_map: dict) -> None:
        """Store dynamic channel_id -> ChannelType mapping from SDR."""
        self.channel_type_map = {int(k): ChannelType[v] for k, v in type_map.items() if v in ChannelType.__members__}
        self.log.info(f"ChannelManager: Dynamic channel classification registry populated: {self.channel_type_map}")

    def get_channel_type(self, channel_id: int) -> ChannelType:
        """Return the ChannelType for a given channel_id."""
        if channel_id == 0:
            return ChannelType.CONTROL
        return self.channel_type_map.get(channel_id, ChannelType.UNKNOWN)

    def on_config_updated(self, config: dict) -> None:
        super().on_config_updated(config)
        # Pre-populate channel_type_map from module configuration defaults
        type_map = {0: ChannelType.CONTROL}
        for ch in config.get("channels", []):
            cid = ch.get("channel_id")
            if cid is None:
                continue
            if "input_channel" in ch:
                type_map[cid] = ChannelType.INPUT
            elif "sensor_channel" in ch:
                type_map[cid] = ChannelType.SENSOR
            elif "av_channel" in ch:
                codec = ch["av_channel"].get("codec", "")
                if "VIDEO" in str(codec):
                    type_map[cid] = ChannelType.VIDEO
                else:
                    type_map[cid] = ChannelType.AUDIO
            elif "av_input_channel" in ch:
                type_map[cid] = ChannelType.AUDIO_MIC
            elif "bluetooth_channel" in ch:
                type_map[cid] = ChannelType.BLUETOOTH
            elif "wifi_channel" in ch:
                type_map[cid] = ChannelType.WIFI
            elif "navigation_channel" in ch:
                type_map[cid] = ChannelType.NAVIGATION
            elif "media_info_channel" in ch:
                type_map[cid] = ChannelType.MEDIA_PLAYBACK
            elif "phone_status_channel" in ch:
                type_map[cid] = ChannelType.PHONE_STATUS
            elif "notification_channel" in ch or "generic_notification_channel" in ch:
                type_map[cid] = ChannelType.NOTIFICATION
        for k, v in type_map.items():
            if k not in self.channel_type_map:
                self.channel_type_map[k] = v
        for ch in config.get("channels", []):
            cid = ch.get("channel_id")
            if cid is not None and cid not in self.active_channels:
                self.active_channels[cid] = ch
        self.log.info(f"ChannelManager: Pre-populated channel_type_map: {self.channel_type_map} and active_channels: {list(self.active_channels.keys())}")


    def get_channel_id_for_type(self, target_type: ChannelType) -> int:
        """Return the first channel_id matching the given ChannelType."""
        for ch_id, c_type in self.channel_type_map.items():
            if c_type == target_type:
                return ch_id
        fallback_map = {
            ChannelType.CONTROL: 0,
            ChannelType.INPUT: 1,
            ChannelType.SENSOR: 2,
            ChannelType.VIDEO: 3,
            ChannelType.AUDIO: 4,
            ChannelType.BLUETOOTH: 8,
            ChannelType.WIFI: 14,
        }
        return fallback_map.get(target_type, 0)


    def get_default_config(self) -> dict[str, Any]:
        try:
            try:
                from modules.channel_manager.service_discovery import SEMANTIC_DEFAULTS
            except ImportError:
                from service_discovery import SEMANTIC_DEFAULTS
            cfg = dict(SEMANTIC_DEFAULTS)
        except Exception as exc:
            self.log.error(f"Failed to load SEMANTIC_DEFAULTS: {exc}")
            cfg = {}
        cfg["autoclose_on_shutdown"] = True
        return cfg

    def get_schema(self) -> dict[str, Any]:
        try:
            try:
                from modules.channel_manager.service_discovery import _SCHEMA
            except ImportError:
                from service_discovery import _SCHEMA
            return _SCHEMA
        except Exception as exc:
            self.log.error(f"Failed to load _SCHEMA: {exc}")
            return {}

    async def setup(self) -> None:
        """Register REST and WebSocket endpoints, ZMQ topic subscriptions."""
        self.add_http_route("GET", "/api/channels/status", self.handle_get_status)
        self.add_http_route("GET", "/api/channels/stream_status", self.handle_stream_status)
        self.add_http_route("POST", "/api/channels/input/touch", self.handle_post_touch)
        self.add_http_route("POST", "/api/channels/input/media", self.handle_post_media_key)
        self.add_http_route("POST", "/api/channels/media/key", self.handle_post_media_key)
        self.add_http_route("POST", "/api/channels/focus", self.handle_post_focus)
        self.add_http_route("POST", "/api/channels/phone/action", self.handle_phone_action)
        self.add_http_route("POST", "/api/channels/notification/action", self.handle_notification_action)
        self.add_ws_route("/stream", self.handle_ws_stream)

        # Bus subscriptions
        self.subscribe("aa.frame.shm", self.on_frame_shm)
        self.subscribe("aa.frame.ch0", self.on_ch0_frame)
        self.subscribe("aa.frame.received", self.on_frame_received)
        self.subscribe("aa.sdr.response", self.on_sdr_response)
        self.subscribe("tcp.session.connected", self.on_tcp_session_connected)
        self.subscribe("tcp.server.tls_handshake_completed", self.on_tls_handshake_completed)

        # Media transport layer subscriptions
        self.subscribe("media.video.transport_active", self.on_video_transport_active)
        self.subscribe("media.video.request_focus", self.on_video_request_focus)
        self.subscribe("media.video.release_focus", self.on_video_release_focus)
        self.subscribe("media.audio.mic_shm", self.on_mic_audio_shm)
        self.subscribe("input.event", self.on_input_event)
        self.subscribe("phone.status", self.on_phone_status_external)


    async def run(self) -> None:
        self.log.info("ChannelManager active (SHM zero-copy & unified WebCodecs stream ready)")
        while self._running:
            await asyncio.sleep(1.0)

    async def teardown(self) -> None:
        for ws in list(self.ws_clients):
            await ws.close(code=aiohttp.WSCloseCode.GOING_AWAY, message="Module shutdown")
        self.ws_clients.clear()
        self.shm.close()

    # ------------------------------------------------------------------
    # SHM & Bus Callbacks
    # ------------------------------------------------------------------

    async def on_tcp_session_connected(self, data: dict) -> None:
        """HU speaks first: send Version Request (VERSION_REQUEST) on Channel 0 immediately when phone TCP connects."""
        address = data.get("address", "")
        self.control_handler.tls_started = False
        self.current_stage_index = 7
        self._notify_status_changed()
        self.log.info(f"🌐 [TCP Stage 4/5] Phone TCP session connected from {address} — HU sending VERSION_REQUEST...")
        version_payload = struct.pack(">H H", 1, 1)  # Major=1, Minor=1
        await self.send_wire_frame(0, MSG.VERSION_REQUEST, version_payload)

    async def on_tls_handshake_completed(self, data: dict) -> None:
        """Send AUTH_COMPLETE to phone upon TLS completion, then await SERVICE_DISCOVERY_REQUEST."""
        self.current_stage_index = 9
        self._notify_status_changed()
        self.log.info("🔒 [TLS Stage 5/5] TLS handshake complete — HU sending AUTH_COMPLETE to phone...")
        from protos.oaa.control.AuthCompleteIndicationMessage_pb2 import AuthCompleteIndication
        auth = AuthCompleteIndication()
        auth.status = 0  # STATUS_OK
        auth_payload = auth.SerializeToString()
        await self.send_wire_frame(0, MSG.AUTH_COMPLETE, auth_payload, encrypted=False)

    def _notify_status_changed(self) -> None:
        if hasattr(self, "_status_changed_evt") and self._status_changed_evt:
            self._status_changed_evt.set()

    async def handle_stream_status(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )
        await response.prepare(request)

        labels = {
            0: ("IDLE", "Disconnected", "Disconnected from Phone"),
            7: ("TCP_CONNECTED", "Phone WiFi Connected", "Phone Connected to WiFi Server"),
            8: ("TLS_STARTING", "Securing Session", "Initializing TLS 1.2 Security Session"),
            9: ("TLS_COMPLETED", "Security Active", "TLS Session Encrypted & Authenticated"),
            10: ("PROJECTION_ACTIVE", "Android Auto Active", "Android Auto Video Projection Active"),
        }

        if not hasattr(self, "_status_changed_evt") or self._status_changed_evt is None:
            self._status_changed_evt = asyncio.Event()

        try:
            while request.protocol and request.protocol.transport and not request.protocol.transport.is_closing():
                st_idx = getattr(self, "current_stage_index", 0)
                if self.video_handler and getattr(self.video_handler, "setup_completed", False):
                    st_idx = 10
                    self.current_stage_index = 10

                code, lbl, msg = labels.get(st_idx, ("IDLE", "Disconnected", "Disconnected from Phone"))

                nav_info = None
                if self.navigation_handler and (self.navigation_handler.active_road or self.navigation_handler.distance_meters >= 0):
                    nav_info = {
                        "road": self.navigation_handler.active_road,
                        "distance_meters": self.navigation_handler.distance_meters,
                        "maneuver_type": getattr(self.navigation_handler, "last_maneuver_type", 0),
                        "turn_side": getattr(self.navigation_handler, "last_turn_side", 0),
                    }

                media_info = None
                if self.media_playback_handler and (self.media_playback_handler.track_title or self.media_playback_handler.artist):
                    media_info = {
                        "title": self.media_playback_handler.track_title,
                        "artist": self.media_playback_handler.artist,
                        "album": self.media_playback_handler.album,
                        "playback_state": self.media_playback_handler.playback_state,
                        "album_art": getattr(self.media_playback_handler, "album_art_b64", ""),
                    }

                payload = {
                    "stage_index": st_idx,
                    "stage_code": code,
                    "stage_label": lbl,
                    "toast_message": msg if st_idx > 0 else None,
                    "active_channels": list(self.active_channels.keys()),
                    "ws_clients": len(self.ws_clients),
                    "video_transport": self.active_video_transport,
                    "navigation": nav_info,
                    "media": media_info,
                }
                data = f"data: {json.dumps(payload)}\n\n"
                await response.write(data.encode('utf-8'))

                # Wait strictly for an actual state change event (with 10s heartbeat fallback)
                self._status_changed_evt.clear()
                try:
                    await asyncio.wait_for(self._status_changed_evt.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:
            if "is_closing" not in str(exc):
                self.log.warning(f"handle_stream_status stream notice: {exc}")
        return response

    async def on_frame_shm(self, data: dict) -> None:
        """Received lightweight notification for media frame written to SHM."""
        ch_id = data.get("channel_id")
        msg_id = data.get("message_id")
        offset = data.get("shm_offset", -1)
        ts_us = data.get("timestamp_us", 0)
        payload_len = data.get("payload_len", 0)

        if offset < 0 or ch_id is None:
            return

        ch_type = self.get_channel_type(ch_id)

        if ch_type == ChannelType.VIDEO:
            await self.video_handler.process_shm_frame(msg_id, offset, ts_us, payload_len)
        elif ch_type == ChannelType.AUDIO:
            await self.audio_handler.process_shm_frame(ch_id, msg_id, offset, ts_us, payload_len)


    async def on_ch0_frame(self, data: dict) -> None:
        """Handle Channel 0 Control messages."""
        msg_id = data.get("message_id")
        payload_hex = data.get("payload_hex", "")
        body = bytes.fromhex(payload_hex) if payload_hex else b""
        await self.control_handler.handle_frame(msg_id, body)

    async def on_frame_received(self, data: dict) -> None:
        """Handle incoming non-Channel 0 frames dynamically routed by ChannelType."""
        ch_id = data.get("channel_id", 0)
        msg_id = data.get("message_id", 0)
        payload_hex = data.get("payload_hex", "")
        body = bytes.fromhex(payload_hex) if payload_hex else b""
        ch_type = self.get_channel_type(ch_id)

        # Media frames (0x0000 and 0x0001) are handled zero-copy via SHM (on_frame_shm)
        from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
        if msg_id in (
            AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION,
            AVChannelMessage.Enum.AV_MEDIA_INDICATION,
        ):
            return

        try:
            if ch_type == ChannelType.CONTROL:
                return  # Handled by on_ch0_frame via aa.frame.ch0 subscription
            elif ch_type == ChannelType.INPUT:
                await self.input_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.SENSOR:
                await self.sensor_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.VIDEO:
                await self.video_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.AUDIO:
                await self.audio_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.AUDIO_MIC:
                await self.av_input_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.BLUETOOTH:
                await self.bluetooth_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.WIFI:
                await self.wifi_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.NAVIGATION:
                await self.navigation_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.MEDIA_PLAYBACK:
                await self.media_playback_handler.handle_frame(ch_id, msg_id, body)
            elif ch_type == ChannelType.PHONE_STATUS:
                await self.phone_status_handler.handle_message(ch_id, msg_id, body)
            elif ch_type == ChannelType.NOTIFICATION:
                await self.notification_handler.handle_message(ch_id, msg_id, body)
            else:
                self.log.warning(f"⚠️ [Unhandled Channel Frame] Received frame on unhandled channel ch={ch_id} (type={ch_type.name}) msgId=0x{msg_id:04x} len={len(body)}")
        except Exception as exc:
            self.log.error(f"❌ Error dispatching frame ch={ch_id} msgId=0x{msg_id:04x}: {exc}", exc_info=True)



    async def on_sdr_response(self, data: dict) -> None:
        sdr_hex = data.get("sdr_hex", "")
        if sdr_hex:
            parsed = channels_from_sdr_bytes(sdr_hex)
            self.active_channels = {ch["channel_id"]: ch for ch in parsed if "channel_id" in ch}
            for ch in parsed:
                ch_id = ch.get("channel_id")
                if ch_id is not None:
                    c_type = classify_channel_descriptor(ch)
                    self.channel_type_map[ch_id] = c_type.name
            self.log.info("SDR registered %d channels: %s", len(self.active_channels), self.channel_type_map)
            await self.broadcast_ws_json(self.get_stream_config_dict())

    async def on_mic_audio_shm(self, data: dict) -> None:
        """Receive upstream mic PCM audio chunk written by media_server to SHM and send to phone."""
        offset = data.get("shm_offset", -1)
        if offset < 0:
            return
        _, _, pcm_data = self.shm.upstream.read_frame(offset)
        if pcm_data:
            await self.av_input_handler.send_mic_data(pcm_data)

    async def on_input_event(self, data: dict) -> None:
        """Receive input touch event from frontend (qt6_gui) and transmit via Input Channel to phone."""
        ev_type = data.get("type", "press")
        x = data.get("x", 0)
        y = data.get("y", 0)
        pointer_id = data.get("pointer_id", 0)
        action_index = data.get("action_index", 0)
        pointers = data.get("pointers", None)
        
        # TouchAction enum: 0 = PRESS, 1 = RELEASE, 2 = DRAG / MOVE, 5 = POINTER_DOWN, 6 = POINTER_UP
        action = data.get("action", None)
        if action is None:
            if ev_type == "release":
                action = 1
            elif ev_type == "move":
                action = 2
            elif ev_type == "pointer_down":
                action = 5
            elif ev_type == "pointer_up":
                action = 6
            else:
                action = 0

        if action != 2:
            self.log.info(
                f"👇 [Touch Input] Dispatching action={action} action_index={action_index} x={x} y={y} pointers={pointers} to phone"
            )
        await self.input_handler.handle_touch_event(
            action=action,
            pointers=pointers,
            x=int(x),
            y=int(y),
            pointer_id=pointer_id,
            action_index=action_index,
        )

    async def on_phone_status_external(self, data: dict) -> None:
        """Receive Bluetooth HFP phone telemetry from connectivity_manager and merge into phone_status_handler."""
        if isinstance(data, dict) and data.get("source") == "bluetooth_hfp":
            await self.phone_status_handler.update_telemetry(data)


    async def on_video_transport_active(self, data: dict) -> None:
        """Update active transport name when video_decoder switches modes."""
        transport_name = data.get("transport_name", "h264")
        if transport_name != self.active_video_transport:
            self.active_video_transport = transport_name
            self.log.info(f"📹 VideoTransport: Active transport changed to '{transport_name}'")
            # Re-broadcast stream_config so frontend switches rendering path
            await self.broadcast_ws_json(self.get_stream_config_dict())

    async def on_video_request_focus(self, data: dict) -> None:
        """Handle focus request from media_server or qt6_gui."""
        sender = data.get("sender", "unknown")
        mode_str = data.get("mode", "PROJECTED")
        from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
        focus_mode = VideoFocusMode.Enum.PROJECTED if mode_str == "PROJECTED" else VideoFocusMode.Enum.NATIVE
        mode_name = "PROJECTED" if focus_mode == VideoFocusMode.Enum.PROJECTED else "NATIVE"
        self.log.info(f"📹 VideoChannel: Focus ({mode_name}) requested by {sender} — sending VideoFocusIndication({mode_name}) to phone")
        await self.video_handler.send_focus_indication(focus_mode)

    async def on_video_release_focus(self, data: dict) -> None:
        """Handle focus release from media_server when all WebSocket clients disconnect."""
        sender = data.get("sender", "unknown")
        self.log.info(f"📹 VideoChannel: Focus released by {sender} — sending VideoFocusIndication(NATIVE) to phone")
        from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
        await self.video_handler.send_focus_indication(VideoFocusMode.Enum.NATIVE)

    def get_stream_config_dict(self) -> dict:
        """Construct dynamic stream_config JSON payload for all active media channels."""
        from shared.proto_utils import get_codec_descriptor
        streams = {}
        for ch_id, ch_meta in self.active_channels.items():
            ch_type = self.get_channel_type(ch_id)
            if ch_type in (ChannelType.VIDEO, ChannelType.AUDIO):
                av_conf = ch_meta.get("av_channel", {})
                codec_raw = av_conf.get("codec", "MEDIA_CODEC_UNKNOWN")
                desc = get_codec_descriptor(codec_raw)

                if ch_type == ChannelType.VIDEO:
                    v_configs = av_conf.get("video_configs", [{}])
                    v_cfg = v_configs[0] if v_configs else {}
                    res_str = str(v_cfg.get("video_resolution", "VIDEO_1280x720"))
                    w, h = 1280, 720
                    if "x" in res_str:
                        try:
                            parts = res_str.replace("VIDEO_", "").split("x")
                            w, h = int(parts[0]), int(parts[1])
                        except Exception:
                            pass
                    desc.update({
                        "width": w,
                        "height": h,
                        "fps": 30,
                    })
                elif ch_type == ChannelType.AUDIO:
                    a_configs = av_conf.get("audio_configs", [{}])
                    a_cfg = a_configs[0] if a_configs else {}
                    desc.update({
                        "sampleRate": a_cfg.get("sample_rate", 48000 if desc.get("codec") != "PCM" else 16000),
                        "channels": a_cfg.get("channel_count", 2 if desc.get("codec") != "PCM" else 1),
                        "bitDepth": a_cfg.get("bit_depth", 16),
                        "audioType": av_conf.get("audio_type", "MEDIA"),
                    })

                streams[str(ch_id)] = desc

        return {
            "type": "stream_config",
            "video_transport": self.active_video_transport,
            "streams": streams,
        }

    async def send_wire_frame(self, channel_id: int, message_id: int, body: bytes, encrypted: bool = False, log_level: str = None) -> None:
        """Publish outgoing frame to aa.frame.send."""
        frame_dict = {
            "channel_id": channel_id,
            "message_id": message_id,
            "flags": 0x0B,
            "payload_hex": body.hex(),
            "encrypted": encrypted,
            "log_level": log_level,
        }
        self.publish("aa.frame.send", frame_dict)

    async def broadcast_ws_media(self, binary_frame: bytes) -> None:
        if not self.ws_clients:
            return

        stale = []
        for ws in self.ws_clients:
            try:
                await ws.send_bytes(binary_frame)
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.ws_clients.discard(ws)

    async def broadcast_ws_json(self, data: dict) -> None:
        if not self.ws_clients:
            return

        text = json.dumps(data)
        stale = []
        for ws in self.ws_clients:
            try:
                await ws.send_str(text)
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.ws_clients.discard(ws)


    # ------------------------------------------------------------------
    # REST & WebSocket Endpoints
    # ------------------------------------------------------------------

    async def handle_ws_stream(self, request: web.Request) -> web.WebSocketResponse:
        """Unified WebSocket stream multiplexing Video and Audio channels by channel_id."""
        ws = web.WebSocketResponse(protocols=("binary",))
        await ws.prepare(request)
        self.ws_clients.add(ws)
        self._notify_status_changed()

        # Transmit current stream_config metadata immediately on connection
        try:
            config_msg = self.get_stream_config_dict()
            await ws.send_str(json.dumps(config_msg))
            self.log.info(
                f"🌐 WebSocket client connected from {request.remote} — "
                f"sent stream_config (transport={self.active_video_transport}, "
                f"{len(config_msg['streams'])} channel(s))"
            )
        except Exception as exc:
            self.log.warning(f"Failed to send initial stream_config to WebSocket client: {exc}")

        self.log.info("Frontend WebCodecs WS connected from %s", request.remote)
        await self.video_handler.update_video_focus()

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # Upstream Mic audio frame from browser
                    data = msg.data
                    if len(data) > 0:
                        self.log.debug(
                            f"🎤 [Mic Upstream] Received audio chunk from browser client "
                            f"{request.remote} (len={len(data)} bytes)"
                        )
                        offset = self.shm.upstream.write_frame(1, 0, data)
                        self.publish("aa.mic.shm", {"shm_offset": offset, "len": len(data)})
                        await self.av_input_handler.send_mic_data(data)

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.log.error("WS error: %s", ws.exception())
        finally:
            self.ws_clients.discard(ws)
            self._notify_status_changed()
            self.log.info("Frontend WebCodecs WS disconnected from %s", request.remote)
            await self.video_handler.update_video_focus()

        return ws

    async def handle_get_status(self, request: web.Request) -> web.Response:
        stream_config = self.get_stream_config_dict()
        return web.json_response(
            {
                "status": "ok",
                "stream_config": stream_config,
                "active_channels": list(self.active_channels.keys()),
                "connected_ws_clients": len(self.ws_clients),
            }
        )

    async def handle_post_touch(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            x = body.get("x", 0)
            y = body.get("y", 0)
            action = body.get("action", 0)
            pointer_id = body.get("pointer_id", 0)
            action_index = body.get("action_index", 0)
            pointers = body.get("pointers", None)
            await self.input_handler.handle_touch_event(
                action=action,
                pointers=pointers,
                x=x,
                y=y,
                pointer_id=pointer_id,
                action_index=action_index,
            )
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_post_focus(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            mode_str = body.get("mode", "PROJECTED").upper()
            from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
            focus_mode = VideoFocusMode.Enum.PROJECTED if mode_str == "PROJECTED" else VideoFocusMode.Enum.NATIVE
            await self.video_handler.send_focus_indication(focus_mode)
            return web.json_response({"status": "ok", "mode": mode_str})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_post_media_key(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            key_code = body.get("key_code", 85) # default KEYCODE_MEDIA_PLAY_PAUSE (85)
            await self.input_handler.handle_media_key(key_code)
            return web.json_response({"status": "ok", "key_code": key_code})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_phone_action(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            action = body.get("action", "")
            success = await self.phone_status_handler.send_phone_action(action)
            return web.json_response({"status": "ok" if success else "error", "action": action})
        except Exception as exc:
            return web.json_response({"status": "error", "error": str(exc)}, status=500)

    async def handle_notification_action(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            notif_id = body.get("id", "")
            action_id = body.get("action_id", "dismiss")
            success = await self.notification_handler.send_action(notif_id, action_id)
            return web.json_response({"status": "ok" if success else "error", "id": notif_id})
        except Exception as exc:
            return web.json_response({"status": "error", "error": str(exc)}, status=500)


if __name__ == "__main__":
    run_module(ChannelManagerModule)
