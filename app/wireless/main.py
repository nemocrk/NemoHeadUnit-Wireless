"""
Main Application for Android Auto Wireless Connection

Integrates Bluetooth discovery, D-Bus profile registration,
RFCOMM handshake, and TCP server for protocol session.

Architecture:
- Bluetooth Manager (device discovery, pairing)
- D-Bus Manager (ProfileManager1)
- RFCOMM Handler (5-stage handshake)
- TCP Server (Port 5288)

Event Topics:
- wireless.bluetooth.*
- wireless.rfcomm.*
- wireless.tcp.*
- connection.pairing.requested
"""

import signal
import threading
import socket
from typing import Optional
from app.component_registry import ComponentRegistry
from app.wireless.bluetooth_manager import BluetoothManager
from app.wireless.rfcomm_handler import RfcommHandler
from app.wireless.tcp_server import TcpServer
from app.logger import LoggerManager
from app.message_bus import MessageBus


class WirelessApp(ComponentRegistry):
    """
    Main application for wireless Android Auto connection.
    
    Integrates Bluetooth discovery, D-Bus for profile registration,
    RFCOMM for handshake, and TCP server for protocol session.
    """
    
    def __init__(self):
        self.bluetooth = BluetoothManager()
        self.tcp_server = TcpServer()
        self._running = False
        self._thread = None
        self._logger = LoggerManager.get_logger('app.wireless.main')
        self._bus: Optional[MessageBus] = None
    
    def register(self, message_bus: MessageBus) -> bool:
        """Register wireless app with the message bus."""
        self._bus = message_bus
        self._logger.info("WirelessApp registering with bus")
        
        # Subscribe to connection readiness
        self._bus.subscribe('connection.ready', self._on_connection_ready)
        
        return True
    
    def name(self) -> str:
        """Get component name."""
        return "wireless_app"
    
    def _publish_event(self, topic: str, payload: dict) -> None:
        """Publishes an event to the message bus."""
        if self._bus:
            self._bus.publish(topic, self.name(), payload)
    
    def start(self) -> bool:
        """Start the wireless application."""
        self._logger.info("Wireless app starting")
        
        # Initialize Bluetooth
        if not self.bluetooth.init_dbus():
            self._logger.error("Failed to initialize Bluetooth")
            self._publish_event("wireless.app.startup.failed", {"component": "bluetooth"})
            return False
        
        # Register profiles
        if not self.bluetooth.register_profiles():
            self._publish_event("wireless.app.startup.failed", {"component": "profiles"})
            self._logger.error("Failed to register profiles")
            return False
        
        # Set pairing capability
        self.bluetooth.set_pairing_capability("00:00:00:00:00:00")
        
        # Start TCP server
        if not self.tcp_server.start():
            self._publish_event("wireless.app.startup.failed", {"component": "tcp_server"})
            self._logger.error("Failed to start TCP server")
            return False
        
        self._running = True
        self.subscribe_to_events()
        self.subscribe_pairing_request()
        
        # Publish ready event
        self._publish_event("wireless.app.ready", {"wireless_app": self})
        
        return True
    
    def stop(self) -> None:
        """Stop the wireless application."""
        self._running = False
        self.tcp_server.stop()
        self._bus.stop()
    
    def run(self) -> None:
        """Run the main loop."""
        if not self._running:
            return
        
        while self._running:
            # Accept connections
            result = self.tcp_server.accept_connection(timeout=30)
            if result:
                client, addr = result
                self._logger.info(f"Connection from {addr}")
                # Handle connection
                self._handle_connection(client)
    
    def _handle_connection(self, client: socket.socket) -> None:
        """Handle incoming connection."""
        try:
            # Process RFCOMM handshake
            handshake = RfcommHandler(client)
            if handshake.process_handshake():
                self._logger.info("Handshake successful")
                # Start protocol session
                client.setblocking(False)
                self._publish_event("wireless.connection.established", {
                    "address": client.getpeername()
                })
                self._logger.info(f"Protocol session started with {client.getpeername()}")
        except Exception as e:
            self._logger.error(f"Connection handling failed: {e}")
            self._publish_event("wireless.connection.failed", {"error": str(e)})
    
    def subscribe_to_events(self) -> None:
        """Subscribe to wireless events."""
        self._bus.on("wireless.bluetooth.discovery.completed", self._on_discovery_completed)
        self._bus.on("wireless.rfcomm.handshake.completed", self._on_handshake_completed)
        self._bus.on("wireless.tcp.connection.accepted", self._on_connection_accepted)
    
    def _on_discovery_completed(self, payload: dict) -> None:
        """Handle Bluetooth discovery completion."""
        self._logger.info("Bluetooth discovery completed")
    
    def _on_handshake_completed(self, payload: dict) -> None:
        """Handle RFCOMM handshake completion."""
        self._logger.info("RFCOMM handshake completed")
    
    def _on_connection_accepted(self, payload: dict) -> None:
        """Handle TCP connection accepted."""
        self._logger.info(f"Connection accepted from {payload.get('address', 'unknown')}")
    
    def subscribe_pairing_request(self) -> None:
        """Subscribe to pairing requests from GUI."""
        self._bus.on("connection.pairing.requested", self._on_pairing_requested)
    
    def _on_pairing_requested(self, payload: dict) -> None:
        """Handle pairing request from GUI."""
        self._logger.info(f"Pairing requested from GUI: {payload}")
        
        # Trigger Bluetooth discovery
        self.bluetooth.discover_devices()
    
    def _on_connection_ready(self, payload: dict) -> None:
        """Handle connection manager ready event."""
        self._logger.info("Connection manager is ready")
        self.start()