"""
Modular GUI components for NemoHeadUnit-Wireless

This package contains reusable, modular components for the main window.
"""

from app.gui.components.base_tab import BaseTab
from app.gui.components.connection_tab import ConnectionTab
from app.gui.components.media_tab import MediaTab
from app.gui.components.config_tab import ConfigTab
from app.gui.components.status_tab import StatusTab

__all__ = [
    "BaseTab",
    "ConnectionTab",
    "MediaTab",
    "ConfigTab",
    "StatusTab",
]