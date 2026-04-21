"""
NemoHeadUnit-Wireless — Wireless Daemon
Standalone process for Bluetooth, RFCOMM handshake, TCP server
and media frame publishing.

Run independently:
    python services/wireless_daemon.py

Bus topics published:
    wireless.app.ready              {status, method}
    wireless.app.startup.failed     {component}
    wireless.connection.established {address, method}
    wireless.connection.failed      {error, method}
    media.audio.frame               header={seq, pts}, binary=<AAC bytes>
    media.video.frame               header={seq, pts, keyframe}, binary=<H.264 bytes>

Bus topics subscribed:
    connection.ready
    connection.pairing.requested
    wireless.bluetooth.discovery.completed
    wireless.rfcomm.handshake.completed
    wireless.tcp.connection.accepted
"""

import sys
import os
import signal
import socket
import threading

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bus_client import BusClient
from app.wireless.bluetooth_manager import BluetoothManager
from app.wireless.rfcomm_handler import RfcommHandler
from app.wireless.tcp_server import TcpServer
from app.logger import LoggerManager


class WirelessDaemon:
    """
    Wireless service running as an independent process.
    Connects to the ZMQ broker as a BusClient.
    Does NOT call bus.stop() on shutdown — the broker is managed separately.
    """

    def __init__(self):
        self._logger = LoggerManager.get_logger('services.wireless_daemon')
        self._bus = BusClient(name="wireless_daemon")
        self._bluetooth = BluetoothManager()
        self._tcp_server = TcpServer()
        self._running = False
        self._seq_audio = 0
        self._seq_video = 0

    def _publish(self, topic: str, payload: dict, binary: bytes = b"") -> None:
        self._bus.publish(topic, "wireless_daemon", payload, binary=binary)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Initialize hardware and start listening."""
        self._logger.info("Wireless daemon starting")

        if not self._bluetooth.init_dbus():
            self._logger.error("Failed to initialize Bluetooth")
            self._publish("wireless.app.startup.failed", {"component": "bluetooth"})
            return False

        if not self._bluetooth.register_profiles():
            self._logger.error("Failed to register BT profiles")
            self._publish("wireless.app.startup.failed", {"component": "profiles"})
            return False

        self._bluetooth.set_pairing_capability("00:00:00:00:00:00")

        if not self._tcp_server.start():
            self._logger.error("Failed to start TCP server")
            self._publish("wireless.app.startup.failed", {"component": "tcp_server"})
            return False

        self._running = True
        self._subscribe()

        # Publish ready — serializable payload only, no object references
        self._publish("wireless.app.ready", {"status": "ready", "method": "bluetooth"})
        self._logger.info("Wireless daemon ready")
        return True

    def stop(self) -> None:
        """Graceful shutdown — does NOT stop the broker."""
        self._logger.info("Wireless daemon stopping")
        self._running = False
        self._tcp_server.stop()
        self._bus.stop()  # disconnects this client only

    def run(self) -> None:
        """Main accept loop — blocks until stopped."""
        if not self._running:
            return
        self._logger.info("Wireless daemon accepting connections")
        while self._running:
            result = self._tcp_server.accept_connection(timeout=30)
            if result:
                client, addr = result
                self._logger.info(f"Connection from {addr}")
                t = threading.Thread(
                    target=self._handle_connection,
                    args=(client,),
                    daemon=True,
                )
                t.start()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    def _handle_connection(self, client: socket.socket) -> None:
        try:
            handshake = RfcommHandler(client)
            if handshake.process_handshake():
                self._logger.info("Handshake successful")
                client.setblocking(False)
                addr = client.getpeername()
                self._publish("wireless.connection.established", {
                    "address": str(addr),
                    "method": "bluetooth",
                })
                self._media_loop(client)
            else:
                self._publish("wireless.connection.failed", {
                    "error": "handshake failed",
                    "method": "bluetooth",
                })
        except Exception as e:
            self._logger.error(f"Connection handling failed: {e}")
            self._publish("wireless.connection.failed", {
                "error": str(e),
                "method": "bluetooth",
            })

    def _media_loop(self, client: socket.socket) -> None:
        """
        Read AAC/H.264 frames from the smartphone and publish them
        on the bus as binary payloads (zero-copy).

        Frame wire format (simplified Android Auto / CarPlay-like):
            [4 bytes] frame_type  0x01=audio_AAC  0x02=video_H264
            [4 bytes] payload_len
            [N bytes] payload
        """
        import struct
        self._logger.info("Media loop started")
        try:
            while self._running:
                header_raw = self._recv_exact(client, 8)
                if not header_raw:
                    break
                frame_type, payload_len = struct.unpack("!II", header_raw)
                data = self._recv_exact(client, payload_len)
                if not data:
                    break

                if frame_type == 0x01:  # AAC audio
                    self._seq_audio += 1
                    self._bus.publish(
                        "media.audio.frame",
                        "wireless_daemon",
                        {"seq": self._seq_audio, "pts": 0},
                        binary=data,
                    )
                elif frame_type == 0x02:  # H.264 video
                    self._seq_video += 1
                    # IDR keyframe detection: H.264 NAL unit type 5
                    is_idr = len(data) > 4 and (data[4] & 0x1F) == 5
                    self._bus.publish(
                        "media.video.frame",
                        "wireless_daemon",
                        {"seq": self._seq_video, "pts": 0, "keyframe": is_idr},
                        binary=data,
                    )
        except Exception as e:
            self._logger.error(f"Media loop error: {e}")
        finally:
            client.close()
            self._logger.info("Media loop ended")

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        """Read exactly n bytes from socket, return None on disconnect."""
        buf = b""
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except Exception:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    # ------------------------------------------------------------------
    # Bus subscriptions
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        self._bus.on("connection.ready",                          self._on_connection_ready)
        self._bus.on("connection.pairing.requested",              self._on_pairing_requested)
        self._bus.on("wireless.bluetooth.discovery.completed",    self._on_discovery_completed)
        self._bus.on("wireless.rfcomm.handshake.completed",       self._on_handshake_completed)
        self._bus.on("wireless.tcp.connection.accepted",          self._on_connection_accepted)

    def _on_connection_ready(self, payload: dict) -> None:
        self._logger.info("ConnectionManager ready — starting wireless")
        if not self._running:
            self.start()

    def _on_pairing_requested(self, payload: dict) -> None:
        self._logger.info(f"Pairing requested: {payload}")
        self._bluetooth.discover_devices()

    def _on_discovery_completed(self, payload: dict) -> None:
        self._logger.info("Bluetooth discovery completed")

    def _on_handshake_completed(self, payload: dict) -> None:
        self._logger.info("RFCOMM handshake completed")

    def _on_connection_accepted(self, payload: dict) -> None:
        self._logger.info(f"TCP connection accepted from {payload.get('address', 'unknown')}")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main():
    logger = LoggerManager.get_logger('services.wireless_daemon')
    daemon = WirelessDaemon()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    daemon.run()


if __name__ == "__main__":
    main()
