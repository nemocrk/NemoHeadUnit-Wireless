"""
frame_relay.py — Read Android Auto frames from TCP socket and relay to bus.

Responsibilities:
  - Read frames from the connected socket in a loop
  - Parse the AA frame header: [length:u32_be][channel:u8][flags:u8]
  - Publish each frame as aa.frame.received on the bus
  - Detect socket close and publish aa.session.closed

Frame format (Android Auto over TCP):
  Byte 0-3 : payload length (u32 big-endian)
  Byte 4   : channel_id
  Byte 5   : flags (0x0B = first+last+encrypted frame)
  Byte 6+  : payload bytes

No ZMQ dependency — caller injects a publish callable.
"""

import logging
import socket
import struct
from typing import Callable, Optional

log = logging.getLogger("tcp_server.frame_relay")

FRAME_HEADER_SIZE = 6  # length(4) + channel(1) + flags(1)


class FrameRelay:
    """
    Reads AA frames from a connected TCP socket and relays them via callback.

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

    # ------------------------------------------------------------------
    # Frame reading
    # ------------------------------------------------------------------

    def _read_frame(self) -> Optional[tuple]:
        """
        Read one AA frame from the socket.
        Returns (channel_id, flags, payload) or None on error/close.
        """
        header = self._recv_exact(FRAME_HEADER_SIZE)
        if not header:
            return None
        payload_len = struct.unpack_from(">I", header, 0)[0]
        channel_id  = header[4]
        flags       = header[5]
        payload = self._recv_exact(payload_len) if payload_len else b""
        if payload is None:
            return None
        log.debug(
            f"Frame: channel={channel_id} flags=0x{flags:02x} len={payload_len}"
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
