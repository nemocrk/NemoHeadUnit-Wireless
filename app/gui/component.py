"""
GUI Component for NemoHeadUnit-Wireless

Wraps the GUI functionality as a registrable component that can be
initialized through the component registry pattern.
"""

from typing import Optional
from app.component_registry import ComponentRegistry
from app.message_bus import MessageBus
from app.connection import ConnectionManager
from app.logger import LoggerManager
from app.gui import modern_main_window


class GUIComponent(ComponentRegistry):
    """
    GUI component that manages the main window.
    
    Registers with the bus and coordinates GUI lifecycle.
    """
    
    def __init__(self):
        self._bus: Optional[MessageBus] = None
        self._logger = LoggerManager.get_logger('app.gui.component')
        self._connection_manager: Optional[ConnectionManager] = None
        self._window = None
    
    def register(self, message_bus: MessageBus, connection_manager: ConnectionManager) -> bool:
        """
        Register GUI component with the message bus.
        
        Args:
            message_bus: The shared MessageBus instance
            connection_manager: The ConnectionManager component
        
        Returns:
            bool: True if registration successful
        """
        self._bus = message_bus
        self._connection_manager = connection_manager
        self._logger.info("GUIComponent registering with bus")
        
        # Subscribe to relevant events
        self._bus.subscribe('gui.startup.requested', self._on_gui_startup)
        self._bus.subscribe('gui.shutdown.requested', self._on_gui_shutdown)
        
        return True
    
    def name(self) -> str:
        """Get component name."""
        return "gui_component"
    
    def _on_gui_startup(self, payload):
        """Handle GUI startup request."""
        self._logger.info("GUI startup requested")
        self._create_and_show()
    
    def _on_gui_shutdown(self, payload):
        """Handle GUI shutdown request."""
        self._logger.info("GUI shutdown requested")
        # GUI will close naturally from user interaction
    
    def _create_and_show(self):
        """Create and display the GUI."""
        self._logger.debug("Creating main window")
        # Create window without starting event loop - main app will handle that
        self._window = modern_main_window.create(
            message_bus=self._bus,
            connection_manager=self._connection_manager,
            run_event_loop=False
        )
