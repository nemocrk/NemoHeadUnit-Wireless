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
import ipaddress
from typing import Any, Optional

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_enum, field_int, field_string
from shared.nal_utils import pack_media_frame

try:
    from modules.video_decoder.transports import (
        get_transport_class,
        get_available_modes,
        TransportUnavailableError,
    )
    from modules.video_decoder.transports.base import BaseVideoTransport
except ImportError:
    from transports import get_transport_class, get_available_modes, TransportUnavailableError
    from transports.base import BaseVideoTransport


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


class VideoDecoderModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="video_decoder",
            priority=3,
        )
        self._transport: Optional[BaseVideoTransport] = None
        self._transport_lock = asyncio.Lock()
        # Effective transport name (may differ from config if auto-negotiated)
        self._active_transport_name: str = "h264"

    # ------------------------------------------------------------------
    # Config Schema
    # ------------------------------------------------------------------

    def get_default_config(self) -> dict[str, Any]:
        return {
            "transport_mode": "auto",
            "jpeg_quality": 75,
            "video_scale": "",
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "transport_mode": field_enum(
                default="auto",
                choices=["auto", "h264", "mjpeg", "webp", "mjpeg-ffmpeg", "yuv420", "rgba"],
            ),
            "jpeg_quality": field_int(default=75, min=50, max=95),
            "video_scale": field_string(default=""),
        }

    def on_config_updated(self, new_config: dict[str, Any]) -> None:
        """Hot-swap transport mode when config changes at runtime."""
        configured_mode = new_config.get("transport_mode", "auto")
        if configured_mode != "auto":
            # Explicit mode change — re-initialize transport
            asyncio.get_event_loop().create_task(
                self._switch_transport(configured_mode)
            )
        self.log.info(f"VideoDecoder: Config updated — transport_mode={configured_mode}, "
                      f"jpeg_quality={new_config.get('jpeg_quality', 75)}, "
                      f"video_scale='{new_config.get('video_scale', '')}'")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Subscribe to ZMQ topics and initialize the default transport."""
        self.subscribe("video.raw_nal", self._on_raw_nal)
        self.subscribe("video.client_capabilities", self._on_client_capabilities)

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

    async def run(self) -> None:
        self.log.info(
            f"VideoDecoder active — transport='{self._active_transport_name}', "
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

                    transport = transport_cls(jpeg_quality=quality, video_scale=scale)
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

                    # Announce the active transport to channel_manager
                    self.publish("video.transport_active", {
                        "transport_name": candidate_mode,
                        "wire_format": transport.wire_format,
                    })
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
    # ZMQ Bus Callbacks
    # ------------------------------------------------------------------

    async def _on_raw_nal(self, payload: dict) -> None:
        """Receive H.264 NAL bytes from channel_manager and feed into active transport."""
        if not self._transport:
            return

        try:
            nal_b64 = payload.get("payload_b64", "")
            timestamp_us = payload.get("timestamp_us", 0)
            if not nal_b64:
                return
            nal_data = base64.b64decode(nal_b64)
            if nal_data:
                await self._transport.feed_nal(nal_data, timestamp_us)
        except Exception as exc:
            self.log.debug(f"VideoDecoder: Error feeding NAL to transport: {exc}")

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
        Called by the active transport when a decoded/encoded frame is ready.
        Publishes the frame to channel_manager via the ZMQ bus.
        """
        if not frame_bytes:
            return

        # Use channel_id=0 here; channel_manager will use the real video channel_id
        # when it receives this and calls pack_media_frame before broadcasting.
        self.publish("video.transport_frame", {
            "wire_format": wire_format,
            "timestamp_us": timestamp_us,
            "payload_b64": base64.b64encode(frame_bytes).decode(),
        })


if __name__ == "__main__":
    run_module(VideoDecoderModule)
