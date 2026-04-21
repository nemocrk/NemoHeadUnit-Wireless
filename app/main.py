"""
Main entry point for NemoHeadUnit-Wireless

Startup order:
    1. QApplication          — must exist before any QObject/signal
    2. bus_broker.py         — central ZMQ broker (subprocess)
    3. wireless_daemon.py    — wireless/media standalone process (subprocess)
    4. _wait_for_broker()    — poll until broker sockets are reachable
    5. BusClient             — connect GUI process to broker
    6. QtBusBridge install   — wire ZMQ→Qt thread dispatch
    7. Components register   — ConnectionManager + GUIComponent
    8. MainWindow.show()     — show GUI directly (no topic race condition)
    9. app.exec()            — Qt event loop blocks here
"""

import sys
import os
import subprocess
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zmq
from PyQt6.QtWidgets import QApplication
from app.bus_client import BusClient
from app.connection import ConnectionManager
from app.gui.component import GUIComponent
from app.gui.qt_bridge import install as install_qt_bridge
from app.gui import modern_main_window
from app.logger import LoggerManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BROKER_PUB_ADDR = "ipc:///tmp/nemobus.pub"
BROKER_WAIT_TIMEOUT = 10.0   # seconds max to wait for broker
BROKER_POLL_INTERVAL = 0.05  # seconds between attempts


def _wait_for_broker(logger, timeout: float = BROKER_WAIT_TIMEOUT) -> bool:
    """
    Poll until the ZMQ broker pub socket is reachable.
    Uses a throwaway REQ-less PUB connect + poller trick:
    we connect a PUB socket and try to send; if the broker
    XSUB is bound the send will succeed without error.
    A simpler and more reliable approach: try to connect a
    PUSH socket and send a probe, but XSUB accepts any socket.
    Simplest: just retry connect on a fresh PUB and check no
    exception, with a short sleep.
    """
    logger.info("Waiting for broker to be ready...")
    deadline = time.time() + timeout
    ctx = zmq.Context()
    while time.time() < deadline:
        try:
            probe = ctx.socket(zmq.PUB)
            probe.setsockopt(zmq.LINGER, 0)
            probe.connect(BROKER_PUB_ADDR)
            # Send a heartbeat probe — if broker isn’t up yet the message
            # is queued in the PUB socket HWM but no exception is raised.
            # Instead, we check the broker is accepting by trying a
            # synchronous PUSH/PULL pair on a separate inproc won’t work
            # across processes. Best reliable check: stat the socket file.
            probe.close(linger=0)
            sock_path = BROKER_PUB_ADDR.replace("ipc://", "")
            if os.path.exists(sock_path):
                logger.info("Broker socket file found — broker is ready")
                ctx.term()
                return True
        except Exception:
            pass
        time.sleep(BROKER_POLL_INTERVAL)
    ctx.term()
    logger.error(f"Broker not ready after {timeout}s")
    return False


class Application:
    """Application orchestrator."""

    def __init__(self):
        self._logger = LoggerManager.get_logger('app.main')
        self._subprocesses = []
        self._bus: BusClient = None
        self._connection: ConnectionManager = None
        self._gui: GUIComponent = None
        self._window = None

    def _start_subprocesses(self) -> bool:
        procs = [
            ("bus_broker",      [sys.executable, os.path.join(ROOT, "bus_broker.py")]),
            ("wireless_daemon", [sys.executable, os.path.join(ROOT, "services", "wireless_daemon.py")]),
        ]
        for name, cmd in procs:
            try:
                p = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                self._subprocesses.append(p)
                self._logger.info(f"Started subprocess: {name} (pid={p.pid})")
            except FileNotFoundError:
                self._logger.warning(f"Subprocess not found, skipping: {name}")
            except Exception as e:
                self._logger.error(f"Failed to start subprocess {name}: {e}")
                return False
        return True

    def _stop_subprocesses(self) -> None:
        for p in self._subprocesses:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                p.kill()

    def register_components(self) -> bool:
        try:
            self._connection = ConnectionManager()
            self._gui = GUIComponent()

            self._logger.info("Registering ConnectionManager")
            if not self._connection.register(self._bus):
                self._logger.error("Failed to register ConnectionManager")
                return False

            self._logger.info("Registering GUIComponent")
            if not self._gui.register(self._bus, self._connection):
                self._logger.error("Failed to register GUIComponent")
                return False

            self._logger.info("All components registered")
            return True
        except Exception as e:
            self._logger.error(f"Error registering components: {e}")
            return False

    def run(self) -> int:
        try:
            # 1. Qt application — must be first
            app = QApplication.instance() or QApplication(sys.argv)

            # 2. Start infrastructure subprocesses
            if not self._start_subprocesses():
                return 1

            # 3. Wait for broker socket to exist before connecting
            if not _wait_for_broker(self._logger):
                self._logger.error("Cannot connect to broker, aborting")
                self._stop_subprocesses()
                return 1

            # 4. Connect to broker
            self._bus = BusClient(name="gui")

            # 5. Wire Qt bridge
            install_qt_bridge(self._bus)

            # 6. Register components
            if not self.register_components():
                self._bus.stop()
                self._stop_subprocesses()
                return 1

            # 7. Create and show the main window directly
            #    (do NOT rely on gui.startup.requested topic — the broker
            #    may not have forwarded the message to subscribers yet)
            self._logger.info("Creating main window")
            self._window = modern_main_window.create(
                message_bus=self._bus,
                connection_manager=self._connection,
                run_event_loop=False,
            )
            self._window.show()

            # 8. Also publish the topic for any other subscribers
            self._bus.publish('gui.startup.requested', 'app.main', {})

            # 9. Qt event loop
            self._logger.info("Starting Qt event loop")
            exit_code = app.exec()

            # 10. Teardown
            self._logger.info("Qt event loop exited, shutting down")
            self._bus.publish('app.shutdown', 'app.main', {})
            self._bus.stop()
            self._stop_subprocesses()
            self._logger.info("Shutdown complete")
            return exit_code

        except Exception as e:
            self._logger.error(f"Application error: {e}")
            return 1


if __name__ == "__main__":
    application = Application()
    sys.exit(application.run())
