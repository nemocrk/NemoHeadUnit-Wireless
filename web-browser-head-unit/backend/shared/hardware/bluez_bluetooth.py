import asyncio
import os
import socket
import threading
import time
from typing import Callable, Optional

from shared.logger import get_logger
from .base_bluetooth import BaseBluetoothAdapter

log = get_logger("hardware.bluez_bluetooth")

# Workaround: conda injects DBUS_SYSTEM_BUS_ADDRESS as empty string
if not os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"):
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/run/dbus/system_bus_socket"

AA_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
RFCOMM_CHANNEL = 30
PROFILE_PATH = "/org/nemo/rfcomm_handshake/aa"

HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HSP_UUID = "00001108-0000-1000-8000-00805f9b34fb"
HFP_HSP_CHANNEL = 8

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

class BluezBluetoothAdapter(BaseBluetoothAdapter):
    def __init__(self):
        self._bus = None
        self._adapter = None
        self._profile_mgr = None
        self._agent_mgr = None
        self._agent = None
        self._initialized = False
        self._discovery_running = False
        self._discovery_thread = None
        self._glib_loop = None
        self._active_rfcomm_cb = None
        self._on_pin_requested_cb = None
        
        # State trackers for pairing
        self._dbus_reply_handler = None
        self._dbus_error_handler = None

    async def setup(self, adapter_name: str, discoverable: bool, discoverable_timeout: int) -> None:
        import dbus
        try:
            import dbus.mainloop.glib
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        except Exception as e:
            log.warning(f"GLib D-Bus mainloop setup failed: {e}")

        self._bus = dbus.SystemBus()
        if "org.bluez" not in self._bus.list_names():
            raise RuntimeError("org.bluez is not registered on the system D-Bus")

        manager = dbus.Interface(
            self._bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        objects = manager.GetManagedObjects()
        adapter_path = None
        for path, ifaces in objects.items():
            if "org.bluez.Adapter1" in ifaces:
                adapter_path = path
                break

        if not adapter_path:
            raise RuntimeError("No BlueZ Bluetooth adapter found")

        self._adapter = dbus.Interface(
            self._bus.get_object("org.bluez", adapter_path),
            "org.bluez.Adapter1",
        )
        self._profile_mgr = dbus.Interface(
            self._bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1",
        )
        self._initialized = True

        # Set adapter properties
        props = dbus.Interface(self._bus.get_object("org.bluez", adapter_path), "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Adapter1", "Alias", dbus.String(adapter_name))
        props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(discoverable))
        props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(discoverable_timeout))

        # Register standard profiles (HFP/HSP)
        try:
            opts = dbus.Dictionary(
                {"Channel": dbus.UInt16(HFP_HSP_CHANNEL), "AutoConnect": dbus.Boolean(True)},
                signature="sv",
            )
            self._profile_mgr.RegisterProfile("/org/bluez/profile/hfp", HFP_UUID, opts)
            self._profile_mgr.RegisterProfile("/org/bluez/profile/hsp", HSP_UUID, opts)
        except Exception as e:
            if "UUID already registered" not in str(e):
                log.warning(f"Standard HFP/HSP registration error: {e}")

        # Register Pairing Agent
        self._register_pairing_agent()

    def _register_pairing_agent(self) -> None:
        import dbus
        import dbus.service

        class Agent(dbus.service.Object):
            def __init__(self, conn, path, adapter):
                super().__init__(conn, path)
                self._adapter = adapter

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
            def RequestPinCode(self, device):
                mac = str(device).split("dev_")[-1].replace("_", ":").upper()
                log.info(f"RequestPinCode from {mac}")
                if self._adapter._on_pin_requested_cb:
                    self._adapter._on_pin_requested_cb(mac, "0000")
                return "0000"

            @dbus.service.method(
                "org.bluez.Agent1",
                in_signature="ou",
                out_signature="",
                async_callbacks=("reply_handler", "error_handler"),
            )
            def RequestConfirmation(self, device, passkey, reply_handler, error_handler):
                mac = str(device).split("dev_")[-1].replace("_", ":").upper()
                pin_str = f"{passkey:06d}"
                log.info(f"RequestConfirmation from {mac} passkey={pin_str}")
                
                self._adapter._dbus_reply_handler = reply_handler
                self._adapter._dbus_error_handler = error_handler

                if self._adapter._on_pin_requested_cb:
                    self._adapter._on_pin_requested_cb(mac, pin_str)

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
            def RequestPasskey(self, device):
                return dbus.UInt32(0)

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Release(self):
                log.info("Agent Release")

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Cancel(self):
                log.info("Agent Cancel")

        try:
            self._agent = Agent(self._bus, "/org/nemo/agent", self)
            self._agent_mgr = dbus.Interface(
                self._bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            self._agent_mgr.RegisterAgent("/org/nemo/agent", "DisplayYesNo")
            self._agent_mgr.RequestDefaultAgent("/org/nemo/agent")
            log.info("Pairing agent registered on /org/nemo/agent")
        except Exception as e:
            log.warning(f"Pairing agent registration failed: {e}")

    async def start_discovery(self, duration_sec: int, on_device_found_cb: Callable[[dict], None]) -> None:
        if self._discovery_running:
            return
        self._discovery_running = True
        self._discovery_thread = threading.Thread(
            target=self._run_discovery,
            args=(duration_sec, on_device_found_cb),
            daemon=True,
            name="bluez-discovery",
        )
        self._discovery_thread.start()

    def _run_discovery(self, duration_sec: int, on_device_found_cb: Callable[[dict], None]) -> None:
        try:
            self._adapter.StartDiscovery()
            deadline = time.monotonic() + duration_sec
            seen = set()
            while self._discovery_running and time.monotonic() < deadline:
                devices = self._get_devices()
                for dev in devices:
                    addr = dev["address"]
                    if addr not in seen:
                        seen.add(addr)
                        on_device_found_cb(dev)
                time.sleep(1.0)
        except Exception as e:
            log.error(f"Discovery error: {e}")
        finally:
            try:
                self._adapter.StopDiscovery()
            except Exception:
                pass
            self._discovery_running = False

    def _get_devices(self) -> list[dict]:
        import dbus
        manager = dbus.Interface(self._bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        objects = manager.GetManagedObjects()
        results = []
        for path, ifaces in objects.items():
            if "org.bluez.Device1" in ifaces:
                props = ifaces["org.bluez.Device1"]
                results.append({
                    "address": str(props.get("Address", "")),
                    "name": str(props.get("Alias", props.get("Name", "Unknown"))),
                    "rssi": int(props.get("RSSI", 0)),
                })
        return results

    async def stop_discovery(self) -> None:
        self._discovery_running = False

    async def pair_device(self, address: str, on_pin_cb: Callable[[str, str], None]) -> tuple[bool, str]:
        import dbus
        self._on_pin_requested_cb = on_pin_cb
        try:
            device_path = self._find_device_path(address)
            if not device_path:
                return False, "Device not found in DBus objects cache"
            
            # Trust device beforehand to make it auto-connect standard profiles
            self._trust_device(address)

            device = dbus.Interface(self._bus.get_object("org.bluez", device_path), "org.bluez.Device1")
            
            # Start pairing
            device.Pair()
            return True, ""
        except Exception as e:
            return False, str(e)

    def _find_device_path(self, address: str) -> Optional[str]:
        import dbus
        manager = dbus.Interface(self._bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        objects = manager.GetManagedObjects()
        for path, ifaces in objects.items():
            if "org.bluez.Device1" in ifaces:
                if ifaces["org.bluez.Device1"].get("Address") == address:
                    return path
        return None

    def _trust_device(self, address: str) -> None:
        import dbus
        try:
            path = self._find_device_path(address)
            if path:
                props = dbus.Interface(self._bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties")
                props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
                log.info(f"Device {address} trusted")
        except Exception as e:
            log.warning(f"Failed to trust device {address}: {e}")

    async def confirm_pairing(self, address: str, confirm: bool) -> bool:
        import dbus.exceptions
        
        reply = self._dbus_reply_handler
        error = self._dbus_error_handler
        self._dbus_reply_handler = None
        self._dbus_error_handler = None

        if confirm:
            log.info(f"Confirming pairing code for {address}")
            self._trust_device(address)
            if reply:
                reply()
            return True
        else:
            log.warning(f"Rejecting pairing code for {address}")
            if error:
                error(dbus.exceptions.DBusException("org.bluez.Error.Rejected", "User rejected pairing"))
            return True

    async def connect_device(self, address: str) -> tuple[bool, str]:
        import dbus
        try:
            path = self._find_device_path(address)
            if not path:
                return False, "Device not found"
            device = dbus.Interface(self._bus.get_object("org.bluez", path), "org.bluez.Device1")
            device.Connect()
            return True, ""
        except Exception as e:
            return False, str(e)

    async def disconnect_device(self, address: str) -> bool:
        import dbus
        try:
            path = self._find_device_path(address)
            if not path:
                return False
            device = dbus.Interface(self._bus.get_object("org.bluez", path), "org.bluez.Device1")
            device.Disconnect()
            return True
        except Exception:
            return False

    async def remove_paired_device(self, address: str) -> bool:
        import dbus
        try:
            path = self._find_device_path(address)
            if not path:
                return False
            self._adapter.RemoveDevice(path)
            return True
        except Exception:
            return False

    async def get_paired_devices(self) -> list[dict]:
        import dbus
        try:
            manager = dbus.Interface(self._bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
            objects = manager.GetManagedObjects()
            results = []
            for path, ifaces in objects.items():
                if "org.bluez.Device1" in ifaces:
                    props = ifaces["org.bluez.Device1"]
                    if props.get("Paired", False):
                        results.append({
                            "address": str(props.get("Address", "")),
                            "name": str(props.get("Alias", props.get("Name", "Unknown"))),
                            "connected": bool(props.get("Connected", False)),
                            "trusted": bool(props.get("Trusted", False)),
                        })
            return results
        except Exception:
            return []

    def register_rfcomm_server(self, on_connection_cb: Callable[[object, str], None]) -> bool:
        """Register the Android Auto RFCOMM profile with BlueZ and listen for connections."""
        import dbus
        import dbus.service

        self._active_rfcomm_cb = on_connection_cb

        # Setup GLib loop in a background thread if not already running
        try:
            from gi.repository import GLib
            self._glib_loop = GLib.MainLoop()
            threading.Thread(target=self._glib_loop.run, daemon=True, name="glib-main").start()
        except Exception as e:
            log.warning(f"Could not initialize GLib event loop: {e}")

        class Profile(dbus.service.Object):
            def __init__(self, conn, path, callback):
                super().__init__(conn, path)
                self._cb = callback

            @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
            def Release(self):
                pass

            @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
            def NewConnection(self, device, fd, fd_properties):
                raw_fd = fd.take() if hasattr(fd, "take") else int(fd)
                sock = socket.fromfd(raw_fd, socket.AF_UNIX, socket.SOCK_STREAM)
                os.close(raw_fd)

                device_mac = str(device).split("dev_")[-1].replace("_", ":").upper()
                log.info(f"RFCOMM Profile Connection accepted from {device_mac}")
                self._cb(sock, device_mac)

        try:
            self._profile = Profile(self._bus, PROFILE_PATH, self._active_rfcomm_cb)
            opts = dbus.Dictionary({
                "AutoConnect": dbus.Boolean(True),
                "ServiceRecord": dbus.String(SERVICE_RECORD),
                "Role": dbus.String("server"),
            }, signature="sv")
            self._profile_mgr.RegisterProfile(PROFILE_PATH, AA_UUID, opts)
            log.info("Android Auto RFCOMM service profile registered successfully with BlueZ")
            return True
        except Exception as e:
            log.error(f"Failed to register Android Auto RFCOMM profile: {e}")
            return False

    async def teardown(self) -> None:
        if self._glib_loop and self._glib_loop.is_running():
            self._glib_loop.quit()
        try:
            self._profile_mgr.UnregisterProfile(PROFILE_PATH)
        except Exception:
            pass
        try:
            self._agent_mgr.UnregisterAgent("/org/nemo/agent")
        except Exception:
            pass
        self._initialized = False
