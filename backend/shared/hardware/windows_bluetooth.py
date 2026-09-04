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


class WinRTSocketAdapter:
    """Bridge WinRT StreamSocket to native Python blocking socket interface."""
    def __init__(self, stream_sock):
        import winrt.windows.storage.streams as streams
        self.sock = stream_sock
        self.reader = streams.DataReader(stream_sock.input_stream)
        self.reader.input_stream_options = streams.InputStreamOptions.PARTIAL
        self.writer = streams.DataWriter(stream_sock.output_stream)
        self.loop = asyncio.new_event_loop()

    def setblocking(self, flag): pass
    def settimeout(self, timeout): pass

    def recv(self, bufsize: int) -> bytes:
        async def _recv():
            try:
                await self.reader.load_async(bufsize)
                length = self.reader.unconsumed_buffer_length
                if length == 0:
                    return b""
                import winrt.windows.security.cryptography as crypto
                ibuffer = self.reader.read_buffer(length)
                return bytes(crypto.CryptographicBuffer.copy_to_byte_array(ibuffer))
            except Exception:
                return b""
        return self.loop.run_until_complete(_recv())

    def sendall(self, data: bytes) -> None:
        async def _send():
            self.writer.write_bytes(bytearray(data))
            await self.writer.store_async()
        self.loop.run_until_complete(_send())

    def close(self):
        try:
            self.sock.close()
            self.loop.close()
        except Exception:
            pass


def _win32_enumerate_devices(
    return_remembered: bool = True,
    return_connected: bool = True,
    return_unknown: bool = False,
    issue_inquiry: bool = False,
    inquiry_timeout_multiplier: int = 4,
    on_device_found_cb: Optional[Callable[[dict], None]] = None,
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
        on_device_found_cb: Callback invoked immediately as each device is found.

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
        dev_dict = {
            "address": addr_str,
            "name": info.szName or "Unknown",
            "connected": bool(info.fConnected),
            "paired": bool(info.fRemembered),
            "trusted": bool(info.fAuthenticated),
        }
        if on_device_found_cb:
            try:
                on_device_found_cb(dev_dict)
            except Exception as e:
                log.debug(f"Error in on_device_found_cb for {addr_str}: {e}")
        return dev_dict

    try:
        results.append(_extract_device(dev_info))
        # Iterate remaining devices
        bt_apis.BluetoothFindNextDevice.restype = ctypes.c_int
        while bt_apis.BluetoothFindNextDevice(ctypes.c_void_p(h_find), ctypes.byref(dev_info)):
            results.append(_extract_device(dev_info))
    finally:
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
        self._on_battery_cb: Optional[Callable[[str, int, int], None]] = None
        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._mock_paired: list[dict] = []
        self._active_device_handles: dict[str, Any] = {}
        self._active_outbound_sockets: dict[str, Any] = {}
        self._disconnected_override_addrs: set[str] = set()
        self._adapter_address: str = ""
        self._pairing_args: dict[str, Any] = {}
        self._pairing_deferrals: dict[str, Any] = {}

    def set_on_pin_callback(self, on_pin_cb: Callable[[str, str], None]) -> None:
        """Register a persistent global callback for PIN/passkey pairing requests."""
        self._on_pin_requested_cb = on_pin_cb

    def set_on_connection_callback(self, on_conn_cb: Callable[[str, bool], None]) -> None:
        """Register a persistent global callback for device connection state changes."""
        self._on_connection_cb = on_conn_cb

    def set_on_battery_callback(self, on_bat_cb: Callable) -> None:
        """Register a callback for battery level (%), signal strength (bars 0-5), operator name, and roaming updates."""
        self._on_battery_cb = on_bat_cb

    def get_adapter_address(self) -> str:
        """Get the local Windows Bluetooth adapter MAC address."""
        return self._adapter_address

    @staticmethod
    def _rssi_to_bars(rssi: int) -> int:
        if rssi >= -60:
            return 5
        elif rssi >= -70:
            return 4
        elif rssi >= -80:
            return 3
        elif rssi >= -90:
            return 2
        else:
            return 1

    def _check_win_device_telemetry(self, address: str) -> None:
        """Query real battery & signal telemetry on Windows connection via WinRT GATT Battery Service."""
        if not self._on_battery_cb:
            return

        async def _query_telemetry():
            battery_pct = -1
            signal_bars = -1
            operator_name = ""
            is_roaming = False

            bt_mod, _ = _try_import_winrt()
            if bt_mod is not None:
                try:
                    bt_addr = self._parse_bt_address(address)
                    bt_device = await bt_mod.BluetoothDevice.from_bluetooth_address_async(bt_addr)
                    if bt_device is not None:
                        # 1. Query WinRT Signal Strength (Aep property)
                        try:
                            dev_info = bt_device.device_information
                            if dev_info and dev_info.properties:
                                rssi_val = dev_info.properties.lookup("System.Devices.Aep.SignalStrength")
                                if rssi_val is not None:
                                    rssi = int(rssi_val)
                                    signal_bars = self._rssi_to_bars(rssi)
                        except Exception:
                            pass

                        # 2. Query WinRT GATT Battery Service (UUID 0x180F, Char 0x2A19)
                        try:
                            import uuid
                            import winrt.windows.security.cryptography as crypto
                            
                            BATTERY_SERVICE_UUID = uuid.UUID("{0000180F-0000-1000-8000-00805F9B34FB}")
                            BATTERY_LEVEL_CHAR_UUID = uuid.UUID("{00002A19-0000-1000-8000-00805F9B34FB}")

                            services_result = await bt_device.get_gatt_services_for_uuid_async(BATTERY_SERVICE_UUID)
                            if services_result and services_result.services:
                                for s in services_result.services:
                                    chars_result = await s.get_characteristics_for_uuid_async(BATTERY_LEVEL_CHAR_UUID)
                                    if chars_result and chars_result.characteristics:
                                        for c in chars_result.characteristics:
                                            val_res = await c.read_value_async()
                                            if val_res and val_res.value:
                                                data_bytes = crypto.CryptographicBuffer.copy_to_byte_array(val_res.value)
                                                if len(data_bytes) > 0:
                                                    battery_pct = int(data_bytes[0])
                                                    log.info(f"🔋 WinRT GATT Battery level read: {address} battery={battery_pct}%")
                                                    break
                        except Exception as gatt_exc:
                            log.debug(f"WinRT GATT Battery query notice: {gatt_exc}")
                except Exception as e:
                    log.debug(f"WinRT device telemetry query notice: {e}")

            if battery_pct >= 0 or signal_bars >= 0 or operator_name:
                if self._on_battery_cb:
                    self._on_battery_cb(address, battery_pct, signal_bars, operator_name, is_roaming)

        loop = getattr(self, "_loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(_query_telemetry(), loop)
        else:
            try:
                asyncio.run(_query_telemetry())
            except Exception:
                pass



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
                    self._adapter_address = self._format_bt_address(adapter.bluetooth_address)
                    radio = await asyncio.wait_for(adapter.get_radio_async(), timeout=2.0)
                    if radio is not None:
                        from winrt.windows.devices.radios import RadioState
                        if radio.state != RadioState.ON:
                            await asyncio.wait_for(radio.set_state_async(RadioState.ON), timeout=2.0)
                            log.info("Bluetooth radio turned ON via WinRT")
                    log.info(f"Windows Bluetooth Adapter ready (address={self._adapter_address})")
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
                    deferral = getattr(args, "get_deferral", lambda: None)()
                    self._pairing_args[address] = args
                    self._pairing_deferrals[address] = deferral
                    cb = self._on_pin_requested_cb or on_pin_cb
                    if cb:
                        cb(address, str(pin))
                    if not deferral:
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
        args = self._pairing_args.pop(address, None)
        deferral = self._pairing_deferrals.pop(address, None)

        if args and deferral:
            if confirm:
                args.accept()
                log.info(f"WinRT custom pairing approved (args.accept) for {address}")
            else:
                log.info(f"WinRT custom pairing rejected for {address}")
            try:
                deferral.complete()
            except Exception as e:
                log.debug(f"WinRT deferral complete notice: {e}")
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
                import uuid
                import winrt.windows.networking.sockets as sockets

                bt_addr = self._parse_bt_address(address)
                bt_device = await bt_mod.BluetoothDevice.from_bluetooth_address_async(bt_addr)
                if bt_device is None:
                    return False, f"Device {address} not found"

                self._active_device_handles[address] = bt_device

                # Connect to phone's Bluetooth profiles to wake up phone's Android Auto service
                # Android Auto service wakes up upon ANY RFCOMM connection from a device with AA_UUID in SDP.
                # Windows often holds HFP (Hands-Free) exclusively, so we fallback to PBAP, MAP, OPP, etc.
                TARGET_UUIDS = [
                    AA_UUID.lower().replace("-", ""),
                    "0000111f00001000800000805f9b34fb", # HFP
                    "0000111200001000800000805f9b34fb", # HFP AG
                    "0000112f00001000800000805f9b34fb", # PBAP
                    "0000113200001000800000805f9b34fb", # MAP
                    "0000110500001000800000805f9b34fb", # OPP
                ]

                services_result = await bt_device.get_rfcomm_services_async()
                
                connected_successfully = False
                if services_result is not None and services_result.services is not None:
                    services_list = list(services_result.services)
                    log.info(f"WinRT connect: found {len(services_list)} RFCOMM service(s) on {address}")
                    
                    import winrt.windows.networking as networking
                    import winrt.windows.networking.sockets as sockets

                    # Try connecting to services in order of preference
                    for preferred_uuid in TARGET_UUIDS:
                        if not self._running or connected_successfully:
                            break
                            
                        for svc in services_list:
                            if not self._running:
                                break
                            svc_uuid = str(svc.service_id.uuid).lower().replace("-", "").replace("{", "").replace("}", "")
                            if svc_uuid == preferred_uuid:
                                log.info(f"Attempting WinRT StreamSocket connect to service ({svc_uuid}) on {address}...")
                                stream_sock = sockets.StreamSocket()
                                try:
                                    await stream_sock.connect_async(
                                        svc.connection_host_name,
                                        svc.connection_service_name
                                    )
                                    log.info(f"Successfully established WinRT StreamSocket RFCOMM connection to {address} ({svc_uuid})")
                                    self._active_outbound_sockets[address] = stream_sock
                                    connected_successfully = True
                                    break
                                except Exception as e:
                                    if "-2147014848" in str(e) or "10048" in str(e) or "0x80072740" in str(e).lower():
                                        log.info(f"WinRT StreamSocket profile ({svc_uuid}) already active via Windows OS — trying next profile...")
                                        continue
                                    else:
                                        log.warning(f"WinRT StreamSocket connection to {address} ({svc_uuid}) failed: {e}")

                if connected_successfully:
                    # Mark device as connected in our local state
                    for dev in self._mock_paired:
                        if dev["address"] == address:
                            dev["connected"] = True
                            break
                    self._check_win_device_telemetry(address)
                    return True, ""

                # 2. Fallback: Mark connected in local paired list
                for dev in self._mock_paired:
                    if dev["address"] == address:
                        dev["connected"] = True
                        break
                self._check_win_device_telemetry(address)
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
        
        # Close outbound socket if we hold it
        if address in self._active_outbound_sockets:
            try:
                self._active_outbound_sockets[address].close()
            except Exception:
                pass
            del self._active_outbound_sockets[address]

        # In WinRT, disposing the BluetoothDevice object drops the connection
        if address in self._active_device_handles:
            try:
                self._active_device_handles[address].close()
            except Exception:
                pass
            del self._active_device_handles[address]

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
            # Try native Winsock Bluetooth RFCOMM socket on Windows
            self._server_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            bound = False
            for bind_addr in ("", "00:00:00:00:00:00", self.get_adapter_address()):
                if not bind_addr and bind_addr != "":
                    continue
                try:
                    self._server_sock.bind((bind_addr, RFCOMM_CHANNEL))
                    bound = True
                    log.info(f"Windows RFCOMM socket successfully bound to '{bind_addr}' channel {RFCOMM_CHANNEL}")
                    break
                except Exception:
                    pass

            if not bound:
                raise OSError("All Winsock AF_BLUETOOTH bind attempts failed")

            self._server_sock.listen(1)
            self._running = True

            # SDP service registration via Winsock WSASetServiceW (non-blocking thread)
            threading.Thread(target=self._register_sdp_service, daemon=True, name="windows-sdp-register").start()
        except Exception as e:
            log.warning(f"AF_BLUETOOTH binding failed ({e}) — attempting WinRT RfcommServiceProvider real Bluetooth listener fallback...")
            winrt_ok = False
            try:
                self._try_winrt_sdp_publish()
                winrt_ok = True
                self._running = True
                log.info("Successfully published Android Auto UUID via WinRT RfcommServiceProvider fallback")
            except Exception as winrt_err:
                log.warning(f"WinRT fallback failed ({winrt_err}) — falling back to mock loopback RFCOMM server socket for VM testing")

            if not winrt_ok:
                self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_sock.bind(("127.0.0.1", 15289))
                self._server_sock.listen(1)
                self._running = True

        if self._server_sock:
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
            NS_BTH = 16  # Winsock Bluetooth Namespace ID (16)
            RNRSERVICE_REGISTER = 2  # Winsock RNRSERVICE_REGISTER = 2 (0x00000002)
            SOCK_STREAM = socket.SOCK_STREAM

            ws2_32 = ctypes.windll.Ws2_32
            rnr_guid = GUID(AA_UUID)

            # Build SOCKADDR_BTH for the local listen address.
            # For WSASetServiceW registration in NS_BTH, serviceClassId inside SOCKADDR_BTH
            # is zeroed while lpServiceClassId in WSAQUERYSETW identifies the service GUID.
            local_addr = SOCKADDR_BTH()
            local_addr.addressFamily = AF_BTH
            local_addr.btAddr = 0          # BDADDR_ANY — accept on any local adapter
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
            qs.dwNameSpace = NS_BTH  # NS_BTH = 16
            qs.dwNumberOfCsAddrs = 1
            qs.lpcsaBuffer = ctypes.pointer(csa)

            # RNRSERVICE_REGISTER = 2
            ret = ws2_32.WSASetServiceW(ctypes.byref(qs), RNRSERVICE_REGISTER, 0)
            if ret != 0:
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
            import winrt.windows.networking.sockets as sockets
            from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceProvider, RfcommServiceId
            
            u = uuid.UUID(f"{{{AA_UUID.strip('{}')}}}")
            service_id = RfcommServiceId.from_uuid(u)

            async def _publish():
                try:
                    provider_op = RfcommServiceProvider.create_async(service_id)
                    provider = await provider_op
                    if provider is not None:
                        listener = sockets.StreamSocketListener()
                        # Set socket options for shared Bluetooth adapter binding
                        try:
                            listener.control.quality_of_service = sockets.SocketQualityOfService.NORMAL
                        except Exception:
                            pass

                        def on_connection(sender, args):
                            if not self._running:
                                try:
                                    args.socket.close()
                                except Exception:
                                    pass
                                return
                            try:
                                remote_host = args.socket.information.remote_address.display_name.upper()
                            except Exception:
                                try:
                                    remote_host = args.socket.information.remote_host_name.raw_name
                                except Exception:
                                    remote_host = "Unknown"
                            log.info(f"🔵 [BT Stage 1/5] 🎉 RFCOMM Profile Connection accepted from ({remote_host})!")
                            cb = self._on_connection_cb
                            if cb and self._running:
                                adapted_sock = WinRTSocketAdapter(args.socket)
                                cb(adapted_sock, remote_host)
                            else:
                                try:
                                    args.socket.close()
                                except Exception:
                                    pass

                        listener.add_connection_received(on_connection)
                        await listener.bind_service_name_async(provider.service_id.as_string())
                        
                        # Add Service Name "AndroidAuto" to SDP records (Attribute 0x0100)
                        # SDP Text String (Type 4, size index 5) -> (4 << 3) | 5 = 37 (0x25)
                        # Length 11 (0x0b), followed by ASCII "AndroidAuto"
                        try:
                            import winrt.windows.security.cryptography as crypto
                            sdp_name_bytes = b'\x25\x0bAndroidAuto'
                            buf = crypto.CryptographicBuffer.create_from_byte_array(bytearray(sdp_name_bytes))
                            provider.sdp_raw_attributes[0x0100] = buf
                        except Exception as e:
                            log.warning(f"Failed to add Service Name to WinRT SDP: {e}")

                        # Start advertising with radio_discoverable=True
                        try:
                            provider.start_advertising(listener, True)
                        except Exception:
                            # Fallback if overload fails
                            provider.start_advertising(listener)

                        self._winrt_provider = provider
                        self._winrt_listener = listener
                        log.info("Android Auto UUID successfully advertised via WinRT RfcommServiceProvider")
                except Exception as e:
                    log.warning(f"WinRT RfcommServiceProvider advertising failed: [{type(e).__name__}] {e}")
                
            loop = getattr(self, "_loop", None)
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_publish(), loop)
            else:
                asyncio.run(_publish())
        except Exception as e:
            log.warning(f"WinRT SDP publisher fallback failed: [{type(e).__name__}] {e}")

    def _accept_loop(self) -> None:
        log.info("Windows RFCOMM connection accept loop started")
        while self._running:
            try:
                self._server_sock.settimeout(2.0)
                client_sock, client_info = self._server_sock.accept()
                client_mac = client_info[0].upper()
                log.info(f"🔵 [BT Stage 1/5] 🎉 RFCOMM Profile Connection accepted from {client_mac}!")
                self._check_win_device_telemetry(client_mac)
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
        self._on_connection_cb = None
        if hasattr(self, "_winrt_provider") and self._winrt_provider:
            try:
                self._winrt_provider.stop_advertising()
            except Exception as e:
                log.debug(f"WinRT stop_advertising exception: {e}")
            self._winrt_provider = None
        if hasattr(self, "_winrt_listener") and self._winrt_listener:
            try:
                self._winrt_listener.close()
            except Exception as e:
                log.debug(f"WinRT listener close exception: {e}")
            self._winrt_listener = None
        for addr, s in list(self._active_outbound_sockets.items()):
            try:
                s.close()
            except Exception:
                pass
        self._active_outbound_sockets.clear()
        self._active_device_handles.clear()

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
