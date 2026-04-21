"""
rfcomm.py — RFCOMM channel 8 socket listener.

Responsibilities:
  - Listen for incoming RFCOMM connections on channel 8 (AA profile)
  - Accept the socket and deliver it via on_connected_cb

Implementation note:
  Uses Python's built-in socket module with AF_BLUETOOTH / BTPROTO_RFCOMM
  instead of pybluez, which has broken or missing BluetoothSocket in many
  recent builds. AF_BLUETOOTH is available on any Linux kernel with Bluetooth
  support (no extra packages required).

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

# AF_BLUETOOTH / BTPROTO_RFCOMM constants (always present on Linux)
_AF_BLUETOOTH  = socket.AF_BLUETOOTH   # type: ignore[attr-defined]
_BTPROTO_RFCOMM = socket.BTPROTO_RFCOMM  # type: ignore[attr-defined]


class RfcommListener:
    """
    Listens for an incoming RFCOMM connection on channel 8.

    Usage:
        listener = RfcommListener(on_connected_cb=my_cb)
        listener.start()
        # ... later ...
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
            self._server_sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_STREAM, _BTPROTO_RFCOMM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Bind to any local adapter on the given channel
            self._server_sock.bind(("00:00:00:00:00:00", RFCOMM_CHANNEL))
            self._server_sock.listen(1)
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="rfcomm-accept")
            self._thread.start()
            log.info(f"RFCOMM listener started on channel {RFCOMM_CHANNEL}")
            return True
        except AttributeError:
            log.error("AF_BLUETOOTH not available — kernel Bluetooth support missing?")
            return False
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
                    client_sock, client_info = self._server_sock.accept()
                except socket.timeout:
                    continue
                # client_info is (bdaddr, channel) for AF_BLUETOOTH
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
