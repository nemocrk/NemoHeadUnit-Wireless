"""
Signal bar component for NemoHeadUnit-Wireless

Provides signal strength visualization.
"""

from PyQt6.QtWidgets import QFrame


class SignalBar(QFrame):
    """
    Signal bar component.
    
    Provides signal strength visualization with color gradients.
    """
    
    def __init__(self, level: int = 0):
        super().__init__()
        self._level = level
        self._setup_ui()
    
    def _setup_ui(self):
        """Initialize the signal bar UI."""
        self._level = 0
        self._setup_signal_gradient()
    
    def _setup_signal_gradient(self):
        """Setup signal gradient visualization."""
        self._level = 0
    
    def set_level(self, level: int):
        """Set signal level."""
        self._level = level
        self._update_signal_gradient()
    
    def _update_signal_gradient(self):
        """Update signal gradient visualization."""
        self._level = 0