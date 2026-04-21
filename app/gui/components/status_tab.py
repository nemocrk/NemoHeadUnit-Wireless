"""  
Status tab component for NemoHeadUnit-Wireless

Provides system status monitoring and event logging.
"""

from typing import Optional
from PyQt6.QtWidgets import QGroupBox, QFormLayout, QLabel, QTextEdit, QVBoxLayout
from app.message_bus import MessageBus
from app.logger import LoggerManager


class StatusTab(QGroupBox):
    """
    Status tab component.
    
    Provides system status monitoring, active connections, and event logging.
    """
    
    def __init__(self, message_bus: Optional[MessageBus] = None):
        super().__init__("Status")
        self._bus = message_bus
        self._logger = LoggerManager.get_logger('app.gui.status_tab')
        self._connections = []
        self._events = []
        self._setup_ui()
    
    def _setup_ui(self):
        """Initialize the status tab UI."""
        
        layout = QVBoxLayout()
        
        form = QFormLayout()
        
        # System status
        self.system_status = QLabel("Ready")
        self.system_status.setObjectName("systemStatus")
        form.addRow("Overall Status:", self.system_status)
        
        self.cpu_usage = QLabel("0%")
        form.addRow("CPU Usage:", self.cpu_usage)
        
        self.memory_usage = QLabel("0%")
        form.addRow("Memory Usage:", self.memory_usage)
        
        layout.addLayout(form)
        
        # Event log
        layout.addWidget(QLabel("Event Log:"))
        self.event_log = QTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setStyleSheet("background-color: #1e1e2e; color: #a6adc8; border-radius: 5px;")
        layout.addWidget(self.event_log)
        
        self.setLayout(layout)
    
    def update_cpu_usage(self, value: int):
        """Update CPU usage."""
        self.cpu_usage.setText(f"{value}%")
    
    def update_memory_usage(self, value: int):
        """Update memory usage."""
        self.memory_usage.setText(f"{value}%")
    
    def add_event(self, event: str):
        """Add an event to the log."""
        self._events.append(event)
        self.event_log.append(f"[{QTime.currentTime().toString()}] {event}")
        self.event_log.ensureCursorVisible()