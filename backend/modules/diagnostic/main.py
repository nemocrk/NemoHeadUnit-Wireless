#!/usr/bin/env python3
"""
Web Browser Head Unit — Multimedia Diagnostic Module

Cross-platform Priority 5 Microservice extending BaseBackendModule.
Provides point-by-point Audio and Video pipeline diagnostics and benchmarks.
"""

import asyncio
import json
import logging
import sys
import time
from typing import Any, Dict, Optional, Set
import aiohttp
from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_bool, field_int, field_string
from modules.diagnostic.synthetic_media import (
    generate_pcm_tone,
    calculate_audio_rms,
    generate_reference_adts_aac,
    generate_synthetic_h264_frame,
)


class DiagnosticModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="diagnostic",
            priority=5,
            path_prefix="/api/diagnostic",
        )
        self.ws_clients: Set[web.WebSocketResponse] = set()
        self._active_test: Optional[str] = None
        self._test_task: Optional[asyncio.Task] = None
        self._last_metrics: Dict[str, Any] = {}
        self._mic_level_history: list = []

    def get_default_config(self) -> dict[str, Any]:
        return {
            "default_tone_freq": 440,
            "default_tone_duration_ms": 2000,
            "benchmark_fps": 30,
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "default_tone_freq": field_int(default=440, min=100, max=10000),
            "default_tone_duration_ms": field_int(default=2000, min=200, max=10000),
            "benchmark_fps": field_int(default=30, min=15, max=60),
        }

    async def setup(self) -> None:
        """Register REST and WebSocket routes and subscribe to media events."""
        self.add_http_route("GET", "/status", self._handle_get_status)
        self.add_http_route("GET", "/capabilities", self._handle_get_capabilities)
        self.add_http_route("POST", "/run", self._handle_post_run)
        self.add_http_route("POST", "/stop", self._handle_post_stop)
        self.add_ws_route("/ws", self._handle_ws)

        # Subscribe to bus events
        self.subscribe("media.audio.frame_shm", self._on_audio_frame)
        self.subscribe("media.audio.mic_shm", self._on_mic_audio)
        self.subscribe("media.audio.sink_changed", self._on_sink_changed)
        self.subscribe("media.video.transport_active", self._on_video_transport_active)

    async def run(self) -> None:
        self.log.info("Diagnostic Module active on /api/diagnostic")
        while self._running:
            await asyncio.sleep(1.0)

    async def teardown(self) -> None:
        if self._test_task and not self._test_task.done():
            self._test_task.cancel()
        for ws in list(self.ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self.ws_clients.clear()

    # ------------------------------------------------------------------
    # Bus Event Handlers
    # ------------------------------------------------------------------

    async def _on_audio_frame(self, topic: str, data: dict) -> None:
        if isinstance(data, dict) and data.get("synthetic"):
            await self._broadcast_ws({
                "type": "audio_frame_injected",
                "format": data.get("format", "pcm"),
                "len": data.get("len", 0),
                "timestamp": time.time(),
            })

    async def _on_mic_audio(self, topic: str, data: dict) -> None:
        if isinstance(data, dict):
            length = data.get("len", 0)
            await self._broadcast_ws({
                "type": "mic_level",
                "len": length,
                "timestamp": time.time(),
            })

    async def _on_sink_changed(self, topic: str, data: dict) -> None:
        if isinstance(data, dict):
            await self._broadcast_ws({
                "type": "device_changed",
                "sink": data.get("sink"),
                "source": data.get("source"),
            })

    async def _on_video_transport_active(self, topic: str, data: dict) -> None:
        if isinstance(data, dict):
            await self._broadcast_ws({
                "type": "video_transport_active",
                "transport_name": data.get("transport_name"),
            })

    # ------------------------------------------------------------------
    # REST Endpoints
    # ------------------------------------------------------------------

    async def _handle_get_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "active_test": self._active_test,
            "last_metrics": self._last_metrics,
            "connected_ws_clients": len(self.ws_clients),
        })

    async def _handle_get_capabilities(self, request: web.Request) -> web.Response:
        try:
            caps = await self.call_module("media_server", "GET", "/api/media/diagnostic/capabilities")
            if caps:
                return web.json_response(caps)
        except Exception as exc:
            self.log.warning(f"Failed to query media_server capabilities: {exc}")

        return web.json_response({
            "status": "partial",
            "platform": sys.platform,
            "message": "media_server capabilities unavailable",
        })

    async def _handle_post_run(self, request: web.Request) -> web.Response:
        data = {}
        try:
            if request.can_read_body:
                data = await request.json()
        except Exception:
            pass

        test_type = data.get("test_type", "audio_pcm")
        params = data.get("params", {})

        if self._active_test:
            return web.json_response({"status": "busy", "message": f"Test '{self._active_test}' already running"}, status=409)

        self._active_test = test_type
        self._test_task = asyncio.create_task(self._execute_test(test_type, params))

        return web.json_response({
            "status": "started",
            "test_type": test_type,
            "params": params,
        })

    async def _handle_post_stop(self, request: web.Request) -> web.Response:
        if self._test_task and not self._test_task.done():
            self._test_task.cancel()
        stopped_test = self._active_test
        self._active_test = None
        await self._broadcast_ws({"type": "test_stopped", "test_type": stopped_test})
        return web.json_response({"status": "stopped", "test_type": stopped_test})

    # ------------------------------------------------------------------
    # Test Execution Coordinator
    # ------------------------------------------------------------------

    async def _execute_test(self, test_type: str, params: dict) -> None:
        t0 = time.time()
        self.log.info(f"Diagnostic: Starting test '{test_type}' with params {params}")
        await self._broadcast_ws({"type": "test_started", "test_type": test_type, "params": params})

        results: Dict[str, Any] = {"test_type": test_type, "status": "passed"}

        try:
            if test_type == "audio_pcm":
                freq = float(params.get("tone_hz", 440))
                duration_ms = int(params.get("duration_ms", 1500))
                res = await self.call_module(
                    "media_server",
                    "POST",
                    "/api/media/diagnostic/audio/inject",
                    {"format": "pcm", "tone_hz": freq, "duration_ms": duration_ms},
                )
                results["media_server_response"] = res
                results["duration_ms"] = duration_ms
                results["tone_hz"] = freq

            elif test_type == "audio_aac":
                duration_ms = int(params.get("duration_ms", 1500))
                res = await self.call_module(
                    "media_server",
                    "POST",
                    "/api/media/diagnostic/audio/inject",
                    {"format": "aac", "duration_ms": duration_ms},
                )
                results["media_server_response"] = res

            elif test_type == "audio_standalone_proc":
                import pathlib
                script_path = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "test_audio_cli.py"
                if not script_path.exists():
                    script_path = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "test_audio_cli.py"
                if not script_path.exists():
                    script_path = pathlib.Path("/opt/nemo-headunit/scripts/test_audio_cli.py")

                freq = float(params.get("freq", params.get("tone_hz", 440)))
                duration_sec = float(params.get("duration_sec", params.get("duration_ms", 2000) / 1000.0))

                cmd = [sys.executable, str(script_path), "--mode", "qaudiosink", "--freq", str(freq), "--duration", str(duration_sec)]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                results["cmd"] = " ".join(cmd)
                results["stdout"] = stdout.decode("utf-8", errors="ignore")
                results["stderr"] = stderr.decode("utf-8", errors="ignore")
                results["returncode"] = proc.returncode
                if proc.returncode != 0:
                    results["status"] = "failed"
                    results["error"] = f"Process exited with {proc.returncode}"

            elif test_type == "audio_in_process":
                freq = float(params.get("freq", params.get("tone_hz", 440)))
                duration_sec = float(params.get("duration_sec", params.get("duration_ms", 2000) / 1000.0))
                push_mode = bool(params.get("push", False))

                self.publish("diagnostic.audio.in_process_test", {
                    "freq": freq,
                    "duration": duration_sec,
                    "push": push_mode,
                })
                await asyncio.sleep(duration_sec + 0.5)
                results["in_process_dispatched"] = True

            elif test_type == "audio_device_select":
                sink = params.get("sink")
                source = params.get("source")
                res = await self.call_module(
                    "media_server",
                    "POST",
                    "/api/media/diagnostic/audio/set_device",
                    {"sink": sink, "source": source},
                )
                results["media_server_response"] = res

            elif test_type == "video_benchmark" or test_type == "video_hwaccel":
                transport = params.get("transport", "mjpeg")
                decoder = params.get("decoder", "auto")
                duration_sec = float(params.get("duration_sec", 2.0))
                fps = int(params.get("fps", 30))

                # Instruct media_server to benchmark transport
                res = await self.call_module(
                    "media_server",
                    "POST",
                    "/api/media/diagnostic/video/benchmark",
                    {
                        "transport": transport,
                        "decoder": decoder,
                        "duration_sec": duration_sec,
                        "fps": fps,
                    },
                )
                
                # Push synthetic frames via raw NAL SHM / bus for duration
                frame_interval = 1.0 / max(1, fps)
                frames_sent = 0
                end_time = time.time() + duration_sec
                while time.time() < end_time:
                    nal = generate_synthetic_h264_frame(1280, 720, is_idr=(frames_sent % 30 == 0))
                    self.publish("media.video.raw_nal_shm", {
                        "len": len(nal),
                        "is_idr": (frames_sent % 30 == 0),
                        "timestamp": time.time(),
                        "synthetic": True,
                    })
                    frames_sent += 1
                    await asyncio.sleep(frame_interval)

                results["frames_sent"] = frames_sent
                results["duration_sec"] = duration_sec
                results["transport"] = transport
                results["media_server_response"] = res

            else:
                results["status"] = "error"
                results["message"] = f"Unknown test type: {test_type}"

        except asyncio.CancelledError:
            self.log.info(f"Diagnostic test '{test_type}' cancelled")
            results["status"] = "cancelled"
        except Exception as exc:
            self.log.error(f"Diagnostic test '{test_type}' failed: {exc}", exc_info=True)
            results["status"] = "failed"
            results["error"] = str(exc)
        finally:
            results["elapsed_sec"] = round(time.time() - t0, 3)
            self._last_metrics = results
            self._active_test = None
            await self._broadcast_ws({"type": "test_completed", "results": results})
            self.log.info(f"Diagnostic: Test '{test_type}' finished with status '{results.get('status')}' in {results['elapsed_sec']}s")

    # ------------------------------------------------------------------
    # WebSocket Handling
    # ------------------------------------------------------------------

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)

        # Send initial status
        await ws.send_str(json.dumps({
            "type": "initial_status",
            "active_test": self._active_test,
            "last_metrics": self._last_metrics,
        }))

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        cmd = json.loads(msg.data)
                        if cmd.get("action") == "run":
                            await self._handle_post_run(None)
                        elif cmd.get("action") == "stop":
                            await self._handle_post_stop(None)
                    except Exception:
                        pass
        finally:
            self.ws_clients.discard(ws)
        return ws

    async def _broadcast_ws(self, data: dict) -> None:
        if not self.ws_clients:
            return
        payload = json.dumps(data)
        for ws in list(self.ws_clients):
            try:
                await ws.send_str(payload)
            except Exception:
                pass


if __name__ == "__main__":
    run_module(DiagnosticModule)
