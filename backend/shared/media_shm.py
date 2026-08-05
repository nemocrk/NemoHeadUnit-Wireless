"""
media_shm.py — Cross-platform Bidirectional Shared Memory Engine for NemoHeadUnit-Wireless.

Provides zero-copy media transfer between `tcp_server` and `channel_manager`.

Architecture:
  - `nemo_media_shm_down`: Downstream ring buffer (Phone -> tcp_server -> SHM -> channel_manager -> WebCodecs).
  - `nemo_media_shm_up`: Upstream ring buffer (Frontend Mic -> channel_manager -> SHM -> tcp_server -> Phone).
  - Uses standard library `multiprocessing.shared_memory.SharedMemory` (compatible with Linux /dev/shm and Windows Named Memory Maps).
  - Patches resource tracker to prevent premature unlinking across processes.
"""

import logging
from multiprocessing import shared_memory
import struct
from typing import Optional, Tuple

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

DEFAULT_SHM_SIZE = 32 * 1024 * 1024  # 32MB ring buffer capacity
SHM_DOWNSTREAM_NAME = "nemo_media_shm_down"
SHM_UPSTREAM_NAME = "nemo_media_shm_up"
SHM_TRANSCODE_IN_NAME = "nemo_video_transcode_in"



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
            except FileNotFoundError:
                # Fallback: create if non-existent when attaching
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
            logger.debug("Invalid SHM frame magic at offset %d", offset)
            return 0, 0, b""


        if offset + 12 + length > self.size:
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



class BidirectionalMediaSHM:
    """
    Manages Downstream, Upstream, and Video Transcode Input shared memory buffers.
    """

    def __init__(self, create: bool = False, size: int = DEFAULT_SHM_SIZE):
        self.downstream = RingSharedMemoryBuffer(SHM_DOWNSTREAM_NAME, size=size, create=create)
        self.upstream = RingSharedMemoryBuffer(SHM_UPSTREAM_NAME, size=size, create=create)
        self.transcode_in = RingSharedMemoryBuffer(SHM_TRANSCODE_IN_NAME, size=size, create=create)

    def close(self):
        self.downstream.close()
        self.upstream.close()
        self.transcode_in.close()
