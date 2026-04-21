"""
rfcomm.py — RFCOMM channel 8 socket listener.

Usa socket nativo AF_BLUETOOTH/BTPROTO_RFCOMM.
Il bind richiede l'indirizzo reale dell'adapter (non 00:00:00:00:00:00)
quando il modulo rfcomm del kernel non è caricato come modulo separato.
L'indirizzo viene letto via `hciconfig hci0` al momento dell'avvio.

No ZMQ dependency — caller (main.py) handles publishing.
"""

import socket
import subprocess
import re
import threading
from typing import Callable

import sys
from pathlib import Path
_V2 = Path(__file__).parent.parent.parent
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

from shared.logger import get_logger  # noqa: E402

log = get_logger("bluetooth.rfcomm")

RFCOMM_CHANNEL  = 8
_AF_BLUETOOTH   = getattr(socket, "AF_BLUETOOTH",  31)
_BTPROTO_RFCOMM = getattr(socket, "BTPROTO_RFCOMM",  3)


def _get_adapter_address(iface: str = "hci0") -> str:
    """Return the BD address of the local adapter, e.g. '73:4E:4B:7F:EB:FF'."""
    try:
        out = subprocess.check_output(["hciconfig", iface], text=True, stderr=subprocess.DEVNULL)
        match = re.search(r"BD Address:\s*([0-9A-Fa-f:]{17})", out)
        if match:
            return match.group(1)
    except Exception as e:
        log.warning(f"hciconfig failed: {e}")
    return "00:00:00:00:00:00"


class RfcommListener:
    def __init__(self, on_connected_cb: Callable[[socket.socket, str], None] | None = None):
        self._on_connected_cb = on_connected_cb
        self._server_sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            local_addr = _get_adapter_address()
            log.info(f"Binding RFCOMM to {local_addr} ch.{RFCOMM_CHANNEL}")
            self._server_sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_STREAM, _BTPROTO_RFCOMM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((local_addr, RFCOMM_CHANNEL))
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
