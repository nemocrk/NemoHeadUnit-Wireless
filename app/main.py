"""
Main entry point for NemoHeadUnit-Wireless

Startup order:
    1. QApplication          — must exist before any QObject/signal
    2. bus_broker.py         — central ZMQ broker (subprocess)
    3. wireless_daemon.py    — wireless/media standalone process (subprocess)
    4. _wait_for_broker()    — poll until broker socket file exists
    5. BusClient             — connect GUI process to broker
    6. QtBusBridge install   — wire ZMQ→Qt thread dispatch
    7. Components register   — ConnectionManager + GUIComponent
    8. MainWindow.show()     — show GUI directly (no topic race condition)
    9. SIGINT watchdog timer — QTimer 200ms keeps Python interpreter alive
       so KeyboardInterrupt is delivered and Qt can quit gracefully
   10. app.exec()            — Qt event loop blocks here

SIGINT / Ctrl+C design:
    Qt’s C++ event loop blocks the GIL, so Python’s signal handler is never
    called while app.exec() runs — unless the interpreter gets CPU time.
    A QTimer firing every 200ms forces Qt to yield to Python briefly, which
    is enough for signal.signal(SIGINT, handler) to fire and call app.quit().

Subprocess stdout:
    stdout=None (inherit parent’s terminal) — NOT stdout=PIPE.
    With PIPE and no consumer the OS pipe buffer (~64 KB) fills up and
    the subprocess blocks on write(), causing an apparent hang.
"""

import sys
import os
import signal
import subprocess
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zmq
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from app.bus_client import BusClient
from app.connection import ConnectionManager
from app.gui.component import GUIComponent
from app.gui.qt_bridge import install as install_qt_bridge
from app.gui import modern_main_window
from app.logger import LoggerManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BROKER_PUB_ADDR      = "ipc:///tmp/nemobus.pub"
BROKER_WAIT_TIMEOUT  = 10.0
BROKER_POLL_INTERVAL = 0.05
SIGINT_TIMER_MS      = 200


def _wait_for_broker(logger, timeout: float = BROKER_WAIT_TIMEOUT) -> bool:
    """Poll until the broker IPC socket file exists on disk."""
    logger.info("Waiting for broker to be ready...")
    sock_path = BROKER_PUB_ADDR.replace("ipc://", "")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(sock_path):
            logger.info("Broker socket file found — broker is ready")
            return True
        time.sleep(BROKER_POLL_INTERVAL)
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
        self._app: QApplication = None

    # ------------------------------------------------------------------
    # Subprocess management
    # ------------------------------------------------------------------

    def _start_subprocesses(self) -> bool:
        procs = [
            ("bus_broker",      [sys.executable, os.path.join(ROOT, "bus_broker.py")]),
            ("wireless_daemon", [sys.executable, os.path.join(ROOT, "services", "wireless_daemon.py")]),
        ]
        for name, cmd in procs:
            try:
                # stdout=None: inherit parent terminal so subprocess output
                # is visible and the OS pipe buffer cannot fill up and deadlock.
                p = subprocess.Popen(cmd, stdout=None, stderr=None)
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

    # ------------------------------------------------------------------
    # Component registration
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        self._logger.info("Shutting down...")
        if self._bus:
            try:
                self._bus.publish('app.shutdown', 'app.main', {})
            except Exception:
                pass
            self._bus.stop()
        self._stop_subprocesses()
        self._logger.info("Shutdown complete")

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(self) -> int:
        try:
            # 1. Qt application
            self._app = QApplication.instance() or QApplication(sys.argv)

            # 2. SIGINT handler + QTimer watchdog
            _quit_requested = [False]

            def _on_sigint(signum, frame):
                self._logger.info("SIGINT received — requesting Qt quit")
                _quit_requested[0] = True
                self._app.quit()

            signal.signal(signal.SIGINT,  _on_sigint)
            signal.signal(signal.SIGTERM, _on_sigint)

            watchdog = QTimer()
            watchdog.setInterval(SIGINT_TIMER_MS)
            watchdog.timeout.connect(lambda: None)
            watchdog.start()

            # 3. Start subprocesses
            if not self._start_subprocesses():
                return 1

            # 4. Wait for broker
            if not _wait_for_broker(self._logger):
                self._logger.error("Cannot connect to broker, aborting")
                self._stop_subprocesses()
                return 1

            # 5. Connect to broker
            self._bus = BusClient(name="gui")

            # 6. Wire Qt bridge
            install_qt_bridge(self._bus)

            # 7. Register components
            if not self.register_components():
                self._shutdown()
                return 1

            # 8. Show main window directly
            self._logger.info("Creating main window")
            self._window = modern_main_window.create(
                message_bus=self._bus,
                connection_manager=self._connection,
                run_event_loop=False,
            )
            self._window.show()
            self._bus.publish('gui.startup.requested', 'app.main', {})

            # 9. Qt event loop
            self._logger.info("Starting Qt event loop")
            exit_code = self._app.exec()

            # 10. Teardown
            watchdog.stop()
            self._shutdown()
            return exit_code

        except Exception as e:
            self._logger.error(f"Application error: {e}")
            self._shutdown()
            return 1


if __name__ == "__main__":
    application = Application()
    sys.exit(application.run())
