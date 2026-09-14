"""
media_shm.py — Cross-platform Bidirectional Shared Memory Engine for NemoHeadUnit-Wireless.

Provides zero-copy media transfer between `tcp_server` and `channel_manager`.

Architecture:
  - `nemo_media_shm_down`: Downstream ring buffer (Phone -> tcp_server -> SHM -> channel_manager -> WebCodecs).
  - `nemo_media_shm_up`: Upstream ring buffer (Frontend Mic -> channel_manager -> SHM -> tcp_server -> Phone).
  - Uses standard library `multiprocessing.shared_memory.SharedMemory` (compatible with Linux /dev/shm and Windows Named Memory Maps).
  - Patches resource tracker to prevent premature unlinking across processes.
"""

from __future__ import annotations

import logging
from multiprocessing import shared_memory
import struct
from typing import Any, Optional, Tuple

logger = logging.getLogger("media_shm")

# Resource tracker patch for multi-process SHM
def _patch_resource_tracker():
    try:
        from multiprocessing import resource_tracker

        _orig_register = resource_tracker.register
        _orig_unregister = resource_tracker.unregister

        def patched_register(name, rtype):
            if rtype == "shared_memory":
                return
            return _orig_register(name, rtype)

        def patched_unregister(name, rtype):
            if rtype == "shared_memory":
                return
            return _orig_unregister(name, rtype)

        resource_tracker.register = patched_register
        resource_tracker.unregister = patched_unregister
    except Exception as e:
        logger.debug("Resource tracker patch warning: %s", e)

_patch_resource_tracker()

import threading

DEFAULT_SHM_SIZE = 32 * 1024 * 1024  # 32MB ring buffer capacity for video
DEFAULT_AUDIO_SHM_SIZE = 8 * 1024 * 1024  # 8MB ring buffer capacity for audio
SHM_DOWNSTREAM_NAME = "nemo_media_shm_down"
SHM_UPSTREAM_NAME = "nemo_media_shm_up"
SHM_TRANSCODE_IN_NAME = "nemo_video_transcode_in"


def get_downstream_channel_shm_name(channel_id: int) -> str:
    """Return standard per-channel downstream SHM buffer name."""
    return f"nemo_media_shm_down_ch{channel_id}"


def get_wire_channel_shm_name(channel_id: int) -> str:
    """Return standard per-channel inbound wire SHM buffer name."""
    return f"nemo_media_shm_wire_ch{channel_id}"


class RingSharedMemoryBuffer:
    """
    Circular ring buffer wrapping multiprocessing.shared_memory.SharedMemory.
    Each frame entry in SHM is preceded by a 12-byte header:
      [Magic: 2B (0x4E4D = "NM")] + [StreamType: 1B] + [Reserved: 1B] + [Length: 4B uint32 BE] + [TimestampUs: 4B/8B]
    """

    def __init__(self, name: str, size: int = DEFAULT_SHM_SIZE, create: bool = False):
        self.name = name
        self.size = size
        self.create = create
        self.shm: Optional[shared_memory.SharedMemory] = None

        if create:
            try:
                self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)
            except FileExistsError:
                # Attach to existing if already created
                self.shm = shared_memory.SharedMemory(name=name, create=False)
        else:
            try:
                self.shm = shared_memory.SharedMemory(name=name, create=False)
            except (FileNotFoundError, ValueError):
                # Fallback: if non-existent or empty 0-byte file, recreate with specified size
                try:
                    self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)
                except FileExistsError:
                    # If already exists but was 0 bytes, unlink and create
                    try:
                        temp = shared_memory.SharedMemory(name=name, create=False)
                        temp.unlink()
                    except Exception:
                        pass
                    self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)

        self.write_offset = 0

    def write_frame(self, stream_type: int, timestamp_us: int, payload: bytes) -> int:
        """
        Writes frame payload to shared memory buffer.
        Returns the offset in the buffer where the frame was written.
        """
        if not self.shm or not payload:
            return -1

        payload_len = len(payload)
        header_len = 12
        total_len = header_len + payload_len

        if total_len > self.size:
            logger.error("Frame size %d exceeds total SHM size %d", total_len, self.size)
            return -1

        # Wrap around if offset exceeds buffer size
        if self.write_offset + total_len > self.size:
            self.write_offset = 0

        target_offset = self.write_offset

        # Header: Magic(2B) + StreamType(1B) + Reserved(1B) + Length(4B) + TimestampLow(4B)
        header = struct.pack(">2s B B I I", b"NM", stream_type, 0, payload_len, timestamp_us & 0xFFFFFFFF)
        self.shm.buf[target_offset : target_offset + header_len] = header
        self.shm.buf[target_offset + header_len : target_offset + total_len] = payload

        # Advance write pointer
        self.write_offset = (target_offset + total_len) % self.size
        return target_offset

    def read_frame(self, offset: int) -> Tuple[int, int, bytes]:
        """
        Reads frame payload from shared memory buffer at offset.
        Returns (stream_type, timestamp_us_low, payload_bytes).
        """
        if not self.shm or offset < 0 or offset + 12 > self.size:
            return 0, 0, b""

        header_bytes = bytes(self.shm.buf[offset : offset + 12])
        magic, stream_type, _, length, ts_low = struct.unpack(">2s B B I I", header_bytes)

        if magic != b"NM":
            logger.warning(
                f"⚠️ [Video Stall: SHM Overrun] Invalid SHM frame magic {magic!r} (hex={header_bytes.hex()}) "
                f"at offset {offset} (buf size={self.size}) — ring buffer overwritten by writer before reader consumed!"
            )
            return 0, 0, b""

        if offset + 12 + length > self.size:
            logger.warning("SHM frame at offset %d with length %d overflows buffer size %d", offset, length, self.size)
            return 0, 0, b""

        payload = bytes(self.shm.buf[offset + 12 : offset + 12 + length])
        return stream_type, ts_low, payload

    def close(self):
        if self.shm:
            try:
                self.shm.close()
                import sys
                if self.create and sys.platform != "win32":
                    try:
                        self.shm.unlink()
                    except Exception as unlink_err:
                        logger.debug("SHM unlink notice: %s", unlink_err)
            except Exception as e:
                logger.debug("SHM close exception: %s", e)
            self.shm = None


import sys

if not hasattr(sys, "_nemo_inmemory_buffers"):
    sys._nemo_inmemory_buffers = {}
_INMEMORY_BUFFERS: dict[str, InMemoryRingBuffer] = sys._nemo_inmemory_buffers

if not hasattr(sys, "_nemo_inmemory_lock"):
    sys._nemo_inmemory_lock = threading.RLock()
_INMEMORY_LOCK = sys._nemo_inmemory_lock


class InMemoryRingBuffer:
    """
    In-memory bytearray circular ring buffer for multithreading mode.
    Shared by name across threads in the same process without OS /dev/shm allocations.
    """

    @classmethod
    def get_or_create(cls, name: str, size: int = DEFAULT_SHM_SIZE, create: bool = False) -> InMemoryRingBuffer:
        with _INMEMORY_LOCK:
            if name in _INMEMORY_BUFFERS:
                return _INMEMORY_BUFFERS[name]
            buf = cls(name=name, size=size, create=create)
            _INMEMORY_BUFFERS[name] = buf
            return buf

    def __init__(self, name: str, size: int = DEFAULT_SHM_SIZE, create: bool = False):
        self.name = name
        self.size = size
        self.create = create
        with _INMEMORY_LOCK:
            if name in _INMEMORY_BUFFERS:
                existing = _INMEMORY_BUFFERS[name]
                self.buf = existing.buf
                self.size = existing.size
                self.write_offset = getattr(existing, "write_offset", 0)
                self._lock = getattr(existing, "_lock", threading.Lock())
            else:
                self.buf = bytearray(size)
                self.write_offset = 0
                self._lock = threading.Lock()
                _INMEMORY_BUFFERS[name] = self

    def write_frame(self, stream_type: int, timestamp_us: int, payload: bytes) -> int:
        if not payload:
            return -1

        payload_len = len(payload)
        header_len = 12
        total_len = header_len + payload_len

        if total_len > self.size:
            logger.error("Frame size %d exceeds total buffer size %d", total_len, self.size)
            return -1

        with self._lock:
            # Wrap around if offset exceeds buffer size
            if self.write_offset + total_len > self.size:
                self.write_offset = 0

            target_offset = self.write_offset

            # Header: Magic(2B) + StreamType(1B) + Reserved(1B) + Length(4B) + TimestampLow(4B)
            header = struct.pack(">2s B B I I", b"NM", stream_type, 0, payload_len, timestamp_us & 0xFFFFFFFF)
            self.buf[target_offset : target_offset + header_len] = header
            self.buf[target_offset + header_len : target_offset + total_len] = payload

            self.write_offset = (target_offset + total_len) % self.size
            return target_offset

    def read_frame(self, offset: int) -> Tuple[int, int, bytes]:
        if offset < 0 or offset + 12 > self.size:
            return 0, 0, b""

        header_bytes = bytes(self.buf[offset : offset + 12])
        magic, stream_type, _, length, ts_low = struct.unpack(">2s B B I I", header_bytes)

        if magic != b"NM":
            logger.warning("Invalid in-memory frame magic %r (hex=%s) at offset %d (buf size=%d)", magic, header_bytes.hex(), offset, self.size)
            return 0, 0, b""

        if offset + 12 + length > self.size:
            logger.warning("In-memory frame at offset %d with length %d overflows buffer size %d", offset, length, self.size)
            return 0, 0, b""

        payload = bytes(self.buf[offset + 12 : offset + 12 + length])
        return stream_type, ts_low, payload

    def close(self):
        pass


class BidirectionalMediaSHM:
    """
    Manages Downstream, Upstream, and Video Transcode Input shared memory buffers.
    Supports dynamic per-channel downstream buffers (nemo_media_shm_down_chX).
    Automatically switches to in-memory bytearray buffers when multithreading mode is active.
    """

    def __init__(self, create: bool = False, size: int = DEFAULT_SHM_SIZE):
        import os
        self.create = create
        self.default_size = size
        mode = os.environ.get("NEMO_EXECUTION_MODE", os.environ.get("NEMO_MODE", "multiprocessing")).lower().strip()
        self._is_multithreading = mode in ("multithreading", "threading", "thread", "threads")

        self.downstream = self._create_buffer(SHM_DOWNSTREAM_NAME, size=size, create=create)
        self.upstream = self._create_buffer(SHM_UPSTREAM_NAME, size=size, create=create)
        self.transcode_in = self._create_buffer(SHM_TRANSCODE_IN_NAME, size=size, create=create)
        self._downstream_channels: dict[int, Any] = {}
        self._wire_channels: dict[int, Any] = {}
        self._channels_lock = threading.Lock()

    def _create_buffer(self, name: str, size: int, create: bool):
        if self._is_multithreading:
            return InMemoryRingBuffer.get_or_create(name, size=size, create=create)
        return RingSharedMemoryBuffer(name, size=size, create=create)

    def get_downstream_channel(self, channel_id: int, size: Optional[int] = None) -> Any:
        """Dynamically retrieve or allocate a dedicated downstream ring buffer for channel_id."""
        with self._channels_lock:
            buf = self._downstream_channels.get(channel_id)
            if buf is None:
                ch_name = get_downstream_channel_shm_name(channel_id)
                ch_size = size if size is not None else self.default_size
                buf = self._create_buffer(ch_name, size=ch_size, create=self.create)
                self._downstream_channels[channel_id] = buf
            return buf

    def get_wire_channel(self, channel_id: int, size: Optional[int] = None) -> Any:
        """Dynamically retrieve or allocate a dedicated inbound wire ring buffer for channel_id."""
        with self._channels_lock:
            buf = self._wire_channels.get(channel_id)
            if buf is None:
                ch_name = get_wire_channel_shm_name(channel_id)
                ch_size = size if size is not None else self.default_size
                buf = self._create_buffer(ch_name, size=ch_size, create=self.create)
                self._wire_channels[channel_id] = buf
            return buf

    def close(self):
        self.downstream.close()
        self.upstream.close()
        self.transcode_in.close()
        with self._channels_lock:
            for buf in self._downstream_channels.values():
                buf.close()
            self._downstream_channels.clear()
            for buf in self._wire_channels.values():
                buf.close()
            self._wire_channels.clear()
