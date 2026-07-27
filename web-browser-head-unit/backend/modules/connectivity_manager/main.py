#!/usr/bin/env python3
import asyncio
import os
import sys
import threading
import time
from typing import Any, Optional

from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_bool, field_int, field_string, field_enum

from shared.hardware.bluez_bluetooth import BluezBluetoothAdapter
from shared.hardware.apmanager_wifi_ap import APManagerWifiApAdapter
from shared.hardware.windows_bluetooth import WindowsBluetoothAdapter
from shared.hardware.windows_wifi_ap import WindowsWifiApAdapter

from modules.connectivity_manager.handshake import RfcommHandshake

class ConnectivityManagerModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="connectivity_manager",
            priority=3,
            path_prefix="/api/connectivity",
        )
        self._bt_adapter = None
        self._wifi_adapter = None

        # State cache
        self._discoverable = True
        self._discovering = False
        self._discovered_devices = []
        self._rfcomm_listening = False
        self._rfcomm_connected = False
        self._active_device = None
        self._pairing_pin = None
        self._pairing_device = None

        self._handshake_thread = None
        self._wifi_credentials = None

        # Autoconnect loop state
        self._autoconnect_task = None
        self._autoconnect_event = asyncio.Event()

    def get_default_config(self) -> dict[str, Any]:
        return {
            "adapter_name": "NemoHeadUnit",
            "discoverable": True,
            "discoverable_timeout": 0,
            "discovery_duration_sec": 10,
            "wifi_ssid": "AndroidAutoAP",
            "wifi_password": "",
            "wifi_interface": "wlan0",
            "wifi_channel": 36,
            "wifi_hw_mode": "a",
            "wifi_country_code": "IT",
            "autoconnect_enabled": True,
            "autoconnect_connect_timeout_s": 8,
            "autoconnect_backoff_initial_s": 5,
            "autoconnect_backoff_cap_s": 60,
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "adapter_name": field_string(default="NemoHeadUnit"),
            "discoverable": field_bool(default=True),
            "discoverable_timeout": field_int(default=0, min=0),
            "discovery_duration_sec": field_int(default=10, min=1, max=120),
            "wifi_ssid": field_string(default="AndroidAutoAP"),
            "wifi_password": field_string(default=""),
            "wifi_interface": field_string(default="wlan0"),
            "wifi_channel": field_int(default=36, min=1, max=196),
            "wifi_hw_mode": field_enum(default="a", choices=["a", "g"]),
            "wifi_country_code": field_string(default="IT"),
            "autoconnect_enabled": field_bool(default=True),
            "autoconnect_connect_timeout_s": field_int(default=8, min=2, max=30),
            "autoconnect_backoff_initial_s": field_int(default=5, min=1, max=30),
            "autoconnect_backoff_cap_s": field_int(default=60, min=10, max=300),
        }

    async def setup(self) -> None:
        # Register REST Routes
        self.add_http_route("GET", "/status", self.handle_get_status)
        self.add_http_route("GET", "/paired", self.handle_get_paired)
        self.add_http_route("GET", "/discovered", self.handle_get_discovered)
        self.add_http_route("POST", "/discover", self.handle_post_discover)
        self.add_http_route("POST", "/pair", self.handle_post_pair)
        self.add_http_route("POST", "/pair/confirm", self.handle_post_pair_confirm)
        self.add_http_route("POST", "/pair/reject", self.handle_post_pair_reject)
        self.add_http_route("POST", "/remove", self.handle_post_remove)
        self.add_http_route("POST", "/paired/remove", self.handle_post_remove)
        self.add_http_route("POST", "/connect", self.handle_post_connect)
        self.add_http_route("POST", "/disconnect", self.handle_post_disconnect)
        self.add_http_route("POST", "/wifi/start", self.handle_wifi_start)
        self.add_http_route("POST", "/wifi/stop", self.handle_wifi_stop)

        self.subscribe("bluetooth_manager.try_autoconnect", self.on_try_autoconnect)

        # OS-specific Adapter instantiation with robust fallback
        if sys.platform.startswith("linux"):
            try:
                self.log.info("Initializing Linux Bluetooth Adapter (BlueZ D-Bus)...")
                self._bt_adapter = BluezBluetoothAdapter()
                await self._bt_adapter.setup(
                    adapter_name=self.config.get("adapter_name", "NemoHeadUnit"),
                    discoverable=self.config.get("discoverable", True),
                    discoverable_timeout=self.config.get("discoverable_timeout", 0),
                )
            except Exception as e:
                self.log.warning(f"Failed to initialize Linux BlueZ Bluetooth ({e}) — falling back to Windows/Mock Bluetooth Adapter")
                self._bt_adapter = WindowsBluetoothAdapter()
                await self._bt_adapter.setup(
                    adapter_name=self.config.get("adapter_name", "NemoHeadUnit"),
                    discoverable=self.config.get("discoverable", True),
                    discoverable_timeout=self.config.get("discoverable_timeout", 0),
                )

            try:
                self.log.info("Initializing Linux WiFi AP Adapter (APManager D-Bus)...")
                self._wifi_adapter = APManagerWifiApAdapter()
                await self._wifi_adapter.setup()
            except Exception as e:
                self.log.warning(f"Failed to initialize Linux APManager ({e}) — falling back to Windows/Mock WiFi AP Adapter")
                self._wifi_adapter = WindowsWifiApAdapter()
                await self._wifi_adapter.setup()
        else:
            self.log.info("Initializing Windows Bluetooth & WiFi AP Adapters (AF_BLUETOOTH + WinRT)...")
            self._bt_adapter = WindowsBluetoothAdapter()
            self._wifi_adapter = WindowsWifiApAdapter()
            await self._bt_adapter.setup(
                adapter_name=self.config.get("adapter_name", "NemoHeadUnit"),
                discoverable=self.config.get("discoverable", True),
                discoverable_timeout=self.config.get("discoverable_timeout", 0),
            )
            await self._wifi_adapter.setup()

        # Listen for incoming AA RFCOMM connections, pairing PIN requests, and connection state changes
        self._bt_adapter.set_on_pin_callback(self._on_pin_requested)
        self._bt_adapter.set_on_connection_callback(self._on_device_connection_changed)
        self._bt_adapter.register_rfcomm_server(self._on_rfcomm_connection)
        self._rfcomm_listening = True

        # Initialize Autoconnect loop
        if self.config.get("autoconnect_enabled", True):
            self._autoconnect_task = asyncio.create_task(self._autoconnect_loop())

    def _on_pin_requested(self, address: str, pin: str) -> None:
        """Callback when Bluetooth pairing PIN/passkey confirmation is requested."""
        self.log.info(f"🔑 Bluetooth Pairing PIN Requested: Device={address}, PIN={pin}")
        self._pairing_pin = str(pin)
        self._pairing_device = address
        self.publish("bluetooth_manager.pairing.pin", {"device_address": address, "pin": str(pin)})

    def _on_device_connection_changed(self, address: str, is_connected: bool) -> None:
        """Callback when a Bluetooth device connects or disconnects (inbound or outbound)."""
        if is_connected:
            self.log.info(f"🔵 Inbound/Outbound Bluetooth Connection detected for device {address}")
            self.publish("bluetooth_manager.paired.connected", {"device_address": address})
            if not self._rfcomm_connected:
                self.log.info(f"Waking autoconnect loop for connected device {address} to verify Android Auto RFCOMM...")
                self._autoconnect_event.set()
        else:
            self.log.info(f"⚪ Bluetooth device {address} disconnected")
            self.publish("bluetooth_manager.paired.disconnected", {"device_address": address})
            if self._active_device == address:
                self._rfcomm_connected = False
                self._active_device = None

    async def run(self) -> None:
        # Trigger initial autoconnect scan immediately after startup completes
        if self.config.get("autoconnect_enabled", True):
            self.log.info("🚀 Post-boot startup complete — triggering initial paired device scan & connection check...")
            self._autoconnect_event.set()
        while self._running:
            await asyncio.sleep(1.0)

    def on_try_autoconnect(self, topic: str, payload: dict) -> None:
        self.log.info("bluetooth_manager.try_autoconnect received — waking up autoconnect loop")
        self._autoconnect_event.set()

    async def _autoconnect_loop(self) -> None:
        backoff = int(self.config.get("autoconnect_backoff_initial_s", 5))
        cap = int(self.config.get("autoconnect_backoff_cap_s", 60))
        
        self.log.info("Connectivity autoconnect background loop started")
        
        while self._running:
            # Wait for backoff interval OR immediate manual trigger event
            try:
                await asyncio.wait_for(self._autoconnect_event.wait(), timeout=backoff)
                self._autoconnect_event.clear()
                backoff = int(self.config.get("autoconnect_backoff_initial_s", 5))  # Reset backoff on trigger
            except asyncio.TimeoutError:
                pass

            if self._rfcomm_connected or self._wifi_credentials is not None:
                # Active Android Auto Wireless session in progress — do not connect other devices
                continue

            try:
                devices = await self._bt_adapter.get_paired_devices()
                if not devices:
                    continue

                for dev in devices:
                    if self._rfcomm_connected or self._wifi_credentials is not None:
                        break
                    
                    addr = dev["address"]
                    name = dev.get("name", "Unknown")
                    is_already_connected = dev.get("connected", False)

                    self.log.info(f"Autoconnect: checking paired device {addr} ({name}) [already_connected={is_already_connected}]")
                    
                    # Connect to profiles / initiate RFCOMM trigger
                    success, err = await self._bt_adapter.connect_device(addr)
                    if success:
                        self.log.info(f"Autoconnect: successfully connected device profiles for {addr}")
                        self.publish("bluetooth_manager.paired.connected", {"device_address": addr})
                        backoff = int(self.config.get("autoconnect_backoff_initial_s", 5))
                        break
                    else:
                        self.log.debug(f"Autoconnect: connect_device notice for {addr} — {err}")
            except Exception as e:
                self.log.error(f"Error in autoconnect loop round: {e}")

            # Apply exponential backoff
            backoff = min(backoff * 2, cap)

    def _on_rfcomm_connection(self, sock: object, device_address: str) -> None:
        """Callback triggered when phone connects over RFCOMM."""
        self.log.info(f"🔵 [BT Stage 1/5] ConnectivityManager accepted RFCOMM from {device_address} — starting WiFi AP & Handshake thread...")
        self._rfcomm_connected = True
        self._active_device = device_address

        self.publish("rfcomm.handshake.started", {"device_address": device_address})

        loop = getattr(self, "loop", None) or getattr(self, "_loop", None) or asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self._start_ap_and_handshake(sock, device_address), loop)

    async def _start_ap_and_handshake(self, sock: object, device_address: str) -> None:
        ap_config = {
            "ssid": self.config.get("wifi_ssid", "AndroidAutoAP"),
            "ap_password": self.config.get("wifi_password", ""),
            "interface": self.config.get("wifi_interface", "wlan0"),
            "channel": self.config.get("wifi_channel", 36),
            "hw_mode": self.config.get("wifi_hw_mode", "a"),
            "country_code": self.config.get("wifi_country_code", "IT"),
        }

        self.log.info(f"📶 [WiFi Stage 2/5] Initiating WiFi AP launch (SSID='{ap_config['ssid']}', Channel={ap_config['channel']}, Interface='{ap_config['interface']}')...")
        success, creds = await self._wifi_adapter.start_ap(ap_config)
        if not success:
            self.log.error("❌ [WiFi Stage 2/5] Failed to start WiFi AP — aborting RFCOMM handshake")
            self.publish("rfcomm.handshake.failed", {"device_address": device_address, "error": "WiFi AP start failed"})
            self._rfcomm_connected = False
            return

        self._wifi_credentials = creds
        self.log.info(f"📶 [WiFi Stage 2/5] WiFi AP active! Credentials: SSID='{creds['ssid']}', BSSID='{creds['bssid']}', Gateway={creds['gateway_ip']}")

        # Handshake credentials dict
        handshake_creds = {
            "ssid": creds["ssid"],
            "key": creds["key"],
            "bssid": creds["bssid"],
            "gateway_ip": creds["gateway_ip"],
            "tcp_port": 5288,
        }

        # Run blocking socket handshake in thread pool to avoid blocking async loop
        self._handshake_thread = threading.Thread(
            target=self._run_handshake_thread,
            args=(sock, device_address, handshake_creds),
            daemon=True,
            name=f"rfcomm-handshake-{device_address}"
        )
        self._handshake_thread.start()

    def _run_handshake_thread(self, sock: Any, device_address: str, creds: dict) -> None:
        res_success = False
        try:
            hs = RfcommHandshake(sock, creds)
            res = hs.run()
            res_success = bool(res.success)
            if res.success:
                self.log.info(f"🤝 [Handshake Stage 3/5] 🎉 RFCOMM Handshake completed successfully! Phone IP: {res.phone_ip}")
                self.publish("rfcomm.handshake.completed", {
                    "device_address": device_address,
                    "phone_ip": res.phone_ip
                })
            else:
                self.log.error(f"❌ [Handshake Stage 3/5] RFCOMM Handshake failed: {res.error}")
                self.publish("rfcomm.handshake.failed", {
                    "device_address": device_address,
                    "error": res.error
                })
        except Exception as e:
            self.log.error(f"Unexpected error in RFCOMM handshake thread: {e}")
            self.publish("rfcomm.handshake.failed", {
                "device_address": device_address,
                "error": str(e)
            })
        finally:
            if res_success:
                self.log.info("🤝 [Handshake Stage 3/5] Keeping RFCOMM socket alive for active Android Auto session...")
                while self._rfcomm_connected and self._running:
                    time.sleep(1.0)
            try:
                sock.close()
            except Exception:
                pass
            self._rfcomm_connected = False

    # -------------------------------------------------------------------------
    # REST API Handlers
    # -------------------------------------------------------------------------

    async def handle_get_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "adapter_name": self.config.get("adapter_name", "NemoHeadUnit"),
            "discoverable": self._discoverable,
            "discovering": self._discovering,
            "rfcomm_listening": self._rfcomm_listening,
            "rfcomm_connected": self._rfcomm_connected,
            "active_device": self._active_device,
            "wifi_ap_active": self._wifi_credentials is not None,
            "wifi_ap_credentials": self._wifi_credentials,
            "pairing_pin": self._pairing_pin,
            "pairing_device": self._pairing_device,
        })

    async def handle_get_paired(self, request: web.Request) -> web.Response:
        devices = await self._bt_adapter.get_paired_devices()
        return web.json_response({"devices": devices})

    async def handle_post_discover(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        duration = int(body.get("duration_sec", self.config.get("discovery_duration_sec", 10)))

        self._discovering = True
        self._discovered_devices = []
        
        def on_dev(device):
            if not any(d.get("address") == device.get("address") for d in self._discovered_devices):
                self._discovered_devices.append(device)
            self.publish("bluetooth_manager.device.found", device)

        await self._bt_adapter.start_discovery(duration, on_dev)
        
        self._discovering = False
        self.publish("bluetooth_manager.discovery.completed", {})

        return web.json_response({
            "status": "ok",
            "message": f"Discovery scan completed ({len(self._discovered_devices)} devices found)",
            "devices": self._discovered_devices
        })

    async def handle_get_discovered(self, request: web.Request) -> web.Response:
        return web.json_response({"devices": self._discovered_devices})

    async def handle_post_pair(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body["device_address"]
        self._pairing_device = addr
        self._pairing_pin = None
        
        def on_pin(address, pin):
            self._pairing_pin = str(pin)
            self._pairing_device = address
            self.publish("bluetooth_manager.pairing.pin", {"device_address": address, "pin": str(pin)})

        success, err = await self._bt_adapter.pair_device(addr, on_pin)
        if success:
            return web.json_response({
                "status": "ok",
                "message": "Pairing sequence initiated",
                "pin": self._pairing_pin,
                "device_address": addr
            })
        self._pairing_pin = None
        self._pairing_device = None
        return web.json_response({"status": "error", "message": err}, status=400)

    async def handle_post_pair_confirm(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body.get("device_address") or body.get("address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'device_address'"}, status=400)
        res = await self._bt_adapter.confirm_pairing(addr, True)
        self._pairing_pin = None
        self._pairing_device = None
        if res:
            self.publish("bluetooth_manager.pairing.completed", {"device_address": addr, "success": True})
            self.log.info(f"🎉 Bluetooth pairing confirmed by UI for device {addr}")
            asyncio.create_task(self._auto_connect_after_pairing(addr))
        return web.json_response({"status": "ok" if res else "error"})

    async def _auto_connect_after_pairing(self, address: str) -> None:
        await asyncio.sleep(1.0)
        self.log.info(f"🔵 Initiating post-pairing auto-connection for device {address}...")
        success, err = await self._bt_adapter.connect_device(address)
        if success:
            self.publish("bluetooth_manager.paired.connected", {"device_address": address})
            self.log.info(f"🎉 Successfully auto-connected to newly paired device {address}")
        else:
            self.log.warning(f"Post-pairing auto-connect for {address} notice/result: {err}")


    async def handle_post_pair_reject(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body.get("device_address") or body.get("address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'device_address'"}, status=400)
        res = await self._bt_adapter.confirm_pairing(addr, False)
        self._pairing_pin = None
        self._pairing_device = None
        if res:
            self.publish("bluetooth_manager.pairing.completed", {"device_address": addr, "success": False})
            self.log.info(f"❌ Bluetooth pairing rejected by UI for device {addr}")
        return web.json_response({"status": "ok" if res else "error"})


    async def handle_post_remove(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body.get("address") or body.get("device_address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'address' parameter"}, status=400)
        res = await self._bt_adapter.remove_paired_device(addr)
        if res:
            self.publish("bluetooth_manager.paired.removed", {"device_address": addr})
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "error", "message": "Failed to remove paired device"}, status=400)

    async def handle_post_connect(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body.get("address") or body.get("device_address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'address' parameter"}, status=400)
        success, err = await self._bt_adapter.connect_device(addr)
        if success:
            self.publish("bluetooth_manager.paired.connected", {"device_address": addr})
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "error", "message": err}, status=400)

    async def handle_post_disconnect(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body.get("address") or body.get("device_address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'address' parameter"}, status=400)
        res = await self._bt_adapter.disconnect_device(addr)
        if res:
            self.publish("bluetooth_manager.paired.disconnected", {"device_address": addr})
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "error", "message": "Failed to disconnect"}, status=400)

    async def handle_wifi_start(self, request: web.Request) -> web.Response:
        ap_config = {
            "ssid": self.config.get("wifi_ssid", "AndroidAutoAP"),
            "ap_password": self.config.get("wifi_password", ""),
            "interface": self.config.get("wifi_interface", "wlan0"),
            "channel": self.config.get("wifi_channel", 36),
            "hw_mode": self.config.get("wifi_hw_mode", "a"),
            "country_code": self.config.get("wifi_country_code", "IT"),
        }
        success, creds = await self._wifi_adapter.start_ap(ap_config)
        if success:
            self._wifi_credentials = creds
            self.publish("hostapd.ready", creds)
            return web.json_response({"status": "ok", "credentials": creds})
        return web.json_response({"status": "error", "message": "Failed to start WiFi AP"}, status=400)

    async def handle_wifi_stop(self, request: web.Request) -> web.Response:
        res = await self._wifi_adapter.stop_ap()
        self._wifi_credentials = None
        self.publish("hostapd.stopped", {})
        return web.json_response({"status": "ok" if res else "error"})

    async def teardown(self) -> None:
        self.log.info("Teardown ConnectivityManagerModule...")
        if self._autoconnect_task:
            self._autoconnect_task.cancel()
            try:
                await self._autoconnect_task
            except asyncio.CancelledError:
                pass
        if self._bt_adapter:
            await self._bt_adapter.teardown()
        if self._wifi_adapter:
            await self._wifi_adapter.teardown()


if __name__ == "__main__":
    run_module(ConnectivityManagerModule)
