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
        InputChannelHandler,
        SensorChannelHandler,
        BluetoothChannelHandler,
        WifiChannelHandler,
    )
except ImportError:
    from handlers import (
        ControlChannelHandler,
        VideoChannelHandler,
        AudioChannelHandler,
        InputChannelHandler,
        SensorChannelHandler,
        BluetoothChannelHandler,
        WifiChannelHandler,
    )

from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage

MSG = ControlMessage.Enum

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

        # Sub-channel handlers
        self.control_handler = ControlChannelHandler(self)
        self.video_handler = VideoChannelHandler(self)
        self.audio_handler = AudioChannelHandler(self)
        self.input_handler = InputChannelHandler(self)
        self.sensor_handler = SensorChannelHandler(self)
        self.bluetooth_handler = BluetoothChannelHandler(self)
        self.wifi_handler = WifiChannelHandler(self)

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
        self.add_http_route("POST", "/api/channels/input/touch", self.handle_post_touch)
        self.add_ws_route("/stream", self.handle_ws_stream)

        # Bus subscriptions
        self.subscribe("aa.frame.shm", self.on_frame_shm)
        self.subscribe("aa.frame.ch0", self.on_ch0_frame)
        self.subscribe("aa.frame.received", self.on_frame_received)
        self.subscribe("aa.sdr.response", self.on_sdr_response)
        self.subscribe("tcp.session.connected", self.on_tcp_session_connected)
        self.subscribe("tcp.server.tls_handshake_completed", self.on_tls_handshake_completed)

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
        self.log.info(f"🌐 [TCP Stage 4/5] Phone TCP session connected from {address} — HU sending VERSION_REQUEST...")
        version_payload = struct.pack(">H H", 1, 1)  # Major=1, Minor=1
        await self.send_wire_frame(0, MSG.VERSION_REQUEST, version_payload)

    async def on_tls_handshake_completed(self, data: dict) -> None:
        """Send AUTH_COMPLETE to phone upon TLS completion, then await SERVICE_DISCOVERY_REQUEST."""
        self.log.info("🔒 [TLS Stage 5/5] TLS handshake complete — HU sending AUTH_COMPLETE to phone...")
        auth_payload = b"\x08\x00"  # AuthCompleteIndication (status = STATUS_OK)
        await self.send_wire_frame(0, MSG.AUTH_COMPLETE, auth_payload, encrypted=False)

    async def on_frame_shm(self, data: dict) -> None:
        """Received lightweight notification for media frame written to SHM."""
        ch_id = data.get("channel_id")
        msg_id = data.get("message_id")
        offset = data.get("shm_offset", -1)

        if offset < 0:
            return

        stream_type, ts_low, payload = self.shm.downstream.read_frame(offset)
        if not payload:
            return

        if ch_id == 2:
            await self.video_handler.process_shm_frame(msg_id, payload)
        elif ch_id in (3, 4):
            await self.audio_handler.process_shm_frame(msg_id, payload)

    async def on_ch0_frame(self, data: dict) -> None:
        """Handle Channel 0 Control messages."""
        msg_id = data.get("message_id")
        payload_hex = data.get("payload_hex", "")
        body = bytes.fromhex(payload_hex) if payload_hex else b""
        await self.control_handler.handle_frame(msg_id, body)

    async def on_frame_received(self, data: dict) -> None:
        """Handle non-Channel 0 frames (Sensor, Bluetooth, WiFi, etc.)."""
        ch_id = data.get("channel_id", 0)
        msg_id = data.get("message_id", 0)
        payload_hex = data.get("payload_hex", "")
        body = bytes.fromhex(payload_hex) if payload_hex else b""

        if ch_id == 2:
            await self.sensor_handler.handle_frame(ch_id, msg_id, body)
        elif ch_id == 8:
            await self.bluetooth_handler.handle_frame(ch_id, msg_id, body)
        elif ch_id == 14:
            await self.wifi_handler.handle_frame(ch_id, msg_id, body)

    async def on_sdr_response(self, data: dict) -> None:
        sdr_hex = data.get("sdr_hex", "")
        if sdr_hex:
            parsed = channels_from_sdr_bytes(sdr_hex)
            self.active_channels = {ch["channel_id"]: ch for ch in parsed if "channel_id" in ch}
            self.log.info("SDR registered %d channels", len(self.active_channels))

    async def send_wire_frame(self, channel_id: int, message_id: int, body: bytes, encrypted: bool = False) -> None:
        """Publish outgoing frame to aa.frame.send."""
        frame_dict = {
            "channel_id": channel_id,
            "message_id": message_id,
            "flags": 0x0B,
            "payload_hex": body.hex(),
            "encrypted": encrypted,
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

    # ------------------------------------------------------------------
    # REST & WebSocket Endpoints
    # ------------------------------------------------------------------

    async def handle_ws_stream(self, request: web.Request) -> web.WebSocketResponse:
        """Unified WebSocket stream multiplexing Video (0) and Audio (1) arraybuffers."""
        ws = web.WebSocketResponse(protocols=("binary",))
        await ws.prepare(request)
        self.ws_clients.add(ws)
        self.log.info("Frontend WebCodecs WS connected from %s", request.remote)

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # Upstream Mic audio frame from browser
                    data = msg.data
                    if len(data) > 0:
                        offset = self.shm.upstream.write_frame(1, 0, data)
                        self.publish("aa.mic.shm", {"shm_offset": offset, "len": len(data)})
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.log.error("WS error: %s", ws.exception())
        finally:
            self.ws_clients.discard(ws)
            self.log.info("Frontend WebCodecs WS disconnected from %s", request.remote)

        return ws

    async def handle_get_status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
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
            await self.input_handler.handle_touch_event(x, y, action)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


if __name__ == "__main__":
    run_module(ChannelManagerModule)
