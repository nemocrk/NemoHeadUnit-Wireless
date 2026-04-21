"""
Base Interface for NemoHeadUnit-Wireless
Defines the core interface for all components.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseInterface(ABC):
    """
    Abstract base interface for all components.
    All components must implement this interface.
    
    Naming Convention: snake_case for all methods and attributes
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Returns the name of the component."""
        pass
    
    @property
    @abstractmethod
    def status(self) -> str:
        """Returns the current status of the component."""
        pass
    
    @abstractmethod
    def on(self) -> bool:
        """Starts the component."""
        pass
    
    @abstractmethod
    def off(self) -> bool:
        """Stops the component."""
        pass
    
    @abstractmethod
    def is_running(self) -> bool:
        """Checks if the component is running."""
        pass
    
    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Gets the detailed status of the component."""
        pass
    
    @abstractmethod
    def reset(self) -> None:
        """Resets the component to initial state."""
        pass
    
    @abstractmethod
    def log(self, message: str, level: str = "info") -> None:
        """Logs a message at the specified level."""
        pass