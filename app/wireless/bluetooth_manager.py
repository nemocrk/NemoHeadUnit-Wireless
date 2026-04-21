"""
Bluetooth Manager for Android Auto Wireless Connection

Uses D-Bus ProfileManager1 for HFP/HSP/AA profile registration
and RFCOMM channel 8 for WiFi credential handshake.

Design:
    BluetoothManager is a plain Python class with NO autonomous bus
    connection. The bus is injected via set_bus() by the caller
    (WirelessDaemon or WirelessService) after the broker is ready.
    This avoids a race condition where MessageBus() in __init__
    attempts to connect to ZMQ before bus_broker has started.

Event Topics published:
    wireless.bluetooth.discovery.started
    wireless.bluetooth.discovery.completed
    wireless.bluetooth.discovery.failed
    wireless.bluetooth.hfp.started
    wireless.bluetooth.hsp.started
    wireless.bluetooth.aa.started
    wireless.bluetooth.registration.completed
    wireless.bluetooth.registration.failed
    wireless.bluetooth.pairing.started
    wireless.bluetooth.pairing.failed
    wireless.bluetooth.connection.started
    wireless.bluetooth.connection.failed
"""

import socket
from typing import List, Optional, Dict, Any
from app.logger import LoggerManager

log = LoggerManager.get_logger('app.wireless.bluetooth_manager')


class BluetoothManager:
    """
    Manages Bluetooth discovery, pairing, and profile registration.

    Bus dependency is injected via set_bus() — never created internally.
    """

    # ------------------------------------------------------------------
    # Event topic constants
    # ------------------------------------------------------------------
    DISCOVERY_STARTED        = "wireless.bluetooth.discovery.started"
    DISCOVERY_COMPLETED      = "wireless.bluetooth.discovery.completed"
    DISCOVERY_FAILED         = "wireless.bluetooth.discovery.failed"
    HFP_STARTED              = "wireless.bluetooth.hfp.started"
    HSP_STARTED              = "wireless.bluetooth.hsp.started"
    AA_STARTED               = "wireless.bluetooth.aa.started"
    REGISTRATION_COMPLETED   = "wireless.bluetooth.registration.completed"
    REGISTRATION_FAILED      = "wireless.bluetooth.registration.failed"
    PAIRING_STARTED          = "wireless.bluetooth.pairing.started"
    PAIRING_FAILED           = "wireless.bluetooth.pairing.failed"
    CONNECTION_STARTED       = "wireless.bluetooth.connection.started"
    CONNECTION_FAILED        = "wireless.bluetooth.connection.failed"

    # UUIDs
    HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
    HSP_UUID = "00001108-0000-1000-8000-00805f9b34fb"
    AA_UUID  = "4de17a00-52cb-11e6-bdf4-0800200c9a66"

    PAIRING_CAPABILITY = "DisplayYesNo"

    def __init__(self):
        # NO BusClient / MessageBus here — injected later via set_bus()
        self._bus = None
        self._sender = "bluetooth_manager"
        self._initialized = False
        self.dbus = None
        self.profile_manager = None
        self.device_manager = None

    # ------------------------------------------------------------------
    # Bus injection
    # ------------------------------------------------------------------

    def set_bus(self, bus: Any) -> None:
        """Inject the shared BusClient. Call this after the broker is ready."""
        self._bus = bus

    def _publish(self, topic: str, payload: dict) -> None:
        if self._bus:
            self._bus.publish(topic, self._sender, payload)
        else:
            log.debug(f"[no bus] {topic} {payload}")

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def init_dbus(self) -> bool:
        """Initialize D-Bus connection (stub — replace with real BlueZ D-Bus)."""
        try:
            # TODO: replace with actual dbus.SystemBus() + BlueZ proxy
            self.dbus = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._initialized = True
            self._publish(self.DISCOVERY_STARTED, {"status": "initialized"})
            log.info("D-Bus initialized (stub)")
            return True
        except Exception as e:
            log.error(f"Failed to initialize D-Bus: {e}")
            self._publish(self.DISCOVERY_FAILED, {"error": str(e)})
            return False

    def register_profiles(self) -> bool:
        """
        Register HFP AG, HSP HS and AA RFCOMM profiles via
        ProfileManager1 D-Bus API (stub).
        """
        if not self._initialized:
            log.warning("register_profiles called before init_dbus")
            return False
        try:
            self._publish(self.HFP_STARTED,  {"profile": "HFP AG"})
            log.info("Registering HFP AG profile (stub)")

            self._publish(self.HSP_STARTED,  {"profile": "HSP HS"})
            log.info("Registering HSP HS profile (stub)")

            self._publish(self.AA_STARTED,   {"profile": "AA RFCOMM"})
            log.info("Registering AA RFCOMM profile (stub)")

            self._publish(self.REGISTRATION_COMPLETED, {"status": "ok"})
            return True
        except Exception as e:
            log.error(f"Failed to register profiles: {e}")
            self._publish(self.REGISTRATION_FAILED, {"error": str(e)})
            return False

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def set_pairing_capability(self, address: str) -> bool:
        if not self._initialized:
            return False
        try:
            # TODO: real BlueZ SetPairing call
            self._publish(self.PAIRING_STARTED, {"device": address})
            log.info(f"Pairing capability set for {address} (stub)")
            return True
        except Exception as e:
            log.error(f"Failed to set pairing capability: {e}")
            self._publish(self.PAIRING_FAILED, {"error": str(e)})
            return False

    def discover_devices(self) -> List[Dict[str, str]]:
        """Start Bluetooth device discovery (stub)."""
        try:
            self._publish(self.DISCOVERY_STARTED, {"status": "started"})
            devices: List[Dict[str, str]] = []
            # TODO: real BlueZ StartDiscovery
            self._publish(self.DISCOVERY_COMPLETED, {"devices": devices})
            return devices
        except Exception as e:
            log.error(f"Discovery failed: {e}")
            self._publish(self.DISCOVERY_FAILED, {"error": str(e)})
            return []

    def initiate_pairing(self, device_info: Dict[str, str]) -> bool:
        """Initiate pairing with a discovered device."""
        try:
            device = device_info.get("address", "")
            self.set_pairing_capability(device)
            self._publish(self.PAIRING_STARTED, {"device": device})
            return True
        except Exception as e:
            log.error(f"Pairing failed: {e}")
            self._publish(self.PAIRING_FAILED, {"error": str(e)})
            return False

    def handle_connection(self, conn: socket.socket) -> Optional[socket.socket]:
        """Accept an RFCOMM socket and transfer ownership to RfcommHandler."""
        try:
            conn.setblocking(True)
            conn.settimeout(10.0)
            addr = conn.getpeername()
            self._publish(self.CONNECTION_STARTED, {"device": str(addr)})
            return conn
        except Exception as e:
            log.error(f"Connection handling failed: {e}")
            self._publish(self.CONNECTION_FAILED, {"error": str(e)})
            return None


# ---------------------------------------------------------------------------
# Singleton accessor (optional — callers may instantiate directly)
# ---------------------------------------------------------------------------

_instance: Optional[BluetoothManager] = None


def get_bluetooth_manager() -> BluetoothManager:
    """Return the singleton BluetoothManager (bus NOT pre-connected)."""
    global _instance
    if _instance is None:
        _instance = BluetoothManager()
    return _instance
