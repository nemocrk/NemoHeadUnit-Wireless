"""
server.py — TCP server for Android Auto protocol session.

Responsibilities:
  - Bind and listen on port 5288
  - Optionally wrap accepted socket with SSL/TLS (internal)
  - Accept exactly one connection at a time
  - Return the connected socket to the caller (main.py)

No ZMQ dependency.
"""

import logging
import socket
import ssl
from typing import Optional, Tuple

log = logging.getLogger("tcp_server.server")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5288
ACCEPT_TIMEOUT = 60  # seconds to wait for a connection


class TCPServer:
    """
    Single-connection TCP server for the Android Auto protocol session.

    Usage:
        srv = TCPServer()
        ok = srv.start()
        conn, addr = srv.accept()    # blocks up to ACCEPT_TIMEOUT
        srv.stop()

    SSL:
        Pass ssl_certfile + ssl_keyfile to enable TLS wrapping.
        The wrap is done internally after accept(), before returning
        the socket to the caller.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        ssl_certfile: Optional[str] = None,
        ssl_keyfile: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self._ssl_cert = ssl_certfile
        self._ssl_key  = ssl_keyfile
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

    def accept(self, timeout: int = ACCEPT_TIMEOUT) -> Optional[Tuple[socket.socket, str]]:
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
            conn = self._wrap_ssl(raw_sock) if self._ssl_cert else raw_sock
            return conn, address_str
        except socket.timeout:
            log.warning("TCP accept timed out — no connection received")
            return None
        except Exception as e:
            if self._running:
                log.error(f"TCP accept error: {e}")
            return None

    # ------------------------------------------------------------------
    # SSL
    # ------------------------------------------------------------------

    def _wrap_ssl(self, raw_sock: socket.socket) -> socket.socket:
        """
        Wrap raw socket with TLS using server-side cert + key.
        Falls back to plain socket on error.
        """
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=self._ssl_cert, keyfile=self._ssl_key)
            wrapped = ctx.wrap_socket(raw_sock, server_side=True)
            log.info("SSL/TLS wrap applied")
            return wrapped
        except Exception as e:
            log.error(f"SSL wrap failed, falling back to plain socket: {e}")
            return raw_sock

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
