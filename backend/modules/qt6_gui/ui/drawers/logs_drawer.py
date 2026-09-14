"""
logs_drawer.py — Live System Logs Drawer for Qt6 GUI.

Uses native PyQt6.QtWebSockets (QWebSocket) directly on the Qt event loop,
mirroring the web client architecture without background thread or aiohttp overhead.
"""

import json
import urllib.request
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWebSockets import QWebSocket
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
    Live System Logs Drawer Widget using native Qt QWebSockets.
    """

    close_clicked = pyqtSignal()
    filter_changed = pyqtSignal(str, str)  # module, level

    def __init__(self, parent=None, host_port="127.0.0.1:8000"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(400)
        self.host_port = host_port
        self.log_sockets: list[QWebSocket] = []
        self._available_modules: dict = {}

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
        for m in ("channel_manager", "media_server", "tcp_server", "connectivity_manager", "proxy", "qt6_gui"):
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
        self.console.document().setMaximumBlockCount(1000)
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

    def showEvent(self, event):
        super().showEvent(event)
        self._fetch_modules_and_connect()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._close_all_sockets()

    def _close_all_sockets(self):
        for ws in self.log_sockets:
            try:
                ws.textMessageReceived.disconnect()
            except Exception:
                pass
            ws.close()
        self.log_sockets.clear()

    def _fetch_modules_and_connect(self):
        # Fetch active modules from /api/system/modules
        try:
            url = f"http://{self.host_port}/api/system/modules"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._available_modules = data.get("modules", {})
                    self._update_modules_dropdown(list(self._available_modules.keys()))
        except Exception:
            pass

        self.reconnect_stream()

    def _update_modules_dropdown(self, module_names: list[str]):
        if not module_names:
            return
        current_data = self.module_combo.currentData()
        self.module_combo.blockSignals(True)
        try:
            self.module_combo.clear()
            self.module_combo.addItem("📋 All Modules", "all")
            for m in module_names:
                self.module_combo.addItem(f"📦 {m}", m)
            idx = self.module_combo.findData(current_data)
            if idx >= 0:
                self.module_combo.setCurrentIndex(idx)
            else:
                self.module_combo.setCurrentIndex(0)
        finally:
            self.module_combo.blockSignals(False)

    def reconnect_stream(self):
        self._close_all_sockets()

        selected_mod = self.module_combo.currentData() or "all"
        level = self.level_combo.currentData() or "INFO"

        urls_to_connect = []
        if selected_mod != "all":
            if selected_mod in self._available_modules:
                ws_url = self._available_modules[selected_mod].get("log_ws_url", f"/api/{selected_mod}/logs")
                urls_to_connect.append(ws_url)
            else:
                urls_to_connect.append(f"/api/{selected_mod}/logs")
        else:
            if self._available_modules:
                for mod_data in self._available_modules.values():
                    ws_url = mod_data.get("log_ws_url")
                    if ws_url:
                        urls_to_connect.append(ws_url)
            else:
                urls_to_connect.append("/api/logs")

        self.console.setPlainText(f"--- Live Log Stream [{selected_mod.upper()}, {level}] ({len(urls_to_connect)} stream{'s' if len(urls_to_connect) != 1 else ''}) ---\n")

        for rel_path in urls_to_connect:
            sep = "&" if "?" in rel_path else "?"
            full_url = f"ws://{self.host_port}{rel_path}{sep}level={level}"
            ws = QWebSocket(parent=self)
            ws.textMessageReceived.connect(self.append_log_entry)
            ws.open(QUrl(full_url))
            self.log_sockets.append(ws)

    def append_log_entry(self, entry: str):
        """Append log line to console text view."""
        if not entry.endswith("\n"):
            entry += "\n"
        self.console.insertPlainText(entry)
        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_filter_changed(self):
        mod = self.module_combo.currentData() or "all"
        lvl = self.level_combo.currentData() or "INFO"
        self.filter_changed.emit(mod, lvl)
        if self.isVisible():
            self.reconnect_stream()
