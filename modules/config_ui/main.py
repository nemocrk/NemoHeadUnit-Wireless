"""
NemoHeadUnit-Wireless v2 — config_ui module

Standalone PyQt6 window to browse and edit per-module configuration.

Module contract:
  Name        : config_ui
  Priority    : 2  (UI level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                system.modules_response  {modules: [{name, pid, status}, ...]}
                config.response          {module, config: {key: value, ...},
                                          requester: str,
                                          schema: {key: {type, ...}, ...} (optional)
                                          <- only processed when requester == "config_ui"
                config.error             {module, key, value, reason}
  Publishes   : system.module_ready       {name, priority}
                system.ready              {name, priority}
                system.get_modules       {}
                config.get               {module: str, requester: "config_ui"}
                config.set               {module: str, key: str, value: any}
                system.shutdown          {}  <- triggered by the shutdown button

Internal layout
---------------
  field_widgets.py   _FieldWidget, _ScalarListEditor,
                     _OneofWidget, _OptionalMessageWidget
  list_editor.py     _ListEditor (accordion list<struct|oneof>), _AccordionItem
  form_builder.py    build_form_for_schema() recursive builder,
                     build_default_value()
  module_tab.py      ModuleConfigTab
  main.py            ConfigWindow, bus handlers, entrypoint

Flow:
  1. system.readytostart -> publish system.module_ready
  2. system.start (priority==2) -> publish system.ready + system.get_modules
  3. system.modules_response -> build one tab per module,
                               publish config.get {module, requester} for each
  4. config.response (requester=="config_ui") -> populate the tab with typed widgets
  5. User edits + clicks Save -> publish config.set for each changed key
  6. config.error -> show inline error badge next to the offending field
  7. User clicks Shutdown -> publish system.shutdown {}
"""

import signal
import sys
import time
import threading
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

for _p in (str(_REPO_ROOT), str(_MODULES), str(_PROTO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.bus_client import BusClient
from shared.logger import get_logger

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "config_ui"
PRIORITY    = 4  # on_request widget priority (ui_shell at priority 2 is guaranteed ready)

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)


def _request_config(module: str) -> None:
    bus.publish("config.get", {"module": module, "requester": MODULE_NAME})


def _register() -> None:
    """Publish ui.widget.register — on_request so floating_menu_ui shows arc icon."""
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
        "menu_order":    2,
        "icon":          "⚙",
    })
    log.info("ui.widget.register published (on_request)")


# ---------------------------------------------------------------------------
# Module-level window reference
# ---------------------------------------------------------------------------

_window = None
_app = None
_system_start_event = threading.Event()

# Track whether ui.shell.ready has been received
_shell_ready = False
_dpi_factor = 1.0

# Pending geometry from bus thread (applied on Qt thread via apply_pending_geometry slot)
_pending_geometry = None
_pending_geometry_lock = threading.Lock()


def _invoke(slot: str, *args) -> None:
    global _window
    if _window is None:
        return
    try:
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        q_args = [Q_ARG(type(a), a) for a in args]
        QMetaObject.invokeMethod(_window, slot, Qt.ConnectionType.QueuedConnection, *q_args)
    except Exception as exc:
        log.warning(f"_invoke({slot}) failed: {exc}")


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received - announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_ui_shell_ready(topic: str, payload: dict) -> None:
    global _shell_ready
    _shell_ready = True
    log.info("ui.shell.ready received — registering config_ui")
    _register()


def on_widget_geometry(topic: str, payload: dict) -> None:
    global _dpi_factor
    if payload.get("name") != MODULE_NAME:
        return
    df = float(payload.get("dpi_factor", 1.0))
    if df > 0:
        _dpi_factor = df
    x, y, w, h = int(payload["x"]), int(payload["y"]), int(payload["w"]), int(payload["h"])
    log.info(f"Geometry received: x={x} y={y} w={w} h={h} dpi={df}")
    global _pending_geometry
    with _pending_geometry_lock:
        _pending_geometry = (x, y, w, h)
    if _window is not None:
        try:
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(_window, "apply_pending_geometry",
                                     Qt.ConnectionType.QueuedConnection)
        except Exception as exc:
            log.warning(f"on_widget_geometry dispatch failed: {exc}")


def on_module_open(topic: str, payload: dict) -> None:
    if payload.get("name") != MODULE_NAME:
        return
    log.info("ui.module.open received — showing config_ui")
    _invoke("set_visible_slot", True)


def on_module_close(topic: str, payload: dict) -> None:
    if payload.get("name") != MODULE_NAME:
        return
    log.info("ui.module.close received — hiding config_ui")
    _invoke("set_visible_slot", False)


def on_input_event(topic: str, payload: dict) -> None:
    if _window is None:
        return
    _invoke("handle_input", payload)


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} - requesting module list")

    bus.subscribe("system.modules_response", on_modules_response)
    bus.subscribe("config.response",         on_config_response)
    bus.subscribe("config.error",            on_config_error)

    _system_start_event.set()

    bus.publish("system.get_modules", {})
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published - config_ui online")

    if _shell_ready:
        log.info("ui.shell.ready already received — registering immediately")
        _register()


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received")
    _invoke("set_status", "Sistema in arresto...")
    bus.stop()
    global _app
    if _app:
        try:
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(_app, "quit", Qt.ConnectionType.QueuedConnection)
        except Exception as exc:
            log.warning(f"Failed to invoke quit on QApplication: {exc}")


def on_modules_response(topic: str, payload: dict) -> None:
    modules = payload.get("modules", [])
    log.info(f"system.modules_response: {len(modules)} moduli")
    for m in modules:
        _invoke(
            "add_or_update_module_tab",
            m.get("name", ""),
            int(m.get("pid", 0)),
            m.get("status", "unknown"),
        )
    _invoke("set_status", f"{len(modules)} modulo/i trovato/i.")


def on_config_response(topic: str, payload: dict) -> None:
    import json
    if payload.get("requester", "") != MODULE_NAME:
        return
    module     = payload.get("module", "")
    config     = payload.get("config", {})
    schema_raw = payload.get("schema")
    log.info(f"config.response for '{module}': {len(config)} chiavi, schema={'si' if schema_raw else 'no'}")
    _invoke(
        "populate_module_config",
        module,
        json.dumps(config),
        json.dumps(schema_raw) if schema_raw else "",
    )


def on_config_error(topic: str, payload: dict) -> None:
    module = payload.get("module", "")
    key    = payload.get("key", "")
    reason = payload.get("reason", "errore sconosciuto")
    log.warning(f"config.error for '{module}'.{key}: {reason}")
    _invoke("show_config_error", module, key, reason)


# ---------------------------------------------------------------------------
# Qt Thread entry point
# ---------------------------------------------------------------------------

def _run_qt() -> None:
    global _app, _window
    from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QTabWidget, QLabel, QStatusBar, QScrollArea, QFrame,
    )
    from PyQt6.QtGui import QPainter, QImage
    from config_ui.module_tab import ModuleConfigTab

    class ConfigWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            if hasattr(Qt.WidgetAttribute, "WA_DontShowOnScreen"):
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self.setWindowTitle("Configurazione moduli - NemoHeadUnit v2")
            self.hide()
            self._tabs: dict[str, ModuleConfigTab] = {}
            self._tab_buttons = {}
            self._shm_engine = None
            self._keyboard_overlay = None
            self._build_ui()
            self._apply_design_tokens()

        def _apply_design_tokens(self) -> None:
            df = _dpi_factor
            font_size = int(14 * df)
            font_size_small = int(11 * df)
            btn_radius = int(20 * df)
            tab_radius = int(16 * df)
            input_radius = int(8 * df)
            padding_btn = f"{int(6 * df)}px {int(12 * df)}px"

            # Scrollbar tokens
            scrollbar_w = int(12 * df)
            scrollbar_r = int(6 * df)
            scrollbar_h_min = int(24 * df)

            # Slider tokens
            groove_h = int(6 * df)
            groove_r = int(3 * df)
            handle_sz = int(24 * df)
            handle_r = handle_sz // 2
            handle_margin = - (handle_sz - groove_h) // 2

            # Checkbox tokens
            indicator_sz = int(24 * df)
            indicator_r = int(4 * df)
            checkbox_spacing = int(8 * df)

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
                QTabWidget::pane {{
                    border: none;
                    background-color: transparent;
                }}
                QTabBar::tab {{
                    background-color: transparent;
                    color: #121212;
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: {tab_radius}px;
                    padding: {int(6 * df)}px {int(16 * df)}px;
                    margin-right: {int(8 * df)}px;
                    margin-bottom: {int(8 * df)}px;
                }}
                QTabBar::tab:selected {{
                    background-color: #1976d2; /* --color-accent */
                    color: #ffffff;            /* Contrast white text on blue */
                    border: 1px solid #1976d2;
                }}
                QTabBar::tab:hover:!selected {{
                    background-color: rgba(0, 0, 0, 0.06);
                }}
                QPushButton {{
                    background-color: #e9e9e9; /* --color-surface-2 */
                    color: #121212;
                    border: 1px solid rgba(0,0,0,0.12);
                    border-radius: {btn_radius}px;
                    padding: {padding_btn};
                }}
                QPushButton:hover {{
                    background-color: #1976d2;
                    color: #ffffff;
                }}
                QPushButton:disabled {{
                    color: #9aa0a6;
                    background-color: rgba(0,0,0,0.02);
                }}
                QPushButton#btn_shutdown {{
                    color: #d32f2f;
                    font-weight: bold;
                    border: 1px solid rgba(211, 47, 47, 0.3);
                    border-radius: {btn_radius}px;
                }}
                QPushButton#btn_shutdown:hover {{
                    background-color: #fde8e8;
                    color: #d32f2f;
                }}
                QPushButton.tab_btn {{
                    background-color: transparent;
                    color: #121212;
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: {tab_radius}px;
                    padding: {int(6 * df)}px {int(16 * df)}px;
                }}
                QPushButton.tab_btn:hover {{
                    background-color: rgba(0, 0, 0, 0.06);
                }}
                QPushButton.tab_btn[selected="true"] {{
                    background-color: #1976d2;
                    color: #ffffff;
                    border: 1px solid #1976d2;
                }}
                TouchComboBox, QComboBox {{
                    background-color: #ffffff;
                    border: 1px solid rgba(0,0,0,0.12);
                    border-radius: {input_radius}px;
                    color: #121212;
                    padding: {int(6 * df)}px {int(12 * df)}px;
                    min-height: {int(32 * df)}px;
                    text-align: left;
                }}
                QLineEdit, TouchLineEdit {{
                    background-color: #ffffff;
                    border: 1px solid rgba(0,0,0,0.12);
                    border-radius: {input_radius}px;
                    color: #121212;
                    padding: {int(6 * df)}px {int(12 * df)}px;
                    min-height: {int(32 * df)}px;
                }}
                QComboBox::drop-down, TouchComboBox::drop-down {{
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: {combo_arrow_w}px;
                    border-left: none;
                }}
                QComboBox::down-arrow, TouchComboBox::down-arrow {{
                    width: 0;
                    height: 0;
                    border-left: {arrow_size}px solid transparent;
                    border-right: {arrow_size}px solid transparent;
                    border-top: {arrow_size_h}px solid #1976d2;
                    margin-right: {arrow_margin}px;
                }}
                QComboBox QAbstractItemView, TouchComboBox QAbstractItemView {{
                    background-color: #f5f5f5;
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: {input_radius}px;
                    selection-background-color: #1976d2;
                    selection-color: #ffffff;
                    padding: {int(4 * df)}px;
                }}
                QComboBox QAbstractItemView::item, TouchComboBox QAbstractItemView::item {{
                    min-height: {item_h}px;
                    padding: {int(4 * df)}px;
                }}
                QSlider:horizontal {{
                    min-height: {handle_sz + int(8 * df)}px;
                    height: {handle_sz + int(8 * df)}px;
                }}
                QSlider:vertical {{
                    min-width: {handle_sz + int(8 * df)}px;
                    width: {handle_sz + int(8 * df)}px;
                }}
                QSlider::groove:horizontal {{
                    height: {groove_h}px;
                    background: #e0e0e0;
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: {groove_r}px;
                }}
                QSlider::groove:vertical {{
                    width: {groove_h}px;
                    background: #e0e0e0;
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: {groove_r}px;
                }}
                QSlider::handle:horizontal {{
                    background: #1976d2;
                    width: {handle_sz}px;
                    height: {handle_sz}px;
                    margin-top: {handle_margin}px;
                    margin-bottom: {handle_margin}px;
                    border-radius: {handle_r}px;
                    border: none;
                }}
                QSlider::handle:vertical {{
                    background: #1976d2;
                    width: {handle_sz}px;
                    height: {handle_sz}px;
                    margin-left: {handle_margin}px;
                    margin-right: {handle_margin}px;
                    border-radius: {handle_r}px;
                    border: none;
                }}
                QSlider::handle:horizontal:hover, QSlider::handle:vertical:hover {{
                    background: #1565c0;
                }}
                QSlider::handle:horizontal:disabled, QSlider::handle:vertical:disabled {{
                    background: #b0b0b0;
                }}
                QCheckBox {{
                    spacing: {checkbox_spacing}px;
                    background: transparent;
                    min-height: {int(36 * df)}px;
                }}
                QCheckBox::indicator {{
                    width: {indicator_sz}px;
                    height: {indicator_sz}px;
                    border: 1px solid rgba(0, 0, 0, 0.2);
                    border-radius: {indicator_r}px;
                    background-color: #e0e0e0;
                }}
                QCheckBox::indicator:hover {{
                    border-color: #1976d2;
                }}
                QCheckBox::indicator:checked {{
                    background-color: #1976d2;
                    border-color: #1976d2;
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

            # Scale spacing and margins
            if self.centralWidget() and self.centralWidget().layout():
                layout = self.centralWidget().layout()
                layout.setContentsMargins(int(12 * df), int(12 * df), int(12 * df), int(12 * df))
                layout.setSpacing(int(8 * df))
            
            if hasattr(self, "_toolbar_layout"):
                self._toolbar_layout.setContentsMargins(int(8 * df), int(6 * df), int(8 * df), 0)
                self._toolbar_layout.setSpacing(int(8 * df))
                
            if hasattr(self, "_btn_refresh_all"):
                self._btn_refresh_all.setMinimumHeight(int(32 * df))
            if hasattr(self, "_btn_shutdown"):
                self._btn_shutdown.setMinimumHeight(int(32 * df))

            if hasattr(self, "_scroll_tabs"):
                self._scroll_tabs.setMaximumHeight(int(48 * df))
            if hasattr(self, "_tabs_layout"):
                self._tabs_layout.setSpacing(int(8 * df))
                self._tabs_layout.setContentsMargins(0, 0, int(8 * df), 0)

            for btn in self._tab_buttons.values():
                btn.setMinimumHeight(int(32 * df))

            for tab in self._tabs.values():
                tab.scale_layouts(df)

            if self._keyboard_overlay is not None:
                keyboard_h = int(220 * df)
                self._keyboard_spacer.setFixedHeight(keyboard_h)
                self._keyboard_overlay.setGeometry(
                    0, self.height() - keyboard_h,
                    self.width(), keyboard_h
                )

        @pyqtSlot()
        def apply_pending_geometry(self) -> None:
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
                self.hide_keyboard()
            self.render_to_shm()

        def render_to_shm(self) -> None:
            if self._shm_engine is None:
                return
            img = self._shm_engine.get_write_image()
            if img is not None:
                img.fill(0)
                p = QPainter(img)
                self.render(p)
                p.end()
                self._shm_engine.swap_and_notify()

        @pyqtSlot(dict)
        def handle_input(self, payload: dict) -> None:
            from shared.shm_helper import inject_input_event
            inject_input_event(self, payload)
            QApplication.processEvents()
            self.render_to_shm()

        def _build_ui(self) -> None:
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(0, 0, 0, 0)

            toolbar = QHBoxLayout()
            self._toolbar_layout = toolbar
            toolbar.setContentsMargins(8, 6, 8, 0)
            self._btn_refresh_all = QPushButton("Aggiorna lista moduli")
            self._btn_refresh_all.clicked.connect(self._on_refresh_all)
            toolbar.addWidget(self._btn_refresh_all)
            toolbar.addStretch()
            self._btn_shutdown = QPushButton("Spegni sistema")
            self._btn_shutdown.setObjectName("btn_shutdown")
            self._btn_shutdown.setMinimumHeight(32)
            self._btn_shutdown.clicked.connect(self._on_shutdown_clicked)
            toolbar.addWidget(self._btn_shutdown)
            root.addLayout(toolbar)

            # Create scrollable horizontal tabs bar
            self._scroll_tabs = QScrollArea()
            self._scroll_tabs.setWidgetResizable(True)
            self._scroll_tabs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._scroll_tabs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._scroll_tabs.setFrameShape(QFrame.Shape.NoFrame)
            self._scroll_tabs.setStyleSheet("background: transparent;")
            
            self._tabs_container = QWidget()
            self._tabs_container.setStyleSheet("background: transparent;")
            self._tabs_layout = QHBoxLayout(self._tabs_container)
            self._tabs_layout.setContentsMargins(0, 0, 0, 0)
            self._tabs_layout.setSpacing(int(8 * _dpi_factor))
            self._tabs_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            
            self._scroll_tabs.setWidget(self._tabs_container)
            root.addWidget(self._scroll_tabs)

            self._tab_widget = QTabWidget()
            self._tab_widget.tabBar().hide()
            self._tab_widget.currentChanged.connect(self._on_tab_changed)
            root.addWidget(self._tab_widget, stretch=1)

            # Keyboard spacer
            self._keyboard_spacer = QWidget()
            self._keyboard_spacer.setFixedHeight(0)
            root.addWidget(self._keyboard_spacer)

            self._status = QStatusBar()
            self.setStatusBar(self._status)
            self._status.showMessage("In attesa di system.start...")

        @pyqtSlot(str)
        def set_status(self, message: str) -> None:
            self._status.showMessage(message)
            self.render_to_shm()

        def _select_tab(self, index: int) -> None:
            self._tab_widget.setCurrentIndex(index)
            self._update_tab_button_styles()
            self.render_to_shm()

        def _on_tab_changed(self, index: int) -> None:
            self._update_tab_button_styles()
            self.render_to_shm()

        def _update_tab_button_styles(self) -> None:
            current_idx = self._tab_widget.currentIndex()
            for idx, btn in self._tab_buttons.items():
                is_selected = (idx == current_idx)
                btn.setProperty("selected", "true" if is_selected else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)

        @pyqtSlot(str, int, str)
        def add_or_update_module_tab(self, name: str, pid: int, status: str) -> None:
            if name in self._tabs:
                self._tabs[name].update_status(pid, status)
                self.render_to_shm()
                return
            tab = ModuleConfigTab(name, pid, status)
            self._tabs[name] = tab
            
            idx = self._tab_widget.addTab(tab, name)
            
            btn = QPushButton(name)
            btn.setProperty("class", "tab_btn")
            btn.setMinimumHeight(int(32 * _dpi_factor))
            btn.clicked.connect(lambda checked, index=idx: self._select_tab(index))
            self._tab_buttons[idx] = btn
            self._tabs_layout.addWidget(btn)
            
            _request_config(name)
            self._update_tab_button_styles()
            self.render_to_shm()

        @pyqtSlot(str, str, str)
        def populate_module_config(self, module: str, config_json: str, schema_json: str) -> None:
            import json
            tab = self._tabs.get(module)
            if tab is None:
                return
            config     = json.loads(config_json)
            schema_raw = json.loads(schema_json) if schema_json else None
            tab.populate(config, schema_raw)
            self.set_status(f"Configurazione caricata per '{module}'")

        @pyqtSlot(str, str, str)
        def show_config_error(self, module: str, key: str, reason: str) -> None:
            tab = self._tabs.get(module)
            if tab:
                tab.mark_error(key, reason)
            self.set_status(f"Errore di validazione: '{module}'.{key} - {reason}")

        @pyqtSlot(object)
        def show_keyboard(self, line_edit) -> None:
            if self._keyboard_overlay is not None:
                self._keyboard_overlay.close()
                self._keyboard_overlay = None
                
            df = _dpi_factor
            keyboard_h = int(220 * df)
            self._keyboard_spacer.setFixedHeight(keyboard_h)
            
            from shared.touch_widgets import TouchKeyboardOverlay
            self._keyboard_overlay = TouchKeyboardOverlay(self, line_edit)
            self._keyboard_overlay.setGeometry(
                0, self.height() - keyboard_h,
                self.width(), keyboard_h
            )
            self._keyboard_overlay.show()
            self._keyboard_overlay.raise_()
            
            # Find QScrollArea
            widget = line_edit
            scroll = None
            while widget is not None:
                from PyQt6.QtWidgets import QScrollArea
                if isinstance(widget, QScrollArea):
                    scroll = widget
                    break
                widget = widget.parent()
                
            if scroll is not None:
                scroll.ensureWidgetVisible(line_edit)
                
            self.render_to_shm()

        @pyqtSlot()
        def hide_keyboard(self) -> None:
            self._keyboard_spacer.setFixedHeight(0)
            if self._keyboard_overlay is not None:
                self._keyboard_overlay.close()
                self._keyboard_overlay = None
            self.render_to_shm()

        def _on_refresh_all(self) -> None:
            bus.publish("system.get_modules", {})
            self.set_status("Aggiornamento lista moduli...")

        def _on_shutdown_clicked(self) -> None:
            log.info("Shutdown requested by user (direct)")
            bus.publish("system.shutdown", {})
            self.set_status("Segnale di spegnimento inviato...")

    _app = QApplication.instance() or QApplication(sys.argv)
    _window = ConfigWindow()

    # Apply pending geometry if it arrived before window construction
    with _pending_geometry_lock:
        geom = _pending_geometry
    if geom is not None:
        _window.apply_geometry_slot(*geom)

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _app.exec()
    log.info("Qt event loop exited, cleaning up config UI resources...")
    if _window is not None:
        if _window._shm_engine is not None:
            _window._shm_engine.cleanup()
        _window.close()
        _window = None
    _app = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart",              on_system_readytostart)
    bus.subscribe("system.start",                     on_system_start)
    bus.subscribe("system.stop",                      on_system_stop)
    bus.subscribe("ui.shell.ready",                   on_ui_shell_ready)
    bus.subscribe("ui.widget.geometry",               on_widget_geometry)
    bus.subscribe("ui.module.open",                   on_module_open)
    bus.subscribe("ui.module.close",                  on_module_close)
    bus.subscribe(f"input.event.{MODULE_NAME}",       on_input_event)

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
