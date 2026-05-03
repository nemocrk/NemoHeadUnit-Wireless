"""
frame_relay.py — Read Android Auto frames from TCP socket and relay to bus.

Responsibilities:
  - Read frames from the connected socket in a loop
  - Parse the AA frame header and dispatch to callback
  - Detect socket close and notify via on_closed_cb
  - Expose send_raw() for writing pre-encoded frames back to the phone

Wire format (verified against openauto-prodigy FrameSerializer.cpp + FrameHeader.cpp):

  BULK / MIDDLE / LAST frame:
    Byte 0   : channel_id
    Byte 1   : flags  (bits: frameType[1:0] | messageType[2] | encryptionType[3])
    Byte 2-3 : payload length  (u16 big-endian)
    Byte 4+  : payload bytes

  FIRST frame (frameType bits = 0x01):
    Byte 0   : channel_id
    Byte 1   : flags
    Byte 2-3 : this-frame payload length  (u16 big-endian)
    Byte 4-7 : total message length        (u32 big-endian)
    Byte 8+  : payload bytes

  FrameType encoding (flags & 0x03):
    0x00 = Bulk   (single-frame message, most common)
    0x01 = First  (first of multi-frame message)
    0x02 = Middle
    0x03 = Last

No ZMQ dependency — caller injects callbacks.
"""

from shared.logger import get_logger
import socket
import struct
from typing import Callable, Optional

log = get_logger("tcp_server.frame_relay")

# Frame header is always 2 bytes: [channel_id][flags]
FRAME_HEADER_SIZE = 2
# Size field after header is always 2 bytes (u16 BE)
FRAME_SIZE_FIELD  = 2
# FIRST frames have an extra 4-byte total-length field
FRAME_TOTAL_LEN_FIELD = 4

FRAMETYPE_FIRST = 0x01


class FrameRelay:
    """
    Reads AA frames from a connected TCP socket and relays them via callback.
    Also exposes send_raw() so other components (e.g. oaa_control_channel)
    can write frames back to the phone.

    Usage:
        def on_frame(channel_id, flags, payload):
            bus.publish("aa.frame.received", {...})

        relay = FrameRelay(sock, on_frame_cb=on_frame, on_closed_cb=on_closed)
        relay.start()   # runs receive loop in current thread (blocking)
        relay.stop()    # called from another thread to abort
    """

    def __init__(
        self,
        sock: socket.socket,
        on_frame_cb: Callable[[int, int, bytes], None],
        on_closed_cb: Optional[Callable[[], None]] = None,
    ):
        self._sock = sock
        self._on_frame = on_frame_cb
        self._on_closed = on_closed_cb
        self._running = False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the receive loop. Blocks until socket closes or stop() is called."""
        self._running = True
        log.info("FrameRelay started")
        try:
            while self._running:
                frame = self._read_frame()
                if frame is None:
                    log.info("Socket closed or read error — ending relay")
                    break
                channel_id, flags, payload = frame
                self._on_frame(channel_id, flags, payload)
        finally:
            self._running = False
            if self._on_closed:
                self._on_closed()
            log.info("FrameRelay stopped")

    def stop(self) -> None:
        """Signal the receive loop to stop."""
        self._running = False
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def send_raw(self, data: bytes) -> None:
        """Write *data* verbatim to the socket (caller is responsible for framing).

        Thread-safe if callers serialise access externally (tcp_server uses _write_lock).
        Raises OSError / BrokenPipeError on socket failure.
        """
        total = 0
        view = memoryview(data)
        while total < len(data):
            sent = self._sock.send(view[total:])
            if sent == 0:
                raise BrokenPipeError("send_raw: socket closed mid-write")
            total += sent

    # ------------------------------------------------------------------
    # Frame reading
    # ------------------------------------------------------------------

    def _read_frame(self) -> Optional[tuple]:
        """
        Read one AA frame from the socket.
        Returns (channel_id, flags, payload) or None on error/close.

        Layout (BULK frame, the common case):
          [channel_id:1B][flags:1B][payload_len:2B_BE][payload]

        FIRST frames have an extra 4B total_len field before the payload;
        we read and discard it here (reassembly is out of scope for the relay).
        """
        # 1. Read 2-byte header
        header = self._recv_exact(FRAME_HEADER_SIZE)
        if not header:
            return None
        channel_id = header[0]
        flags      = header[1]
        frame_type = flags & 0x03

        # 2. Read 2-byte payload length
        size_field = self._recv_exact(FRAME_SIZE_FIELD)
        if not size_field:
            return None
        payload_len = struct.unpack(">H", size_field)[0]

        # 3. FIRST frames carry an extra 4-byte total-message-length field
        if frame_type == FRAMETYPE_FIRST:
            total_len_field = self._recv_exact(FRAME_TOTAL_LEN_FIELD)
            if not total_len_field:
                return None
            # total_len available if needed: struct.unpack(">I", total_len_field)[0]

        # 4. Read payload
        payload = self._recv_exact(payload_len) if payload_len else b""

        log.info(
            f"Frame: channel={channel_id} flags=0x{flags:02x} len={payload_len if payload else 0}"
        )

        if payload is None:
            return None

        log.debug(
            "Frame: channel=%d flags=0x%02x type=%d len=%d",
            channel_id, flags, frame_type, payload_len,
        )
        return channel_id, flags, payload

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Read exactly n bytes from the socket."""
        buf = b""
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except Exception as e:
                if self._running:
                    log.error(f"recv error: {e}")
                return None
            if not chunk:
                return None
            buf += chunk
        return buf
