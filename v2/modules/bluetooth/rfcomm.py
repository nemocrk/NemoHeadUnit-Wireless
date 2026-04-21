"""
rfcomm.py — RFCOMM channel 8 socket listener.

Responsibilities:
  - Listen for incoming RFCOMM connections on channel 8 (AA profile)
  - Accept the socket and transfer ownership to RfcommHandler (in rfcomm_handshake module)
  - Since processes are isolated, ownership transfer is done by:
      1. Keeping the socket open inside this process
      2. Forwarding the raw bytes via a Unix domain socket bridge
         OR simply publishing `bluetooth.rfcomm.connected` with the
         peer address and letting rfcomm_handshake module open its own
         TCP connection once the handshake is complete.

NOTE: The RFCOMM socket itself lives in this process.
The handshake bytes are relayed internally within this module
(bluetooth/main.py calls rfcomm.py and pairing.py together).
The rfcomm_handshake v2 MODULE is therefore a sibling that reacts
to bus events only — the actual socket relay is done here.

No ZMQ dependency — caller (main.py) handles publishing.
"""

import logging
import socket
import threading
from typing import Callable

log = logging.getLogger("bluetooth.rfcomm")

RFCOMM_CHANNEL = 8


class RfcommListener:
    """
    Listens for an RFCOMM connection on the AA profile channel.

    Usage:
        def on_connected(sock, address):
            ...

        listener = RfcommListener(on_connected_cb=on_connected)
        listener.start()    # non-blocking, accepts in background thread
        listener.stop()
    """

    def __init__(self, on_connected_cb: Callable[[socket.socket, str], None] | None = None):
        self._on_connected_cb = on_connected_cb
        self._server_sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Bind RFCOMM socket and start accept loop in background."""
        try:
            import bluetooth  # pybluez
            self._server_sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            self._server_sock.bind(("", RFCOMM_CHANNEL))
            self._server_sock.listen(1)
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            log.info(f"RFCOMM listener started on channel {RFCOMM_CHANNEL}")
            return True
        except ImportError:
            log.error("pybluez not installed — RFCOMM listener unavailable")
            return False
        except Exception as e:
            log.error(f"RFCOMM listen failed: {e}")
            return False

    def stop(self) -> None:
        """Shutdown the listener."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        log.info("RFCOMM listener stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        log.info("RFCOMM accept loop running...")
        while self._running:
            try:
                if self._server_sock is None:
                    break
                self._server_sock.settimeout(2.0)
                try:
                    client_sock, client_addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                log.info(f"RFCOMM connection from {client_addr}")
                if self._on_connected_cb:
                    # Deliver in a thread so accept loop stays free
                    threading.Thread(
                        target=self._on_connected_cb,
                        args=(client_sock, str(client_addr)),
                        daemon=True,
                    ).start()
            except OSError as e:
                if self._running:
                    log.error(f"RFCOMM accept error: {e}")
                break
        log.info("RFCOMM accept loop exited")
