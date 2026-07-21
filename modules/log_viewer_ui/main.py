"""
NemoHeadUnit-Wireless v2 — log_viewer module

Standalone PyQt6 window that displays log entries published on the bus
in real-time by any module.

UI Architecture note (standalone exception):
  log_viewer is a standalone developer utility, not a dashboard widget.
  It is explicitly exempt from the ui_shell compositor routing:
    - Does NOT publish ui.widget.register
    - Does NOT subscribe to ui.widget.geometry
    - Operates as an independent decorated window
  The setGeometry() call in apply_default_geometry() is an accepted
  exception for this developer-only window.

Design System compliance:
  - _LEVEL_COLORS use design system token values (UI_DESIGN_SYSTEM.md)
  - QTextEdit stylesheet uses design system surface/border tokens
  - Font: DM Mono (--font-mono) for log area, DM Sans for labels

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

import signal
import sys
import time
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))
if str(_PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROTO_ROOT))

from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QMetaObject, Q_ARG       # noqa: E402
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont, QPainter, QImage  # noqa: E402

from PyQt6.QtWidgets import (                                         # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QStatusBar, QFrame,
)
from shared.touch_widgets import TouchComboBox

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_schema import field_int     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "log_viewer_ui"
PRIORITY    = 4  # UI level


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

# Design system tokens (UI_DESIGN_SYSTEM.md) — applied to level indicators
# Using exact token hex values from the palette table.
_LEVEL_COLORS: dict[str, str] = {
    "DEBUG":    "#757575",   # Muted gray
    "INFO":     "#121212",   # Black-ish text
    "WARNING":  "#b26a00",   # Dark sand/orange for high contrast
    "ERROR":    "#d32f2f",   # Danger red
    "CRITICAL": "#d32f2f",   # Danger red
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
    _invoke("render_to_shm")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class LogViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool                 # hidden from taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if hasattr(Qt.WidgetAttribute, "WA_DontShowOnScreen"):
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setWindowTitle("Log Viewer — NemoHeadUnit v2")
        self._line_count  = 0
        self._filter_level = "ALL"
        self._build_ui()
        self._apply_design_tokens()
        self.hide()  # hidden until geometry is applied

        self._shm_engine = None

    @pyqtSlot()
    def apply_pending_geometry(self) -> None:
        """No-arg slot: reads latest geometry from _pending_geometry and applies it."""
        with _pending_geometry_lock:
            pending = _pending_geometry
        if pending is not None:
            x, y, w, h = pending
            self.apply_geometry_slot(x, y, w, h)

    @pyqtSlot(int, int, int, int)
    def apply_geometry_slot(self, x: int, y: int, w: int, h: int) -> None:
        self.setGeometry(x, y, w, h)
        needs_rebuild = (
            self._shm_engine is None
            or w > self._shm_engine.max_width
            or h > self._shm_engine.max_height
        )
        if needs_rebuild:
            if self._shm_engine is not None:
                self._shm_engine.cleanup()
            from shared.shm_helper import OffscreenWidgetEngine
            self._shm_engine = OffscreenWidgetEngine(
                MODULE_NAME, w, h, bus=bus, max_width=w, max_height=h
            )
        else:
            self._shm_engine.resize(w, h)
        self._apply_design_tokens()
        self.render_to_shm()

    @pyqtSlot(bool)
    def set_visible_slot(self, visible: bool) -> None:
        if visible:
            self.show()
            self.raise_()
        else:
            self.hide()
        self.render_to_shm()

    @pyqtSlot()
    def render_to_shm(self) -> None:
        if self._shm_engine is None:
            return
        self._shm_engine.render_and_swap(self)

    @pyqtSlot()
    def handle_frame_ack(self) -> None:
        if self._shm_engine is not None:
            self._shm_engine.on_swap_ack()
            if self._shm_engine.needs_redraw:
                self.render_to_shm()

    @pyqtSlot(dict)
    def handle_input(self, payload: dict) -> None:
        from shared.shm_helper import inject_input_event
        inject_input_event(self, payload)
        QApplication.processEvents()
        self.render_to_shm()

    def closeEvent(self, event) -> None:
        if self._shm_engine is not None:
            self._shm_engine.cleanup()
        super().closeEvent(event)

    def _apply_design_tokens(self) -> None:
        """Apply dynamic design tokens stylesheet scaling for inputs, card panels, and console."""
        df = _dpi_factor
        font_size = int(14 * df)
        font_size_mono = int(10 * df)
        card_radius = int(12 * df)
        console_radius = int(16 * df)
        input_radius = int(8 * df)
        btn_radius = int(20 * df)

        # Scrollbar tokens
        scrollbar_w = int(12 * df)
        scrollbar_r = int(6 * df)
        scrollbar_h_min = int(24 * df)

        # ComboBox tokens
        combo_arrow_w = int(24 * df)
        arrow_size = int(5 * df)
        arrow_size_h = int(7 * df)
        arrow_margin = int(8 * df)
        item_h = int(32 * df)
        
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                font-family: 'DM Sans', sans-serif;
                font-size: {font_size}px;
                background-color: #f5f5f5; /* --color-surface */
                color: #121212;            /* --color-text */
            }}
            QFrame#toolbar_card {{
                background-color: #ffffff; /* Elevated white card background */
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: {card_radius}px;
            }}
            QFrame#toolbar_card QLabel {{
                background-color: transparent;
            }}
            QTextEdit {{
                background-color: #ffffff; /* White background for maximum log contrast */
                color: #121212;
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: {console_radius}px;
                font-family: 'DM Mono', monospace;
                font-size: {font_size_mono}px;
                padding: {int(6 * df)}px;
            }}
            TouchComboBox, QComboBox {{
                background-color: #ffffff; /* White combobox background */
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: {input_radius}px;
                color: #121212;
                padding: {int(6 * df)}px {int(12 * df)}px;
                min-height: {int(32 * df)}px;
                text-align: left;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: {combo_arrow_w}px;
                border-left: none;
            }}
            QComboBox::down-arrow {{
                width: 0;
                height: 0;
                border-left: {arrow_size}px solid transparent;
                border-right: {arrow_size}px solid transparent;
                border-top: {arrow_size_h}px solid #1976d2;
                margin-right: {arrow_margin}px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #f5f5f5;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: {input_radius}px;
                selection-background-color: #1976d2;
                selection-color: #ffffff;
                padding: {int(4 * df)}px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: {item_h}px;
                padding: {int(4 * df)}px;
            }}
            QPushButton {{
                background-color: #e9e9e9;
                color: #121212;
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: {btn_radius}px;
                padding: {int(5 * df)}px {int(12 * df)}px;
            }}
            QPushButton:hover {{
                background-color: #1976d2;
                color: #ffffff;
            }}
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: {scrollbar_w}px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: #b0b0b0;
                border-radius: {scrollbar_r}px;
                min-height: {scrollbar_h_min}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #1976d2;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                border: none;
                background: none;
                height: 0px;
            }}
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {{
                border: none;
                background: none;
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
            QScrollBar:horizontal {{
                border: none;
                background: transparent;
                height: {scrollbar_w}px;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: #b0b0b0;
                border-radius: {scrollbar_r}px;
                min-width: {scrollbar_h_min}px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: #1976d2;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                border: none;
                background: none;
                width: 0px;
            }}
            QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {{
                border: none;
                background: none;
                width: 0px;
            }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: none;
            }}
            QStatusBar {{
                background-color: #f5f5f5;
                color: #5f6368;
            }}
        """)

        # Scale layout spacing and margins
        if self.centralWidget() and self.centralWidget().layout():
            layout = self.centralWidget().layout()
            layout.setContentsMargins(int(10 * df), int(10 * df), int(10 * df), int(10 * df))
            layout.setSpacing(int(8 * df))
            
        if hasattr(self, "_toolbar_layout"):
            self._toolbar_layout.setContentsMargins(int(12 * df), int(8 * df), int(12 * df), int(8 * df))
            self._toolbar_layout.setSpacing(int(12 * df))
            
        if hasattr(self, "_btn_clear"):
            self._btn_clear.setMinimumHeight(int(34 * df))
        if hasattr(self, "_combo_level"):
            self._combo_level.setMinimumWidth(int(110 * df))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(10, 10, 10, 10)

        # Toolbar Card container
        self._toolbar_card = QFrame()
        self._toolbar_card.setObjectName("toolbar_card")
        self._toolbar_layout = QHBoxLayout(self._toolbar_card)
        self._toolbar_layout.setContentsMargins(12, 8, 12, 8)
        self._toolbar_layout.setSpacing(12)

        self._toolbar_layout.addWidget(QLabel("Filtro livello:"))
        self._combo_level = TouchComboBox()
        self._combo_level.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self._combo_level.currentTextChanged.connect(self._on_filter_changed)
        self._combo_level.setMinimumWidth(110)
        self._toolbar_layout.addWidget(self._combo_level)

        self._toolbar_layout.addStretch()

        self._btn_clear = QPushButton("🗑  Clear")
        self._btn_clear.setMinimumHeight(34)
        self._btn_clear.clicked.connect(self._on_clear_clicked)
        self._toolbar_layout.addWidget(self._btn_clear)

        root.addWidget(self._toolbar_card)

        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
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
        self.render_to_shm()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def set_status(self, message: str):
        self._status.showMessage(message)
        self.render_to_shm()

    def _on_clear_clicked(self):
        self._log_area.clear()
        self._line_count = 0
        self._status.showMessage("Log pulito.")
        self.render_to_shm()

    def _on_filter_changed(self, level: str):
        self._filter_level = level
        self._status.showMessage(f"Filtro: {level}")
        self.render_to_shm()



# ---------------------------------------------------------------------------
# Module-level window / app references
# ---------------------------------------------------------------------------

_window: LogViewerWindow | None = None
_app:    QApplication    | None = None
_system_start_event = threading.Event()

_shell_ready = False
_geometry_set = False
_dpi_factor: float = 1.0
_pending_geometry: tuple[int, int, int, int] | None = None
_pending_geometry_lock = threading.Lock()


def _invoke(slot_name: str, *args):
    if _window is None:
        return
    try:
        q_args = [Q_ARG(type(a), a) for a in args]
        QMetaObject.invokeMethod(_window, slot_name, Qt.ConnectionType.QueuedConnection, *q_args)
    except Exception as exc:
        log.warning(f"_invoke({slot_name}) failed: {exc}")


def _register() -> None:
    bus.publish("ui.widget.register", {
        "name":          MODULE_NAME,
        "z_order":       2,
        "dock":          "center",
        "width":         None,
        "min_width":     None,
        "max_width":     None,
        "height":        None,
        "min_height":    None,
        "max_height":    None,
        "aspect_ratio":  None,
        "on_request":    True,
        "menu_order":    3,
        "icon":          "📝",
    })
    log.info("ui.widget.register published (on_request)")


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_ui_shell_ready(topic: str, payload: dict) -> None:
    global _shell_ready
    _shell_ready = True
    log.info("ui.shell.ready received — registering log_viewer_ui")
    _register()


def on_widget_geometry(topic: str, payload: dict) -> None:
    global _geometry_set, _dpi_factor
    if payload.get("name") != MODULE_NAME:
        return
    df = float(payload.get("dpi_factor", 1.0))
    if df > 0:
        _dpi_factor = df
    x, y, w, h = int(payload["x"]), int(payload["y"]), int(payload["w"]), int(payload["h"])
    log.info(f"Geometry received: x={x} y={y} w={w} h={h} dpi={df}")
    _geometry_set = True

    with _pending_geometry_lock:
        global _pending_geometry
        _pending_geometry = (x, y, w, h)

    if _window is not None:
        try:
            QMetaObject.invokeMethod(_window, "apply_pending_geometry",
                                     Qt.ConnectionType.QueuedConnection)
        except Exception as exc:
            log.warning(f"on_widget_geometry dispatch failed: {exc}")
    else:
        log.debug("Geometry queued (Qt not ready yet)")


def on_module_open(topic: str, payload: dict) -> None:
    if payload.get("name") != MODULE_NAME:
        return
    log.info("ui.module.open received — showing log_viewer_ui")
    _invoke("set_visible_slot", True)


def on_module_close(topic: str, payload: dict) -> None:
    if payload.get("name") != MODULE_NAME:
        return
    log.info("ui.module.close received — hiding log_viewer_ui")
    _invoke("set_visible_slot", False)


def on_input_event(topic: str, payload: dict) -> None:
    if _window is None:
        return
    _invoke("handle_input", payload)


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

    _system_start_event.set()

    if _shell_ready:
        log.info("ui.shell.ready already received — registering immediately")
        _register()


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — log_viewer exiting")
    bus.publish("ui.widget.unregister", {"name": MODULE_NAME})
    if _window is not None:
        _invoke("set_status", "Sistema in arresto…")
    bus.stop()
    if _app is not None:
        QMetaObject.invokeMethod(_app, "quit", Qt.ConnectionType.QueuedConnection)


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
# Qt Thread entry point
# ---------------------------------------------------------------------------

def _run_qt() -> None:
    global _app, _window
    _app = QApplication.instance() or QApplication(sys.argv)
    _window = LogViewerWindow()

    # Apply any geometry that arrived before this thread was ready.
    def _apply_pending() -> None:
        with _pending_geometry_lock:
            pending = _pending_geometry
        if pending is not None:
            log.debug(f"Applying pending geometry: {pending}")
            if _window is not None:
                _window.apply_geometry_slot(*pending)

    QTimer.singleShot(0, _apply_pending)

    # QTimer drives the buffer flush on the Qt main thread.
    flush_timer = QTimer()
    flush_timer.setInterval(LOG_FLUSH_INTERVAL_MS)
    flush_timer.timeout.connect(_window.flush_log_buffer)
    flush_timer.start()
    _window.flush_timer = flush_timer  # keep reference alive

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _app.exec()

    log.info("Qt event loop exited, cleaning up log viewer UI resources...")
    flush_timer.stop()
    if _window is not None:
        if _window._shm_engine is not None:
            _window._shm_engine.cleanup()
        _window.close()
        _window = None
    _app = None


def on_widget_frame_ack(topic: str, payload: dict) -> None:
    _invoke("handle_frame_ack")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    bus.subscribe("ui.shell.ready",             on_ui_shell_ready)
    bus.subscribe("ui.widget.geometry",         on_widget_geometry)
    bus.subscribe("ui.module.open",             on_module_open)
    bus.subscribe("ui.module.close",            on_module_close)
    bus.subscribe(f"input.event.{MODULE_NAME}", on_input_event)
    bus.subscribe(f"ui.widget.frame_ack.{MODULE_NAME}", on_widget_frame_ack)

    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        _system_start_event.wait()
        import gc
        gc.collect()
        gc.set_threshold(50000, 10, 10)
        _run_qt()
        if bus_thread is not None:
            bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()

