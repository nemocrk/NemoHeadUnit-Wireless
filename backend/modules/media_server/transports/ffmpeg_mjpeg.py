"""
video_decoder/transports/ffmpeg_mjpeg.py

FFmpeg subprocess transport: H.264 HW decode → JPEG per-frame via pipe.

Command:
    ffmpeg -hwaccel auto -f h264 -i pipe:0 \\
           -f image2pipe -vcodec mjpeg -q:v {quality} pipe:1

Writes H.264 NAL bytes to stdin, reads JPEG blobs from stdout.
JPEG frame boundaries are detected via SOI (0xFFD8) / EOI (0xFFD9) markers.

This transport is the Windows-friendly fallback when GStreamer is not installed,
since FFmpeg is available cross-platform via conda-forge or system packages.

Availability: Requires 'ffmpeg' CLI tool on PATH (shutil.which('ffmpeg')).
"""

import asyncio
import shutil
from typing import Optional

from .base import BaseVideoTransport, TransportUnavailableError


from shared.logger import get_logger

log = get_logger("media_server")



class FFmpegMjpegTransport(BaseVideoTransport):
    """
    FFmpeg subprocess H.264 → JPEG transport.

    Uses -hwaccel auto to automatically select the best HW accelerator:
      - VAAPI (Linux Intel/AMD)
      - NVDEC / CUDA (Linux/Windows NVIDIA)
      - DXVA2 / D3D11VA (Windows)
      - VideoToolbox (macOS)
      - Software fallback (any platform)
    """

    transport_name = "mjpeg-ffmpeg"
    wire_format = "mjpeg"

    # JPEG quality maps to ffmpeg -q:v (1=best, 31=worst; invert our 50-95 range)
    _QSCALE_MAP = {50: 20, 60: 16, 70: 12, 75: 10, 80: 8, 85: 6, 90: 4, 95: 2}

    def __init__(self, jpeg_quality: int = 75, video_scale: str = "") -> None:
        super().__init__(jpeg_quality, video_scale)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._jpeg_buf = bytearray()
        self._hw_accel_name: str = "ffmpeg_auto"
        self._is_hw_accelerated: bool = True
        self._decoder_type_desc: str = "FFmpeg -hwaccel auto"

    @staticmethod
    def is_available() -> bool:
        return shutil.which("ffmpeg") is not None

    def _quality_to_qscale(self) -> int:
        """Convert our 50-95 quality range to ffmpeg's 1-31 q:v scale (lower = better)."""
        closest = min(self._QSCALE_MAP.keys(), key=lambda k: abs(k - self.jpeg_quality))
        return self._QSCALE_MAP[closest]

    def _build_ffmpeg_args(self) -> list[str]:
        qscale = self._quality_to_qscale()
        args = [
            "ffmpeg",
            "-loglevel", "info",
            "-hwaccel", "auto",
            "-f", "h264",
            "-i", "pipe:0",
        ]
        # Optional scaling
        scale = self._parse_video_scale()
        if scale:
            w, h = scale
            args += ["-vf", f"scale={w}:{h}"]

        args += [
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", str(qscale),
            "pipe:1",
        ]
        return args

    async def start(self) -> None:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise TransportUnavailableError(
                "FFmpeg not found on PATH. Install via: sudo apt install ffmpeg "
                "or conda install -c conda-forge ffmpeg"
            )

        args = self._build_ffmpeg_args()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            raise TransportUnavailableError(f"Failed to start FFmpeg subprocess: {exc}") from exc

        self._jpeg_buf = bytearray()
        self._reader_task = asyncio.create_task(self._read_jpeg_frames())
        self._stderr_task = asyncio.create_task(self._read_stderr_diagnostics())

        log.info(
            "🎬 [Video Decoder] Started FFmpeg subprocess mode: 'mjpeg-ffmpeg' (-hwaccel auto)"
        )

    async def _read_stderr_diagnostics(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        try:
            while True:
                line_bytes = await self._proc.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if "hwaccel" in line.lower() or "using" in line.lower() or "decoder" in line.lower():
                    if "Selected hwaccel" in line or "Using" in line:
                        self._hw_accel_name = line
                        if "failed" in line.lower() or "cannot" in line.lower():
                            self._is_hw_accelerated = False
                            self._decoder_type_desc = "Software Fallback (CPU)"
                        else:
                            self._is_hw_accelerated = True
                            self._decoder_type_desc = f"Hardware Accelerated ({line})"
                        log.info(f"🎬 [Video Decoder] FFmpeg stderr diagnostic: {line}")
        except Exception:
            pass

    def get_diagnostics(self) -> dict:
        return {
            "transport": self.transport_name,
            "decoder_element": self._hw_accel_name,
            "hw_accelerated": self._is_hw_accelerated,
            "decoder_type": self._decoder_type_desc,
            "details": {
                "ffmpeg_args": " ".join(self._build_ffmpeg_args()),
            },
        }


    async def _read_jpeg_frames(self) -> None:
        """
        Read JPEG frames from FFmpeg stdout.
        JPEG frame boundaries: SOI = 0xFFD8, EOI = 0xFFD9.
        """
        assert self._proc and self._proc.stdout

        try:
            while True:
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                self._jpeg_buf.extend(chunk)
                await self._flush_jpeg_frames()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _flush_jpeg_frames(self) -> None:
        """Extract and emit complete JPEG frames from the internal buffer."""
        while True:
            # Find SOI marker
            soi = self._jpeg_buf.find(b"\xff\xd8")
            if soi == -1:
                self._jpeg_buf.clear()
                return

            # Trim leading garbage before SOI
            if soi > 0:
                del self._jpeg_buf[:soi]

            # Find EOI marker (must be after SOI)
            eoi = self._jpeg_buf.find(b"\xff\xd9", 2)
            if eoi == -1:
                return  # Incomplete frame — wait for more data

            frame_end = eoi + 2
            jpeg_bytes = bytes(self._jpeg_buf[:frame_end])
            del self._jpeg_buf[:frame_end]

            if self.on_frame_ready:
                await self.on_frame_ready(jpeg_bytes, 0, self.wire_format)

    async def feed_nal(self, nal_data: bytes, timestamp_us: int) -> None:
        if self._proc and self._proc.stdin and nal_data:
            try:
                self._proc.stdin.write(nal_data)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
                pass

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._proc:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
