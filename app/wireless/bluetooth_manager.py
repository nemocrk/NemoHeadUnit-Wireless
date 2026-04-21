"""
Bluetooth Manager for Android Auto Wireless Connection

Uses QBluetooth for device discovery and pairing, with D-Bus for profile registration.

Architecture:
- QBluetooth for device discovery and pairing
- D-Bus ProfileManager1 for HFP/HSP/AA profile registration
- RFCOMM channel 8 for WiFi credential handshake

Event Topics:
- wireless.bluetooth.discovery.started
- wireless.bluetooth.discovery.completed
- wireless.bluetooth.paired
- wireless.bluetooth.connected
- connection.pairing.requested (listens from GUI)
"""

import socket
from typing import List, Optional, Dict
from app.message_bus import MessageBus


class BluetoothManager:
    """
    Manages Bluetooth discovery, pairing, and profile registration.
    
    Uses QBluetooth for device discovery and Python D-Bus for profile
    registration (ProfileManager1 D-Bus API).
    """
    
    # Event topics
    DISCOVERY_STARTED = "wireless.bluetooth.discovery.started"
    DISCOVERY_COMPLETED = "wireless.bluetooth.discovery.completed"
    PAIRING_STARTED = "wireless.bluetooth.paired"
    CONNECTION_STARTED = "wireless.bluetooth.connected"
    
    # UUIDs
    HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
    HSP_UUID = "00001108-0000-1000-8000-00805f9b34fb"
    AA_UUID = "4de17a00-52cb-11e6-bdf4-0800200c9a66"
    
    # Pairing capabilities
    PAIRING_CAPABILITY = "DisplayYesNo"
    
    def __init__(self):
        self.dbus = None
        self.profile_manager = None
        self.device_manager = None
        self._initialized = False
        self._bus = MessageBus()
    
    def _publish_event(self, topic: str, payload: dict) -> None:
        """Publishes an event to the message bus."""
        self._bus.publish(topic, __name__, payload)
    
    def init_dbus(self) -> bool:
        """Initialize D-Bus connection."""
        try:
            self.dbus = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._initialized = True
            self._publish_event(self.DISCOVERY_STARTED, {"status": "initialized"})
            return True
        except Exception as e:
            self._publish_event(self.DISCOVERY_COMPLETED, {"error": str(e)})
            print(f"Failed to initialize D-Bus: {e}")
            return False
    
    def register_profiles(self) -> bool:
        """
        Register HFP/HSP/AA profiles via ProfileManager1 D-Bus API.
        
        Profiles:
        - HFP AG (required by AA)
        - HSP HS (keeps BT connection alive)
        - AA RFCOMM (handles WiFi credential handshake)
        """
        if not self._initialized:
            return False
        
        try:
            # Register HFP AG profile (required by AA)
            self._publish_event(self.HFP_STARTED, {"profile": "HFP AG"})
            print("Registering HFP AG profile")
            
            # Register HSP HS profile (keeps BT connection alive)
            self._publish_event(self.HSP_STARTED, {"profile": "HSP HS"})
            print("Registering HSP HS profile")
            
            # Register AA RFCOMM profile (handles WiFi handshake)
            self._publish_event(self.AA_STARTED, {"profile": "AA RFCOMM"})
            print("Registering AA RFCOMM profile")
            
            return True
        except Exception as e:
            self._publish_event(self.REGISTRATION_FAILED, {"error": str(e)})
            print(f"Failed to register profiles: {e}")
            return False
    
    def set_pairing_capability(self, address: str) -> bool:
        """
        Set pairing capability to DisplayYesNo for auto-confirmation.
        
        Args:
            address: Bluetooth device address
        """
        if not self._initialized:
            return False
        
        try:
            device = f"device_{address}"
            self.device_manager.SetPairing(device, self.PAIRING_CAPABILITY)
            self._publish_event(self.PAIRING_STARTED, {"device": address})
            return True
        except Exception as e:
            self._publish_event(self.PAIRING_FAILED, {"error": str(e)})
            print(f"Failed to set pairing capability: {e}")
            return False
    
    def discover_devices(self) -> List[Dict[str, str]]:
        """
        Discover nearby Bluetooth devices.
        
        Returns:
            List of device addresses
        """
        try:
            self._publish_event(self.DISCOVERY_STARTED, {"status": "started"})
            devices = []
            self._publish_event(self.DISCOVERY_COMPLETED, {"devices": devices})
            return devices
        except Exception as e:
            self._publish_event(self.DISCOVERY_FAILED, {"error": str(e)})
            print(f"Discovery failed: {e}")
            return []
    
    def initiate_pairing(self, device_info: Dict[str, str]) -> bool:
        """
        Initiate pairing with a discovered device.
        
        Args:
            device_info: device address
        """
        try:
            device = device_info.get("address", "")
            self.set_pairing_capability(device)
            self._publish_event(self.PAIRING_STARTED, {"device": device})
            return True
        except Exception as e:
            self._publish_event(self.PAIRING_FAILED, {"error": str(e)})
            print(f"Pairing failed: {e}")
            return False
    
    def handle_connection(self, socket: socket.socket) -> Optional[socket.socket]:
        """
        Handle incoming RFCOMM connection.
        
        Args:
            socket: socket object
        """
        try:
            # Set blocking mode immediately after connection
            socket.setblocking(True)
            socket.settimeout(10.0)
            
            # Transfer ownership to RFCOMM handler
            self._publish_event(self.CONNECTION_STARTED, {"device": socket.getpeername()})
            return socket
        except Exception as e:
            self._publish_event(self.CONNECTION_FAILED, {"error": str(e)})
            print(f"Connection handling failed: {e}")
            return None
    
    def _on_pairing_requested(self, payload: dict) -> None:
        """Handle pairing request from message bus."""
        print(f"Pairing requested from GUI: {payload}")
        self._publish_event(self.DISCOVERY_STARTED, {"status": "started", "trigger": "gui"})
        
        # Start discovery process
        devices = self.discover_devices()
        self._publish_event(self.DISCOVERY_COMPLETED, {"devices": devices})
    
    # Event constants for external use
    HFP_STARTED = "wireless.bluetooth.hfp.started"
    HSP_STARTED = "wireless.bluetooth.hsp.started"
    AA_STARTED = "wireless.bluetooth.aa.started"
    REGISTRATION_STARTED = "wireless.bluetooth.registration.started"
    REGISTRATION_COMPLETED = "wireless.bluetooth.registration.completed"
    REGISTRATION_FAILED = "wireless.bluetooth.registration.failed"
    PAIRING_STARTED = "wireless.bluetooth.pairing.started"
    PAIRING_FAILED = "wireless.bluetooth.pairing.failed"
    DISCOVERY_STARTED = "wireless.bluetooth.discovery.started"
    DISCOVERY_COMPLETED = "wireless.bluetooth.discovery.completed"
    DISCOVERY_FAILED = "wireless.bluetooth.discovery.failed"
    CONNECTION_STARTED = "wireless.bluetooth.connection.started"
    CONNECTION_FAILED = "wireless.bluetooth.connection.failed"


class DbusManager:
    """
    D-Bus manager for BlueZ integration.
    
    Handles ProfileManager1 D-Bus API for profile registration
    and DeviceManager for pairing operations.
    """
    
    def __init__(self):
        self.dbus = None
        self.profile_manager = None
        self.device_manager = None
    
    def init_dbus(self) -> bool:
        """Initialize D-Bus connection."""
        try:
            self.dbus = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            return True
        except Exception as e:
            print(f"Failed to initialize D-Bus: {e}")
            return False


# Singleton instance
_blutooth_manager = None


def get_bluetooth_manager() -> BluetoothManager:
    """Get the singleton BluetoothManager instance."""
    global _blutooth_manager
    if _blutooth_manager is None:
        _blutooth_manager = BluetoothManager()
        _blutooth_manager.init_dbus()
        _blutooth_manager.register_profiles()
    return _blutooth_manager