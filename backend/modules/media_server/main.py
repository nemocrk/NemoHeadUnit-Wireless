#!/usr/bin/env python3
"""
Web Browser Head Unit — Video Decoder Module

Cross-platform Priority 3 Microservice extending BaseBackendModule.

Responsibilities:
  1. Subscribes to ZMQ topic 'video.raw_nal' for H.264 NAL payloads from channel_manager.
  2. Routes each NAL through the active BaseVideoTransport strategy (decode + encode/convert).
  3. Publishes decoded frames to ZMQ topic 'video.transport_frame'.
  4. On WebSocket connect, receives client capabilities ('video.client_capabilities') and
     performs auto-negotiation to select the optimal transport for that client.
  5. Exposes config schema: transport_mode (enum), jpeg_quality (int), video_scale (string).
  6. Supports runtime hot-swap of transport mode via on_config_updated().

Transport Modes:
  auto          — Auto-negotiated per client connection (recommended default)
  h264          — Passthrough: raw H.264 NAL units, WebCodecs frontend
  mjpeg         — GStreamer HW decode → JPEG per-frame
  webp          — GStreamer HW decode → WebP per-frame
  mjpeg-ffmpeg  — FFmpeg subprocess → JPEG per-frame
  yuv420        — GStreamer HW decode → raw YUV420p, WebGL shader frontend
  rgba          — GStreamer HW decode → raw RGBA, putImageData frontend
"""

import asyncio
import base64
import json
import ipaddress
from typing import Any, Optional
import aiohttp
from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_enum, field_int, field_string
from shared.nal_utils import pack_media_frame

try:
    from modules.media_server.transports import (
        get_transport_class,
        get_available_modes,
        TransportUnavailableError,
    )
    from modules.media_server.transports.base import BaseVideoTransport
    from modules.media_server.diagnostic_routes import register_diagnostic_routes
except ImportError:
    from transports import get_transport_class, get_available_modes, TransportUnavailableError
    from transports.base import BaseVideoTransport
    from diagnostic_routes import register_diagnostic_routes


# Fallback chain for auto-negotiation when a transport fails
_LOOPBACK_FALLBACK_CHAIN = ["yuv420", "rgba", "mjpeg", "mjpeg-ffmpeg", "h264"]
_REMOTE_FALLBACK_CHAIN = ["webp", "mjpeg", "mjpeg-ffmpeg", "h264"]


def _is_loopback(remote_addr: str) -> bool:
    """Detect whether a client address is loopback (same machine as backend)."""
    if not remote_addr:
        return False
    host = remote_addr.split(":")[0].strip("[]")
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "::1", "127.0.0.1")


from shared.hardware.base_audio import get_audio_adapter
from shared.media_shm import BidirectionalMediaSHM
from shared.nal_utils import pack_media_frame

class MediaServerModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="media_server",
            priority=4,
            path_prefix="/api/media",
        )
        self._transport: Optional[BaseVideoTransport] = None
        self._transport_lock = asyncio.Lock()
        self._active_transport_name: str = "h264"
        self._active_video_codec: str = "H264"
        self.audio_adapter = get_audio_adapter()
        self.shm = BidirectionalMediaSHM(create=False)
        self.ws_clients: set = set()
        self._status_changed_evt = asyncio.Event()

    async def _handle_volume(self, request):
        from aiohttp import web
        if request.method == "POST":
            data = {}
            try:
                if request.can_read_body:
                    data = await request.json()
            except Exception:
                data = {}

            action = data.get("action") or request.query.get("action")
            step = int(data.get("step") or request.query.get("step") or 5)
            val = data.get("volume") or request.query.get("volume")

            if action == "up":
                res = await self.audio_adapter.volume_up(step)
            elif action == "down":
                res = await self.audio_adapter.volume_down(step)
            elif action == "mute":
                res = await self.audio_adapter.toggle_mute()
            elif val is not None:
                res = await self.audio_adapter.set_volume(int(val))
            else:
                res = await self.audio_adapter.get_volume()
            self._notify_status_changed()
            return web.json_response(res)
        else:
            res = await self.audio_adapter.get_volume()
            return web.json_response(res)

    async def handle_get_status(self, request: web.Request) -> web.Response:
        """REST endpoint GET /api/media/status."""
        vol_info = await self.audio_adapter.get_volume()
        diag = self._transport.get_diagnostics() if self._transport else {}
        return web.json_response({
            "module": "media_server",
            "active_transport": self._active_transport_name,
            "ws_clients_count": len(self.ws_clients),
            "volume": vol_info.get("volume", 80),
            "muted": vol_info.get("muted", False),
            "video_decoder_diagnostics": diag,
        })


    def _notify_status_changed(self) -> None:
        if hasattr(self, "_status_changed_evt") and self._status_changed_evt:
            self._status_changed_evt.set()

    async def _handle_stream_status(self, request):
        from aiohttp import web
        import json
        response = web.StreamResponse(
            status=200,
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )
        await response.prepare(request)

        if not hasattr(self, "_status_changed_evt") or self._status_changed_evt is None:
            self._status_changed_evt = asyncio.Event()

        try:
            while request.protocol and request.protocol.transport and not request.protocol.transport.is_closing():
                vol_info = await self.audio_adapter.get_volume()
                payload = {
                    "transport": self._active_transport_name,
                    "volume": vol_info.get("volume", 80),
                    "muted": vol_info.get("muted", False),
                }
                data = f"data: {json.dumps(payload)}\n\n"
                await response.write(data.encode('utf-8'))

                self._status_changed_evt.clear()
                try:
                    await asyncio.wait_for(self._status_changed_evt.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:
            if "is_closing" not in str(exc):
                self.log.warning(f"_handle_stream_status notice: {exc}")
        return response

    async def _handle_audio_devices(self, request: web.Request) -> web.Response:
        """REST endpoint GET/POST /api/media/audio_devices to list and select audio sinks/sources."""
        if request.method == "POST":
            data = {}
            try:
                if request.can_read_body:
                    data = await request.json()
            except Exception:
                pass
            sink = data.get("sink") or request.query.get("sink")
            source = data.get("source") or request.query.get("source")
            if sink is not None:
                await self.audio_adapter.set_active_sink(sink)
            if source is not None:
                await self.audio_adapter.set_active_source(source)
            self.publish("media.audio.sink_changed", {
                "sink": sink or self.config.get("audio_output_sink", "default"),
                "source": source or self.config.get("audio_input_source", "default"),
            })

        sinks = await self.audio_adapter.get_available_sinks()
        sources = await self.audio_adapter.get_available_sources()
        vol_info = await self.audio_adapter.get_volume()
        return web.json_response({
            "sinks": sinks,
            "sources": sources,
            "active_sink": self.config.get("audio_output_sink", "default"),
            "active_source": self.config.get("audio_input_source", "default"),
            "volume": vol_info.get("volume", 80),
            "muted": vol_info.get("muted", False),
        })

    # ------------------------------------------------------------------
    # Config Schema
    # ------------------------------------------------------------------

    def get_default_config(self) -> dict[str, Any]:
        return {
            "transport_mode": "auto",
            "jpeg_quality": 75,
            "video_scale": "",
            "audio_output_sink": "default",
            "audio_input_source": "default",
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "transport_mode": field_enum(
                default="auto",
                choices=["auto", "h264", "mjpeg", "webp", "mjpeg-ffmpeg", "yuv420", "rgba"],
            ),
            "jpeg_quality": field_int(default=75, min=50, max=95),
            "video_scale": field_string(default=""),
            "audio_output_sink": field_string(default="default"),
            "audio_input_source": field_string(default="default"),
        }

    def on_config_updated(self, new_config: dict[str, Any]) -> None:
        """Hot-swap transport mode and audio sinks when config changes at runtime."""
        configured_mode = new_config.get("transport_mode", "auto")
        if configured_mode != "auto":
            # Thread-safe schedule back to main loop without asyncio.get_event_loop()
            if hasattr(self, 'loop') and self.loop:
                asyncio.run_coroutine_threadsafe(
                    self._switch_transport(new_config.get("transport_mode")), 
                    self.loop
                )

        sink = new_config.get("audio_output_sink")
        source = new_config.get("audio_input_source")
        if (sink is not None or source is not None) and hasattr(self, 'loop') and self.loop:
            async def _apply_audio():
                if sink is not None:
                    await self.audio_adapter.set_active_sink(sink)
                if source is not None:
                    await self.audio_adapter.set_active_source(source)
                self.publish("media.audio.sink_changed", {
                    "sink": sink or "default",
                    "source": source or "default",
                })
            asyncio.run_coroutine_threadsafe(_apply_audio(), self.loop)

        self.log.info(f"MediaServer: Config updated — transport_mode={configured_mode}, "
                      f"sink='{sink}', source='{source}', "
                      f"jpeg_quality={new_config.get('jpeg_quality', 75)}, "
                      f"video_scale='{new_config.get('video_scale', '')}'")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Subscribe to ZMQ topics and initialize the default transport."""
        # REST endpoints for volume, devices, and status controls
        self.add_http_route("GET", "/volume", self._handle_volume)
        self.add_http_route("POST", "/volume", self._handle_volume)
        self.add_http_route("GET", "/audio_devices", self._handle_audio_devices)
        self.add_http_route("POST", "/audio_devices", self._handle_audio_devices)
        self.add_http_route("GET", "/status", self.handle_get_status)
        # Per-module SSE status stream endpoint
        self.add_http_route("GET", "/stream_status", self._handle_stream_status)
        # Unified Direct WebSocket Stream Endpoint for Frontend WebCodecsPlayer
        self.add_ws_route("/stream", self.handle_ws_stream)
        # Register diagnostic subsystem endpoints
        register_diagnostic_routes(self)

        # Initialize active audio sink/source from config
        initial_sink = self.config.get("audio_output_sink", "default")
        initial_source = self.config.get("audio_input_source", "default")
        if initial_sink and initial_sink != "default":
            await self.audio_adapter.set_active_sink(initial_sink)
        if initial_source and initial_source != "default":
            await self.audio_adapter.set_active_source(initial_source)

        self.subscribe("media.video.raw_nal_shm", self._on_raw_nal_shm)
        self.subscribe("media.audio.frame", self._on_audio_frame)
        self.subscribe("media.audio.mic_control", self._on_mic_control)
        self.subscribe("video.stream_start", self._on_video_stream_start)

        configured_mode = self.config.get("transport_mode", "auto")
        if configured_mode == "auto":
            # Default to h264 passthrough until a client connects and negotiates
            await self._switch_transport("h264")
        else:
            await self._switch_transport(configured_mode)

        available = get_available_modes()
        self.log.info(
            f"VideoDecoder: Available transport modes on this platform: {available}"
        )

    def _on_video_stream_start(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        """Handle video.stream_start event to dynamically reconfigure video transport for negotiated codec."""
        data = payload if payload is not None else topic_or_payload
        if not isinstance(data, dict):
            return

        codec = data.get("codec", "MEDIA_CODEC_VIDEO_H264_BP")
        short_codec = "H264"
        c = str(codec).upper()
        if "H265" in c or "HEVC" in c:
            short_codec = "H265"
        elif "VP9" in c:
            short_codec = "VP9"
        elif "AV1" in c:
            short_codec = "AV1"

        if short_codec != self._active_video_codec:
            self.log.info(f"MediaServer: Negotiated video codec '{short_codec}' — reconfiguring video transport")
            self._active_video_codec = short_codec
            asyncio.create_task(self._switch_transport(self._active_transport_name))

    async def run(self) -> None:
        self.log.info(
            f"VideoDecoder active — transport='{self._active_transport_name}', "
            f"codec='{self._active_video_codec}', "
            f"quality={self.config.get('jpeg_quality', 75)}, "
            f"scale='{self.config.get('video_scale', '') or 'native'}'"
        )
        while self._running:
            await asyncio.sleep(1.0)

    async def teardown(self) -> None:
        await self._stop_transport()

    # ------------------------------------------------------------------
    # Transport Management
    # ------------------------------------------------------------------

    async def _switch_transport(self, mode: str) -> bool:
        """
        Tear down the current transport and start a new one for the given mode.
        Returns True on success, False if the mode is unavailable.
        Falls back through the chain automatically.
        """
        async with self._transport_lock:
            await self._stop_transport()

            quality = self.config.get("jpeg_quality", 75)
            scale = self.config.get("video_scale", "")

            for candidate_mode in self._resolve_fallback_chain(mode):
                try:
                    transport_cls = get_transport_class(candidate_mode)
                    if not transport_cls.is_available():
                        self.log.warning(
                            f"VideoDecoder: Transport '{candidate_mode}' not available on this platform — skipping"
                        )
                        continue

                    transport = transport_cls(
                        jpeg_quality=quality,
                        video_scale=scale,
                        video_codec=self._active_video_codec,
                    )
                    transport.on_frame_ready = self._on_frame_ready
                    await transport.start()

                    self._transport = transport
                    self._active_transport_name = candidate_mode

                    if candidate_mode != mode:
                        self.log.warning(
                            f"VideoDecoder: Requested mode '{mode}' unavailable — "
                            f"fell back to '{candidate_mode}'"
                        )
                    else:
                        self.log.info(f"VideoDecoder: Transport '{candidate_mode}' started successfully")

                    diag = transport.get_diagnostics()
                    self.log.info(
                        f"🎬 VideoDecoder Diagnostics — Mode: '{candidate_mode}' | "
                        f"Element: '{diag.get('decoder_element', 'unknown')}' | "
                        f"Acceleration: {diag.get('decoder_type', 'unknown')}"
                    )

                    # Announce the active transport to channel_manager and WebSocket clients
                    self.publish("media.video.transport_active", {
                        "transport_name": candidate_mode,
                        "wire_format": transport.wire_format,
                    })

                    if self.ws_clients:
                        msg_json = json.dumps({
                            "type": "stream_config",
                            "video_transport": candidate_mode,
                        })
                        for ws in list(self.ws_clients):
                            try:
                                await ws.send_str(msg_json)
                            except Exception:
                                pass

                    return True

                except TransportUnavailableError as exc:
                    self.log.warning(f"VideoDecoder: Transport '{candidate_mode}' failed to start: {exc}")
                    continue
                except Exception as exc:
                    self.log.error(f"VideoDecoder: Unexpected error starting '{candidate_mode}': {exc}", exc_info=True)
                    continue

            self.log.error(
                f"VideoDecoder: All fallback transports exhausted for mode '{mode}'. "
                "No video transport is active."
            )
            return False

    def _resolve_fallback_chain(self, mode: str) -> list[str]:
        """Build the ordered list of modes to try, starting from the requested one."""
        if mode == "yuv420" or mode == "rgba":
            chain = _LOOPBACK_FALLBACK_CHAIN
        else:
            chain = _REMOTE_FALLBACK_CHAIN

        # Always try the explicitly requested mode first
        ordered = [mode]
        for m in chain:
            if m != mode:
                ordered.append(m)
        return ordered

    async def _stop_transport(self) -> None:
        if self._transport:
            try:
                await self._transport.stop()
            except Exception as exc:
                self.log.warning(f"VideoDecoder: Error stopping transport '{self._active_transport_name}': {exc}")
            self._transport = None

    # ------------------------------------------------------------------
    # WebSocket Streaming Endpoint
    # ------------------------------------------------------------------

    async def handle_ws_stream(self, request: web.Request) -> web.WebSocketResponse:
        """Unified WebSocket stream pushing video frames directly to WebCodecsPlayer."""
        ws = web.WebSocketResponse(protocols=("binary",))
        await ws.prepare(request)
        self.ws_clients.add(ws)

        self.log.info("Frontend WebCodecs WS connected directly to media_server from %s", request.remote)

        # Transmit current dynamic stream_config metadata from channel_manager immediately on connection
        try:
            config_msg = await self.call_module("channel_manager", "GET", "/api/channels/status")
            if not config_msg or "stream_config" not in config_msg or "streams" not in config_msg["stream_config"]:
                raise Exception("No stream_config found")
            else:
                config_msg["stream_config"]["type"] = "stream_config"
                config_msg["stream_config"]["video_transport"] = self._active_transport_name

            await ws.send_str(json.dumps(config_msg["stream_config"]))
            self.log.info(f"📹 Sent dynamic stream_config to WS client {request.remote} (transport: {self._active_transport_name}, {len(config_msg.get('stream_config', {}).get('streams', {}))} streams)")
        except Exception as exc:
            self.log.warning(f"Failed to send initial stream_config to WS client: {exc}")
            # Fallback descriptor
            config_msg = {
                "stream_config": {
                    "type": "stream_config",
                    "video_transport": self._active_transport_name,
                    "streams": {
                        "3": {"media_type": "VIDEO", "codec": "avc1.42E01E", "codec_enum": 3, "codec_name": "MEDIA_CODEC_VIDEO_H264_BP", "width": 1280, "height": 720, "fps": 30}, 
                        "4": {"media_type": "AUDIO", "codec": "mp4a.40.2", "audio_format": "aac_adts", "description": [17, 144], "codec_enum": 4, "codec_name": "MEDIA_CODEC_AUDIO_AAC_LC_ADTS", "sampleRate": 48000, "channels": 2, "bitDepth": 16, "audioType": "MEDIA"}, 
                        "5": {"media_type": "AUDIO", "codec": "PCM", "audio_format": "pcm", "codec_enum": 1, "codec_name": "MEDIA_CODEC_AUDIO_PCM", "sampleRate": 16000, "channels": 1, "bitDepth": 16, "audioType": "SPEECH"}, 
                        "6": {"media_type": "AUDIO", "codec": "PCM", "audio_format": "pcm", "codec_enum": 1, "codec_name": "MEDIA_CODEC_AUDIO_PCM", "sampleRate": 16000, "channels": 1, "bitDepth": 16, "audioType": "SYSTEM"}
                    }
                }
            }
            await ws.send_str(json.dumps(config_msg["stream_config"]))
            self.log.info(f"📹 Sent fallback stream_config to WS client {request.remote} (transport: {self._active_transport_name}, {len(config_msg.get('stream_config', {}).get('streams', {}))} streams)")

        # Request video focus from phone via ZMQ bus
        self.publish("media.video.request_focus", {"sender": "media_server"})

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        parsed = json.loads(msg.data)
                        if parsed.get("type") == "client_capabilities":
                            parsed["remote_addr"] = request.remote or ""
                            await self._on_client_capabilities(parsed)
                    except Exception:
                        pass
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Upstream Mic audio frame from browser client
                    pcm_bytes = msg.data
                    if len(pcm_bytes) > 0:
                        offset = self.shm.upstream.write_frame(1, 0, pcm_bytes)
                        self.publish("media.audio.mic_shm", {"shm_offset": offset, "len": len(pcm_bytes)})
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.log.error("WS error: %s", ws.exception())
        finally:
            self.ws_clients.discard(ws)
            self.log.info(f"Frontend WebCodecs WS disconnected from media_server {request.remote}")
            if len(self.ws_clients) == 0:
                self.publish("media.video.release_focus", {"sender": "media_server"})
        return ws

    async def _on_mic_control(self, payload: dict) -> None:
        """Receive mic enable/disable command from av_input_handler and relay JSON to WS clients."""
        enabled = payload.get("enabled", False)
        msg_json = json.dumps({"type": "mic_control", "enabled": enabled})
        for ws in list(self.ws_clients):
            try:
                await ws.send_str(msg_json)
            except Exception:
                pass

    async def broadcast_ws_media(self, binary_data: bytes) -> None:
        """Broadcast binary frame directly to all connected WebSocket clients."""
        if not self.ws_clients or not binary_data:
            return

        stale = set()
        for ws in list(self.ws_clients):
            try:
                await ws.send_bytes(binary_data)
            except Exception:
                stale.add(ws)

        for ws in stale:
            self.ws_clients.discard(ws)

    # ------------------------------------------------------------------
    # ZMQ Bus & SHM Callbacks
    # ------------------------------------------------------------------

    async def _on_audio_frame(self, payload: dict) -> None:
        """Receive audio frame from channel_manager, write to SHM downstream ring buffer, and broadcast to WS clients."""
        try:
            payload_b64 = payload.get("payload_b64", "")
            if not payload_b64:
                return
            binary_data = base64.b64decode(payload_b64)
            if len(binary_data) >= 9:
                from shared.nal_utils import unpack_media_frame
                channel_id, timestamp_us, pcm_payload = unpack_media_frame(binary_data)
                # Write to SHM with preserved channel_id
                shm_offset = self.shm.downstream.write_frame(channel_id, timestamp_us, pcm_payload)
                if shm_offset >= 0:
                    self.publish("media.audio.frame_shm", {
                        "shm_offset": shm_offset,
                        "len": len(pcm_payload),
                        "timestamp_us": timestamp_us,
                        "channel_id": channel_id,
                    })

            await self.broadcast_ws_media(binary_data)
        except Exception as exc:
            self.log.debug(f"MediaServer: Error relaying audio frame: {exc}")


    async def _on_raw_nal_shm(self, payload: dict) -> None:
        """Receive H.264 NAL bytes zero-copy off SHM (nemo_video_transcode_in) from channel_manager."""
        if not self._transport:
            return

        try:
            offset = payload.get("shm_offset", -1)
            timestamp_us = payload.get("timestamp_us", 0)
            if offset < 0:
                return

            _, _, nal_data = self.shm.transcode_in.read_frame(offset)
            if nal_data:
                await self._transport.feed_nal(nal_data, timestamp_us)
        except Exception as exc:
            self.log.debug(f"VideoDecoder: Error reading NAL from SHM: {exc}")

    async def _on_client_capabilities(self, payload: dict) -> None:
        """
        Receive client capability report from channel_manager and perform auto-negotiation.

        Called when a new WebSocket client connects and sends its capabilities.
        Only triggers re-negotiation if transport_mode is set to 'auto'.
        """
        if self.config.get("transport_mode", "auto") != "auto":
            return

        remote_addr = payload.get("remote_addr", "")
        webcodecs_h264_hw = payload.get("webcodecs_h264_hw", False)
        webgl = payload.get("webgl", False)

        negotiated = self._negotiate_transport(
            remote_addr=remote_addr,
            webcodecs_h264_hw=webcodecs_h264_hw,
            webgl=webgl,
        )

        self.log.info(
            f"VideoDecoder: Auto-negotiation for {remote_addr} — "
            f"hw_decode={webcodecs_h264_hw}, webgl={webgl}, loopback={_is_loopback(remote_addr)} "
            f"→ selected '{negotiated}'"
        )

        if negotiated != self._active_transport_name:
            await self._switch_transport(negotiated)

    def _negotiate_transport(
        self,
        remote_addr: str,
        webcodecs_h264_hw: bool,
        webgl: bool,
    ) -> str:
        """
        Auto-negotiation decision tree:
          1. Client has WebCodecs HW H.264 decode → h264 (zero backend work)
          2. Client is loopback + WebGL → yuv420 (lowest latency, GPU shader)
          3. Client is loopback (no WebGL) → rgba (raw pixels, putImageData)
          4. Remote client → webp (best bandwidth) or mjpeg (universal fallback)
        """
        available = get_available_modes()

        if webcodecs_h264_hw:
            return "h264"

        if _is_loopback(remote_addr):
            if webgl and "yuv420" in available:
                return "yuv420"
            if "rgba" in available:
                return "rgba"
            if "mjpeg" in available:
                return "mjpeg"
            if "mjpeg-ffmpeg" in available:
                return "mjpeg-ffmpeg"
            return "h264"

        # Remote client
        if "webp" in available:
            return "webp"
        if "mjpeg" in available:
            return "mjpeg"
        if "mjpeg-ffmpeg" in available:
            return "mjpeg-ffmpeg"
        return "h264"

    # ------------------------------------------------------------------
    # Frame Output Callback
    # ------------------------------------------------------------------

    async def _on_frame_ready(self, frame_bytes: bytes, timestamp_us: int, wire_format: str) -> None:
        """
        Called by active transport when a decoded/encoded frame is ready.
        Writes frame to SHM downstream ring buffer and broadcasts to WS clients.
        """
        if not frame_bytes:
            return

        # Write video frame to SHM downstream ring buffer (stream_type 4 = VIDEO)
        shm_offset = self.shm.downstream.write_frame(4, timestamp_us, frame_bytes)
        if shm_offset >= 0:
            self.publish("media.video.transport_frame_shm", {
                "shm_offset": shm_offset,
                "len": len(frame_bytes),
                "timestamp_us": timestamp_us,
                "wire_format": wire_format,
            })

        # Pack binary frame with video channel_id=3 (ChannelType.VIDEO) for WebSocket clients
        binary_frame = pack_media_frame(3, timestamp_us, frame_bytes)
        await self.broadcast_ws_media(binary_frame)


if __name__ == "__main__":
    run_module(MediaServerModule)

