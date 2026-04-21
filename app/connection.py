"""
Connection Manager for NemoHeadUnit-Wireless
Handles Bluetooth and WiFi AP protocols for smartphone connectivity.

Communicates exclusively via bus topics — no Python object references
in payloads (incompatible with IPC serialization).
"""

from typing import Optional
from app.base_interface import BaseInterface
from app.component_registry import ComponentRegistry
from app.bus_client import BusClient
from app.logger import LoggerManager


class ConnectionManager(BaseInterface, ComponentRegistry):
    """
    Manages connection protocols for smartphone connectivity.
    Supports Bluetooth and WiFi AP protocols.
    """

    def __init__(self):
        self._bluetooth_connected = False
        self._wifi_ap_connected = False
        self._lock_import = None
        self._bus: Optional[BusClient] = None
        self._logger = LoggerManager.get_logger('app.connection')

    def register(self, message_bus: BusClient) -> bool:
        """Register this component with the message bus."""
        self._bus = message_bus
        self._logger.info("ConnectionManager registering with bus")

        # wireless_daemon publishes this when ready — no object in payload
        self._bus.subscribe('wireless.app.ready', self._on_wireless_ready)
        self._bus.subscribe('wireless.connection.established', self._on_connection_established)
        self._bus.subscribe('wireless.connection.failed', self._on_connection_failed)

        return self.on()

    @property
    def name(self) -> str:
        return "connection_manager"

    @property
    def status(self) -> str:
        if self._bluetooth_connected and self._wifi_ap_connected:
            return "connected"
        elif self._bluetooth_connected:
            return "bluetooth_connected"
        elif self._wifi_ap_connected:
            return "wifi_ap_connected"
        return "disconnected"

    def on(self) -> bool:
        """Start the connection manager."""
        self._logger.info("Connection manager starting")
        self._bus.publish('connection.ready', self.name, {})
        return True

    def off(self) -> bool:
        """Stop the connection manager."""
        self._logger.info("Connection manager stopping")
        self._bus.publish('connection.stopped', self.name, {})
        return True

    def is_running(self) -> bool:
        return self._bluetooth_connected or self._wifi_ap_connected

    def get_status(self) -> dict:
        return {
            "bluetooth": self._bluetooth_connected,
            "wifi_ap": self._wifi_ap_connected,
            "status": self.status,
        }

    def reset(self) -> None:
        self._bluetooth_connected = False
        self._wifi_ap_connected = False

    def log(self, message: str, level: str = "info") -> None:
        logger = LoggerManager.get_logger('app.connection')
        getattr(logger, level.lower(), logger.info)(message)

    def _notify_status_change(self, status: str, method: str) -> None:
        """Publish a connection status change to the bus."""
        if self._bus:
            self._bus.publish("connection.status", self.name, {
                "status": status,
                "method": method,
            })

    # ------------------------------------------------------------------
    # Bus event handlers
    # ------------------------------------------------------------------

    def _on_wireless_ready(self, payload: dict) -> None:
        """
        Wireless daemon is up and ready.
        Payload contains only serializable metadata — no object references.
        """
        self._logger.info(f"Wireless service ready: {payload}")
        self._notify_status_change("wireless_ready", payload.get("method", "unknown"))

    def _on_connection_established(self, payload: dict) -> None:
        """Handle successful connection from wireless daemon."""
        method = payload.get("method", "bluetooth")
        self._logger.info(f"Connection established via {method}")
        if method == "bluetooth":
            self._bluetooth_connected = True
        elif method == "wifi_ap":
            self._wifi_ap_connected = True
        self._notify_status_change("Connected", method)

    def _on_connection_failed(self, payload: dict) -> None:
        """Handle connection failure from wireless daemon."""
        self._logger.warning(f"Connection failed: {payload.get('error', 'unknown')}")
        self._notify_status_change("Disconnected", payload.get("method", "unknown"))
