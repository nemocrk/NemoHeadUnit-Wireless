"""
Base tab component for NemoHeadUnit-Wireless

Provides a reusable base class for all tab components.
All bus subscriptions support thread='main' for Qt thread safety.
"""

from typing import Callable, Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from app.bus_client import BusClient
from app.logger import LoggerManager


class BaseTab(QWidget):
    """
    Base class for tab components.

    Provides common functionality including layout management,
    styling, and bus integration with thread-safe subscription support.
    """

    def __init__(self, title: str, message_bus: Optional[BusClient] = None):
        super().__init__()
        self._title = title
        self._layout = QVBoxLayout()
        self._bus = message_bus
        self._logger = LoggerManager.get_logger(f'app.gui.{title}')
        self._setup_ui()

    def _setup_ui(self):
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(10)
        self.setLayout(self._layout)

    @property
    def title(self) -> str:
        return self._title

    @property
    def layout(self) -> QVBoxLayout:
        return self._layout

    def set_title(self, title: str):
        self._title = title

    def add_widget(self, widget, layout_position: int = 0):
        self._layout.addWidget(widget, layout_position)

    def publish_to_bus(self, topic: str, payload: dict) -> None:
        if self._bus:
            self._bus.publish(topic, f'app.gui.{self._title}', payload)

    def subscribe_to_topic(self, topic: str, callback: Callable, thread: str = "") -> None:
        """
        Subscribe to a bus topic.
        Use thread='main' for any callback that touches Qt widgets.
        """
        if self._bus:
            self._bus.subscribe(topic, callback, thread=thread)
