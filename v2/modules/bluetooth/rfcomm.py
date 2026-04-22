"""
rfcomm.py — RFCOMM listener via BlueZ D-Bus Profile1 interface.

Registra un oggetto D-Bus che implementa org.bluez.Profile1.
BlueZ consegna il file descriptor via NewConnection() ogni volta che
un dispositivo remoto si connette al canale RFCOMM registrato.

Nessuna dipendenza da socket AF_BLUETOOTH nativo né da hciconfig:
richiede solo dbus-python + gi (già presenti nell'environment).

No ZMQ dependency — caller (main.py) handles publishing.
"""

import os
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
AA_UUID        = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
_PROFILE_PATH  = "/org/nemo/rfcomm/aa"


class _RfcommProfile:
    """
    Oggetto D-Bus che implementa org.bluez.Profile1.

    BlueZ chiama NewConnection(device_path, fd, properties) quando un
    dispositivo si connette al profilo registrato.  Il file descriptor
    viene wrappato in un socket Python e passato alla callback.
    """

    DBUS_INTERFACE = "org.bluez.Profile1"

    def __init__(self, on_connected_cb: Callable[[socket.socket, str], None] | None):
        self._on_connected_cb = on_connected_cb

    # ------------------------------------------------------------------
    # org.bluez.Profile1 methods
    # ------------------------------------------------------------------

    def Release(self):
        log.info("Profile1.Release called by BlueZ")

    def NewConnection(self, device_path: str, fd, properties):
        """Called by BlueZ when a remote device connects."""
        try:
            # fd arriva come dbus.types.UnixFd — estraiamo l'int
            raw_fd: int = fd.take() if hasattr(fd, "take") else int(fd)
            sock = socket.fromfd(raw_fd, socket.AF_UNIX, socket.SOCK_STREAM)
            os.close(raw_fd)  # fromfd duplica l'fd; chiudi l'originale

            # Ricava l'indirizzo del device dal path D-Bus
            # es. /org/bluez/hci0/dev_73_4E_4B_7F_EB_FF → 73:4E:4B:7F:EB:FF
            device_addr = device_path.split("/dev_")[-1].replace("_", ":") if "/dev_" in str(device_path) else str(device_path)
            log.info(f"RFCOMM NewConnection from {device_addr}")

            if self._on_connected_cb:
                threading.Thread(
                    target=self._on_connected_cb,
                    args=(sock, device_addr),
                    daemon=True,
                    name=f"rfcomm-handler-{device_addr}",
                ).start()
        except Exception as e:
            log.error(f"NewConnection handler error: {e}")

    def RequestDisconnection(self, device_path: str):
        log.info(f"Profile1.RequestDisconnection: {device_path}")


class RfcommListener:
    """
    Registra il profilo AA RFCOMM su BlueZ via D-Bus e riceve
    connessioni tramite NewConnection() del Profile1 object.

    Interfaccia pubblica identica alla versione precedente:
        listener = RfcommListener(on_connected_cb=...)
        listener.start() -> bool
        listener.stop()
    """

    def __init__(self, on_connected_cb: Callable[[socket.socket, str], None] | None = None):
        self._on_connected_cb = on_connected_cb
        self._bus = None
        self._profile_obj = None
        self._registered = False

    def start(self) -> bool:
        try:
            import dbus
            import dbus.service

            self._bus = dbus.SystemBus()

            self._profile_obj = _RfcommProfileService(
                conn=self._bus,
                object_path=_PROFILE_PATH,
                on_connected_cb=self._on_connected_cb,
            )

            profile_mgr = dbus.Interface(
                self._bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.ProfileManager1",
            )
            opts = dbus.Dictionary(
                {
                    "Channel":     dbus.UInt16(RFCOMM_CHANNEL),
                    "AutoConnect": dbus.Boolean(True),
                    "Role":        dbus.String("server"),
                    "Name":        dbus.String("NemoHeadUnit AA"),
                },
                signature="sv",
            )
            profile_mgr.RegisterProfile(_PROFILE_PATH, AA_UUID, opts)
            self._registered = True
            log.info(f"RFCOMM Profile1 registered: {AA_UUID} ch.{RFCOMM_CHANNEL}")
            return True

        except Exception as e:
            log.error(f"RFCOMM Profile1 registration failed: {e}")
            return False

    def stop(self) -> None:
        if self._registered and self._bus:
            try:
                import dbus
                profile_mgr = dbus.Interface(
                    self._bus.get_object("org.bluez", "/org/bluez"),
                    "org.bluez.ProfileManager1",
                )
                profile_mgr.UnregisterProfile(_PROFILE_PATH)
                log.info("RFCOMM Profile1 unregistered")
            except Exception as e:
                log.warning(f"UnregisterProfile failed (non-fatal): {e}")
            self._registered = False
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
        log.info("RFCOMM listener stopped")


class _RfcommProfileService(dbus.service.Object):
    """
    dbus.service.Object che espone org.bluez.Profile1 sul system bus.
    """

    def __init__(self, conn, object_path: str, on_connected_cb):
        import dbus.service
        super().__init__(conn=conn, object_path=object_path)
        self._on_connected_cb = on_connected_cb

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        log.info("Profile1.Release")

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device_path, fd, properties):
        try:
            raw_fd: int = fd.take() if hasattr(fd, "take") else int(fd)
            sock = socket.fromfd(raw_fd, socket.AF_UNIX, socket.SOCK_STREAM)
            os.close(raw_fd)

            device_addr = (
                device_path.split("/dev_")[-1].replace("_", ":")
                if "/dev_" in str(device_path)
                else str(device_path)
            )
            log.info(f"RFCOMM NewConnection from {device_addr}")

            if self._on_connected_cb:
                threading.Thread(
                    target=self._on_connected_cb,
                    args=(sock, device_addr),
                    daemon=True,
                    name=f"rfcomm-handler-{device_addr}",
                ).start()
        except Exception as e:
            log.error(f"NewConnection error: {e}")

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device_path):
        log.info(f"Profile1.RequestDisconnection: {device_path}")
