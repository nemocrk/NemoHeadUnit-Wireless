"""
bluez_adapter.py — BlueZ D-Bus adapter initialisation and profile registration.

Responsibilities:
  - Connect to org.bluez via D-Bus SystemBus
  - Retrieve the default Adapter1 proxy
  - Register HFP AG, HSP HS, AA RFCOMM profiles via ProfileManager1

This module has NO bus (ZMQ) dependency — it is a plain helper
instantiated by main.py after the broker is ready.
"""

import logging

log = logging.getLogger("bluetooth.bluez_adapter")

# Android Auto RFCOMM profile UUID
HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HSP_UUID = "00001108-0000-1000-8000-00805f9b34fb"
AA_UUID  = "4de17a00-52cb-11e6-bdf4-0800200c9a66"

RFCOMM_CHANNEL = 8


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
        """Register HFP AG, HSP HS and AA RFCOMM profiles."""
        if not self._initialized:
            log.warning("register_profiles called before init()")
            return False
        try:
            import dbus
            opts = dbus.Dictionary(
                {"Channel": dbus.UInt16(RFCOMM_CHANNEL), "AutoConnect": dbus.Boolean(True)},
                signature="sv",
            )
            self._profile_mgr.RegisterProfile("/org/bluez/profile/hfp", HFP_UUID, opts)
            log.info("Registered HFP AG profile")

            self._profile_mgr.RegisterProfile("/org/bluez/profile/hsp", HSP_UUID, opts)
            log.info("Registered HSP HS profile")

            self._profile_mgr.RegisterProfile("/org/bluez/profile/aa", AA_UUID, opts)
            log.info("Registered AA RFCOMM profile")

            return True
        except Exception as e:
            log.error(f"Profile registration failed: {e}")
            return False

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
