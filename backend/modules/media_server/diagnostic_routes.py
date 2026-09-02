"""
diagnostic_routes.py — REST routes and handlers for media_server diagnostic subsystem.
Allows point-by-point testing of Audio & Video pipelines directly on media_server.
"""

import asyncio
import json
import math
import shutil
import struct
import sys
import time
from typing import Any, Dict, Optional
from aiohttp import web

try:
    from modules.media_server.transports import get_available_modes
except ImportError:
    try:
        from transports import get_available_modes
    except ImportError:
        get_available_modes = lambda: ["h264", "mjpeg", "webp", "yuv420", "rgba"]


def _generate_pcm_sine(freq_hz: float = 440.0, duration_ms: int = 1000, sample_rate: int = 48000, channels: int = 2, amplitude: float = 0.5) -> bytes:
    """Generate 16-bit signed PCM sine wave."""
    total_samples = int(sample_rate * (duration_ms / 1000.0))
    max_amp = int(32767 * max(0.0, min(1.0, amplitude)))
    buffer = bytearray()

    for i in range(total_samples):
        t = float(i) / float(sample_rate)
        val = int(max_amp * math.sin(2.0 * math.pi * freq_hz * t))
        val = max(-32768, min(32767, val))
        sample_bytes = struct.pack("<h", val)
        for _ in range(channels):
            buffer.extend(sample_bytes)

    return bytes(buffer)


def _detect_video_decoders() -> list[dict[str, Any]]:
    """Probe for hardware and software H.264 decoders on current platform."""
    decoders = []
    
    # Try GStreamer ElementFactory if available
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)

        candidates = [
            ("vah264dec", "Hardware (VA-API vah264dec)", "linux"),
            ("vaapih264dec", "Hardware (VAAPI legacy)", "linux"),
            ("v4l2slh264dec", "Hardware (V4L2 Stateless)", "linux"),
            ("v4l2h264dec", "Hardware (V4L2 Stateful)", "linux"),
            ("nvh264dec", "Hardware (NVDEC)", "all"),
            ("d3d11h264dec", "Hardware (Direct3D 11)", "windows"),
            ("vtdec_hw", "Hardware (VideoToolbox)", "darwin"),
            ("avdec_h264", "Software (FFmpeg libavcodec)", "all"),
        ]

        for elem_name, desc, plat in candidates:
            factory = Gst.ElementFactory.find(elem_name)
            if factory:
                decoders.append({
                    "element": elem_name,
                    "description": desc,
                    "available": True,
                    "is_hardware": "Hardware" in desc,
                })
    except Exception:
        # Fallback to shutil.which or known platform defaults
        if sys.platform.startswith("linux"):
            decoders.append({"element": "v4l2slh264dec", "description": "Hardware (V4L2)", "available": shutil.which("v4l2-ctl") is not None, "is_hardware": True})
            decoders.append({"element": "vaapih264dec", "description": "Hardware (VAAPI)", "available": shutil.which("vainfo") is not None, "is_hardware": True})
        elif sys.platform == "win32":
            decoders.append({"element": "d3d11h264dec", "description": "Hardware (Direct3D 11)", "available": True, "is_hardware": True})
        decoders.append({"element": "avdec_h264", "description": "Software (FFmpeg)", "available": shutil.which("ffmpeg") is not None, "is_hardware": False})

    return decoders


def register_diagnostic_routes(media_module) -> None:
    """Register all /diagnostic/* endpoints on media_server."""

    async def handle_capabilities(request: web.Request) -> web.Response:
        """GET /api/media/diagnostic/capabilities"""
        sinks = []
        sources = []
        if hasattr(media_module, "audio_adapter") and media_module.audio_adapter:
            try:
                sinks = await media_module.audio_adapter.get_available_sinks()
                sources = await media_module.audio_adapter.get_available_sources()
            except Exception as exc:
                media_module.log.warning(f"Diagnostic: Error getting audio devices: {exc}")

        return web.json_response({
            "status": "ok",
            "audio": {
                "sinks": sinks,
                "sources": sources,
                "active_sink": media_module.config.get("audio_output_sink", "default"),
                "active_source": media_module.config.get("audio_input_source", "default"),
            },
            "video": {
                "active_transport": getattr(media_module, "_active_transport_name", "auto"),
                "available_transports": get_available_modes(),
                "decoders": _detect_video_decoders(),
            },
            "platform": sys.platform,
        })

    async def handle_audio_inject(request: web.Request) -> web.Response:
        """POST /api/media/diagnostic/audio/inject"""
        data = {}
        try:
            if request.can_read_body:
                data = await request.json()
        except Exception:
            pass

        fmt = data.get("format", "pcm").lower()
        freq = float(data.get("tone_hz", 440.0))
        duration_ms = int(data.get("duration_ms", 1000))
        rate = int(data.get("sample_rate", 48000))
        channels = int(data.get("channels", 2))
        channel_id = int(data.get("channel_id", 4))  # 4 = MEDIA Audio channel

        if fmt == "pcm":
            pcm_bytes = _generate_pcm_sine(freq, duration_ms, rate, channels, amplitude=0.75)

            # Write directly to downstream audio SHM if available
            offset = -1
            if hasattr(media_module, "shm") and media_module.shm:
                try:
                    shm_buf = media_module.shm.get_downstream_channel(channel_id, size=8 * 1024 * 1024)
                    offset = shm_buf.write_frame(channel_id, 0, pcm_bytes)
                except Exception as exc:
                    media_module.log.warning(f"Diagnostic SHM audio write notice: {exc}")

            # Notify GUI and bus consumers
            media_module.publish("media.audio.frame_shm", {
                "channel_id": channel_id,
                "shm_offset": offset,
                "len": len(pcm_bytes),
                "sample_rate": rate,
                "channels": channels,
                "format": "pcm",
                "synthetic": True,
            })
            media_module.publish("media.audio.frame", {
                "channel_id": channel_id,
                "len": len(pcm_bytes),
                "format": "pcm",
                "synthetic": True,
            })

            return web.json_response({
                "status": "ok",
                "format": "pcm",
                "bytes_generated": len(pcm_bytes),
                "duration_ms": duration_ms,
                "channel_id": channel_id,
            })

        elif fmt == "aac":
            # Reference silence/chime AAC frame or notify AAC test trigger
            media_module.publish("media.audio.frame", {
                "channel_id": channel_id,
                "format": "aac",
                "synthetic": True,
                "duration_ms": duration_ms,
            })
            return web.json_response({
                "status": "ok",
                "format": "aac",
                "duration_ms": duration_ms,
            })

        return web.json_response({"status": "error", "message": f"Unsupported format: {fmt}"}, status=400)

    async def handle_audio_set_device(request: web.Request) -> web.Response:
        """POST /api/media/diagnostic/audio/set_device"""
        data = {}
        try:
            if request.can_read_body:
                data = await request.json()
        except Exception:
            pass

        sink = data.get("sink")
        source = data.get("source")

        if hasattr(media_module, "audio_adapter") and media_module.audio_adapter:
            if sink:
                await media_module.audio_adapter.set_active_sink(sink)
            if source:
                await media_module.audio_adapter.set_active_source(source)

        media_module.publish("media.audio.sink_changed", {
            "sink": sink or media_module.config.get("audio_output_sink", "default"),
            "source": source or media_module.config.get("audio_input_source", "default"),
            "diagnostic_override": True,
        })

        return web.json_response({
            "status": "ok",
            "active_sink": sink,
            "active_source": source,
        })

    async def handle_video_benchmark(request: web.Request) -> web.Response:
        """POST /api/media/diagnostic/video/benchmark"""
        data = {}
        try:
            if request.can_read_body:
                data = await request.json()
        except Exception:
            pass

        target_transport = data.get("transport", "mjpeg")
        target_decoder = data.get("decoder", "auto")
        duration_sec = float(data.get("duration_sec", 2.0))
        target_fps = int(data.get("fps", 30))

        # Switch transport if needed
        prev_transport = getattr(media_module, "_active_transport_name", "auto")
        if target_transport != prev_transport:
            await media_module._switch_transport(target_transport)

        # Retrieve transport diagnostics
        diag = {}
        if hasattr(media_module, "_transport") and media_module._transport:
            try:
                diag = media_module._transport.get_diagnostics()
            except Exception:
                pass

        return web.json_response({
            "status": "ok",
            "transport": target_transport,
            "requested_decoder": target_decoder,
            "duration_sec": duration_sec,
            "target_fps": target_fps,
            "diagnostics": diag,
        })

    # Register endpoints
    media_module.add_http_route("GET", "/diagnostic/capabilities", handle_capabilities)
    media_module.add_http_route("POST", "/diagnostic/audio/inject", handle_audio_inject)
    media_module.add_http_route("POST", "/diagnostic/audio/set_device", handle_audio_set_device)
    media_module.add_http_route("POST", "/diagnostic/video/benchmark", handle_video_benchmark)
