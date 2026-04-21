"""
Wireless Android Auto Connection Module

This module provides wireless connection management for Android Auto:
- Bluetooth discovery and pairing
- WiFi AP credential exchange
- RFCOMM handshake protocol
- TCP server on port 5288

Architecture:
- Bluetooth Manager (device discovery, pairing)
- RFCOMM Handler (5-stage handshake)
- TCP Server (Port 5288)

Event Topics:
- wireless.bluetooth.discovery.started
- wireless.bluetooth.discovery.completed
- wireless.bluetooth.paired
- wireless.bluetooth.connected
- wireless.rfcomm.handshake.started
- wireless.rfcomm.handshake.completed
- wireless.rfcomm.credentials.exchanged
- wireless.tcp.server.started
- wireless.tcp.connection.accepted
- wireless.tcp.connection.closed
"""

from app.message_bus import MessageBus
from .bluetooth_manager import BluetoothManager
from .rfcomm_handler import RfcommHandler
from .tcp_server import TcpServer
from .wireless_service import WirelessService

__all__ = [
    'MessageBus',
    'BluetoothManager',
    'RfcommHandler',
    'TcpServer',
    'WirelessService',
]