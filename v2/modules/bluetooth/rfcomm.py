"""
rfcomm.py — RFCOMM channel 8 socket listener.

Uses Python's built-in socket module with AF_BLUETOOTH / BTPROTO_RFCOMM.
When socket.AF_BLUETOOTH is not exposed (some minimal Python builds),
falls back to the stable Linux kernel constants (AF_BLUETOOTH=31,
BTPROTO_RFCOMM=3) which are valid on every Linux kernel with BT support.

No ZMQ dependency — caller (main.py) handles publishing.
"""

import socket
import threading
from typing import Callable

import sys
from pathlib import Path
_V2 = Path(__file__).parent.parent.parent
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

from shared.logger import get_logger  # noqa: E402

log = get_logger("bluetooth.rfcomm")

RFCOMM_CHANNEL = 8

# Linux kernel constants — stable across all distributions
_AF_BLUETOOTH   = getattr(socket, "AF_BLUETOOTH",  31)
_BTPROTO_RFCOMM = getattr(socket, "BTPROTO_RFCOMM",  3)


class RfcommListener:
    def __init__(self, on_connected_cb: Callable[[socket.socket, str], None] | None = None):
        self._on_connected_cb = on_connected_cb
        self._server_sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._server_sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_STREAM, _BTPROTO_RFCOMM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(("00:00:00:00:00:00", RFCOMM_CHANNEL))
            self._server_sock.listen(1)
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="rfcomm-accept")
            self._thread.start()
            log.info(f"RFCOMM listener started on channel {RFCOMM_CHANNEL}")
            return True
        except OSError as e:
            log.error(f"RFCOMM listen failed: {e}")
            return False
        except Exception as e:
            log.error(f"RFCOMM listen unexpected error: {e}")
            return False

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        log.info("RFCOMM listener stopped")

    def _accept_loop(self) -> None:
        log.info("RFCOMM accept loop running...")
        while self._running:
            try:
                if self._server_sock is None:
                    break
                self._server_sock.settimeout(2.0)
                try:
                    client_sock, client_info = self._server_sock.accept()
                except socket.timeout:
                    continue
                client_addr = client_info[0] if isinstance(client_info, tuple) else str(client_info)
                log.info(f"RFCOMM connection from {client_addr}")
                if self._on_connected_cb:
                    threading.Thread(
                        target=self._on_connected_cb,
                        args=(client_sock, client_addr),
                        daemon=True,
                        name=f"rfcomm-handler-{client_addr}",
                    ).start()
            except OSError as e:
                if self._running:
                    log.error(f"RFCOMM accept error: {e}")
                break
        log.info("RFCOMM accept loop exited")
