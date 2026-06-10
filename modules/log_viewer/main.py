"""
NemoHeadUnit-Wireless v2 — log_viewer module

Standalone PyQt6 window that displays log entries published on the bus
in real-time by any module.

Performance note:
  on_log_entry() accumulates records in a thread-safe deque.
  A QTimer fires every LOG_FLUSH_INTERVAL_MS ms on the Qt main thread
  and drains the deque with a single bulk-append into the QTextEdit.
  This replaces the previous per-record QMetaObject.invokeMethod() call,
  which caused UI stalls under high log throughput (12+ concurrent modules).

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
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from PyQt6.QtCore import Qt, QTimer, pyqtSlot                        # noqa: E402
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont  # noqa: E402
from PyQt6.QtWidgets import (                                         # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QStatusBar,
)

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_schema import field_int     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "log_viewer"
PRIORITY    = 2  # UI level

# Flush interval: drain the record buffer and update the QTextEdit every N ms.
# 250 ms gives ~4 redraws/s which is visually responsive without thrashing Qt.
LOG_FLUSH_INTERVAL_MS = 250

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "max_lines": field_int(default=500, min=50, max=10000),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

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

_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# ---------------------------------------------------------------------------
# Thread-safe record buffer
# ---------------------------------------------------------------------------
# on_log_entry() (bus thread) appends here.
# _flush_buffer()  (Qt main thread, via QTimer) drains and renders.

_record_buffer: deque[tuple[str, str, str, str]] = deque()
_buffer_lock: Lock = Lock()

# ---------------------------------------------------------------------------
# Config callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged
    log.info(f"Config loaded: {_config}")

    bus.subscribe("log.entry",           on_log_entry)

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — log_viewer online")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        return
    if isinstance(value, (dict, list)):
        log.warning(f"config.changed: structural value for '{key}' rejected")
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
        self._line_count  = 0
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

    # ------------------------------------------------------------------
    # Timer-driven flush (Qt main thread only)
    # ------------------------------------------------------------------

    @pyqtSlot()
    def flush_log_buffer(self) -> None:
        """Drain the record buffer and bulk-append lines to the QTextEdit.

        Called by a QTimer every LOG_FLUSH_INTERVAL_MS ms on the Qt thread,
        so no QMetaObject.invokeMethod() is needed per record.
        """
        with _buffer_lock:
            if not _record_buffer:
                return
            batch = list(_record_buffer)
            _record_buffer.clear()

        max_lines = int(_config.get("max_lines", 500))

        # Apply level filter and build text+format pairs
        cursor = self._log_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        appended = 0
        for ts, module, level, message in batch:
            if self._filter_level != "ALL":
                try:
                    if _LEVEL_ORDER.index(level) < _LEVEL_ORDER.index(self._filter_level):
                        continue
                except ValueError:
                    pass

            color = _LEVEL_COLORS.get(level, "#d4d4d4")
            line  = f"[{ts}] [{module:>20}] [{level:<8}] {message}"

            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cursor.setCharFormat(fmt)
            cursor.insertText(line + "\n")
            appended += 1

        self._line_count += appended

        # Trim excess lines
        if self._line_count > max_lines:
            excess = self._line_count - max_lines
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                excess,
            )
            cursor.removeSelectedText()
            self._line_count = max_lines

        scrollbar = self._log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def set_status(self, message: str):
        self._status.showMessage(message)

    def _on_clear_clicked(self):
        self._log_area.clear()
        self._line_count = 0
        self._status.showMessage("Log pulito.")

    def _on_filter_changed(self, level: str):
        self._filter_level = level
        self._status.showMessage(f"Filtro: {level}")


# ---------------------------------------------------------------------------
# Module-level window / app references
# ---------------------------------------------------------------------------

_window: LogViewerWindow | None = None
_app:    QApplication    | None = None

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
    cfg.get(schema=_SCHEMA)
    if _window is not None:
        _window.set_status("Sistema pronto. In ascolto log…")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — log_viewer exiting")
    if _window is not None:
        _window.set_status("Sistema in arresto…")
    bus.stop()
    if _app is not None:
        from PyQt6.QtCore import QMetaObject, Qt as _Qt
        QMetaObject.invokeMethod(_app, "quit", _Qt.ConnectionType.QueuedConnection)


def on_log_entry(topic: str, payload: dict) -> None:
    """Bus callback — runs on the bus thread.

    Records are appended to the shared deque and rendered in bulk by
    LogViewerWindow.flush_log_buffer() on the Qt main thread every
    LOG_FLUSH_INTERVAL_MS ms.
    """
    raw_ts  = payload.get("ts", time.time())
    module  = str(payload.get("module", "unknown"))
    level   = str(payload.get("level",  "INFO")).upper()
    message = str(payload.get("message", ""))
    ts_str  = datetime.fromtimestamp(raw_ts).strftime("%H:%M:%S.%f")[:-3]

    with _buffer_lock:
        _record_buffer.append((ts_str, module, level, message))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    global _app, _window

    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()

    _app = QApplication(sys.argv)
    _window = LogViewerWindow()
    _window.apply_default_geometry(_app)
    _window.show()

    # QTimer drives the buffer flush on the Qt main thread.
    flush_timer = QTimer()
    flush_timer.setInterval(LOG_FLUSH_INTERVAL_MS)
    flush_timer.timeout.connect(_window.flush_log_buffer)
    flush_timer.start()

    log.info("log_viewer window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
