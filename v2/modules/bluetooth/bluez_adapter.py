"""
bluez_adapter.py — BlueZ D-Bus adapter initialisation and profile registration.

Responsibilities:
  - Connect to org.bluez via D-Bus SystemBus
  - Retrieve the default Adapter1 proxy
  - Register HFP AG, HSP HS profiles via ProfileManager1

Note: AA RFCOMM profile (AA_UUID) is registered by RfcommListener in rfcomm.py
      as a proper Profile1 D-Bus object so BlueZ can deliver NewConnection() fd.

This module has NO bus (ZMQ) dependency — it is a plain helper
instantiated by main.py after the broker is ready.
"""

import logging
import os

log = logging.getLogger("bluetooth.bluez_adapter")

# Workaround: conda injects DBUS_SYSTEM_BUS_ADDRESS as empty string,
# causing dbus-python to fall back to a non-existent conda-env socket.
if not os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"):
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/run/dbus/system_bus_socket"

HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HSP_UUID = "00001108-0000-1000-8000-00805f9b34fb"
# AA_UUID is owned by rfcomm.py — do not register here to avoid duplicate UUID error.

RFCOMM_CHANNEL = 8


def _setup_glib_mainloop() -> None:
    """Install GLib mainloop as default D-Bus mainloop.

    Must be called once before the first dbus.SystemBus() so that
    BlueZ agent callbacks (RequestConfirmation, RequestPinCode …)
    are dispatched by the GLib event loop while blocking D-Bus calls
    (e.g. Device1.Pair()) are running in a background thread.
    """
    try:
        import dbus.mainloop.glib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        log.debug("GLib D-Bus mainloop installed")
    except Exception as e:
        log.warning(f"GLib mainloop setup failed (agent callbacks may not work): {e}")


# Install mainloop at import time so it is always set before any SystemBus().
_setup_glib_mainloop()


class BluezAdapter:
    """
    Wraps the BlueZ D-Bus Adapter1 and ProfileManager1 interfaces.

    Usage:
        adapter = BluezAdapter()
        ok = adapter.init()          # connect D-Bus
        ok = adapter.register_profiles()
        adapter.set_name("NemoHeadUnit")
        adapter.set_discoverable(True, timeout=0)
        adapter.shutdown()
    """

    def __init__(self):
        self._bus = None          # dbus.SystemBus
        self._adapter = None      # org.bluez.Adapter1 proxy
        self._profile_mgr = None  # org.bluez.ProfileManager1 proxy
        self._initialized = False

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def init(self) -> bool:
        """Connect to D-Bus and obtain BlueZ Adapter1 proxy."""
        try:
            import dbus
            self._bus = dbus.SystemBus()
            manager = dbus.Interface(
                self._bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            objects = manager.GetManagedObjects()
            adapter_path = self._find_adapter_path(objects)
            if not adapter_path:
                log.error("No Bluetooth adapter found via BlueZ")
                return False

            self._adapter = dbus.Interface(
                self._bus.get_object("org.bluez", adapter_path),
                "org.bluez.Adapter1",
            )
            self._profile_mgr = dbus.Interface(
                self._bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.ProfileManager1",
            )
            self._initialized = True
            log.info(f"BlueZ adapter initialised at {adapter_path}")
            return True
        except Exception as e:
            log.error(f"D-Bus init failed: {e}")
            return False

    def _find_adapter_path(self, objects: dict) -> str | None:
        for path, ifaces in objects.items():
            if "org.bluez.Adapter1" in ifaces:
                return path
        return None

    # ------------------------------------------------------------------
    # Profile registration
    # ------------------------------------------------------------------

    def register_profiles(self) -> bool:
        """Register HFP AG and HSP HS profiles.

        AA RFCOMM profile is registered separately by RfcommListener
        as a Profile1 D-Bus object so BlueZ delivers NewConnection() fd.

        If a profile UUID is already registered (e.g. after an unclean
        shutdown), the error is treated as non-fatal and registration
        continues for the remaining profiles.
        """
        if not self._initialized:
            log.warning("register_profiles called before init()")
            return False
        try:
            import dbus
            opts = dbus.Dictionary(
                {"Channel": dbus.UInt16(RFCOMM_CHANNEL), "AutoConnect": dbus.Boolean(True)},
                signature="sv",
            )
            self._register_one("/org/bluez/profile/hfp", HFP_UUID, opts, "HFP AG")
            self._register_one("/org/bluez/profile/hsp", HSP_UUID, opts, "HSP HS")
            return True
        except Exception as e:
            log.error(f"Profile registration failed: {e}")
            return False

    def _register_one(self, path: str, uuid: str, opts, label: str) -> None:
        """Register a single profile, ignoring 'UUID already registered'."""
        try:
            self._profile_mgr.RegisterProfile(path, uuid, opts)
            log.info(f"Registered {label} profile")
        except Exception as e:
            if "UUID already registered" in str(e) or "org.bluez.Error.NotPermitted" in str(e):
                log.warning(f"{label} profile already registered — skipping")
            else:
                raise

    # ------------------------------------------------------------------
    # Adapter controls
    # ------------------------------------------------------------------

    def set_discoverable(self, enabled: bool, timeout: int = 0) -> None:
        """Make adapter discoverable. timeout=0 → permanent."""
        if not self._initialized:
            return
        try:
            import dbus
            props = dbus.Interface(
                self._bus.get_object("org.bluez", self._adapter.object_path),
                "org.freedesktop.DBus.Properties",
            )
            props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(enabled))
            props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(timeout))
            log.info(f"Adapter discoverable={enabled} timeout={timeout}")
        except Exception as e:
            log.error(f"set_discoverable failed: {e}")

    def set_name(self, name: str) -> None:
        """Set the Bluetooth adapter alias (visible name during discovery)."""
        if not self._initialized:
            log.warning("set_name called before init()")
            return
        try:
            import dbus
            props = dbus.Interface(
                self._bus.get_object("org.bluez", self._adapter.object_path),
                "org.freedesktop.DBus.Properties",
            )
            props.Set("org.bluez.Adapter1", "Alias", dbus.String(name))
            log.info(f"Adapter name set to '{name}'")
        except Exception as e:
            log.error(f"set_name failed: {e}")

    def get_adapter_address(self) -> str:
        """Return the local adapter MAC address."""
        if not self._initialized:
            return ""
        try:
            import dbus
            props = dbus.Interface(
                self._bus.get_object("org.bluez", self._adapter.object_path),
                "org.freedesktop.DBus.Properties",
            )
            return str(props.Get("org.bluez.Adapter1", "Address"))
        except Exception as e:
            log.error(f"get_adapter_address failed: {e}")
            return ""

    def shutdown(self) -> None:
        """Release D-Bus connection."""
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
        self._initialized = False
        log.info("BluezAdapter shutdown")
