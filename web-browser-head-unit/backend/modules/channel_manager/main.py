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

AV_MEDIA_WITH_TIMESTAMP_INDICATION = 0x0001
AV_MEDIA_INDICATION = 0x0002


class ControlChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager
        self.log = manager.log

    async def handle_frame(self, message_id: int, body: bytes) -> None:
        self.log.debug("ControlChannel (ch0) msgId=0x%04x len=%d", message_id, len(body))
        if message_id == 0x0001:  # Version Request
            # Respond with Version Response (0x0002)
            resp = struct.pack(">H H H", 0x0002, 1, 1)  # Version 1.1 OK
            await self.manager.send_wire_frame(0, 0x0002, resp)
        elif message_id == 0x0005:  # Service Discovery Request
            self.log.info("Received Service Discovery Request from phone")


class VideoChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager

    async def process_shm_frame(self, message_id: int, payload: bytes) -> None:
        ts_us = 0
        codec_payload = b""

        if message_id == AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            ts_us, codec_payload = parse_media_with_timestamp(payload)
        elif message_id == AV_MEDIA_INDICATION:
            codec_payload = payload
        else:
            return

        if not codec_payload:
            return

        binary_frame = pack_media_frame(STREAM_TYPE_VIDEO, ts_us, codec_payload)
        await self.manager.broadcast_ws_media(binary_frame)


class AudioChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager

    async def process_shm_frame(self, message_id: int, payload: bytes) -> None:
        ts_us = 0
        audio_payload = b""

        if message_id == AV_MEDIA_WITH_TIMESTAMP_INDICATION:
            ts_us, audio_payload = parse_media_with_timestamp(payload)
        elif message_id == AV_MEDIA_INDICATION:
            audio_payload = payload
        else:
            return

        if not audio_payload:
            return

        binary_frame = pack_media_frame(STREAM_TYPE_AUDIO, ts_us, audio_payload)
        await self.manager.broadcast_ws_media(binary_frame)


class InputChannelHandler:
    def __init__(self, manager: "ChannelManagerModule"):
        self.manager = manager

    async def handle_touch_event(self, x: int, y: int, action: int) -> None:
        """Encode AA Input Event message and send via channel 4."""
        # Simple InputReport payload
        payload = struct.pack(">H H H", action, x, y)
        await self.manager.send_wire_frame(4, 0x0001, payload)


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

    def get_default_config(self) -> dict[str, Any]:
        return {
            "autoclose_on_shutdown": True,
        }

    async def setup(self) -> None:
        """Register REST and WebSocket endpoints, ZMQ topic subscriptions."""
        self.add_http_route("GET", "/api/channels/status", self.handle_get_status)
        self.add_http_route("POST", "/api/channels/input/touch", self.handle_post_touch)
        self.add_ws_route("/stream", self.handle_ws_stream)

        # Bus subscriptions
        self.subscribe("aa.frame.shm", self.on_frame_shm)
        self.subscribe("aa.frame.ch0", self.on_ch0_frame)
        self.subscribe("aa.sdr.response", self.on_sdr_response)

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

    async def on_sdr_response(self, data: dict) -> None:
        sdr_hex = data.get("sdr_hex", "")
        if sdr_hex:
            parsed = channels_from_sdr_bytes(sdr_hex)
            self.active_channels = {ch["channel_id"]: ch for ch in parsed if "channel_id" in ch}
            self.log.info("SDR registered %d channels", len(self.active_channels))

    async def send_wire_frame(self, channel_id: int, message_id: int, body: bytes) -> None:
        """Publish outgoing frame to aa.frame.send."""
        payload = struct.pack(">H", message_id) + body
        frame_dict = {
            "channel_id": channel_id,
            "flags": 0x0B,
            "payload_hex": payload.hex(),
        }
        await self.publish("aa.frame.send", frame_dict)

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
