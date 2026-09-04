"""
shm_media_engine.py — SHM Media Transport Engine for Qt6 Frontend.

Reads downstream media frames (video RGBA/YUV420 & audio PCM) zero-copy from `nemo_media_shm_down`
and dispatches them directly to Qt6 render surfaces and audio sinks.
"""

import os
import logging
import asyncio
from typing import Callable, Optional, Tuple

try:
    from shared.media_shm import BidirectionalMediaSHM, RingSharedMemoryBuffer
except ImportError:
    from backend.shared.media_shm import BidirectionalMediaSHM, RingSharedMemoryBuffer


try:
    from shared.logger import get_logger
except ImportError:
    from backend.shared.logger import get_logger

try:
    import av
except ImportError:
    av = None


logger = get_logger("qt6_gui.shm_engine")

# GL availability probe — checked once at module load
try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget as _probe_ogl  # noqa: F401
    _HAS_QOPENGL = True
except ImportError:
    _HAS_QOPENGL = False


try:
    from shared.hardware.video_decoder import (
        scan_gstreamer_plugin_paths,
        get_best_hardware_decoder,
        build_video_pipeline,
    )
except ImportError:
    from backend.shared.hardware.video_decoder import (
        scan_gstreamer_plugin_paths,
        get_best_hardware_decoder,
        build_video_pipeline,
    )


class GStreamerHwDecoder:
    """
    Cross-platform hardware-accelerated H.264 video decoder using GStreamer
    (NVDEC, VA-API, Direct3D 11, QSV, MediaFoundation, or V4L2) with zero-clock-sync appsink.
    """

    def __init__(self, on_frame_callback: Callable[[bytes, int, int, int], None]):
        self.on_frame_callback = on_frame_callback
        self.is_available = False
        self.frames_decoded = 0
        self._pipeline = None
        self._appsrc = None
        self._appsink = None
        self._Gst = None

        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstApp", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self._Gst = Gst

            scan_gstreamer_plugin_paths(Gst)

            pipe_str, dec_name = build_video_pipeline(
                Gst=Gst,
                mode="appsink",
                sink_name="sink",
                src_name="src",
            )

            if not pipe_str:
                logger.info("ℹ️ [Qt6 Video HW Decoder] No GStreamer video decoder pipeline available — using PyAV fallback")
                return

            logger.info(f"🎬 [Qt6 Video HW Decoder] Launching GStreamer pipeline: {pipe_str}")
            self._pipeline = Gst.parse_launch(pipe_str)
            self._appsrc = self._pipeline.get_by_name("src")
            self._appsink = self._pipeline.get_by_name("sink")

            if self._appsrc and self._appsink:
                self._appsink.connect("new-sample", self._on_new_sample)
                ret = self._pipeline.set_state(Gst.State.PLAYING)
                if ret != Gst.StateChangeReturn.FAILURE:
                    self.is_available = True
                    logger.info(f"🎬 [Qt6 Video HW Decoder] GStreamer pipeline active using {dec_name}")
        except Exception as exc:
            logger.warning(f"Could not initialize GStreamer HW decoder: {exc}")
            self.close()

    def decode_nal(self, nal_data: bytes, ts_us: int = 0) -> bool:
        if not self.is_available or not self._appsrc:
            return False
        try:
            buf = self._Gst.Buffer.new_wrapped(nal_data)
            if ts_us > 0:
                buf.pts = ts_us * 1000  # GstClockTime nanoseconds
            self._appsrc.emit("push-buffer", buf)
            return True
        except Exception:
            return False

    def _on_new_sample(self, sink) -> int:
        try:
            sample = sink.emit("pull-sample")
            if not sample:
                return self._Gst.FlowReturn.ERROR
            caps = sample.get_caps()
            w, h = 1280, 720
            if caps:
                structure = caps.get_structure(0)
                ok_w, sw = structure.get_int("width")
                ok_h, sh = structure.get_int("height")
                if ok_w and ok_h:
                    w, h = sw, sh

            buf = sample.get_buffer()
            pts_us = int(buf.pts // 1000) if buf and buf.pts != self._Gst.CLOCK_TIME_NONE else 0

            success, map_info = buf.map(self._Gst.MapFlags.READ)
            if not success:
                return self._Gst.FlowReturn.ERROR
            try:
                rgba_bytes = bytes(map_info.data)
            finally:
                buf.unmap(map_info)

            if rgba_bytes and self.on_frame_callback:
                self.frames_decoded += 1
                if self.frames_decoded == 1 or self.frames_decoded % 150 == 0:
                    logger.info(f"🎬 [Qt6 Video HW Decoder] Active GPU decode: Frame #{self.frames_decoded} ({w}x{h} RGBA, 0% CPU)")
                self.on_frame_callback(rgba_bytes, w, h, pts_us)
            return self._Gst.FlowReturn.OK
        except Exception:
            return self._Gst.FlowReturn.ERROR

    def close(self):
        if self._pipeline:
            try:
                self._pipeline.set_state(self._Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None
            self._appsrc = None
            self._appsink = None
        self.is_available = False


class Qml6ZeroCopyDecoder:
    """
    Zero-CPU H.264 decoder using GStreamer VA-API, DMABuf, and qml6glsink.

    Pipeline:
      appsrc name=src is-live=true format=bytes
      ! h264parse config-interval=-1
      ! vah264dec
      ! vapostproc add-borders=true
      ! video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=YV12,width=1280,height=800
      ! glupload
      ! qml6glsink name=qml_sink sync=false
    """

    def __init__(self, on_frame_callback: Callable[[bytes, int, int, int], None]):
        self.on_frame_callback = on_frame_callback
        self.is_available = False
        self.frames_decoded = 0
        self._pipeline = None
        self._appsrc = None
        self._sink = None
        self._viewport = None
        self._Gst = None
        self._is_sink_bound = False
        self._is_playing = False
        self._try_init_pipeline()

    def _try_init_pipeline(self) -> None:
        """Build GStreamer zero-copy pipeline using unified video_decoder factory."""
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstGL", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self._Gst = Gst

            scan_gstreamer_plugin_paths(Gst)

            pipe_str, dec_desc = build_video_pipeline(
                Gst=Gst,
                mode="zero_copy",
                sink_name="qml_sink",
                src_name="src",
            )

            if not pipe_str:
                logger.info(
                    "ℹ️ [Qml6ZeroCopyDecoder] Zero-copy pipeline not supported or elements missing — falling back to GStreamerHwDecoder"
                )
                return

            logger.info(f"🎬 [Qml6ZeroCopyDecoder] Launching GStreamer pipeline: {pipe_str}")
            self._pipeline = Gst.parse_launch(pipe_str)
            self._appsrc = self._pipeline.get_by_name("src")
            self._sink = self._pipeline.get_by_name("qml_sink")

            if not self._appsrc or not self._sink:
                logger.warning("[Qml6ZeroCopyDecoder] Pipeline element lookup failed")
                return

            # Monitor GStreamer bus messages for errors
            bus = self._pipeline.get_bus()
            bus.add_signal_watch()

            def _on_gst_error(b, msg):
                err, dbg = msg.parse_error()
                logger.error(f"❌ [Qml6ZeroCopyDecoder Gst Error] {err.message} - {dbg}")

            bus.connect("message::error", _on_gst_error)

            self.is_available = True
            logger.info(f"🎬 [Qml6ZeroCopyDecoder] Pipeline initialized ({dec_desc} -> qml6glsink)")
        except Exception as exc:
            logger.warning(f"[Qml6ZeroCopyDecoder] Init failed: {exc}")

    def attach_viewport(self, viewport) -> None:
        """Wire the qml6glsink element to the VideoViewportWidget."""
        self._viewport = viewport
        if hasattr(viewport, "set_sink_bound_callback"):
            viewport.set_sink_bound_callback(self._on_sink_bound)
        if self._sink and hasattr(viewport, "attach_gstreamer_sink"):
            viewport.attach_gstreamer_sink(self._sink)

    def _on_sink_bound(self) -> None:
        """Invoked when sceneGraphInitialized has successfully bound widget to qml6glsink."""
        self._is_sink_bound = True
        logger.info("🎬 [Qml6ZeroCopyDecoder] Sink bound to GstGLQt6VideoItem confirmed — ready for stream")

    def set_focused(self, focused: bool) -> None:
        """Track whether video is in foreground to drop NALs when suspended."""
        self._is_focused = focused

    def decode_nal(self, nal_data: bytes, ts_us: int = 0) -> bool:
        """Push a NAL unit into appsrc. Starts playback on first frame."""
        if not self.is_available or not self._appsrc or not getattr(self, "_is_focused", True):
            return False
        try:
            if not self._is_playing and self._is_sink_bound and self._pipeline:
                self._is_playing = True
                ret = self._pipeline.set_state(self._Gst.State.PLAYING)
                logger.info(f"🎬 [Qml6ZeroCopyDecoder] First NAL arrived -> pipeline set to PLAYING ({ret})")

            buf = self._Gst.Buffer.new_wrapped(nal_data)
            if ts_us > 0:
                buf.pts = ts_us * 1000
            self._appsrc.emit("push-buffer", buf)
            self.frames_decoded += 1
            return True
        except Exception as exc:
            logger.debug(f"[Qml6ZeroCopyDecoder] decode_nal error: {exc}")
            return False

    def close(self) -> None:
        if self._pipeline:
            try:
                self._pipeline.set_state(self._Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None
            self._appsrc = None
            self._sink = None
        self.is_available = False


class QtSHMMediaEngine:
    """
    Shared Memory Media Dispatcher for Qt6 Frontend.
    """

    def __init__(self):
        self.shm: Optional[BidirectionalMediaSHM] = None
        self.on_video_frame: Optional[Callable[[bytes, int, int, int], None]] = None  # payload, width, height, ts_us
        self.on_audio_frame: Optional[Callable[[bytes, int, int], None]] = None  # payload, channel_id, ts_us
        self.on_stream_start: Optional[Callable[[], None]] = None
        self.on_stream_stop: Optional[Callable[[], None]] = None
        self.is_connected = False
        self.is_video_focused = True
        self._codec_ctx = None
        self._nal_counter = 0

        # 1. Initialize best available video decoder
        self._hw_decoder = Qml6ZeroCopyDecoder(on_frame_callback=self._on_hw_decoded_frame)
        if not self._hw_decoder.is_available:
            logger.info(
                "ℹ️ [SHM Engine] Qml6ZeroCopyDecoder not available — falling back to GStreamerHwDecoder"
            )
            self._hw_decoder = GStreamerHwDecoder(self._on_hw_decoded_frame)

        # 2. PyAV CPU fallback decoder
        if av is not None:
            try:
                self._codec_ctx = av.CodecContext.create('h264', 'r')
                self._codec_ctx.thread_type = 'AUTO'
                self._codec_ctx.thread_count = 2
                logger.info("🎬 QtSHMMediaEngine PyAV H.264 CodecContext initialized (2 threads fallback)")
            except Exception as e:
                logger.warning("Could not initialize PyAV H.264 CodecContext: %s", e)

    def set_video_focused(self, focused: bool) -> None:
        """Enable or suspend video decode to conserve CPU/GPU when not in foreground."""
        self.is_video_focused = focused
        if hasattr(self._hw_decoder, "set_focused"):
            self._hw_decoder.set_focused(focused)

    def _on_hw_decoded_frame(self, rgba_pixels: bytes, width: int, height: int, ts_us: int):
        """Dispatch hardware-decoded RGBA frame directly to Qt6 video viewport."""
        if self.on_video_frame and self.is_video_focused:
            self.on_video_frame(rgba_pixels, width, height, ts_us)

    def connect_shm(self) -> bool:
        """Attach to existing shared memory buffers created by media_server/channel_manager."""
        logger.info("🔍 [SHM Engine Trace] Attempting BidirectionalMediaSHM(create=False)...")
        try:
            self.shm = BidirectionalMediaSHM(create=False)
            self.is_connected = True
            logger.info("QtSHMMediaEngine successfully attached to BidirectionalMediaSHM buffers")
            return True
        except Exception as exc:
            logger.warning("QtSHMMediaEngine failed to attach to SHM: %s", exc)
            self.is_connected = False
            return False


    def process_downstream_video(self, offset: int, channel_id: Optional[int] = None) -> None:
        """
        Reads video frame at offset from dedicated per-channel SHM and dispatches to on_video_frame callback.
        """
        if not self.shm or offset < 0 or not self.is_video_focused:
            return

        try:
            shm_buf = self.shm.get_downstream_channel(channel_id) if channel_id is not None else self.shm.downstream
            _, ts_low, payload = shm_buf.read_frame(offset)
            if not payload:
                return

            # 1. Direct raw RGBA frame header check
            if len(payload) >= 12:
                import struct
                width, height, _ = struct.unpack(">III", payload[:12])
                if 0 < width <= 4096 and 0 < height <= 4096:
                    expected_len = width * height * 4
                    if len(payload) == 12 + expected_len:
                        rgba_pixels = payload[12:]
                        if self.on_video_frame:
                            self.on_video_frame(rgba_pixels, width, height, ts_low)
                        return

            # 2. Decode raw H.264 NAL units via Hardware VA-API (or PyAV Fallback)
            if payload.startswith(b"\x00\x00\x00\x01") or payload.startswith(b"\x00\x00\x01"):
                if self._hw_decoder and self._hw_decoder.is_available:
                    self._hw_decoder.decode_nal(payload, ts_low)
                    if self._hw_decoder.frames_decoded > 0 or self._nal_counter < 30:
                        self._nal_counter += 1
                        return

                if self._codec_ctx is not None:
                    try:
                        packet = av.Packet(payload)
                        frames = self._codec_ctx.decode(packet)
                        for frame in frames:
                            rgba_frame = frame.reformat(format="rgba")
                            rgba_pixels = bytes(rgba_frame.planes[0])
                            w, h = frame.width, frame.height
                            if self.on_video_frame:
                                self.on_video_frame(rgba_pixels, w, h, ts_low)
                    except Exception as e:
                        logger.debug("PyAV decode error: %s", e)
                return

            # 3. Fallback check for complete compressed image formats (JPEG / WebP)
            if (payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")) or payload.startswith(b"RIFF"):
                from PyQt6.QtGui import QImage
                qimg = QImage.fromData(payload)
                if not qimg.isNull():
                    rgba_img = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
                    w = rgba_img.width()
                    h = rgba_img.height()
                    ptr = rgba_img.bits()
                    ptr.setsize(rgba_img.sizeInBytes())
                    rgba_pixels = bytes(ptr)
                    if self.on_video_frame:
                        self.on_video_frame(rgba_pixels, w, h, ts_low)
        except Exception as exc:
            logger.debug("SHM video processing error at offset %d: %s", offset, exc)

    def process_downstream_audio(self, offset: int, channel_id: Optional[int] = None) -> None:
        """
        Reads audio frame at offset from dedicated per-channel SHM and dispatches to on_audio_frame callback.
        """
        if not self.shm or offset < 0:
            return

        try:
            shm_buf = self.shm.get_downstream_channel(channel_id) if channel_id is not None else self.shm.downstream
            stream_type, ts_low, payload = shm_buf.read_frame(offset)
            if not payload:
                return

            effective_channel_id = channel_id if channel_id is not None else stream_type
            if self.on_audio_frame:
                self.on_audio_frame(payload, effective_channel_id, ts_low)
        except Exception as exc:
            logger.debug("SHM audio processing error at offset %d: %s", offset, exc)

    def process_downstream_frame(self, offset: int) -> None:
        """Fallback dispatcher for downstream frames when stream type is read from legacy downstream SHM header."""
        if not self.shm or offset < 0:
            return

        try:
            stream_type, ts_low, payload = self.shm.downstream.read_frame(offset)
            if not payload:
                return
            # Treat non-video payloads as audio
            if self.on_audio_frame:
                self.on_audio_frame(payload, stream_type, ts_low)
        except Exception as exc:
            logger.debug("SHM frame processing error at offset %d: %s", offset, exc)

    def write_upstream_mic(self, pcm_data: bytes) -> int:
        """
        Writes 16kHz 16-bit Mono PCM mic frame zero-copy to `nemo_media_shm_up`.
        Returns SHM offset.
        """
        if not self.shm or not pcm_data:
            return -1
        try:
            # stream_type 1 for Speech/Mic audio
            return self.shm.upstream.write_frame(1, 0, pcm_data)
        except Exception as exc:
            logger.warning("Failed to write mic frame to SHM upstream: %s", exc)
            return -1

    def close(self):
        if hasattr(self, "_hw_decoder") and self._hw_decoder:
            try:
                self._hw_decoder.close()
            except Exception:
                pass
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
        self.is_connected = False
