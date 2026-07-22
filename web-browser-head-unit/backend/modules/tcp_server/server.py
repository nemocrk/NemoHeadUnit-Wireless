"""
server.py — TCP server for Android Auto protocol session.

Responsibilities:
  - Bind and listen on port 5288
  - Accept exactly one plain TCP connection at a time
  - Return the connected socket to the caller (main.py)

TLS note:
  Android Auto does NOT use TLS directly on the TCP socket.
  Encryption is negotiated in-band as AA frames on channel 0 (msgId 0x0003).
  The Cryptor in openauto-prodigy uses memory BIOs: SSL handshake bytes are
  exchanged as AA frame payloads, not as raw TLS records on the wire.
  → This server must remain plain TCP. TLS is handled by oaa_control_channel.

No ZMQ dependency.
"""

from shared.logger import get_logger
import socket
from typing import Optional, Tuple

log = get_logger("tcp_server.server")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5288
ACCEPT_TIMEOUT = 60  # seconds to wait for a connection


class TCPServer:
    """
    Single-connection plain TCP server for the Android Auto protocol session.

    Usage:
        srv = TCPServer()
        ok = srv.start()
        conn, addr = srv.accept()    # blocks up to ACCEPT_TIMEOUT
        srv.stop()
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ):
        self.host = host
        self.port = port
        self._server_sock: Optional[socket.socket] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Bind and start listening. Returns True on success."""
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(1)
            self._running = True
            log.info(f"TCP server listening on {self.host}:{self.port}")
            return True
        except Exception as e:
            log.error(f"TCP server start failed: {e}")
            return False

    def stop(self) -> None:
        """Close server socket."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        log.info("TCP server stopped")

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def accept(self, timeout: Optional[int] = None) -> Optional[Tuple[socket.socket, str]]:
        """
        Block until a client connects or timeout expires.
        Returns (socket, address_str) or None on timeout/error.
        """
        if not self._running or not self._server_sock:
            return None
        try:
            self._server_sock.settimeout(timeout)
            raw_sock, addr = self._server_sock.accept()
            address_str = f"{addr[0]}:{addr[1]}"
            log.info(f"Connection accepted from {address_str}")
            return raw_sock, address_str
        except socket.timeout:
            log.warning("TCP accept timed out — no connection received")
            return None
        except Exception as e:
            if self._running:
                log.error(f"TCP accept error: {e}")
            return None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
