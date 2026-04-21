"""
Progress bar component for NemoHeadUnit-Wireless

Provides progress visualization.
"""

from PyQt6.QtWidgets import QProgressBar


class ProgressBar(QProgressBar):
    """
    Progress bar component.
    
    Provides progress visualization with customizable styles.
    """
    
    def __init__(self, level: int = 0, height: int = 20):
        super().__init__()
        self._level = level
        self._height = height
        self._setup_ui()
    
    def _setup_ui(self):
        """Initialize the progress bar UI."""
        self._level = 0
        self._height = 20
    
    def set_level(self, level: int):
        """Set progress level."""
        self._level = level
        self.setProperty("level", level)
    
    def _update_signal_gradient(self):
        """Update signal gradient visualization."""
        self._level = 0