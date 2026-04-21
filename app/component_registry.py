"""
Component Registry - Abstract base for all registrable components

Defines the interface for components that can register themselves with the
message bus. This allows main.py to simply call register() on each component
without knowing implementation details.

Components now declare which thread they need, and the bus executes
handlers in the appropriate thread.
"""

import threading
from abc import ABC, abstractmethod
from typing import Optional
from app.message_bus import MessageBus


class ComponentRegistry(ABC):
    """
    Abstract base class for all components that can register with the bus.
    
    Components inherit from this and implement register() to handle their
    own initialization and subscription to bus events.
    
    This allows main.py to be decoupled from component implementation details.
    """
    
    def __init__(self):
        """Initialize component with optional thread declaration."""
        self._thread_name: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._message_bus: Optional[MessageBus] = None
    
    @abstractmethod
    def register(self, message_bus: MessageBus) -> bool:
        """
        Register this component with the message bus.
        
        Called by main.py during initialization. The component should:
        1. Store the bus reference
        2. Subscribe to relevant events
        3. Publish ready event when initialization is complete
        4. Return True if successful, False otherwise
        
        Args:
            message_bus: The shared MessageBus instance
        
        Returns:
            bool: True if registration successful, False otherwise
        """
        pass
    
    @abstractmethod
    def name(self) -> str:
        """Get the component name for logging."""
        pass
    
    def declare_thread(self, message_bus: MessageBus, thread_name: str) -> None:
        """
        Declare which thread this component needs.
        
        Called by main.py to register the component's thread requirement.
        
        Args:
            message_bus: The shared MessageBus instance
            thread_name: Name of the thread this component will use
        """
        self._thread_name = thread_name
        self._message_bus = message_bus
        self._thread = message_bus._register_thread(thread_name)
        self._thread.start()
    
    def get_thread_name(self) -> Optional[str]:
        """Get the thread name for this component."""
        return self._thread_name
    
    def get_thread(self) -> Optional[threading.Thread]:
        """Get the thread object for this component."""
        return self._thread
    
    def stop_thread(self) -> None:
        """Stop the component's thread."""
        if self._thread:
            self._thread.join()
            self._thread = None