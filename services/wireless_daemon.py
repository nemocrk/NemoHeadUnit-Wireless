"""
NemoHeadUnit-Wireless — Wireless Daemon
Standalone process for Bluetooth, RFCOMM handshake, TCP server
and media frame publishing.

Run independently:
    python services/wireless_daemon.py

Startup sequence:
    1. _wait_for_broker()   — poll until IPC socket file exists
    2. BusClient()          — connect to ZMQ broker
    3. bluetooth.set_bus()  — inject shared bus into BluetoothManager
    4. daemon.start()       — init hardware, subscribe to topics
    5. daemon.run()         — accept loop (blocks)

Bus topics published:
    wireless.app.ready              {status, method}
    wireless.app.startup.failed     {component}
    wireless.connection.established {address, method}
    wireless.connection.failed      {error, method}
    media.audio.frame               binary=<AAC bytes>
    media.video.frame               binary=<H.264 bytes>

Bus topics subscribed:
    connection.pairing.requested
    wireless.bluetooth.discovery.completed
    wireless.rfcomm.handshake.completed
    wireless.tcp.connection.accepted
    app.shutdown
"""

import sys
import os
import signal
import socket
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bus_client import BusClient
from app.wireless.bluetooth_manager import BluetoothManager
from app.wireless.rfcomm_handler import RfcommHandler
from app.wireless.tcp_server import TcpServer
from app.logger import LoggerManager

BROKER_PUB_ADDR    = "ipc:///tmp/nemobus.pub"
BROKER_WAIT_TIMEOUT  = 15.0
BROKER_POLL_INTERVAL = 0.05


def _wait_for_broker(logger, timeout: float = BROKER_WAIT_TIMEOUT) -> bool:
    """Poll until the broker IPC socket file appears on disk."""
    sock_path = BROKER_PUB_ADDR.replace("ipc://", "")
    logger.info("Waiting for broker socket...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(sock_path):
            logger.info("Broker ready")
            return True
        time.sleep(BROKER_POLL_INTERVAL)
    logger.error(f"Broker not ready after {timeout}s")
    return False


class WirelessDaemon:
    """
    Wireless service running as an independent process.
    BusClient is created only after the broker is confirmed ready.
    BluetoothManager receives the bus via set_bus() — no autonomous ZMQ.
    """

    def __init__(self, bus: BusClient):
        self._logger = LoggerManager.get_logger('services.wireless_daemon')
        self._bus = bus
        self._bluetooth = BluetoothManager()
        self._bluetooth.set_bus(bus)   # inject — no autonomous BusClient
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
        """Initialize hardware and subscribe to bus topics."""
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

        self._publish("wireless.app.ready", {"status": "ready", "method": "bluetooth"})
        self._logger.info("Wireless daemon ready")
        return True

    def stop(self) -> None:
        self._logger.info("Wireless daemon stopping")
        self._running = False
        self._tcp_server.stop()
        self._bus.stop()

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
        Read AAC/H.264 frames and publish as binary bus messages.
        Wire format: [4B frame_type][4B payload_len][N bytes payload]
            0x01 = AAC audio
            0x02 = H.264 video
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
                if frame_type == 0x01:
                    self._seq_audio += 1
                    self._bus.publish(
                        "media.audio.frame", "wireless_daemon",
                        {"seq": self._seq_audio, "pts": 0},
                        binary=data,
                    )
                elif frame_type == 0x02:
                    self._seq_video += 1
                    is_idr = len(data) > 4 and (data[4] & 0x1F) == 5
                    self._bus.publish(
                        "media.video.frame", "wireless_daemon",
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
        self._bus.on("connection.pairing.requested",           self._on_pairing_requested)
        self._bus.on("wireless.bluetooth.discovery.completed", self._on_discovery_completed)
        self._bus.on("wireless.rfcomm.handshake.completed",    self._on_handshake_completed)
        self._bus.on("wireless.tcp.connection.accepted",       self._on_connection_accepted)
        self._bus.on("app.shutdown",                           self._on_shutdown)

    def _on_pairing_requested(self, payload) -> None:
        self._logger.info(f"Pairing requested: {payload}")
        self._bluetooth.discover_devices()

    def _on_discovery_completed(self, payload) -> None:
        self._logger.info("Bluetooth discovery completed")

    def _on_handshake_completed(self, payload) -> None:
        self._logger.info("RFCOMM handshake completed")

    def _on_connection_accepted(self, payload) -> None:
        self._logger.info(f"TCP connection accepted from {payload.get('address', 'unknown') if isinstance(payload, dict) else payload}")

    def _on_shutdown(self, payload) -> None:
        self._logger.info("Shutdown received")
        self.stop()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger = LoggerManager.get_logger('services.wireless_daemon')

    # 1. Wait for broker
    if not _wait_for_broker(logger):
        logger.error("Broker unavailable — wireless daemon exiting")
        sys.exit(1)

    # 2. Connect to broker
    bus = BusClient(name="wireless_daemon")

    # 3. Build daemon (bus injected into BluetoothManager inside __init__)
    daemon = WirelessDaemon(bus)

    # 4. Signal handlers
    def _shutdown(signum, frame):
        logger.info("Signal received, stopping wireless daemon")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 5. Start hardware + subscriptions
    if not daemon.start():
        logger.error("Wireless daemon failed to start")
        bus.stop()
        sys.exit(1)

    # 6. Block in accept loop
    daemon.run()


if __name__ == "__main__":
    main()
