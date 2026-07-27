"""
dbus_rfcomm.py - BlueZ D-Bus RFCOMM Profile1 listener for AA handshake.

BlueZ owns the native Bluetooth socket setup.  This module registers an
org.bluez.Profile1 object and receives the connected RFCOMM file descriptor
through NewConnection(), avoiding PyBluez and AF_BLUETOOTH client sockets.
"""

import os
import socket
import threading
from typing import Callable, Optional

import dbus
import dbus.service


from shared.logger import get_logger  # noqa: E402

log = get_logger("rfcomm_handshake.dbus_rfcomm")

if not os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"):
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/run/dbus/system_bus_socket"

AA_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
RFCOMM_CHANNEL = 30
PROFILE_PATH = "/org/nemo/rfcomm_handshake/aa"

SERVICE_RECORD = """
<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence>
      <uuid value="4de17a00-52cb-11e6-bdf4-0800200c9a66"/>
    </sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100"/></sequence>
      <sequence>
        <uuid value="0x0003"/>
        <uint8 value="0x1e"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100">
    <text value="NemoHeadUnit AA"/>
  </attribute>
</record>
"""


def setup_glib_mainloop() -> None:
    """Install dbus-python's GLib mainloop before SystemBus is created."""
    try:
        import dbus.mainloop.glib

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        log.debug("GLib D-Bus mainloop installed")
    except Exception as e:
        log.warning(f"GLib D-Bus mainloop setup failed: {e}")


class _RfcommProfileService(dbus.service.Object):
    """D-Bus object implementing org.bluez.Profile1."""

    def __init__(
        self,
        conn,
        object_path: str,
        on_connected_cb: Callable[[socket.socket, str], None],
    ):
        super().__init__(conn=conn, object_path=object_path)
        self._on_connected_cb = on_connected_cb

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        log.info("Profile1.Release")

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device_path, fd, properties):
        raw_fd = None
        try:
            raw_fd = fd.take() if hasattr(fd, "take") else int(fd)
            sock = _socket_from_fd(raw_fd)
            os.close(raw_fd)
            raw_fd = None

            device_addr = _device_address_from_path(str(device_path))
            log.info(f"RFCOMM NewConnection from {device_addr}")

            threading.Thread(
                target=self._on_connected_cb,
                args=(sock, device_addr),
                daemon=True,
                name=f"rfcomm-handshake-{device_addr}",
            ).start()
        except Exception as e:
            if raw_fd is not None:
                try:
                    os.close(raw_fd)
                except Exception:
                    pass
            log.error(f"Profile1.NewConnection failed: {e}")

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device_path):
        log.info(f"Profile1.RequestDisconnection: {device_path}")


class DbusRfcommListener:
    """Register the AA RFCOMM Profile1 service and deliver connected sockets."""

    def __init__(self, on_connected_cb: Callable[[socket.socket, str], None]):
        self._on_connected_cb = on_connected_cb
        self._bus = None
        self._profile_obj: Optional[_RfcommProfileService] = None
        self._registered = False

    def start(self) -> bool:
        try:
            setup_glib_mainloop()
            self._bus = dbus.SystemBus()
            # Skip check or fail gracefully if bus is mocked in unit tests
            if hasattr(self._bus, "list_names"):
                try:
                    names = self._bus.list_names()
                    if isinstance(names, (list, tuple)):
                        if "org.bluez" not in names:
                            log.warning("BlueZ service 'org.bluez' is not registered on D-Bus — skipping listener startup")
                            return False
                except Exception:
                    pass

            self._profile_obj = _RfcommProfileService(
                conn=self._bus,
                object_path=PROFILE_PATH,
                on_connected_cb=self._on_connected_cb,
            )

            profile_mgr = dbus.Interface(
                self._bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.ProfileManager1",
            )
            opts = dbus.Dictionary(
                {
                    "Channel": dbus.UInt16(RFCOMM_CHANNEL),
                    "Role": dbus.String("server"),
                    "Name": dbus.String("NemoHeadUnit AA"),
                    "ServiceRecord": dbus.String(SERVICE_RECORD),
                    "RequireAuthentication": dbus.Boolean(False),
                    "RequireAuthorization": dbus.Boolean(False),
                    "AutoConnect": dbus.Boolean(False),
                },
                signature="sv",
            )
            profile_mgr.RegisterProfile(PROFILE_PATH, AA_UUID, opts)
            self._registered = True
            log.info(f"RFCOMM Profile1 registered: {AA_UUID} ch.{RFCOMM_CHANNEL}")
            return True
        except Exception as e:
            log.error(f"RFCOMM Profile1 registration failed: {e}")
            return False

    def stop(self) -> None:
        if self._registered and self._bus:
            try:
                profile_mgr = dbus.Interface(
                    self._bus.get_object("org.bluez", "/org/bluez"),
                    "org.bluez.ProfileManager1",
                )
                profile_mgr.UnregisterProfile(PROFILE_PATH)
                log.info("RFCOMM Profile1 unregistered")
            except Exception as e:
                log.warning(f"UnregisterProfile failed (non-fatal): {e}")
            self._registered = False
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
        self._bus = None
        self._profile_obj = None


def _socket_from_fd(raw_fd: int) -> socket.socket:
    family = getattr(socket, "AF_BLUETOOTH", socket.AF_UNIX)
    proto = getattr(socket, "BTPROTO_RFCOMM", 0) if family != socket.AF_UNIX else 0
    return socket.fromfd(raw_fd, family, socket.SOCK_STREAM, proto)


def _device_address_from_path(device_path: str) -> str:
    if "/dev_" not in device_path:
        return device_path
    return device_path.split("/dev_")[-1].replace("_", ":")
