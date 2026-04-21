"""
Main entry point for NemoHeadUnit-Wireless
Registers all components through the ComponentRegistry pattern.
"""

import sys
import os
import threading
import logging

# Ensure the app directory is in the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from app.message_bus import MessageBus
from app.connection import ConnectionManager
from app.wireless.wireless_service import WirelessService
from app.gui.component import GUIComponent
from app.logger import LoggerManager


class Application:
    """
    Application orchestrator.
    
    Registers all components with the message bus and manages startup/shutdown.
    """
    
    def __init__(self):
        self._bus = MessageBus()
        self._logger = LoggerManager.get_logger('app.main')
        
        # Instantiate all components
        self._connection = ConnectionManager()
        self._wireless = WirelessService()
        self._gui = GUIComponent()
        
        self._logger.info("Application initialized")
    
    def register_components(self) -> bool:
        """Register all components with the bus."""
        try:
            self._logger.info("Registering components")
            
            # Register in dependency order with thread declarations
            self._logger.info("Registering ConnectionManager")
            self._connection.declare_thread(self._bus, "connection_thread")
            if not self._connection.register(self._bus):
                self._logger.error("Failed to register ConnectionManager")
                return False
            
            self._logger.info("Registering WirelessService")
            self._wireless.declare_thread(self._bus, "wireless_thread")
            if not self._wireless.register(self._bus):
                self._logger.error("Failed to register WirelessApp")
                return False
            
            self._logger.info("Registering GUIComponent")
            self._gui.declare_thread(self._bus, "MAIN")
            if not self._gui.register(self._bus, self._connection):
                self._logger.error("Failed to register GUIComponent")
                return False
            
            self._logger.info("All components registered successfully")
            return True
        except Exception as e:
            self._logger.error(f"Error registering components: {e}")
            return False
    
    def run(self) -> int:
        """Run the application."""
        try:
            # Register all components
            if not self.register_components():
                return 1
            
            # Start the message bus with main thread
            self._logger.info("Starting message bus with main thread")
            self._bus.start(threading.current_thread())
            
            # Request GUI startup
            self._logger.info("Requesting GUI startup")
            self._bus.publish('gui.startup.requested', 'app.main', {})
            
            # Start Qt event loop (runs in main thread)
            # This will process GUI events and shutdown signals
            self._logger.info("Starting Qt event loop")
            app = QApplication.instance()
            if app:
                # Run the event loop - will block until application quits
                exit_code = app.exec()
                self._logger.info("Qt event loop ended")
            else:
                # Fallback: wait for shutdown from bus
                self._logger.info("No Qt application, waiting for shutdown")
                self._bus.wait_for_shutdown()
                exit_code = 0
            
            self._logger.info("Application shutdown complete")
            return exit_code
        except Exception as e:
            self._logger.error(f"Application error: {e}")
            return 1


if __name__ == "__main__":
    app = Application()
    exit_code = app.run()
    sys.exit(exit_code)