import asyncio
import os
import threading
from typing import Optional

from shared.logger import get_logger
from .base_wifi_ap import BaseWifiApAdapter  # Wait, let's import it from .base_wifi_ap

log = get_logger("hardware.apmanager_wifi_ap")

_DBUS_BUS_NAME    = "org.nemo.APManager"
_DBUS_OBJECT_PATH = "/org/nemo/APManager"
_DBUS_INTERFACE   = "org.nemo.APManager"

class APManagerWifiApAdapter(BaseWifiApAdapter):
    def __init__(self):
        self._bus = None
        self._proxy = None
        self._glib_loop = None
        self._glib_thread = None
        self._running = False
        self._started_credentials = None
        self._ready_event = asyncio.Event()

    async def setup(self) -> None:
        import dbus
        import dbus.mainloop.glib

        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self._bus = dbus.SystemBus()
            self._proxy = dbus.Interface(
                self._bus.get_object(_DBUS_BUS_NAME, _DBUS_OBJECT_PATH, introspect=False),
                _DBUS_INTERFACE
            )

            # Start GLib Loop for receiving DBus signals (APStarted, APStopped, etc.)
            from gi.repository import GLib
            self._glib_loop = GLib.MainLoop()
            self._glib_thread = threading.Thread(target=self._glib_loop.run, daemon=True, name="apmanager-glib")
            self._glib_thread.start()

            # Subscribe to signals
            self._bus.add_signal_receiver(
                self._on_ap_started,
                signal_name="APStarted",
                dbus_interface=_DBUS_INTERFACE
            )
            self._bus.add_signal_receiver(
                self._on_ap_failed,
                signal_name="APFailed",
                dbus_interface=_DBUS_INTERFACE
            )

            log.info("APManager D-Bus client initialized successfully")
        except Exception as e:
            log.error(f"Failed to connect to org.nemo.APManager: {e}")
            raise e

    def _on_ap_started(self, config_dict: dict) -> None:
        log.info(f"APStarted signal received from D-Bus: {config_dict}")
        # Convert DBus types to standard Python types
        self._started_credentials = {
            "ssid": str(config_dict.get("ssid", "AndroidAutoAP")),
            "key": str(config_dict.get("key", "")),
            "bssid": str(config_dict.get("bssid", "")),
            "interface": str(config_dict.get("interface", "wlan0")),
            "gateway_ip": str(config_dict.get("gateway_ip", "10.0.0.1")),
            "security_mode": int(config_dict.get("security_mode", 8)),
            "ap_type": int(config_dict.get("ap_type", 1)),
        }
        # Run thread-safe event setting in loop
        asyncio.get_event_loop().call_soon_threadsafe(self._ready_event.set)

    def _on_ap_failed(self, error: str) -> None:
        log.error(f"APFailed signal received from D-Bus: {error}")
        self._started_credentials = None
        asyncio.get_event_loop().call_soon_threadsafe(self._ready_event.set)

    async def start_ap(self, config: dict) -> tuple[bool, dict]:
        import dbus
        self._ready_event.clear()
        self._started_credentials = None

        log.info(f"Invoking Start() on APManager D-Bus service...")
        dbus_config = dbus.Dictionary(
            {
                k: dbus.String(str(v)) if not isinstance(v, int) else dbus.Int32(v)
                for k, v in config.items()
            },
            signature="sv",
        )

        try:
            success, msg = self._proxy.Start(dbus_config, dbus_interface=_DBUS_INTERFACE, signature="a{sv}")
            if not success:
                log.error(f"APManager Start method failed: {msg}")
                return False, {}

            # Wait for the async APStarted D-Bus signal
            await self._ready_event.wait()
            if self._started_credentials:
                return True, self._started_credentials
            return False, {}
        except Exception as e:
            log.error(f"Error calling APManager.Start(): {e}")
            return False, {}

    async def stop_ap(self) -> bool:
        try:
            log.info("Invoking Stop() on APManager D-Bus service...")
            success, msg = self._proxy.Stop(dbus_interface=_DBUS_INTERFACE)
            return bool(success)
        except Exception as e:
            log.error(f"Error calling APManager.Stop(): {e}")
            return False

    async def teardown(self) -> None:
        await self.stop_ap()
        if self._glib_loop and self._glib_loop.is_running():
            self._glib_loop.quit()
