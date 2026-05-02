"""
NemoHeadUnit-Wireless v2 — log_viewer module

Standalone PyQt6 window that displays log entries published on the bus
in real-time by any module.

Module contract:
  Name        : log_viewer
  Priority    : 2  (UI level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                log.entry   → {module: str, level: str, message: str, ts: float}
  Publishes   : system.module_ready  → {name, priority}
                system.ready        → {name, priority}
  Config keys : max_lines    int     500    max lines kept in the view
  State       : private
"""

import sys
import time
from datetime import datetime
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot          # noqa: E402
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont # noqa: E402
from PyQt6.QtWidgets import (                                        # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QStatusBar,
)

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "log_viewer"
PRIORITY    = 2  # UI level

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

_DEFAULTS = {
    "max_lines": 500,
}

_config: dict = dict(_DEFAULTS)

# ---------------------------------------------------------------------------
# Level → colour mapping
# ---------------------------------------------------------------------------

_LEVEL_COLORS: dict[str, str] = {
    "DEBUG":    "#888888",
    "INFO":     "#d4d4d4",
    "WARNING":  "#f0c040",
    "ERROR":    "#e05050",
    "CRITICAL": "#ff4444",
}

# ---------------------------------------------------------------------------
# Config callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        return
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in config.items() if k in _DEFAULTS})
    _config = merged
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _DEFAULTS:
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class LogViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Log Viewer — NemoHeadUnit v2")
        self._line_count = 0
        self._filter_level = "ALL"
        self._build_ui()

    def apply_default_geometry(self, app: QApplication) -> None:
        """Right half of the primary screen, full height."""
        screen = app.primaryScreen().availableGeometry()
        w = screen.width() // 2
        h = screen.height()
        x = screen.x() + w
        y = screen.y()
        self.setGeometry(x, y, w, h)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(10, 10, 10, 10)

        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("Filtro livello:"))
        self._combo_level = QComboBox()
        self._combo_level.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self._combo_level.currentTextChanged.connect(self._on_filter_changed)
        self._combo_level.setMinimumWidth(110)
        toolbar.addWidget(self._combo_level)

        toolbar.addStretch()

        self._btn_clear = QPushButton("🗑  Clear")
        self._btn_clear.setMinimumHeight(34)
        self._btn_clear.clicked.connect(self._on_clear_clicked)
        toolbar.addWidget(self._btn_clear)

        root.addLayout(toolbar)

        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setFont(QFont("Monospace", 10))
        self._log_area.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #444;"
        )
        root.addWidget(self._log_area, stretch=1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("In attesa di system.start…")

    @pyqtSlot(str)
    def set_status(self, message: str):
        self._status.showMessage(message)

    @pyqtSlot(str, str, str, str)
    def append_log_line(self, ts: str, module: str, level: str, message: str):
        _level_order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        if self._filter_level != "ALL":
            try:
                if _level_order.index(level) < _level_order.index(self._filter_level):
                    return
            except ValueError:
                pass

        color = _LEVEL_COLORS.get(level, "#d4d4d4")
        line  = f"[{ts}] [{module:>20}] [{level:<8}] {message}"

        cursor = self._log_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(line + "\n")

        self._line_count += 1
        max_lines = int(_config.get("max_lines", 500))
        if self._line_count > max_lines:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                self._line_count - max_lines,
            )
            cursor.removeSelectedText()
            self._line_count = max_lines

        scrollbar = self._log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_clear_clicked(self):
        self._log_area.clear()
        self._line_count = 0
        self._status.showMessage("Log pulito.")

    def _on_filter_changed(self, level: str):
        self._filter_level = level
        self._status.showMessage(f"Filtro: {level}")


# ---------------------------------------------------------------------------
# Module-level window reference
# ---------------------------------------------------------------------------

_window: LogViewerWindow | None = None


def _invoke(slot_name: str, *args):
    if _window is None:
        return
    q_args = [Q_ARG(type(a), a) for a in args]
    QMetaObject.invokeMethod(_window, slot_name, Qt.ConnectionType.QueuedConnection, *q_args)


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return

    log.info(f"system.start priority={PRIORITY} — log_viewer ready")
    cfg.get(defaults=_DEFAULTS)
    _invoke("set_status", "Sistema pronto. In ascolto log…")

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — log_viewer online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — log_viewer exiting")
    _invoke("set_status", "Sistema in arresto…")
    bus.stop()
    if _app:
        _app.quit()


def on_log_entry(topic: str, payload: dict) -> None:
    raw_ts  = payload.get("ts", time.time())
    module  = str(payload.get("module", "unknown"))
    level   = str(payload.get("level", "INFO")).upper()
    message = str(payload.get("message", ""))
    ts_str  = datetime.fromtimestamp(raw_ts).strftime("%H:%M:%S.%f")[:-3]

    _invoke("append_log_line", ts_str, module, level, message)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_app: QApplication | None = None


def run() -> None:
    global _app, _window

    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)
    bus.subscribe("log.entry",           on_log_entry)

    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()

    _app = QApplication(sys.argv)
    _window = LogViewerWindow()
    _window.apply_default_geometry(_app)  # right half, full height
    _window.show()

    log.info("log_viewer window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
