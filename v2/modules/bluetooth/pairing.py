"""
pairing.py — Bluetooth pairing logic: agent registration, PIN exchange, confirm.

Responsibilities:
  - Register a BlueZ Agent2 via AgentManager1
  - Expose RequestPinCode / RequestConfirmation / DisplayPinCode D-Bus methods
  - Initiate pairing with a specific device address
  - Confirm pairing given device address + PIN

No ZMQ dependency — callbacks notify main.py which publishes on the bus.
"""

import logging
import random
import string
from typing import Callable

log = logging.getLogger("bluetooth.pairing")

AGENT_PATH = "/org/nemo/agent"
PAIRING_CAPABILITY = "DisplayYesNo"


class PairingAgent:
    """
    BlueZ Agent2 implementation.

    Callbacks (all optional, set before registering):
        on_pin_requested(device_address: str, pin: str)
            → called when BlueZ asks us to display the PIN
        on_pairing_completed(device_address: str)
            → called after successful pair + trust
        on_pairing_failed(device_address: str, error: str)
            → called on any pairing error
    """

    def __init__(
        self,
        adapter,  # BluezAdapter
        on_pin_requested: Callable[[str, str], None] | None = None,
        on_pairing_completed: Callable[[str], None] | None = None,
        on_pairing_failed: Callable[[str, str], None] | None = None,
    ):
        self._adapter = adapter
        self.on_pin_requested = on_pin_requested
        self.on_pairing_completed = on_pairing_completed
        self.on_pairing_failed = on_pairing_failed
        self._pending_pin: str = ""
        self._pending_address: str = ""
        self._registered = False

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def register(self) -> bool:
        """Register this agent with BlueZ AgentManager1."""
        try:
            import dbus
            import dbus.service

            # Build the D-Bus service object for the agent
            self._dbus_agent = _DBusAgent(
                self._adapter._bus,
                on_pin_code=self._handle_pin_code_request,
                on_confirm=self._handle_confirm_request,
            )
            mgr = dbus.Interface(
                self._adapter._bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            mgr.RegisterAgent(AGENT_PATH, PAIRING_CAPABILITY)
            mgr.RequestDefaultAgent(AGENT_PATH)
            self._registered = True
            log.info("Pairing agent registered")
            return True
        except Exception as e:
            log.error(f"Agent registration failed: {e}")
            return False

    def unregister(self) -> None:
        if not self._registered:
            return
        try:
            import dbus
            mgr = dbus.Interface(
                self._adapter._bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            mgr.UnregisterAgent(AGENT_PATH)
            self._registered = False
            log.info("Pairing agent unregistered")
        except Exception as e:
            log.warning(f"Agent unregister failed: {e}")

    # ------------------------------------------------------------------
    # Initiate pairing
    # ------------------------------------------------------------------

    def pair(self, device_address: str) -> None:
        """
        Initiate pairing with device_address asynchronously.
        Result is delivered via on_pairing_completed / on_pairing_failed.
        """
        self._pending_address = device_address
        self._pending_pin = self._generate_pin()
        log.info(f"Initiating pairing with {device_address}, pin={self._pending_pin}")
        try:
            import dbus
            import threading
            device_path = self._resolve_device_path(device_address)
            if not device_path:
                raise ValueError(f"Device {device_address} not found in BlueZ objects")
            device_iface = dbus.Interface(
                self._adapter._bus.get_object("org.bluez", device_path),
                "org.bluez.Device1",
            )
            # Pair is blocking in D-Bus — run in thread
            t = threading.Thread(
                target=self._do_pair, args=(device_iface, device_address), daemon=True
            )
            t.start()
        except Exception as e:
            log.error(f"pair() setup failed: {e}")
            if self.on_pairing_failed:
                self.on_pairing_failed(device_address, str(e))

    def confirm(self, device_address: str, pin: str) -> bool:
        """
        Called by main.py when the user confirms the displayed PIN.
        Returns True if the PIN matches what we generated.
        """
        if pin != self._pending_pin or device_address != self._pending_address:
            log.warning(f"confirm() PIN mismatch for {device_address}")
            if self.on_pairing_failed:
                self.on_pairing_failed(device_address, "PIN mismatch")
            return False
        log.info(f"PIN confirmed for {device_address}")
        # Trust the device so it auto-connects next time
        self._trust_device(device_address)
        if self.on_pairing_completed:
            self.on_pairing_completed(device_address)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_pair(self, device_iface, device_address: str) -> None:
        try:
            device_iface.Pair()
            log.info(f"D-Bus Pair() returned for {device_address}")
            # on_pairing_completed fired from confirm() after PIN ack
        except Exception as e:
            log.error(f"D-Bus Pair() failed for {device_address}: {e}")
            if self.on_pairing_failed:
                self.on_pairing_failed(device_address, str(e))

    def _handle_pin_code_request(self, device_path: str) -> str:
        """Called by _DBusAgent when BlueZ calls RequestPinCode."""
        addr = self._path_to_address(device_path)
        log.info(f"BlueZ requesting PIN for {addr} → {self._pending_pin}")
        if self.on_pin_requested:
            self.on_pin_requested(addr, self._pending_pin)
        return self._pending_pin

    def _handle_confirm_request(self, device_path: str, passkey: int) -> None:
        """Called by _DBusAgent when BlueZ calls RequestConfirmation."""
        addr = self._path_to_address(device_path)
        pin = str(passkey).zfill(6)
        self._pending_pin = pin
        self._pending_address = addr
        log.info(f"BlueZ confirm request for {addr} passkey={pin}")
        if self.on_pin_requested:
            self.on_pin_requested(addr, pin)

    def _resolve_device_path(self, address: str) -> str | None:
        try:
            import dbus
            manager = dbus.Interface(
                self._adapter._bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            for path, ifaces in manager.GetManagedObjects().items():
                dev = ifaces.get("org.bluez.Device1")
                if dev and str(dev.get("Address", "")) == address:
                    return path
        except Exception as e:
            log.error(f"_resolve_device_path error: {e}")
        return None

    def _path_to_address(self, device_path: str) -> str:
        """Convert /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF → AA:BB:CC:DD:EE:FF."""
        part = device_path.split("/")[-1]
        return part.replace("dev_", "").replace("_", ":")

    def _trust_device(self, address: str) -> None:
        try:
            import dbus
            device_path = self._resolve_device_path(address)
            if not device_path:
                return
            props = dbus.Interface(
                self._adapter._bus.get_object("org.bluez", device_path),
                "org.freedesktop.DBus.Properties",
            )
            props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
            log.info(f"Device {address} trusted")
        except Exception as e:
            log.warning(f"_trust_device failed: {e}")

    @staticmethod
    def _generate_pin(length: int = 6) -> str:
        return "".join(random.choices(string.digits, k=length))


# ---------------------------------------------------------------------------
# D-Bus Agent object (exposed on the session bus)
# ---------------------------------------------------------------------------

class _DBusAgent:
    """
    Minimal BlueZ Agent2 D-Bus object.
    Exposes RequestPinCode and RequestConfirmation.
    """

    IFACE = "org.bluez.Agent1"

    def __init__(
        self,
        bus,
        on_pin_code: Callable[[str], str],
        on_confirm: Callable[[str, int], None],
    ):
        try:
            import dbus.service
            import dbus.mainloop.glib

            class _Agent(dbus.service.Object):
                def __init__(self_, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self_._on_pin_code = on_pin_code
                    self_._on_confirm = on_confirm

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
                def RequestPinCode(self_, device):
                    return self_._on_pin_code(str(device))

                @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
                def RequestConfirmation(self_, device, passkey):
                    self_._on_confirm(str(device), int(passkey))

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Release(self_):
                    log.info("Agent Release called")

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Cancel(self_):
                    log.info("Agent Cancel called")

            self._agent_obj = _Agent(bus, AGENT_PATH)
            log.debug("_DBusAgent D-Bus object created")
        except Exception as e:
            log.error(f"_DBusAgent init failed: {e}")
