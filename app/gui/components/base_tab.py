"""
Base tab component for NemoHeadUnit-Wireless

Provides a reusable base class for all tab components.
"""

from typing import Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from app.message_bus import MessageBus
from app.logger import LoggerManager


class BaseTab(QWidget):
    """
    Base class for tab components.
    
    Provides common functionality for all tabs including layout management,
    styling, event handling, and bus integration.
    """
    
    def __init__(self, title: str, message_bus: Optional[MessageBus] = None):
        super().__init__()
        self._title = title
        self._layout = QVBoxLayout()
        self._bus = message_bus
        self._logger = LoggerManager.get_logger(f'app.gui.{title}')
        self._setup_ui()
    
    def _setup_ui(self):
        """Initialize the base UI components."""
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(10)
        self.setLayout(self._layout)
    
    @property
    def title(self) -> str:
        """Get the tab title."""
        return self._title
    
    @property
    def layout(self) -> QVBoxLayout:
        """Get the layout."""
        return self._layout
    
    def set_title(self, title: str):
        """Set the tab title."""
        self._title = title
    
    def add_widget(self, widget, layout_position: int = 0):
        """Add a widget to the layout."""
        self._layout.addWidget(widget, layout_position)
    
    def publish_to_bus(self, topic: str, payload: dict) -> None:
        """Publish an event to the message bus."""
        if self._bus:
            self._bus.publish(topic, f'app.gui.{self._title}', payload)
    
    def subscribe_to_topic(self, topic: str, callback) -> None:
        """Subscribe to a topic on the message bus."""
        if self._bus:
            self._bus.subscribe(topic, callback)