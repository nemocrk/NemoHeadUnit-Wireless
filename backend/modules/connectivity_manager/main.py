#!/usr/bin/env python3
import asyncio
import os
import sys
import threading
import time
from typing import Any, Optional

from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_bool, field_int, field_string, field_enum, ConfigFieldList

from shared.hardware.base_bluetooth import get_bluetooth_adapter
from shared.hardware.base_wifi_ap import get_wifi_adapter
from shared.hardware.base_audio import get_audio_adapter
from shared.hardware.bluez_hfp import BlueZHFClient
from shared.hardware.bluez_pbap import BlueZPBAPClient

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
        self._audio_adapter = None
        self._hfp_client = None
        self._pbap_client = None

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
        self._autoconnect_cursor = 0
        self._status_changed_evt = asyncio.Event()

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
            "known_aa_devices": [],
            "ignored_devices": [],
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
            "known_aa_devices": ConfigFieldList(item_schema=field_string(), default=[]),
            "ignored_devices": ConfigFieldList(item_schema=field_string(), default=[]),
        }

    async def setup(self) -> None:
        self._loop = asyncio.get_running_loop()
        # Register REST Routes
        self.add_http_route("GET", "/status", self.handle_get_status)
        self.add_http_route("GET", "/paired", self.handle_get_paired)
        self.add_http_route("GET", "/discovered", self.handle_get_discovered)
        self.add_http_route("POST", "/discover", self.handle_post_discover)
        self.add_http_route("GET", "/stream_status", self.handle_stream_status)
        self.add_http_route("POST", "/pair", self.handle_post_pair)
        self.add_http_route("POST", "/pair/confirm", self.handle_post_pair_confirm)
        self.add_http_route("POST", "/pair/reject", self.handle_post_pair_reject)
        self.add_http_route("POST", "/remove", self.handle_post_remove)
        self.add_http_route("POST", "/paired/remove", self.handle_post_remove)
        self.add_http_route("POST", "/connect", self.handle_post_connect)
        self.add_http_route("POST", "/disconnect", self.handle_post_disconnect)
        self.add_http_route("POST", "/devices/ignore", self.handle_post_ignore_device)
        self.add_http_route("POST", "/devices/unignore", self.handle_post_unignore_device)
        self.add_http_route("POST", "/wifi/start", self.handle_wifi_start)
        self.add_http_route("POST", "/wifi/stop", self.handle_wifi_stop)

        # Standalone Bluetooth Telephony & PBAP Routes
        self.add_http_route("GET", "/phone/status", self.handle_get_phone_status)
        self.add_http_route("POST", "/phone/dial", self.handle_post_phone_dial)
        self.add_http_route("POST", "/phone/action", self.handle_post_phone_action)
        self.add_http_route("POST", "/phone/dtmf", self.handle_post_phone_dtmf)
        self.add_http_route("GET", "/phone/contacts", self.handle_get_phone_contacts)
        self.add_http_route("GET", "/phone/recents", self.handle_get_phone_recents)
        self.add_http_route("GET", "/phone/favorites", self.handle_get_phone_favorites)
        self.add_http_route("POST", "/phone/sync", self.handle_post_phone_sync)

        self.subscribe("bluetooth_manager.try_autoconnect", self.on_try_autoconnect)

        # Cross-platform Adapter instantiation via factories with graceful fallback
        self.log.info("Initializing Hardware Bluetooth & WiFi AP Adapters...")
        self._audio_adapter = get_audio_adapter()
        self._hfp_client = BlueZHFClient(on_state_changed=self._on_hfp_state_changed)
        self._pbap_client = BlueZPBAPClient()
        self._bt_adapter = get_bluetooth_adapter()
        try:
            await self._bt_adapter.setup(
                adapter_name=self.config.get("adapter_name", "NemoHeadUnit"),
                discoverable=self.config.get("discoverable", True),
                discoverable_timeout=self.config.get("discoverable_timeout", 0),
            )
        except Exception as e:
            self.log.warning(f"Primary Bluetooth adapter setup failed ({e}) — falling back to Windows/Mock adapter")
            from shared.hardware.windows_bluetooth import WindowsBluetoothAdapter
            self._bt_adapter = WindowsBluetoothAdapter()
            await self._bt_adapter.setup(
                adapter_name=self.config.get("adapter_name", "NemoHeadUnit"),
                discoverable=self.config.get("discoverable", True),
                discoverable_timeout=self.config.get("discoverable_timeout", 0),
            )

        self._wifi_adapter = get_wifi_adapter()
        try:
            await self._wifi_adapter.setup()
        except Exception as e:
            self.log.warning(f"Primary WiFi AP adapter setup failed ({e}) — falling back to Windows/Mock adapter")
            from shared.hardware.windows_wifi_ap import WindowsWifiApAdapter
            self._wifi_adapter = WindowsWifiApAdapter()
            await self._wifi_adapter.setup()

        # Listen for incoming AA RFCOMM connections, pairing PIN requests, connection state, and telemetry
        self._bt_adapter.set_on_pin_callback(self._on_pin_requested)
        self._bt_adapter.set_on_connection_callback(self._on_device_connection_changed)
        self._bt_adapter.set_on_battery_callback(self._on_bluetooth_telemetry_changed)
        self._bt_adapter.register_rfcomm_server(self._on_rfcomm_connection)
        self._rfcomm_listening = True

        # Initialize Autoconnect loop
        if self.config.get("autoconnect_enabled", True):
            self._autoconnect_task = asyncio.create_task(self._autoconnect_loop())
        self._telemetry_poll_task = asyncio.create_task(self._telemetry_poll_loop())

    def _on_hfp_state_changed(self, state: dict[str, Any]) -> None:
        """Callback when BlueZ HFP call state or active call changes."""
        self.log.info(f"📱 Publishing HFP phone.status: {state}")
        self.publish("phone.status", state)
        if self._audio_adapter:
            coro = self._audio_adapter.ensure_hfp_loopback(state.get("is_in_call", False))
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                if hasattr(self, "_loop") and self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _on_bluetooth_telemetry_changed(
        self,
        address: str,
        battery_pct: int,
        signal_bars: int,
        operator_name: str = "",
        is_roaming: bool = False,
    ) -> None:
        """Callback when Bluetooth Battery, RSSI, operator name, or roaming changes."""
        if self._hfp_client:
            self._hfp_client.update_telemetry(
                battery_pct=battery_pct,
                signal_bars=signal_bars,
                carrier=operator_name,
                is_roaming=is_roaming,
            )
        state = {
            "source": "bluetooth_hfp",
            "device_address": address,
            "is_in_call": False,
            "call_state": "IDLE",
        }
        if battery_pct >= 0:
            state["battery_level"] = max(0, min(100, battery_pct))
        if signal_bars >= 0:
            state["signal_strength"] = max(0, min(5, signal_bars))
        if operator_name:
            state["operator_name"] = operator_name
        if is_roaming is not None:
            state["is_roaming"] = bool(is_roaming)
        self.log.info(f"📱 Publishing Bluetooth phone.status telemetry: {state}")
        self.publish("phone.status", state)

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
            self._active_device = address
            self._trigger_device_telemetry(address)
            self.publish("bluetooth_manager.paired.connected", {"device_address": address})
            if hasattr(self, "_loop") and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._auto_sync_pbap(address), self._loop)
            else:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._auto_sync_pbap(address))
                except RuntimeError:
                    pass
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

            if not self._running:
                break

            if self._rfcomm_connected or self._wifi_credentials is not None:
                # Active Android Auto Wireless session in progress — do not connect other devices
                continue

            try:
                devices = await self._bt_adapter.get_paired_devices()
                if not devices or not self._running:
                    continue

                ignored = set(self.config.get("ignored_devices", []))
                known_aa = list(self.config.get("known_aa_devices", []))

                # Filter out ignored devices (e.g. BT speakers)
                candidates = [d for d in devices if d.get("address") not in ignored]
                if not candidates or not self._running:
                    continue

                # Prioritize known AA devices, maintaining relative order
                candidates.sort(key=lambda d: 0 if d.get("address") in known_aa else 1)

                num_candidates = len(candidates)
                self._autoconnect_cursor = self._autoconnect_cursor % num_candidates
                ordered_candidates = candidates[self._autoconnect_cursor:] + candidates[:self._autoconnect_cursor]

                for dev in ordered_candidates:
                    if not self._running or self._rfcomm_connected or self._wifi_credentials is not None:
                        break

                    addr = dev["address"]
                    name = dev.get("name", "Unknown")
                    is_already_connected = dev.get("connected", False)
                    is_known = addr in known_aa

                    if is_already_connected:
                        self.log.debug(f"Autoconnect: device {addr} ({name}) is already connected — skipping")
                        continue

                    self.log.info(f"Autoconnect: attempting connection to paired device {addr} ({name}) [known_aa={is_known}]")

                    # Connect to profiles / initiate RFCOMM trigger
                    success, err = await self._bt_adapter.connect_device(addr)
                    if success:
                        self.log.info(f"Autoconnect: successfully connected device profiles for {addr}")
                        self.publish("bluetooth_manager.paired.connected", {"device_address": addr})
                        self._autoconnect_cursor = (self._autoconnect_cursor + 1) % num_candidates
                        backoff = int(self.config.get("autoconnect_backoff_initial_s", 5))
                        break
                    else:
                        self.log.debug(f"Autoconnect: connect_device notice for {addr} — {err}")
                        self._autoconnect_cursor = (self._autoconnect_cursor + 1) % num_candidates
            except Exception as e:
                self.log.error(f"Error in autoconnect loop round: {e}")

            # Apply exponential backoff
            backoff = min(backoff * 2, cap)

    def _record_known_device(self, addr: str) -> None:
        """Persist newly verified Android Auto device to known_aa_devices in config."""
        known = list(self.config.get("known_aa_devices", []))
        if addr not in known:
            known.append(addr)
            self.config["known_aa_devices"] = known
            self.publish("config.set", {"module": self.name, "key": "known_aa_devices", "value": known})
            self.log.info(f"💾 Added {addr} to known Android Auto devices and saved to config")
            this_notify = getattr(self, "_notify_status_changed", lambda: None)
            this_notify()

    def _on_rfcomm_connection(self, sock: object, device_address: str) -> None:
        """Callback triggered when phone connects over RFCOMM."""
        if not self._running or not self._rfcomm_listening:
            self.log.info(f"Ignoring RFCOMM connection from {device_address} because ConnectivityManager is stopping")
            try:
                sock.close()
            except Exception:
                pass
            return

        self.log.info(f"🔵 [BT Stage 1/5] ConnectivityManager accepted RFCOMM from {device_address} — starting WiFi AP & Handshake thread...")
        self._rfcomm_connected = True
        self._active_device = device_address
        self._trigger_device_telemetry(device_address)
        self.current_stage_index = 1
        self._notify_status_changed()

        self.publish("rfcomm.handshake.started", {"device_address": device_address})

        loop = getattr(self, "loop", None) or getattr(self, "_loop", None) or asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self._start_ap_and_handshake(sock, device_address), loop)

    async def _start_ap_and_handshake(self, sock: object, device_address: str) -> None:
        if not self._running or not self._rfcomm_listening:
            try:
                sock.close()
            except Exception:
                pass
            return
        self.current_stage_index = 2
        self._notify_status_changed()
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
            self.current_stage_index = 0
            self._notify_status_changed()
            return

        mode_str = creds.get("mode", "join" if creds.get("ap_type") == 2 else "ap")
        self.log.info(f"📶 [WiFi Stage 2/5] WiFi AP active ({mode_str} mode)! Credentials: SSID='{creds['ssid']}', BSSID='{creds['bssid']}', Gateway={creds['gateway_ip']}")

        # Handshake credentials dict
        handshake_creds = {
            "ssid": creds["ssid"],
            "key": creds.get("key", ""),
            "bssid": creds["bssid"],
            "gateway_ip": creds["gateway_ip"],
            "tcp_port": 5288,
            "security_mode": creds.get("security_mode", 8),
            "ap_type": creds.get("ap_type", 0 if mode_str == "join" else 1),
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
        self.current_stage_index = 4
        self._notify_status_changed()

        def on_stage(stage_name: str):
            if stage_name == "WifiInfoRequest":
                self.current_stage_index = 5
                self._notify_status_changed()

        try:
            hs = RfcommHandshake(sock, creds, on_stage_cb=on_stage)
            res = hs.run()
            res_success = bool(res.success)
            if res.success:
                self.current_stage_index = 6
                self._notify_status_changed()
                self.log.info(f"🤝 [Handshake Stage 3/5] 🎉 RFCOMM Handshake completed successfully! Phone IP: {res.phone_ip}")
                self._record_known_device(device_address)
                self.publish("rfcomm.handshake.completed", {
                    "device_address": device_address,
                    "phone_ip": res.phone_ip
                })
            else:
                self.current_stage_index = 0
                self._notify_status_changed()
                self.log.error(f"❌ [Handshake Stage 3/5] RFCOMM Handshake failed: {res.error}")
                self.publish("rfcomm.handshake.failed", {
                    "device_address": device_address,
                    "error": res.error
                })
        except Exception as e:
            self.current_stage_index = 0
            self._notify_status_changed()
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
            self.current_stage_index = 0
            self._notify_status_changed()

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
            "known_aa_devices": self.config.get("known_aa_devices", []),
            "ignored_devices": self.config.get("ignored_devices", []),
        })

    async def handle_post_ignore_device(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        addr = body.get("address") or body.get("device_address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'address' parameter"}, status=400)
        ignored = list(self.config.get("ignored_devices", []))
        if addr not in ignored:
            ignored.append(addr)
            self.config["ignored_devices"] = ignored
            self.publish("config.set", {"module": self.name, "key": "ignored_devices", "value": ignored})
            self.log.info(f"🚫 Added {addr} to ignored Bluetooth devices")
        this_notify = getattr(self, "_notify_status_changed", lambda: None)
        this_notify()
        return web.json_response({"status": "ok", "ignored_devices": ignored})

    async def handle_post_unignore_device(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        addr = body.get("address") or body.get("device_address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'address' parameter"}, status=400)
        ignored = list(self.config.get("ignored_devices", []))
        if addr in ignored:
            ignored.remove(addr)
            self.config["ignored_devices"] = ignored
            self.publish("config.set", {"module": self.name, "key": "ignored_devices", "value": ignored})
            self.log.info(f"✅ Removed {addr} from ignored Bluetooth devices")
        this_notify = getattr(self, "_notify_status_changed", lambda: None)
        this_notify()
        return web.json_response({"status": "ok", "ignored_devices": ignored})

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
        this_notify = getattr(self, "_notify_status_changed", lambda: None)
        this_notify()
        self._discovered_devices = []
        
        def on_dev(device):
            if not any(d.get("address") == device.get("address") for d in self._discovered_devices):
                self._discovered_devices.append(device)
                this_notify()
            self.publish("bluetooth_manager.device.found", device)

        try:
            await self._bt_adapter.start_discovery(duration, on_dev)
        except Exception as e:
            self.log.error(f"Error during Bluetooth discovery: {e}")
        finally:
            self._discovering = False
            this_notify()
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
        this_notify = getattr(self, "_notify_status_changed", lambda: None)
        this_notify()
        
        def on_pin(address, pin):
            self._pairing_pin = str(pin)
            self._pairing_device = address
            this_notify()
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
        this_notify()
        return web.json_response({"status": "error", "message": err}, status=400)

    async def handle_post_pair_confirm(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body.get("device_address") or body.get("address")
        if not addr:
            return web.json_response({"status": "error", "message": "Missing 'device_address'"}, status=400)
        res = await self._bt_adapter.confirm_pairing(addr, True)
        self._pairing_pin = None
        self._pairing_device = None
        this_notify = getattr(self, "_notify_status_changed", lambda: None)
        this_notify()
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
        this_notify = getattr(self, "_notify_status_changed", lambda: None)
        this_notify()
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
            this_notify = getattr(self, "_notify_status_changed", lambda: None)
            this_notify()
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
            this_notify = getattr(self, "_notify_status_changed", lambda: None)
            this_notify()
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

    def _notify_status_changed(self) -> None:
        if hasattr(self, "_status_changed_evt") and self._status_changed_evt:
            self._status_changed_evt.set()

    async def handle_stream_status(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )
        await response.prepare(request)
        import json

        labels = {
            0: ("IDLE", "Disconnected", "Disconnected from Phone"),
            1: ("BT_ACCEPTED", "Bluetooth Connected", "Phone Accepted Bluetooth Connection"),
            2: ("WIFI_AP_LAUNCHING", "Starting WiFi Hotspot", "Starting WiFi Hotspot & Credentials"),
            3: ("WIFI_AP_ACTIVE", "WiFi Hotspot Ready", "WiFi Hotspot Active & Broadcasting"),
            4: ("HANDSHAKE_START", "Handshake Initiated", "Sent WiFi Credentials Request to Phone"),
            5: ("HANDSHAKE_INFO_REQ", "Exchanging Credentials", "Exchanging WiFi Security Credentials"),
            6: ("HANDSHAKE_COMPLETE", "Handshake Complete", "RFCOMM Handshake Completed Successfully"),
        }

        if not hasattr(self, "_status_changed_evt") or self._status_changed_evt is None:
            self._status_changed_evt = asyncio.Event()

        try:
            while request.protocol and request.protocol.transport and not request.protocol.transport.is_closing():
                wifi_st = self._wifi_adapter.get_status() if self._wifi_adapter else {}
                paired_devs = []
                try:
                    paired_devs = await asyncio.wait_for(self._bt_adapter.get_paired_devices(), timeout=1.0) if self._bt_adapter else []
                except Exception as e:
                    self.log.debug(f"Stream status get_paired_devices notice: {e}")

                st_idx = getattr(self, "current_stage_index", 0)
                code, lbl, msg = labels.get(st_idx, ("IDLE", "Disconnected", "Disconnected from Phone"))

                payload = {
                    "stage_index": st_idx,
                    "stage_code": code,
                    "stage_label": lbl,
                    "toast_message": msg if st_idx > 0 else None,
                    "discovering": self._discovering,
                    "rfcomm_connected": self._rfcomm_connected,
                    "active_device": self._active_device,
                    "wifi_ap": wifi_st,
                    "paired_devices": paired_devs,
                    "discovered_devices": self._discovered_devices,
                    "pairing_pin": self._pairing_pin,
                    "pairing_device": self._pairing_device,
                    "known_aa_devices": self.config.get("known_aa_devices", []),
                    "ignored_devices": self.config.get("ignored_devices", []),
                }
                data = f"data: {json.dumps(payload)}\n\n"
                await response.write(data.encode('utf-8'))

                # Wait strictly for an actual state change event (with 10s heartbeat fallback)
                self._status_changed_evt.clear()
                try:
                    await asyncio.wait_for(self._status_changed_evt.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:
            if "is_closing" not in str(exc):
                self.log.warning(f"handle_stream_status stream notice: {exc}")
        return response

    # -------------------------------------------------------------------------
    # Standalone Bluetooth Telephony & PBAP Route Handlers
    # -------------------------------------------------------------------------
    async def handle_get_phone_status(self, request: web.Request) -> web.Response:
        if not self._hfp_client:
            return web.json_response({"status": "error", "message": "HFP client not initialized"}, status=503)
        return web.json_response({"status": "ok", "phone": self._hfp_client.get_state()})

    async def handle_post_phone_dial(self, request: web.Request) -> web.Response:
        if not self._hfp_client:
            return web.json_response({"status": "error", "message": "HFP client not initialized"}, status=503)
        try:
            data = await request.json()
        except Exception:
            data = {}
        number = str(data.get("number", "")).strip()
        if not number:
            return web.json_response({"status": "error", "message": "Missing 'number'"}, status=400)
        success = self._hfp_client.dial(number)
        return web.json_response({"status": "ok" if success else "failed", "phone": self._hfp_client.get_state()})

    async def handle_post_phone_action(self, request: web.Request) -> web.Response:
        if not self._hfp_client:
            return web.json_response({"status": "error", "message": "HFP client not initialized"}, status=503)
        try:
            data = await request.json()
        except Exception:
            data = {}
        action = str(data.get("action", "")).strip().lower()
        if action == "answer":
            success = self._hfp_client.answer()
        elif action == "hangup":
            success = self._hfp_client.hangup()
        elif action == "mute":
            success = self._hfp_client.set_mute(True)
        elif action == "unmute":
            success = self._hfp_client.set_mute(False)
        else:
            return web.json_response({"status": "error", "message": f"Unknown action: {action}"}, status=400)
        return web.json_response({"status": "ok" if success else "failed", "phone": self._hfp_client.get_state()})

    async def handle_post_phone_dtmf(self, request: web.Request) -> web.Response:
        if not self._hfp_client:
            return web.json_response({"status": "error", "message": "HFP client not initialized"}, status=503)
        try:
            data = await request.json()
        except Exception:
            data = {}
        key = str(data.get("key", "")).strip()
        if not key:
            return web.json_response({"status": "error", "message": "Missing 'key'"}, status=400)
        success = self._hfp_client.send_dtmf(key)
        return web.json_response({"status": "ok" if success else "failed"})

    async def handle_get_phone_contacts(self, request: web.Request) -> web.Response:
        if not self._pbap_client:
            return web.json_response({"status": "error", "message": "PBAP client not initialized"}, status=503)
        return web.json_response({"status": "ok", "contacts": self._pbap_client.get_contacts()})

    async def handle_get_phone_recents(self, request: web.Request) -> web.Response:
        if not self._pbap_client:
            return web.json_response({"status": "error", "message": "PBAP client not initialized"}, status=503)
        return web.json_response({"status": "ok", "recents": self._pbap_client.get_recents()})

    async def handle_get_phone_favorites(self, request: web.Request) -> web.Response:
        if not self._pbap_client:
            return web.json_response({"status": "error", "message": "PBAP client not initialized"}, status=503)
        return web.json_response({"status": "ok", "favorites": self._pbap_client.get_favorites()})

    async def handle_post_phone_sync(self, request: web.Request) -> web.Response:
        if not self._pbap_client:
            return web.json_response({"status": "error", "message": "PBAP client not initialized"}, status=503)
        res = await self._pbap_client.sync(self._active_device or "")
        payload = {
            "status": "ok",
            "sync": res,
            "contacts": self._pbap_client.get_contacts(),
            "favorites": self._pbap_client.get_favorites(),
            "recents": self._pbap_client.get_recents(),
        }
        self.publish("phone.pbap_synced", payload)
        return web.json_response(payload)

    async def _auto_sync_pbap(self, address: str) -> None:
        await asyncio.sleep(2.0)
        if self._pbap_client and self._active_device == address:
            try:
                res = await self._pbap_client.sync(address)
                self.publish("phone.pbap_synced", {
                    "status": "ok",
                    "sync": res,
                    "contacts": self._pbap_client.get_contacts(),
                    "favorites": self._pbap_client.get_favorites(),
                    "recents": self._pbap_client.get_recents(),
                })
            except Exception as e:
                self.log.debug(f"Auto PBAP sync notice: {e}")

    def _trigger_device_telemetry(self, address: str) -> None:
        """Trigger underlying Bluetooth adapter to query real telemetry (battery, RSSI, operator)."""
        if not address:
            return
        if hasattr(self._bt_adapter, "_check_win_device_telemetry"):
            try:
                self._bt_adapter._check_win_device_telemetry(address)
            except Exception as exc:
                self.log.debug(f"Win telemetry trigger notice for {address}: {exc}")
        elif hasattr(self._bt_adapter, "_check_device_telemetry") and hasattr(self._bt_adapter, "_bus"):
            try:
                dev_path = f"/org/bluez/hci0/dev_{address.replace(':', '_').upper()}"
                self._bt_adapter._check_device_telemetry(dev_path, address)
            except Exception as exc:
                self.log.debug(f"BlueZ telemetry trigger notice for {address}: {exc}")

    async def _telemetry_poll_loop(self) -> None:
        """Periodic background refresh of Bluetooth battery & RSSI while a device is active."""
        while self._running:
            await asyncio.sleep(10.0)
            if self._active_device and self._running:
                try:
                    self._trigger_device_telemetry(self._active_device)
                except Exception as exc:
                    self.log.debug(f"Periodic telemetry refresh notice: {exc}")

    async def teardown(self) -> None:
        self.log.info("Teardown ConnectivityManagerModule...")
        self._running = False
        self._rfcomm_listening = False
        self._autoconnect_event.set()
        if hasattr(self, "_status_changed_evt") and self._status_changed_evt:
            self._status_changed_evt.set()
        if self._autoconnect_task:
            self._autoconnect_task.cancel()
            try:
                await self._autoconnect_task
            except asyncio.CancelledError:
                pass
            self._autoconnect_task = None
        if hasattr(self, "_telemetry_poll_task") and self._telemetry_poll_task:
            self._telemetry_poll_task.cancel()
            try:
                await self._telemetry_poll_task
            except asyncio.CancelledError:
                pass
            self._telemetry_poll_task = None
        if self._bt_adapter:
            await self._bt_adapter.teardown()
        if self._wifi_adapter:
            await self._wifi_adapter.teardown()


if __name__ == "__main__":
    run_module(ConnectivityManagerModule)
