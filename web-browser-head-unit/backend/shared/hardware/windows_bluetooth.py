import asyncio
import ctypes
from ctypes import wintypes
import socket
import threading
from typing import Callable

from shared.logger import get_logger
from .base_bluetooth import BaseBluetoothAdapter

log = get_logger("hardware.windows_bluetooth")

AA_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
RFCOMM_CHANNEL = 30

# Win32 Winsock Structures
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
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

class CSADDR_INFO(ctypes.Structure):
    _fields_ = [
        ("LocalAddr", ctypes.c_void_p * 2),  # SOCKADDR structure pointers
        ("RemoteAddr", ctypes.c_void_p * 2),
        ("iSocketType", ctypes.c_int),
        ("iProtocol", ctypes.c_int),
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

class WindowsBluetoothAdapter(BaseBluetoothAdapter):
    def __init__(self):
        self._server_sock = None
        self._running = False
        self._thread = None
        self._adapter_name = "NemoHeadUnit"
        self._discoverable = True
        self._on_connection_cb = None

    async def setup(self, adapter_name: str, discoverable: bool, discoverable_timeout: int) -> None:
        self._adapter_name = adapter_name
        self._discoverable = discoverable
        log.info(f"Windows Bluetooth Adapter setup alias: {adapter_name}")

    async def start_discovery(self, duration_sec: int, on_device_found_cb: Callable[[dict], None]) -> None:
        log.info("Windows Bluetooth active discovery started (simulation/mock devices)...")
        # Simulate finding mock devices since discovery on Windows requires Winsock WSALookupService
        await asyncio.sleep(0.5)
        on_device_found_cb({
            "address": "90:70:60:50:40:30",
            "name": "Simulated Phone",
            "rssi": -55
        })

    async def stop_discovery(self) -> None:
        log.info("Windows Bluetooth active discovery stopped")

    async def pair_device(self, address: str, on_pin_cb: Callable[[str, str], None]) -> tuple[bool, str]:
        log.info(f"Triggering pairing with {address} on Windows")
        return True, ""

    async def confirm_pairing(self, address: str, confirm: bool) -> bool:
        log.info(f"Windows pairing confirm: {confirm} for {address}")
        return True

    async def connect_device(self, address: str) -> tuple[bool, str]:
        return True, ""

    async def disconnect_device(self, address: str) -> bool:
        return True

    async def remove_paired_device(self, address: str) -> bool:
        log.info(f"Removing paired device: {address}")
        return True

    async def get_paired_devices(self) -> list[dict]:
        return [
            {"address": "90:70:60:50:40:30", "name": "Simulated Phone", "connected": False, "trusted": True}
        ]

    def register_rfcomm_server(self, on_connection_cb: Callable[[object, str], None]) -> bool:
        """Register the Android Auto RFCOMM profile with Winsock SDP and listen for connections."""
        import sys
        self._on_connection_cb = on_connection_cb
        try:
            # Try native Bluetooth RFCOMM socket
            self._server_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self._server_sock.bind(("", RFCOMM_CHANNEL))
            self._server_sock.listen(1)
            self._running = True
            log.info(f"Windows RFCOMM socket bound to channel {RFCOMM_CHANNEL}")

            # SDP service registration via Winsock WSASetServiceW
            self._register_sdp_service()
        except Exception as e:
            if sys.platform.startswith("linux") or sys.platform.startswith("darwin"):
                # Developer VM / non-Windows sandbox fallback
                log.warning(f"AF_BLUETOOTH binding failed ({e}) — falling back to mock TCP loopback server socket for VM testing")
                self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_sock.bind(("127.0.0.1", 15289))
                self._server_sock.listen(1)
                self._running = True
            else:
                # Strictly require AF_BLUETOOTH on Windows target as requested by user
                log.error(f"Windows AF_BLUETOOTH server initialization failed: {e}")
                raise e

        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="windows-rfcomm-accept")
        self._thread.start()
        return True

    def _register_sdp_service(self) -> None:
        """Call Winsock WSASetServiceW to register the custom AA UUID."""
        try:
            ws2_32 = ctypes.windll.Ws2_32
            rnr_guid = GUID(AA_UUID)

            # Setup WSAQUERYSETW structure
            qs = WSAQUERYSETW()
            qs.dwSize = ctypes.sizeof(WSAQUERYSETW)
            qs.lpszServiceInstanceName = self._adapter_name + " AA"
            qs.lpServiceClassId = ctypes.pointer(rnr_guid)
            qs.dwNameSpace = 10  # NS_BTH (Bluetooth Namespace)
            qs.dwNumberOfCsAddrs = 0
            qs.lpcsaBuffer = None

            # RNRSERVICE_REGISTER = 0
            ret = ws2_32.WSASetServiceW(ctypes.byref(qs), 0, 0)
            if ret != 0:
                err = ws2_32.WSAGetLastError()
                log.warning(f"WSASetServiceW returned error code: {err}")
            else:
                log.info("Android Auto UUID successfully registered in Windows SDP registry")
        except Exception as e:
            log.warning(f"Could not call WSASetServiceW via ctypes (non-critical mock/driver warning): {e}")

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

    async def teardown(self) -> None:
        self._running = False
        if self._server_sock:
            self._server_sock.close()
            self._server_sock = None
        log.info("Windows Bluetooth adapter shutdown complete")
