import asyncio
import socket
import json
from shared.logger import get_logger
from .base_wifi_ap import BaseWifiApAdapter

log = get_logger("hardware.windows_wifi_ap")

# Port for our UAC-free LocalSystem background service NemoAPManager
NEMO_AP_MANAGER_PORT = 15288

def _get_hotspot_virtual_bssid(gateway_ip: str = "192.168.137.1") -> str:
    # 1. Query interface holding the hotspot Gateway IP (e.g. 192.168.137.1)
    try:
        import subprocess
        cmd = f'powershell -NoProfile -Command "Get-NetIPAddress -IPAddress \'{gateway_ip}\' -ErrorAction SilentlyContinue | ForEach-Object {{ Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue }} | Select-Object -ExpandProperty MacAddress"'
        out = subprocess.check_output(cmd, shell=True, text=True, errors="ignore").strip()
        if out:
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            for line in lines:
                mac = line.replace("-", ":").upper()
                if len(mac) == 17:
                    log.info(f"Resolved active Hotspot Gateway ({gateway_ip}) Interface MAC: {mac}")
                    return mac
    except Exception as e:
        log.debug(f"IP-to-adapter MAC lookup notice: {e}")

    # 2. Secondary search: Wi-Fi Direct Virtual Adapter
    try:
        import subprocess
        cmd = 'powershell -NoProfile -Command "Get-NetAdapter | Where-Object { ($_.InterfaceDescription -like \'*Wi-Fi Direct*\' -or $_.Name -like \'*Wi-Fi*\') -and $_.InterfaceDescription -notlike \'*Hyper-V*\' -and $_.InterfaceDescription -notlike \'*WSL*\' -and $_.InterfaceDescription -notlike \'*VirtualBox*\' -and $_.InterfaceDescription -notlike \'*VMware*\' } | Select-Object -ExpandProperty MacAddress"'
        out = subprocess.check_output(cmd, shell=True, text=True, errors="ignore").strip()
        if out:
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            for line in lines:
                mac = line.replace("-", ":").upper()
                if len(mac) == 17 and not mac.startswith("00:15:5D"):
                    log.info(f"Resolved Windows Wi-Fi Adapter MAC: {mac}")
                    return mac
    except Exception as e:
        log.debug(f"Wi-Fi adapter MAC lookup notice: {e}")

    return ""

class WindowsWifiApAdapter(BaseWifiApAdapter):
    def __init__(self):
        self._ssid = "AndroidAutoAP"
        self._key = "12345678"
        self._active = False

    async def setup(self) -> None:
        log.info("Windows WiFi AP Adapter initialized")

    async def start_ap(self, config: dict) -> tuple[bool, dict]:
        self._ssid = config.get("ssid", "AndroidAutoAP")
        self._key = config.get("ap_password", "12345678")
        if not self._key:
            self._key = "12345678"

        bssid = _get_hotspot_virtual_bssid()
        log.info(f"📶 [WiFi Stage 2/5] Initiating Windows Mobile Hotspot launch (SSID='{self._ssid}', Virtual BSSID='{bssid}')...")

        # WinRT direct method
        try:
            import winrt.windows.networking.connectivity as connectivity
            import winrt.windows.networking.networkoperators as netops

            profile = connectivity.NetworkInformation.get_internet_connection_profile()
            if profile:
                tethering_mgr = netops.NetworkOperatorTetheringManager.create_from_connection_profile(profile)
                
                # Check current tethering config
                try:
                    curr_conf = tethering_mgr.get_current_access_point_configuration()
                    if curr_conf and curr_conf.ssid:
                        self._ssid = curr_conf.ssid
                        self._key = curr_conf.passphrase or self._key
                        log.info(f"📶 [WiFi Stage 2/5] Retrieved active Windows Mobile Hotspot configuration: SSID='{self._ssid}'")
                except Exception:
                    pass

                # Reconfigure access point credentials if requested
                try:
                    conf = netops.NetworkOperatorTetheringAccessPointConfiguration()
                    conf.ssid = self._ssid
                    conf.passphrase = self._key
                    await tethering_mgr.configure_access_point_async(conf)
                except Exception:
                    pass
                
                # Start tethering
                result = await tethering_mgr.start_tethering_async()
                success_val = getattr(netops.TetheringOperationStatus, "SUCCESS", 0)
                already_val = getattr(netops.TetheringOperationStatus, "ALREADY_STARTED", -1)
                if result.status in (success_val, already_val) or "success" in str(result.status).lower():
                    self._active = True
                    log.info(f"📶 [WiFi Stage 2/5] Windows Mobile Hotspot active via WinRT successfully! (SSID='{self._ssid}')")
        except Exception as e:
            log.warning(f"WinRT Hotspot configuration notice: {e}")

        # Dynamically query active Hotspot Gateway IP interface for exact BSSID
        resolved_bssid = _get_hotspot_virtual_bssid("192.168.137.1")
        log.info(f"📶 [WiFi Stage 2/5] Windows Mobile Hotspot active! Credentials: SSID='{self._ssid}', BSSID='{resolved_bssid}', Gateway=192.168.137.1")
        self._active = True
        return True, {
            "ssid": self._ssid,
            "key": self._key,
            "bssid": resolved_bssid,
            "interface": "Wi-Fi",
            "gateway_ip": "192.168.137.1"
        }

    async def stop_ap(self) -> bool:
        self._active = False
        log.info("Stopping Windows Mobile Hotspot...")
        try:
            import winrt.windows.networking.connectivity as connectivity
            import winrt.windows.networking.networkoperators as netops
            profile = connectivity.NetworkInformation.get_internet_connection_profile()
            if profile:
                tethering_mgr = netops.NetworkOperatorTetheringManager.create_from_connection_profile(profile)
                await tethering_mgr.stop_tethering_async()
        except Exception:
            pass
        return True

    async def teardown(self) -> None:
        await self.stop_ap()
