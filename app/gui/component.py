"""
GUI Component for NemoHeadUnit-Wireless

Wraps the GUI functionality as a registrable component that can be
initialized through the component registry pattern.

All bus subscriptions use thread='main' to ensure widget creation
and manipulation happen on the Qt main thread.
"""

from typing import Optional
from app.component_registry import ComponentRegistry
from app.bus_client import BusClient
from app.connection import ConnectionManager
from app.logger import LoggerManager
from app.gui import modern_main_window


class GUIComponent(ComponentRegistry):
    """
    GUI component that manages the main window.

    Registers with the bus and coordinates GUI lifecycle.
    All callbacks that touch Qt widgets are dispatched on the main thread
    via thread='main' (QtBusBridge).
    """

    def __init__(self):
        self._bus: Optional[BusClient] = None
        self._logger = LoggerManager.get_logger('app.gui.component')
        self._connection_manager: Optional[ConnectionManager] = None
        self._window = None

    def register(self, message_bus: BusClient, connection_manager: ConnectionManager) -> bool:
        """
        Register GUI component with the message bus.

        Args:
            message_bus:        BusClient instance
            connection_manager: ConnectionManager component

        Returns:
            bool: True if registration successful
        """
        self._bus = message_bus
        self._connection_manager = connection_manager
        self._logger.info("GUIComponent registering with bus")

        # thread='main' — both handlers create/manipulate Qt widgets,
        # so they must execute on the Qt main thread via QtBusBridge.
        self._bus.subscribe('gui.startup.requested',  self._on_gui_startup,  thread="main")
        self._bus.subscribe('gui.shutdown.requested', self._on_gui_shutdown, thread="main")

        return True

    def name(self) -> str:
        """Get component name."""
        return "gui_component"

    def _on_gui_startup(self, payload):
        """Handle GUI startup request — runs on Qt main thread."""
        self._logger.info("GUI startup requested")
        self._create_and_show()

    def _on_gui_shutdown(self, payload):
        """Handle GUI shutdown request — runs on Qt main thread."""
        self._logger.info("GUI shutdown requested")
        if self._window:
            self._window.close()

    def _create_and_show(self):
        """Create and display the main window."""
        self._logger.debug("Creating main window")
        self._window = modern_main_window.create(
            message_bus=self._bus,
            connection_manager=self._connection_manager,
            run_event_loop=False,
        )
