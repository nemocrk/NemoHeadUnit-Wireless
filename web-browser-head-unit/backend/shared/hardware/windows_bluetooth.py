import asyncio
import ctypes
from ctypes import wintypes
import socket
import threading
from typing import Callable, Optional

from shared.logger import get_logger
from .base_bluetooth import BaseBluetoothAdapter

log = get_logger("hardware.windows_bluetooth")

AA_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
RFCOMM_CHANNEL = 30

# WinRT Bluetooth device selector AQS fragment
_BT_AQS_SELECTOR = 'System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}"'

# --------------------------------------------------------------------------
# Win32 Winsock Structures (for SDP registration)
# --------------------------------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),        # 4 bytes on all platforms (c_ulong is 8 on Linux)
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, uuid_str: str):
        # Format: 4de17a00-52cb-11e6-bdf4-0800200c9a66
        parts = uuid_str.split("-")
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        self.Data4[0] = int(parts[3][:2], 16)
        self.Data4[1] = int(parts[3][2:], 16)
        for i in range(6):
            self.Data4[2+i] = int(parts[4][2*i:2*i+2], 16)

# SOCKADDR_BTH — Winsock Bluetooth socket address (Ws2bth.h)
# _pack_ = 1 matches the actual 30-byte layout defined in Ws2bth.h;
# without explicit packing, ctypes inserts 6 bytes of padding after
# addressFamily (aligning btAddr to 8 bytes) producing an incorrect struct.
class SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("addressFamily", ctypes.c_ushort),    # AF_BTH = 32 (2 bytes)
        ("btAddr",        ctypes.c_ulonglong),  # BTH_ADDR  (8 bytes)
        ("serviceClassId", GUID),               # GUID      (16 bytes)
        ("port",          ctypes.c_uint32),     # ULONG / RFCOMM channel (4 bytes)
    ]  # Total: 2+8+16+4 = 30 bytes, matching sizeof(SOCKADDR_BTH) in Ws2bth.h

class SOCKADDR_INFO(ctypes.Structure):
    """Generic socket address container used in CSADDR_INFO."""
    _fields_ = [
        ("lpSockaddr", ctypes.c_void_p),
        ("iSockaddrLength", ctypes.c_int),
    ]

class CSADDR_INFO(ctypes.Structure):
    _fields_ = [
        ("LocalAddr",  SOCKADDR_INFO),
        ("RemoteAddr", SOCKADDR_INFO),
        ("iSocketType", ctypes.c_int),
        ("iProtocol",   ctypes.c_int),
    ]

class WSAQUERYSETW(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("lpszServiceInstanceName", wintypes.LPWSTR),
        ("lpServiceClassId", ctypes.POINTER(GUID)),
        ("lpVersion", ctypes.c_void_p),
        ("lpszComment", wintypes.LPWSTR),
        ("dwNameSpace", wintypes.DWORD),
        ("lpNSProviderId", ctypes.POINTER(GUID)),
        ("lpszContext", wintypes.LPWSTR),
        ("dwNumberOfProtocols", wintypes.DWORD),
        ("lpafpProtocols", ctypes.c_void_p),
        ("lpszQueryString", wintypes.LPWSTR),
        ("dwNumberOfCsAddrs", wintypes.DWORD),
        ("lpcsaBuffer", ctypes.POINTER(CSADDR_INFO)),
        ("dwOutputFlags", wintypes.DWORD),
        ("lpBlob", ctypes.c_void_p),
    ]


# --------------------------------------------------------------------------
# Win32 Bluetooth Enumeration Structures (BluetoothAPIs.h)
# --------------------------------------------------------------------------
# These wrap the classic Win32 Bluetooth device enumeration API which is
# stable and avoids the broken WinRT DeviceInformation.find_all_async
# overload issue in winrt-python 3.x.

class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", ctypes.c_ushort),
        ("wMonth", ctypes.c_ushort),
        ("wDayOfWeek", ctypes.c_ushort),
        ("wDay", ctypes.c_ushort),
        ("wHour", ctypes.c_ushort),
        ("wMinute", ctypes.c_ushort),
        ("wSecond", ctypes.c_ushort),
        ("wMilliseconds", ctypes.c_ushort),
    ]

class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("Address", ctypes.c_ulonglong),   # BTH_ADDR (8 bytes)
        ("ulClassofDevice", ctypes.c_uint32),
        ("fConnected", ctypes.c_int),      # BOOL
        ("fRemembered", ctypes.c_int),     # BOOL — "paired" in Windows terminology
        ("fAuthenticated", ctypes.c_int),  # BOOL
        ("stLastSeen", SYSTEMTIME),
        ("stLastUsed", SYSTEMTIME),
        ("szName", ctypes.c_wchar * 248),  # WCHAR szName[248]
    ]

class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("fReturnAuthenticated", ctypes.c_int),
        ("fReturnRemembered", ctypes.c_int),
        ("fReturnUnknown", ctypes.c_int),
        ("fReturnConnected", ctypes.c_int),
        ("fIssueInquiry", ctypes.c_int),
        ("cTimeoutMultiplier", ctypes.c_ubyte),
        ("hRadio", ctypes.c_void_p),       # HANDLE — NULL = all radios
    ]


def _win32_enumerate_devices(
    return_remembered: bool = True,
    return_connected: bool = True,
    return_unknown: bool = False,
    issue_inquiry: bool = False,
    inquiry_timeout_multiplier: int = 4,
) -> list[dict]:
    """Enumerate Bluetooth devices using the Win32 BluetoothFindFirstDevice API.

    This avoids the broken WinRT DeviceInformation.find_all_async(selector)
    overload in winrt-python 3.x.

    Args:
        return_remembered: Include paired ("remembered") devices.
        return_connected: Include currently connected devices.
        return_unknown: Include unknown/unpaired devices visible nearby.
        issue_inquiry: Perform an active Bluetooth inquiry scan.
        inquiry_timeout_multiplier: Inquiry duration = multiplier * 1.28 seconds.

    Returns:
        List of device dicts with address, name, connected, paired keys.
    """
    try:
        bt_apis = ctypes.windll.BluetoothAPIs
    except OSError:
        try:
            bt_apis = ctypes.windll.bthprops
        except OSError:
            log.warning("Neither BluetoothAPIs.dll nor bthprops.cpl found — cannot enumerate devices")
            return []

    # Setup search params
    search_params = BLUETOOTH_DEVICE_SEARCH_PARAMS()
    search_params.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_SEARCH_PARAMS)
    search_params.fReturnAuthenticated = 1  # Always include authenticated
    search_params.fReturnRemembered = 1 if return_remembered else 0
    search_params.fReturnUnknown = 1 if return_unknown else 0
    search_params.fReturnConnected = 1 if return_connected else 0
    search_params.fIssueInquiry = 1 if issue_inquiry else 0
    search_params.cTimeoutMultiplier = inquiry_timeout_multiplier
    search_params.hRadio = None  # Search all radios

    # Setup device info buffer
    dev_info = BLUETOOTH_DEVICE_INFO()
    dev_info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)

    results: list[dict] = []

    # BluetoothFindFirstDevice returns a HANDLE (HBLUETOOTH_DEVICE_FIND)
    bt_apis.BluetoothFindFirstDevice.restype = ctypes.c_void_p
    h_find = bt_apis.BluetoothFindFirstDevice(
        ctypes.byref(search_params),
        ctypes.byref(dev_info),
    )

    if h_find is None or h_find == 0:
        # No devices found or error
        return results

    def _extract_device(info: BLUETOOTH_DEVICE_INFO) -> dict:
        addr = info.Address
        addr_str = ":".join(
            f"{(addr >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1)
        )
        return {
            "address": addr_str,
            "name": info.szName or "Unknown",
            "connected": bool(info.fConnected),
            "paired": bool(info.fRemembered),
            "trusted": bool(info.fAuthenticated),
        }

    results.append(_extract_device(dev_info))

    # Iterate remaining devices
    bt_apis.BluetoothFindNextDevice.restype = ctypes.c_int
    while bt_apis.BluetoothFindNextDevice(ctypes.c_void_p(h_find), ctypes.byref(dev_info)):
        results.append(_extract_device(dev_info))

    # Close the search handle
    bt_apis.BluetoothFindDeviceClose(ctypes.c_void_p(h_find))

    return results


# --------------------------------------------------------------------------
# WinRT helper — lazy import guard (used for setup/pair/connect/disconnect)
# --------------------------------------------------------------------------

def _try_import_winrt():
    """Attempt to import WinRT Bluetooth modules. Returns tuple of modules or Nones.

    Package names on PyPI (add to environment.windows.yml pip section):
        winrt-Windows.Devices.Bluetooth
        winrt-Windows.Devices.Enumeration
        winrt-Windows.Devices.Radios
    """
    try:
        import winrt.windows.devices.bluetooth as bt          # winrt-Windows.Devices.Bluetooth
        import winrt.windows.devices.enumeration as enum_mod  # winrt-Windows.Devices.Enumeration
        return bt, enum_mod
    except ImportError as e:
        log.warning(
            f"WinRT Bluetooth import failed: {e}\n"
            "  → Real Bluetooth discovery/pairing unavailable; running in mock mode.\n"
            "  → Fix: pip install winrt-Windows.Devices.Bluetooth "
            "winrt-Windows.Devices.Enumeration winrt-Windows.Devices.Radios"
        )
        return None, None


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------

class WindowsBluetoothAdapter(BaseBluetoothAdapter):
    def __init__(self):
        self._adapter_name = "NemoHeadUnit"
        self._discoverable = True
        self._discoverable_timeout = 0
        self._on_pin_requested_cb: Optional[Callable[[str, str], None]] = None
        self._on_connection_cb: Optional[Callable[[object, str], None]] = None
        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._mock_paired: list[dict] = []
        self._active_device_handles: dict[str, Any] = {}
        self._disconnected_override_addrs: set[str] = set()


    async def setup(self, adapter_name: str, discoverable: bool, discoverable_timeout: int) -> None:
        self._adapter_name = adapter_name
        self._discoverable = discoverable
        self._loop = asyncio.get_running_loop()

        # Attempt to set the adapter name via WinRT BluetoothAdapter
        bt_mod, _ = _try_import_winrt()
        if bt_mod is not None:
            try:
                adapter = await asyncio.wait_for(bt_mod.BluetoothAdapter.get_default_async(), timeout=2.0)
                if adapter is not None:
                    radio = await asyncio.wait_for(adapter.get_radio_async(), timeout=2.0)
                    if radio is not None:
                        from winrt.windows.devices.radios import RadioState
                        if radio.state != RadioState.ON:
                            await asyncio.wait_for(radio.set_state_async(RadioState.ON), timeout=2.0)
                            log.info("Bluetooth radio turned ON via WinRT")
                    log.info(f"Windows Bluetooth Adapter ready (address={adapter.bluetooth_address:#014x})")
                else:
                    log.warning("WinRT BluetoothAdapter.get_default_async() returned None — no adapter found")
            except Exception as e:
                log.warning(f"WinRT BluetoothAdapter setup notice (non-critical): {e}")
        else:
            log.info("WinRT not available — running in mock Bluetooth mode")

        log.info(f"Windows Bluetooth Adapter setup alias: {adapter_name}")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def start_discovery(self, duration_sec: int, on_device_found_cb: Callable[[dict], None]) -> None:
        """Start a Bluetooth Classic discovery scan using Win32 API."""
        log.info(f"Windows Bluetooth discovery started (duration={duration_sec}s)...")

        # Calculate inquiry timeout: duration_sec / 1.28 ≈ multiplier (min 1, max 48)
        timeout_mult = max(1, min(48, int(duration_sec / 1.28)))

        # Run the blocking Win32 enumeration in a thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        try:
            devices = await loop.run_in_executor(
                None,
                lambda: _win32_enumerate_devices(
                    return_remembered=True,
                    return_connected=True,
                    return_unknown=True,
                    issue_inquiry=True,
                    inquiry_timeout_multiplier=timeout_mult,
                ),
            )

            if devices:
                for dev in devices:
                    on_device_found_cb({
                        "address": dev["address"],
                        "name": dev["name"],
                        "rssi": 0,  # Win32 Classic API doesn't expose RSSI
                    })
                log.info(f"Win32 Bluetooth discovery completed — found {len(devices)} device(s)")
                return
            else:
                log.info("Win32 Bluetooth discovery completed — no devices found")
                return
        except Exception as e:
            log.warning(f"Win32 discovery failed ({e}) — falling back to mock")

        # Mock fallback — emit a simulated device after a short delay
        await asyncio.sleep(0.5)
        on_device_found_cb({
            "address": "90:70:60:50:40:30",
            "name": "Simulated Phone",
            "rssi": -55
        })

    # ------------------------------------------------------------------
    # Stop discovery
    # ------------------------------------------------------------------

    async def stop_discovery(self) -> None:
        log.info("Windows Bluetooth active discovery stopped")

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    async def pair_device(self, address: str, on_pin_cb: Callable[[str, str], None]) -> tuple[bool, str]:
        log.info(f"Triggering pairing with {address} on Windows")
        bt_mod, enum_mod = _try_import_winrt()

        if bt_mod is not None and enum_mod is not None:
            try:
                return await self._winrt_pair(bt_mod, enum_mod, address, on_pin_cb)
            except Exception as e:
                log.warning(f"WinRT pairing failed ({e}) — returning mock success")

        # Mock fallback — simulate successful pairing
        self._ensure_mock_paired(address, "Unknown Device")
        return True, ""

    async def _winrt_pair(self, bt_mod, enum_mod, address: str,
                          on_pin_cb: Callable[[str, str], None]) -> tuple[bool, str]:
        """Pair with a Bluetooth device via WinRT DeviceInformationPairing."""
        bt_addr = self._parse_bt_address(address)
        bt_device = await bt_mod.BluetoothDevice.from_bluetooth_address_async(bt_addr)
        if bt_device is None:
            return False, f"Device {address} not found via WinRT"

        dev_info = bt_device.device_information
        if dev_info is None:
            return False, "Could not retrieve DeviceInformation for device"

        pairing = dev_info.pairing
        if pairing is None:
            return False, "DeviceInformationPairing unavailable"

        if pairing.is_paired:
            log.info(f"Device {address} is already paired")
            name = bt_device.name or "Unknown"
            self._ensure_mock_paired(address, str(name))
            return True, ""

        # Use custom pairing to handle PIN confirmation
        custom = pairing.custom
        if custom is not None:
            from winrt.windows.devices.enumeration import DevicePairingKinds

            def on_custom_pairing_requested(sender, args):
                kind = args.pairing_kind
                if kind == DevicePairingKinds.CONFIRM_ONLY:
                    args.accept()
                elif kind == DevicePairingKinds.CONFIRM_PIN_MATCH:
                    pin = args.pin or "000000"
                    on_pin_cb(address, str(pin))
                    args.accept()
                elif kind == DevicePairingKinds.PROVIDE_PIN:
                    args.accept("0000")
                else:
                    args.accept()

            custom.add_pairing_requested(on_custom_pairing_requested)

            result = await custom.pair_async(
                DevicePairingKinds.CONFIRM_ONLY
                | DevicePairingKinds.CONFIRM_PIN_MATCH
                | DevicePairingKinds.PROVIDE_PIN
            )
        else:
            # Fallback to basic pairing
            result = await pairing.pair_async()

        from winrt.windows.devices.enumeration import DevicePairingResultStatus
        if result.status == DevicePairingResultStatus.PAIRED:
            name = bt_device.name or "Unknown"
            self._ensure_mock_paired(address, str(name))
            log.info(f"WinRT pairing with {address} succeeded")
            return True, ""
        elif result.status == DevicePairingResultStatus.ALREADY_PAIRED:
            name = bt_device.name or "Unknown"
            self._ensure_mock_paired(address, str(name))
            return True, ""
        else:
            return False, f"Pairing result: {result.status}"

    async def confirm_pairing(self, address: str, confirm: bool) -> bool:
        log.info(f"Windows pairing confirm: {confirm} for {address}")
        # WinRT custom pairing auto-accepts in the callback; this is a no-op here.
        return True

    # ------------------------------------------------------------------
    # Connect / Disconnect
    # ------------------------------------------------------------------

    async def connect_device(self, address: str) -> tuple[bool, str]:
        log.info(f"Connecting to {address} on Windows")
        self._disconnected_override_addrs.discard(address)
        bt_mod, _ = _try_import_winrt()

        if bt_mod is not None:
            try:
                bt_addr = self._parse_bt_address(address)
                bt_device = await bt_mod.BluetoothDevice.from_bluetooth_address_async(bt_addr)
                if bt_device is None:
                    return False, f"Device {address} not found"

                # Save active WinRT device object reference
                self._active_device_handles[address] = bt_device

                # Accessing RFCOMM services triggers SDP inquiry + profile connect
                from winrt.windows.devices.bluetooth.rfcomm import RfcommDeviceServicesResult
                services_result = await bt_device.get_rfcomm_services_async()
                if services_result is not None and services_result.services is not None:
                    count = len(list(services_result.services))
                    log.info(f"WinRT connect: found {count} RFCOMM service(s) on {address}")

                # Mark as connected in our local paired list
                for dev in self._mock_paired:
                    if dev["address"] == address:
                        dev["connected"] = True
                        break

                return True, ""
            except Exception as e:
                log.warning(f"WinRT connect failed ({e}) — returning mock success")

        # Mock fallback
        for dev in self._mock_paired:
            if dev["address"] == address:
                dev["connected"] = True
                break
        return True, ""

    async def disconnect_device(self, address: str) -> bool:
        log.info(f"Disconnecting {address} on Windows")
        self._disconnected_override_addrs.add(address)

        # 1. Close and release existing active WinRT handle if present
        bt_handle = self._active_device_handles.pop(address, None)
        if bt_handle is not None:
            try:
                bt_handle.close()
                log.info(f"WinRT held device handle for {address} closed (disconnect)")
            except Exception as e:
                log.debug(f"Error closing held WinRT device handle: {e}")

        # 2. Query fresh WinRT instance and call close()
        bt_mod, _ = _try_import_winrt()
        if bt_mod is not None:
            try:
                bt_addr = self._parse_bt_address(address)
                bt_device = await bt_mod.BluetoothDevice.from_bluetooth_address_async(bt_addr)
                if bt_device is not None:
                    bt_device.close()
                    log.info(f"WinRT freshly fetched device handle for {address} released")
            except Exception as e:
                log.debug(f"WinRT disconnect notice: {e}")

        # 3. Update local mock state
        for dev in self._mock_paired:
            if dev["address"] == address:
                dev["connected"] = False
                break

        return True

    # ------------------------------------------------------------------
    # Paired devices
    # ------------------------------------------------------------------

    async def remove_paired_device(self, address: str) -> bool:
        log.info(f"Removing paired device: {address}")
        bt_mod, enum_mod = _try_import_winrt()

        if bt_mod is not None and enum_mod is not None:
            try:
                bt_addr = self._parse_bt_address(address)
                bt_device = await bt_mod.BluetoothDevice.from_bluetooth_address_async(bt_addr)
                if bt_device is not None:
                    dev_info = bt_device.device_information
                    if dev_info is not None and dev_info.pairing is not None:
                        result = await dev_info.pairing.unpair_async()
                        from winrt.windows.devices.enumeration import DeviceUnpairingResultStatus
                        if result.status == DeviceUnpairingResultStatus.UNPAIRED:
                            log.info(f"WinRT unpair {address} succeeded")
                        elif result.status == DeviceUnpairingResultStatus.ALREADY_UNPAIRED:
                            log.info(f"WinRT unpair {address} — already unpaired")
                        else:
                            log.warning(f"WinRT unpair {address} result: {result.status}")
            except Exception as e:
                log.warning(f"WinRT unpair failed ({e})")

        # Remove from local list regardless
        self._mock_paired = [d for d in self._mock_paired if d["address"] != address]
        return True

    async def get_paired_devices(self) -> list[dict]:
        """Return paired Bluetooth devices using Win32 API (primary) with mock fallback."""
        try:
            # Run the synchronous Win32 call in a thread executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._win32_get_paired)
            if result:
                return result
        except Exception as e:
            log.warning(f"Win32 get_paired_devices failed ({e}) — returning mock list")

        # Mock fallback
        if not self._mock_paired:
            return [
                {"address": "90:70:60:50:40:30", "name": "Simulated Phone", "connected": False, "trusted": True}
            ]
        return list(self._mock_paired)

    def _win32_get_paired(self) -> list[dict]:
        """Enumerate paired (remembered) Bluetooth devices using Win32 API.

        Uses BluetoothFindFirstDevice/BluetoothFindNextDevice from
        BluetoothAPIs.dll — avoids the broken WinRT
        DeviceInformation.find_all_async(selector) overload entirely.
        """
        devices = _win32_enumerate_devices(
            return_remembered=True,
            return_connected=True,
            return_unknown=False,
            issue_inquiry=False,
        )

        results: list[dict] = []
        for dev in devices:
            addr = dev["address"]
            is_connected = dev["connected"] and (addr not in self._disconnected_override_addrs)
            results.append({
                "address": addr,
                "name": dev["name"],
                "connected": is_connected,
                "trusted": dev.get("trusted", True),
            })

        # Merge mock devices not visible to Win32 (e.g. loopback testing)
        win32_addrs = {d["address"] for d in results}
        for mock_dev in self._mock_paired:
            if mock_dev["address"] not in win32_addrs:
                results.append(mock_dev)

        log.debug(f"Win32 paired devices: {[d['address'] for d in results]}")
        return results

    # ------------------------------------------------------------------
    # RFCOMM Server
    # ------------------------------------------------------------------

    def register_rfcomm_server(self, on_connection_cb: Callable[[object, str], None]) -> bool:
        """Register the Android Auto RFCOMM profile with Winsock SDP and listen for connections."""
        self._on_connection_cb = on_connection_cb
        try:
            # Try native Bluetooth RFCOMM socket on Windows (BDADDR_ANY is "00:00:00:00:00:00")
            self._server_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            try:
                self._server_sock.bind(("00:00:00:00:00:00", RFCOMM_CHANNEL))
            except Exception:
                self._server_sock.bind(("", RFCOMM_CHANNEL))

            self._server_sock.listen(1)
            self._running = True
            log.info(f"Windows RFCOMM socket bound to channel {RFCOMM_CHANNEL}")

            # SDP service registration via Winsock WSASetServiceW (non-blocking thread)
            threading.Thread(target=self._register_sdp_service, daemon=True, name="windows-sdp-register").start()
        except Exception as e:
            # Fallback for VM / desktop testing environments without active Bluetooth hardware
            log.warning(f"AF_BLUETOOTH binding failed ({e}) — falling back to mock loopback RFCOMM server socket for VM testing")
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(("127.0.0.1", 15289))
            self._server_sock.listen(1)
            self._running = True

        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="windows-rfcomm-accept")
        self._thread.start()
        return True

    def _register_sdp_service(self) -> None:
        """Call Winsock WSASetServiceW to register the custom AA UUID in the SDP database.

        WSASetServiceW with NS_BTH requires lpcsaBuffer to be populated with at least
        one CSADDR_INFO entry whose LocalAddr points to a SOCKADDR_BTH describing the
        RFCOMM channel. Without this the namespace provider returns WSANO_DATA (11004).
        """
        try:
            AF_BTH = 32  # Ws2bth.h AF_BTH
            BTHPROTO_RFCOMM = 3
            SOCK_STREAM = socket.SOCK_STREAM

            ws2_32 = ctypes.windll.Ws2_32
            rnr_guid = GUID(AA_UUID)

            # Build SOCKADDR_BTH for the local listen address.
            # serviceClassId must match lpServiceClassId in WSAQUERYSETW so the
            # NS_BTH provider can locate and store the record.
            local_addr = SOCKADDR_BTH()
            local_addr.addressFamily = AF_BTH
            local_addr.btAddr = 0          # BDADDR_ANY — accept on any local adapter
            local_addr.serviceClassId = GUID(AA_UUID)  # must match lpServiceClassId
            local_addr.port = RFCOMM_CHANNEL

            # Build CSADDR_INFO with LocalAddr pointing to the SOCKADDR_BTH
            csa = CSADDR_INFO()
            csa.LocalAddr.lpSockaddr = ctypes.cast(
                ctypes.pointer(local_addr), ctypes.c_void_p
            )
            csa.LocalAddr.iSockaddrLength = ctypes.sizeof(SOCKADDR_BTH)
            csa.RemoteAddr.lpSockaddr = None
            csa.RemoteAddr.iSockaddrLength = 0
            csa.iSocketType = int(SOCK_STREAM)
            csa.iProtocol = BTHPROTO_RFCOMM

            # Build WSAQUERYSETW
            qs = WSAQUERYSETW()
            qs.dwSize = ctypes.sizeof(WSAQUERYSETW)
            qs.lpszServiceInstanceName = self._adapter_name + " AA"
            qs.lpServiceClassId = ctypes.pointer(rnr_guid)
            qs.dwNameSpace = 10  # NS_BTH
            qs.dwNumberOfCsAddrs = 1
            qs.lpcsaBuffer = ctypes.pointer(csa)

            # RNRSERVICE_REGISTER = 0
            ret = ws2_32.WSASetServiceW(ctypes.byref(qs), 0, 0)
            if ret != 0:
                # Fallback: try calling WSASetServiceW with RNRSERVICE_REGISTER (0) and null service class or try WinRT SDP publisher
                err = ws2_32.WSAGetLastError()
                log.warning(f"WSASetServiceW returned error code: {err} — trying WinRT RfcommServiceProvider fallback")
                self._try_winrt_sdp_publish()
            else:
                log.info("Android Auto UUID successfully registered in Windows SDP registry")
        except Exception as e:
            log.warning(f"Could not call WSASetServiceW via ctypes: {e}")
            self._try_winrt_sdp_publish()

    def _try_winrt_sdp_publish(self) -> None:
        """Fallback to WinRT RfcommServiceProvider to advertise AA_UUID in SDP database."""
        try:
            import uuid
            from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceProvider, RfcommServiceId
            
            # winrt-Windows.Devices.Bluetooth.Rfcomm accepts standard Python uuid.UUID or Guid
            u = uuid.UUID(AA_UUID)
            try:
                service_id = RfcommServiceId.from_uuid(u)
            except TypeError:
                # If Guid object required from winrt.system:
                import winrt.system as _wsys
                service_id = RfcommServiceId.from_uuid(_wsys.Guid(str(u)))
            
            async def _publish():
                try:
                    provider = await RfcommServiceProvider.create_async(service_id)
                    provider.start_advertising()
                    log.info("Android Auto UUID successfully advertised via WinRT RfcommServiceProvider")
                except Exception as e:
                    log.warning(f"WinRT RfcommServiceProvider advertising failed: {e}")
                
            loop = getattr(self, "_loop", None)
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_publish(), loop)
            else:
                asyncio.run(_publish())
        except Exception as e:
            log.warning(f"WinRT SDP publisher fallback failed: {e}")

    def _accept_loop(self) -> None:
        log.info("Windows RFCOMM connection accept loop started")
        while self._running:
            try:
                self._server_sock.settimeout(2.0)
                client_sock, client_info = self._server_sock.accept()
                client_mac = client_info[0].upper()
                log.info(f"Windows RFCOMM Connection accepted from {client_mac}")
                threading.Thread(
                    target=self._on_connection_cb,
                    args=(client_sock, client_mac),
                    daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.error(f"Windows RFCOMM accept error: {e}")
                break

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def teardown(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        log.info("Windows Bluetooth adapter shutdown complete")

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_bt_address(addr_int: int) -> str:
        """Convert a 48-bit integer Bluetooth address to colon-separated MAC string."""
        hex_str = f"{addr_int:012X}"
        return ":".join(hex_str[i:i+2] for i in range(0, 12, 2))

    @staticmethod
    def _parse_bt_address(mac: str) -> int:
        """Convert a colon-separated MAC string to a 48-bit integer."""
        return int(mac.replace(":", "").replace("-", ""), 16)

    def _ensure_mock_paired(self, address: str, name: str) -> None:
        """Add a device to the local mock paired list if not already present."""
        for dev in self._mock_paired:
            if dev["address"] == address:
                return
        self._mock_paired.append({
            "address": address,
            "name": name,
            "connected": False,
            "trusted": True,
        })
