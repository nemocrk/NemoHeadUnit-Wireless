"""
NemoHeadUnit-Wireless Application
Base module for Android Auto head unit emulation.
"""

from .base_interface import BaseInterface
from .component_registry import ComponentRegistry
from .message_bus import MessageBus
from .connection import ConnectionManager

__version__ = "0.1.0"
__author__ = "Nemo Development Team"