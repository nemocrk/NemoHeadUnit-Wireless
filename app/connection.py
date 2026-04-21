"""
Connection Manager for NemoHeadUnit-Wireless
Handles Bluetooth and WiFi AP protocols for smartphone connectivity.
"""

import threading
from typing import Optional
from app.base_interface import BaseInterface
from app.component_registry import ComponentRegistry
from app.message_bus import MessageBus
from app.logger import LoggerManager


class ConnectionManager(BaseInterface, ComponentRegistry):
    """
    Manages connection protocols for smartphone connectivity.
    Supports Bluetooth and WiFi AP protocols.
    """
    
    def __init__(self):
        self._bluetooth_connected = False
        self._wifi_ap_connected = False
        self._bluetooth_thread: Optional[threading.Thread] = None
        self._wifi_ap_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._bus: Optional[MessageBus] = None
        self._wireless = None
        self._logger = LoggerManager.get_logger('app.connection')
    
    def register(self, message_bus: MessageBus) -> bool:
        """Register this component with the message bus."""
        self._bus = message_bus
        self._logger.info("ConnectionManager registering with bus")
        
        # Subscribe to wireless initialization
        self._bus.subscribe('wireless.app.ready', self._on_wireless_ready)
        
        # Start the connection manager
        return self.on()
    
    @property
    def name(self) -> str:
        """Returns the name of the component."""
        return "connection_manager"
    
    @property
    def status(self) -> str:
        """Returns the current status of the component."""
        if self._bluetooth_connected and self._wifi_ap_connected:
            return "connected"
        elif self._bluetooth_connected:
            return "bluetooth_connected"
        elif self._wifi_ap_connected:
            return "wifi_ap_connected"
        else:
            return "disconnected"
    
    def on(self) -> bool:
        """Starts the connection manager."""
        self._logger.info("Connection manager starting")
        
        # Publish initialization event for wireless app
        self._bus.publish('connection.ready', 'connection_manager', {})
        
        return True
    
    def off(self) -> bool:
        """Stops the connection manager."""
        self._logger.info("Connection manager stopping")
        
        if self._wireless:
            self._wireless.stop()
        
        self._bus.publish('connection.stopped', 'connection_manager', {})
        return True
    
    def is_running(self) -> bool:
        """Checks if the connection manager is running."""
        return self._bluetooth_thread is not None and self._bluetooth_thread.is_alive()
    
    def get_status(self) -> dict:
        """Gets the detailed status of the connection manager."""
        return {
            "bluetooth": self._bluetooth_connected,
            "wifi_ap": self._wifi_ap_connected,
            "status": self.status
        }
    
    def reset(self) -> None:
        """Resets the connection manager to initial state."""
        self._bluetooth_connected = False
        self._wifi_ap_connected = False
        self._bluetooth_thread = None
        self._wifi_ap_thread = None
    
    def log(self, message: str, level: str = "info") -> None:
        """Logs a message at the specified level."""
        logger = LoggerManager.get_logger('app.connection')
        level_map = {
            "debug": lambda m: logger.debug(m),
            "info": lambda m: logger.info(m),
            "warning": lambda m: logger.warning(m),
            "error": lambda m: logger.error(m),
            "critical": lambda m: logger.critical(m)
        }
        log_func = level_map.get(level.lower(), level_map["info"])
        log_func(message)

    def _notify_status_change(self, status: str, method: str) -> None:
        """Publishes a status change to the message bus."""
        if self._bus:
            payload = {
                "status": status,
                "method": method
            }
            self._bus.publish("connection.status", self.name, payload)

    def _on_wireless_ready(self, payload: any) -> None:
        """Callback for when wireless app is ready."""
        self._logger.info(f"Wireless app ready: {payload}")
        if isinstance(payload, dict) and payload.get('wireless_app'):
            self._wireless = payload['wireless_app']
