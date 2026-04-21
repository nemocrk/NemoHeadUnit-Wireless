"""
TCP Server for Android Auto Wireless Connection

Listens on port 5288 for phone connections after WiFi credential exchange.

Architecture:
- TCP listener on port 5288
- SSL/TLS wrapper for encryption
- Protocol session handling

Event Topics:
- wireless.tcp.server.started
- wireless.tcp.connection.accepted
- wireless.tcp.connection.closed
"""

import socket
from typing import Optional
from app.message_bus import MessageBus


class TcpServer:
    """
    TCP server for Android Auto protocol session.
    
    Listens on port 5288 for phone connections after
    WiFi credential exchange.
    """
    
    # Event topics
    SERVER_STARTED = "wireless.tcp.server.started"
    CONNECTION_ACCEPTED = "wireless.tcp.connection.accepted"
    CONNECTION_CLOSED = "wireless.tcp.connection.closed"
    
    def __init__(self, host: str = "0.0.0.0", port: int = 5288):
        self.host = host
        self.port = port
        self.server = None
        self._running = False
        self._bus = MessageBus()
    
    def _publish_event(self, topic: str, payload: dict) -> None:
        """Publishes an event to the message bus."""
        self._bus.publish(topic, __name__, payload)
    
    def start(self) -> bool:
        """Start the TCP server."""
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((self.host, self.port))
            self.server.listen(1)
            self._running = True
            self._publish_event(self.SERVER_STARTED, {"host": self.host, "port": self.port})
            return True
        except Exception as e:
            print(f"Failed to start TCP server: {e}")
            return False
    
    def stop(self) -> None:
        """Stop the TCP server."""
        if self.server:
            self.server.close()
            self._publish_event(self.CONNECTION_CLOSED, {"status": "stopped"})
            self._running = False
    
    def accept_connection(self, timeout: int = 30) -> Optional[tuple]:
        """
        Accept incoming connection.
        
        Returns:
            Tuple of (client_address, socket) if successful, None if timeout
        """
        if not self._running:
            return None
        
        try:
            client, addr = self.server.accept(timeout)
            self._publish_event(self.CONNECTION_ACCEPTED, {
                "address": addr,
                "port": self.port
            })
            return (client, addr)
        except socket.timeout:
            return None
        except Exception as e:
            print(f"Connection failed: {e}")
            return None
    
    def send_message(self, client: socket.socket, data: bytes) -> bool:
        """
        Send message to client.
        
        Args:
            client: socket object
            data: message data
        """
        try:
            client.sendall(data)
            return True
        except Exception as e:
            print(f"Send failed: {e}")
            return False
    
    def close(self) -> None:
        """Close the server."""
        if self.server:
            self.server.close()
            self._running = False
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_value, exc_tb):
        """Context manager exit."""
        self.stop()
    
    # Event constants for external use
    SERVER_STARTED = "wireless.tcp.server.started"
    CONNECTION_ACCEPTED = "wireless.tcp.connection.accepted"
    CONNECTION_CLOSED = "wireless.tcp.connection.closed"


# Singleton instance
_tcp_server = None


def get_tcp_server() -> TcpServer:
    """Get the singleton TcpServer instance."""
    global _tcp_server
    if _tcp_server is None:
        _tcp_server = TcpServer()
    return _tcp_server