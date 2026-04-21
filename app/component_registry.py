"""
Component Registry — Abstract base for all registrable components

Defines the interface for components that register themselves with the
message bus. Thread management is now the responsibility of each
independent process — the bus handles only IPC routing.
"""

from abc import ABC, abstractmethod
from typing import Optional
from app.bus_client import BusClient


class ComponentRegistry(ABC):
    """
    Abstract base class for all components that register with the bus.

    Components implement register() to subscribe to relevant topics
    and publish their ready event. No thread declaration is needed:
    threading is an internal concern of each process.
    """

    def __init__(self):
        self._message_bus: Optional[BusClient] = None

    @abstractmethod
    def register(self, message_bus: BusClient, *args, **kwargs) -> bool:
        """
        Register this component with the message bus.

        The component should:
        1. Store the bus reference
        2. Subscribe to relevant topics
        3. Publish a ready event when initialization is complete
        4. Return True if successful, False otherwise
        """
        pass

    @abstractmethod
    def name(self) -> str:
        """Return the component name used for logging and bus sender field."""
        pass
