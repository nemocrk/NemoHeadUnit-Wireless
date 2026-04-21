"""
NemoHeadUnit-Wireless — Media Renderer
Standalone process that subscribes to audio/video frames from the bus
and renders them via GStreamer (preferred) or FFmpeg subprocess fallback.

Run independently:
    python services/media_renderer.py

Bus topics subscribed:
    media.audio.frame   binary=AAC frame,   header={seq, pts}
    media.video.frame   binary=H.264 frame, header={seq, pts, keyframe}
    app.shutdown

Latency budget:
    Audio: deadline 10ms  — 50 frames/sec, ~320-640 bytes each
    Video: deadline 33ms  — 30 frames/sec, ~5-40 KB (IDR up to ~400 KB)
"""

import sys
import os
import signal
import subprocess
import threading
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bus_client import BusClient
from app.logger import LoggerManager

log = LoggerManager.get_logger('services.media_renderer')


# ---------------------------------------------------------------------------
# GStreamer pipeline helpers
# ---------------------------------------------------------------------------

def _try_import_gst():
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        return Gst
    except Exception:
        return None


class GstAudioSink:
    """
    GStreamer appsrc -> aacparse -> avdec_aac -> autoaudiosink pipeline.
    Receives raw AAC frames and plays them via the system audio output.
    """

    def __init__(self, Gst):
        self._Gst = Gst
        self._pipeline = None
        self._appsrc = None
        self._lock = threading.Lock()
        self._build()

    def _build(self):
        pipeline_str = (
            "appsrc name=src stream-type=0 format=time is-live=true "
            "! aacparse ! avdec_aac ! audioconvert ! audioresample "
            "! autoaudiosink sync=false"
        )
        self._pipeline = self._Gst.parse_launch(pipeline_str)
        self._appsrc = self._pipeline.get_by_name("src")
        self._pipeline.set_state(self._Gst.State.PLAYING)
        log.info("GstAudioSink pipeline started")

    def push(self, data: bytes, pts: int = 0) -> None:
        if not self._appsrc:
            return
        buf = self._Gst.Buffer.new_wrapped(data)
        buf.pts = pts
        with self._lock:
            self._appsrc.emit("push-buffer", buf)

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(self._Gst.State.NULL)


class GstVideoSink:
    """
    GStreamer appsrc -> h264parse -> avdec_h264 -> autovideosink pipeline.
    Receives raw H.264 Annex-B frames and displays them.
    """

    def __init__(self, Gst):
        self._Gst = Gst
        self._pipeline = None
        self._appsrc = None
        self._lock = threading.Lock()
        self._build()

    def _build(self):
        pipeline_str = (
            "appsrc name=src stream-type=0 format=time is-live=true "
            "! h264parse ! avdec_h264 ! videoconvert "
            "! autovideosink sync=false"
        )
        self._pipeline = self._Gst.parse_launch(pipeline_str)
        self._appsrc = self._pipeline.get_by_name("src")
        self._pipeline.set_state(self._Gst.State.PLAYING)
        log.info("GstVideoSink pipeline started")

    def push(self, data: bytes, pts: int = 0) -> None:
        if not self._appsrc:
            return
        buf = self._Gst.Buffer.new_wrapped(data)
        buf.pts = pts
        with self._lock:
            self._appsrc.emit("push-buffer", buf)

    def stop(self):
        if self._pipeline:
            self._pipeline.set_state(self._Gst.State.NULL)


# ---------------------------------------------------------------------------
# FFmpeg fallback (writes frames to stdin of ffplay)
# ---------------------------------------------------------------------------

class FfmpegAudioSink:
    """Fallback: pipe AAC frames to ffplay via stdin."""

    def __init__(self):
        self._proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-f", "aac", "-i", "pipe:0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("FfmpegAudioSink started (ffplay fallback)")

    def push(self, data: bytes, pts: int = 0) -> None:
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except BrokenPipeError:
            log.warning("FfmpegAudioSink pipe broken")

    def stop(self):
        try:
            self._proc.stdin.close()
            self._proc.terminate()
        except Exception:
            pass


class FfmpegVideoSink:
    """Fallback: pipe H.264 Annex-B frames to ffplay via stdin."""

    def __init__(self):
        self._proc = subprocess.Popen(
            ["ffplay", "-f", "h264", "-i", "pipe:0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("FfmpegVideoSink started (ffplay fallback)")

    def push(self, data: bytes, pts: int = 0) -> None:
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except BrokenPipeError:
            log.warning("FfmpegVideoSink pipe broken")

    def stop(self):
        try:
            self._proc.stdin.close()
            self._proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MediaRenderer
# ---------------------------------------------------------------------------

class MediaRenderer:
    """
    Subscribes to media.audio.frame and media.video.frame on the bus
    and feeds frames to GStreamer (or FFmpeg fallback) sinks.

    No Qt dependency — runs as a pure background process.
    """

    def __init__(self):
        self._bus = BusClient(name="media_renderer")
        self._Gst = _try_import_gst()

        if self._Gst:
            log.info("GStreamer available — using native pipelines")
            self._audio_sink = GstAudioSink(self._Gst)
            self._video_sink = GstVideoSink(self._Gst)
        else:
            log.warning("GStreamer not found — falling back to ffplay")
            self._audio_sink = FfmpegAudioSink()
            self._video_sink = FfmpegVideoSink()

        self._subscribe()

    def _subscribe(self) -> None:
        # No thread='main' needed — this process has no Qt event loop
        self._bus.on("media.audio.frame", self._on_audio_frame)
        self._bus.on("media.video.frame", self._on_video_frame)
        self._bus.on("app.shutdown",      self._on_shutdown)
        log.info("MediaRenderer subscribed to bus topics")

    def _on_audio_frame(self, payload) -> None:
        """
        payload is raw AAC bytes (BusClient returns binary as-is
        when msgpack decoding fails).
        """
        if isinstance(payload, (bytes, bytearray)):
            self._audio_sink.push(payload)
        elif isinstance(payload, dict):
            # Should not happen — audio frames are always binary
            log.warning(f"Unexpected dict payload on media.audio.frame: {payload}")

    def _on_video_frame(self, payload) -> None:
        """
        payload is raw H.264 Annex-B bytes.
        """
        if isinstance(payload, (bytes, bytearray)):
            self._video_sink.push(payload)
        elif isinstance(payload, dict):
            log.warning(f"Unexpected dict payload on media.video.frame: {payload}")

    def _on_shutdown(self, payload) -> None:
        log.info("Shutdown received, stopping media renderer")
        self.stop()
        sys.exit(0)

    def stop(self) -> None:
        self._audio_sink.stop()
        self._video_sink.stop()
        self._bus.stop()

    def wait(self) -> None:
        """Block until shutdown event."""
        self._bus.wait_for_shutdown()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    renderer = MediaRenderer()

    def _shutdown(signum, frame):
        log.info("Signal received, stopping")
        renderer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("MediaRenderer running — waiting for frames")
    renderer.wait()


if __name__ == "__main__":
    main()
