"""
Main entry point for NemoHeadUnit-Wireless

Startup order:
    1. bus_broker.py        — central ZMQ broker (subprocess)
    2. wireless_daemon.py   — wireless/media standalone process (subprocess)
    3. BusClient            — connect GUI process to broker
    4. Qt event loop        — runs in main thread
"""

import sys
import os
import subprocess
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from app.bus_client import BusClient
from app.connection import ConnectionManager
from app.gui.component import GUIComponent
from app.gui.qt_bridge import install as install_qt_bridge
from app.logger import LoggerManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Application:
    """
    Application orchestrator.
    Starts infrastructure subprocesses, connects to bus, registers components.
    """

    def __init__(self):
        self._logger = LoggerManager.get_logger('app.main')
        self._subprocesses = []
        self._bus: BusClient = None
        self._connection: ConnectionManager = None
        self._gui: GUIComponent = None

    def _start_subprocesses(self) -> bool:
        """Start broker and wireless daemon as independent subprocesses."""
        procs = [
            ("bus_broker",      [sys.executable, os.path.join(ROOT, "bus_broker.py")]),
            ("wireless_daemon", [sys.executable, os.path.join(ROOT, "services", "wireless_daemon.py")]),
        ]
        for name, cmd in procs:
            try:
                p = subprocess.Popen(cmd)
                self._subprocesses.append(p)
                self._logger.info(f"Started subprocess: {name} (pid={p.pid})")
            except FileNotFoundError:
                # wireless_daemon may not exist yet — non-blocking during migration
                self._logger.warning(f"Subprocess not found, skipping: {name}")
            except Exception as e:
                self._logger.error(f"Failed to start subprocess {name}: {e}")
                return False

        # Give broker time to bind sockets before clients connect
        time.sleep(0.2)
        return True

    def _stop_subprocesses(self) -> None:
        """Terminate all managed subprocesses on shutdown."""
        for p in self._subprocesses:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                p.kill()

    def register_components(self) -> bool:
        """Instantiate and register all in-process components."""
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
        """Run the application."""
        try:
            # 1. Start Qt application first (needed before any QObject/signal)
            app = QApplication.instance() or QApplication(sys.argv)

            # 2. Start infrastructure subprocesses
            if not self._start_subprocesses():
                return 1

            # 3. Connect to broker
            self._bus = BusClient(name="gui")

            # 4. Wire Qt bridge so thread='main' subscriptions work
            install_qt_bridge(self._bus)

            # 5. Register in-process components
            if not self.register_components():
                return 1

            # 6. Request GUI startup
            self._bus.publish('gui.startup.requested', 'app.main', {})

            # 7. Run Qt event loop (blocks until window closed)
            self._logger.info("Starting Qt event loop")
            exit_code = app.exec()

            # 8. Teardown
            self._bus.publish('app.shutdown', 'app.main', {})
            self._bus.stop()
            self._stop_subprocesses()

            self._logger.info("Application shutdown complete")
            return exit_code

        except Exception as e:
            self._logger.error(f"Application error: {e}")
            return 1


if __name__ == "__main__":
    application = Application()
    sys.exit(application.run())
