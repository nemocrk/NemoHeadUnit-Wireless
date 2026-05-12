"""
pairing.py — Bluetooth pairing logic: agent registration, PIN exchange, confirm.

Responsibilities:
  - Register a BlueZ Agent1 via AgentManager1
  - Expose RequestPinCode / RequestConfirmation / DisplayPinCode D-Bus methods
  - Initiate pairing with a specific device address
  - Confirm pairing given device address + PIN

No ZMQ dependency — callbacks notify main.py which publishes on the bus.

Pairing flow (SSP — used by Android/modern devices):
  1. pair(address) is called → calls Device1.Pair() ASYNC (no timeout)
  2. BlueZ calls RequestConfirmation(device, passkey) on the agent
  3. Agent stores passkey, fires on_pin_requested
  4. A background thread waits up to AUTO_ACCEPT_TIMEOUT_S for user input.
     If no confirm/reject arrives within the timeout, pairing is auto-accepted.
  5. User (or auto-accept) sets _confirm_event → background thread resolves
     the pending D-Bus reply via the stored reply_handler/error_handler.
  6. _pair_reply_handler fires → device is trusted → on_pairing_completed called

Legacy PIN flow (older headsets that call RequestPinCode):
  1. BlueZ calls RequestPinCode → agent returns a generated PIN
  2. User types PIN on the remote device

Key design notes:
  - Device1.Pair() is called with async reply_handler/error_handler so that
    dbus-python never applies its ~25 s timeout while waiting for confirmation.
  - RequestConfirmation returns immediately to the GLib mainloop; the actual
    D-Bus reply is sent later from a background thread via stored callbacks.
    This keeps the GLib thread free to dispatch Cancel and other agent methods.
"""

import sys
import threading
from pathlib import Path
from typing import Callable

_V2 = Path(__file__).parent.parent.parent
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

from shared.logger import get_logger  # noqa: E402

log = get_logger("bluetooth.pairing")

AGENT_PATH = "/org/nemo/agent"
PAIRING_CAPABILITY = "DisplayYesNo"
AUTO_ACCEPT_TIMEOUT_S = 5  # seconds before auto-accepting if no user input


class PairingAgent:
    """
    BlueZ Agent1 implementation.

    Callbacks (all optional, set before registering):
        on_pin_requested(device_address: str, pin: str)
        on_pairing_completed(device_address: str)
        on_pairing_failed(device_address: str, error: str)
    """

    def __init__(
        self,
        adapter,
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

        self._confirm_event = threading.Event()
        self._confirm_accepted = False

        # Stored D-Bus reply callbacks for async RequestConfirmation.
        # Set by _handle_confirm_request, consumed by _confirm_worker.
        self._dbus_reply_handler: Callable | None = None
        self._dbus_error_handler: Callable | None = None

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def register(self) -> bool:
        try:
            import dbus.service

            self._dbus_agent = _DBusAgent(
                self._adapter._bus,
                on_pin_code=self._handle_pin_code_request,
                on_confirm=self._handle_confirm_request,
                on_cancel=self._handle_cancel,
            )
            import dbus
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
    # Initiate pairing — async Pair() call
    # ------------------------------------------------------------------

    def pair(self, device_address: str) -> None:
        """
        Initiate pairing asynchronously.
        Device1.Pair() is called with reply_handler/error_handler so
        dbus-python never times out while waiting for user confirmation.
        """
        self._pending_address = device_address
        self._pending_pin = ""
        self._confirm_event.clear()
        self._confirm_accepted = False
        log.info(f"Initiating pairing with {device_address}")
        try:
            import dbus
            device_path = self._resolve_device_path(device_address)
            if not device_path:
                raise ValueError(f"Device {device_address} not found in BlueZ objects")
            device_iface = dbus.Interface(
                self._adapter._bus.get_object("org.bluez", device_path),
                "org.bluez.Device1",
            )
            # Async call: no blocking, no timeout on our side.
            # reply_handler is called by GLib mainloop when Pair() succeeds.
            device_iface.Pair(
                reply_handler=lambda: self._pair_reply_handler(device_address),
                error_handler=lambda e: self._pair_error_handler(device_address, e),
            )
            log.debug(f"Pair() async call dispatched for {device_address}")
        except Exception as e:
            log.error(f"pair() setup failed: {e}")
            if self.on_pairing_failed:
                self.on_pairing_failed(device_address, str(e))

    def _pair_reply_handler(self, device_address: str) -> None:
        """Called by GLib mainloop when Device1.Pair() completes successfully."""
        log.info(f"D-Bus Pair() completed for {device_address}")
        self._trust_device(device_address)
        if self.on_pairing_completed:
            self.on_pairing_completed(device_address)

    def _pair_error_handler(self, device_address: str, error) -> None:
        """Called by GLib mainloop when Device1.Pair() fails."""
        # AlreadyExists means the device was already paired — treat as success
        err_str = str(error)
        if "AlreadyExists" in err_str:
            log.info(f"Device {device_address} already paired — treating as success")
            self._trust_device(device_address)
            if self.on_pairing_completed:
                self.on_pairing_completed(device_address)
            return
        log.error(f"D-Bus Pair() failed for {device_address}: {error}")
        if self.on_pairing_failed:
            self.on_pairing_failed(device_address, err_str)

    # ------------------------------------------------------------------
    # User confirmation
    # ------------------------------------------------------------------

    def confirm(self, device_address: str, pin: str) -> bool:
        if device_address != self._pending_address:
            log.warning(f"confirm() address mismatch: got {device_address}, expected {self._pending_address}")
            return False
        if pin != self._pending_pin:
            log.warning(f"confirm() PIN mismatch for {device_address}")
            self._confirm_accepted = False
            self._confirm_event.set()
            return False
        log.info(f"User confirmed passkey for {device_address}")
        self._confirm_accepted = True
        self._confirm_event.set()
        return True

    def reject(self, device_address: str) -> None:
        log.info(f"User rejected pairing for {device_address}")
        self._confirm_accepted = False
        self._confirm_event.set()

    # ------------------------------------------------------------------
    # Agent callbacks
    # ------------------------------------------------------------------

    def _handle_pin_code_request(self, device_path: str) -> str:
        """Legacy PIN flow (older devices)."""
        import random
        import string
        addr = self._path_to_address(device_path)
        pin = "".join(random.choices(string.digits, k=4))
        self._pending_pin = pin
        self._pending_address = addr
        log.info(f"BlueZ requesting legacy PIN for {addr} → {pin}")
        if self.on_pin_requested:
            self.on_pin_requested(addr, pin)
        return pin

    def _handle_confirm_request(
        self,
        device_path: str,
        passkey: int,
        reply_handler: Callable,
        error_handler: Callable,
    ) -> None:
        """
        SSP flow: called by the GLib mainloop when BlueZ invokes
        RequestConfirmation.

        Returns immediately to keep the GLib thread free. A background
        thread (_confirm_worker) waits for user input (or auto-accepts
        after AUTO_ACCEPT_TIMEOUT_S seconds) and sends the D-Bus reply
        via the stored reply_handler / error_handler.
        """
        addr = self._path_to_address(device_path)
        pin = str(passkey).zfill(6)
        self._pending_pin = pin
        self._pending_address = addr
        self._confirm_event.clear()
        self._confirm_accepted = False
        self._dbus_reply_handler = reply_handler
        self._dbus_error_handler = error_handler

        log.info(f"BlueZ SSP confirm request for {addr} — passkey={pin} (show to user)")
        if self.on_pin_requested:
            self.on_pin_requested(addr, pin)

        t = threading.Thread(
            target=self._confirm_worker,
            args=(addr,),
            daemon=True,
            name=f"pairing-confirm-{addr}",
        )
        t.start()
        # Return immediately — GLib mainloop stays unblocked.

    def _confirm_worker(self, addr: str) -> None:
        """
        Waits for user input up to AUTO_ACCEPT_TIMEOUT_S, then auto-accepts.
        Sends the pending D-Bus reply and cleans up stored callbacks.
        """
        import dbus.exceptions

        confirmed = self._confirm_event.wait(timeout=AUTO_ACCEPT_TIMEOUT_S)

        reply_handler = self._dbus_reply_handler
        error_handler = self._dbus_error_handler
        self._dbus_reply_handler = None
        self._dbus_error_handler = None

        if not confirmed:
            log.info(
                f"No user input for {addr} within {AUTO_ACCEPT_TIMEOUT_S}s — auto-accepting"
            )
            self._confirm_accepted = True

        if self._confirm_accepted:
            log.info(f"Passkey accepted for {addr}")
            if reply_handler:
                reply_handler()
        else:
            log.warning(f"Pairing rejected for {addr}")
            if error_handler:
                error_handler(
                    dbus.exceptions.DBusException(
                        "org.bluez.Error.Rejected",
                        "User rejected pairing",
                    )
                )

    def _handle_cancel(self) -> None:
        log.info("Agent Cancel — aborting pending confirmation")
        self._confirm_accepted = False
        self._confirm_event.set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# D-Bus Agent object
# ---------------------------------------------------------------------------

class _DBusAgent:
    IFACE = "org.bluez.Agent1"

    def __init__(
        self,
        bus,
        on_pin_code: Callable[[str], str],
        on_confirm: Callable,
        on_cancel: Callable[[], None],
    ):
        try:
            import dbus
            import dbus.service

            class _Agent(dbus.service.Object):
                def __init__(self_, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self_._on_pin_code = on_pin_code
                    self_._on_confirm = on_confirm
                    self_._on_cancel = on_cancel

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
                def RequestPinCode(self_, device):
                    return self_._on_pin_code(str(device))

                @dbus.service.method(
                    "org.bluez.Agent1",
                    in_signature="ou",
                    out_signature="",
                    async_callbacks=("reply_handler", "error_handler"),
                )
                def RequestConfirmation(self_, device, passkey, reply_handler, error_handler):
                    self_._on_confirm(
                        str(device), int(passkey), reply_handler, error_handler
                    )

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
                def RequestPasskey(self_, device):
                    log.warning("RequestPasskey called — not supported, returning 0")
                    return dbus.UInt32(0)

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Release(self_):
                    log.info("Agent Release called")

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Cancel(self_):
                    log.info("Agent Cancel called")
                    self_._on_cancel()

            self._agent_obj = _Agent(bus, AGENT_PATH)
            log.debug("_DBusAgent D-Bus object created")
        except Exception as e:
            log.error(f"_DBusAgent init failed: {e}")
