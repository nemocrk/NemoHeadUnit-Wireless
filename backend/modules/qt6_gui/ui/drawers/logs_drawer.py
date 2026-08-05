"""
logs_drawer.py — Live System Logs Slide-Over Drawer with WebSocket Log Streaming.

Displays real-time system logs with module and level filter options.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LogsDrawerWidget(QWidget):
    """
    Live System Logs Drawer.
    """

    close_clicked = pyqtSignal()
    filter_changed = pyqtSignal(str, str)  # module, level

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(400)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)

        # Drawer Header
        header_layout = QHBoxLayout()
        title_label = QLabel("Live System Logs", self)
        title_label.setProperty("class", "drawer-title")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("×", self)
        close_btn.setProperty("class", "close-btn")
        close_btn.clicked.connect(self.close_clicked.emit)
        header_layout.addWidget(close_btn)
        self.layout.addLayout(header_layout)

        # Filter controls bar
        filter_layout = QHBoxLayout()
        self.module_combo = QComboBox(self)
        self.module_combo.setProperty("class", "styled-select")
        self.module_combo.addItem("📋 All Modules", "all")
        for m in ("channel_manager", "tcp_server", "connectivity_manager", "proxy", "qt6_gui"):
            self.module_combo.addItem(f"📦 {m}", m)
        self.module_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.module_combo, 1)

        self.level_combo = QComboBox(self)
        self.level_combo.setProperty("class", "styled-select")
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.level_combo.addItem(lvl, lvl)
        self.level_combo.setCurrentText("INFO")
        self.level_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.level_combo)

        self.layout.addLayout(filter_layout)

        # Log Console Text View
        self.console = QTextEdit(self)
        self.console.setObjectName("log-console")
        self.console.setReadOnly(True)
        self.console.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #58a6ff;
                font-family: Consolas, Monaco, monospace;
                font-size: 11px;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self.console.setPlainText("Connecting to live log stream...\n")
        self.layout.addWidget(self.console)

        self.hide()

    def append_log_entry(self, entry: str):
        """Append log line to console text view."""
        if not entry.endswith("\n"):
            entry += "\n"
        self.console.insertPlainText(entry)
        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_modules_list(self, modules: list):
        current_data = self.module_combo.currentData()
        self.module_combo.clear()
        self.module_combo.addItem("📋 All Modules", "all")
        for m in modules:
            self.module_combo.addItem(f"📦 {m}", m)
        idx = self.module_combo.findData(current_data)
        if idx >= 0:
            self.module_combo.setCurrentIndex(idx)

    def _on_filter_changed(self):
        mod = self.module_combo.currentData() or "all"
        lvl = self.level_combo.currentData() or "INFO"
        self.filter_changed.emit(mod, lvl)
