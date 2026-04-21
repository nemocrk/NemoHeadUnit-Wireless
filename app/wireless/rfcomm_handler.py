"""
RFCOMM Handler for Android Auto Wireless Connection

Handles the 5-stage RFCOMM handshake protocol over channel 8:
1. WifiStartRequest (msgId=1) -> IP + port
2. WifiStartResponse (msgId=7) -> Status
3. WifiInfoRequest (msgId=2) -> Empty or cached info
4. WifiInfoResponse (msgId=3) -> SSID, key, BSSID, security, AP type
5. WifiConnectStatus (msgId=6) -> Status

Packet format: [length:u16_be][msg_id:u16_be][protobuf_payload]

Event Topics:
- wireless.rfcomm.handshake.started
- wireless.rfcomm.handshake.completed
- wireless.rfcomm.credentials.exchanged
"""

import struct
from typing import Optional
from app.message_bus import MessageBus


class RfcommHandler:
    """
    RFCOMM handshake handler for WiFi credential exchange.
    
    Packet format: [length:u16_be][msg_id:u16_be][protobuf_payload]
    """
    
    # Event topics
    HANDSHAKE_STARTED = "wireless.rfcomm.handshake.started"
    HANDSHAKE_COMPLETED = "wireless.rfcomm.handshake.completed"
    CREDENTIALS_EXCHANGED = "wireless.rfcomm.credentials.exchanged"
    
    # Message IDs
    WIFI_START_REQUEST = 1
    WIFI_START_RESPONSE = 7
    WIFI_INFO_REQUEST = 2
    WIFI_INFO_RESPONSE = 3
    WIFI_CONNECT_STATUS = 6
    
    # Security modes
    WPA2_PERSONAL = 8
    
    # Access point types
    DYNAMIC = 1
    
    def __init__(self, socket=None):
        self.socket = socket
        self._state = 'idle'
        self._bus = MessageBus()
    
    def _publish_event(self, topic: str, payload: dict) -> None:
        """Publishes an event to the message bus."""
        self._bus.publish(topic, __name__, payload)
    
    def send_wifi_start_request(self, ip_address: str, port: int) -> bool:
        """
        Stage 1: Send WifiStartRequest with IP and port.
        
        Args:
            ip_address: TCP server IP address (e.g., "10.0.0.1")
            port: TCP server port (e.g., 5288)
        """
        if self.socket is None:
            return False
        
        try:
            # Packet format: [length:u16_be][msg_id:u16_be][payload]
            packet = struct.pack(">H", 1) + struct.pack(">H", self.WIFI_START_REQUEST)
            # Payload: IP address + port
            packet += struct.pack(">H", 0x08000000)  # field tag 0x08
            packet += struct.pack(">H", 0x0a)  # field tag 0x0a
            packet += ip_address.encode()
            packet += struct.pack(">H", port)
            
            self.socket.send(packet)
            self._publish_event(self.HANDSHAKE_STARTED, {"stage": "WifiStartRequest"})
            return True
        except Exception as e:
            print(f"Failed to send WifiStartRequest: {e}")
            return False
    
    def receive_wifi_start_response(self) -> int:
        """
        Stage 2: Receive WifiStartResponse.
        
        Returns:
            Status (0 = success)
        """
        if self.socket is None:
            return -1
        
        try:
            response = self.socket.recv(1024)
            if response:
                self._publish_event(self.HANDSHAKE_COMPLETED, {"stage": "WifiStartResponse", "status": 0})
                return 0  # SUCCESS
            return -1  # FAILURE
        except Exception as e:
            print(f"Failed to receive WifiStartResponse: {e}")
            return -1
    
    def send_wifi_info_response(self, ssid: str, key: str, bssid: str, security_mode: int, ap_type: int) -> bool:
        """
        Stage 3: Send WifiInfoResponse with WiFi credentials.
        
        Args:
            ssid: WiFi network name
            key: WiFi password
            bssid: MAC address (colon-separated uppercase)
            security_mode: WPA2_PERSONAL = 8
            ap_type: DYNAMIC = 1
        """
        if self.socket is None:
            return False
        
        try:
            # Packet format: [length:u16_be][msg_id:u16_be][payload]
            packet = struct.pack(">H", 3) + struct.pack(">H", self.WIFI_INFO_RESPONSE)
            
            # Payload: SSID, key, BSSID, security, AP type
            packet += ssid.encode()
            packet += key.encode()
            packet += bssid
            packet += struct.pack(">H", security_mode)
            packet += struct.pack(">H", ap_type)
            
            self.socket.send(packet)
            self._publish_event(self.CREDENTIALS_EXCHANGED, {
                "ssid": ssid,
                "security": security_mode,
                "ap_type": ap_type
            })
            return True
        except Exception as e:
            print(f"Failed to send WifiInfoResponse: {e}")
            return False
    
    def receive_wifi_connect_status(self) -> int:
        """
        Stage 5: Receive WifiConnectStatus.
        
        Returns:
            Status (0 = success, phone joined WiFi)
        """
        if self.socket is None:
            return -1
        
        try:
            response = self.socket.recv(1024)
            if response:
                self._publish_event(self.HANDSHAKE_COMPLETED, {"stage": "WifiConnectStatus", "status": 0})
                return 0  # SUCCESS
            return -1  # FAILURE
        except Exception as e:
            print(f"Failed to receive WifiConnectStatus: {e}")
            return -1
    
    def process_handshake(self) -> bool:
        """
        Process complete 5-stage handshake.
        
        Returns:
            True if handshake successful
        """
        if self.socket is None:
            return False
        
        try:
            # Stage 1: Send WifiStartRequest
            if not self.send_wifi_start_request("10.0.0.1", 5288):
                return False
            
            # Stage 2: Receive WifiStartResponse
            if self.receive_wifi_start_response() < 0:
                return False
            
            # Stage 3: Send WifiInfoResponse
            if not self.send_wifi_info_response(
                "Android Auto",
                "password123",
                "DC:A6:32:E7:5A:FE",
                self.WPA2_PERSONAL,
                self.DYNAMIC
            ):
                return False
            
            # Stage 4: Phone connects to WiFi
            # Stage 5: Receive WifiConnectStatus
            if self.receive_wifi_connect_status() < 0:
                return False
            
            self._publish_event(self.HANDSHAKE_COMPLETED, {"status": "success"})
            return True
        except Exception as e:
            self._publish_event(self.HANDSHAKE_FAILED, {"error": str(e)})
            print(f"Handshake failed: {e}")
            return False
    
    # Event constants for external use
    HANDSHAKE_STARTED = "wireless.rfcomm.handshake.started"
    HANDSHAKE_COMPLETED = "wireless.rfcomm.handshake.completed"
    HANDSHAKE_FAILED = "wireless.rfcomm.handshake.failed"
    CREDENTIALS_EXCHANGED = "wireless.rfcomm.credentials.exchanged"


# Singleton instance
_rfcomm_handler = None


def get_rfcomm_handler() -> RfcommHandler:
    """Get the singleton RfcommHandler instance."""
    global _rfcomm_handler
    if _rfcomm_handler is None:
        _rfcomm_handler = RfcommHandler(None)
    return _rfcomm_handler