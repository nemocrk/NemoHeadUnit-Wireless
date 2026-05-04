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

import sys
import time
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

for _p in (str(_V2), str(_MODULES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot           # noqa: E402
from PyQt6.QtWidgets import (                                         # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTabWidget, QLabel,
    QStatusBar, QMessageBox,
)

from shared.bus_client import BusClient              # noqa: E402
from shared.logger import get_logger                 # noqa: E402
from v2.modules.config_ui.module_tab import ModuleConfigTab  # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "config_ui"
PRIORITY    = 2  # UI level

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)


def _request_config(module: str) -> None:
    bus.publish("config.get", {"module": module, "requester": MODULE_NAME})


# ---------------------------------------------------------------------------
# ConfigWindow
# ---------------------------------------------------------------------------

class ConfigWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Configurazione moduli - NemoHeadUnit v2")
        self._tabs: dict[str, ModuleConfigTab] = {}
        self._build_ui()

    def apply_default_geometry(self, app: QApplication) -> None:
        screen = app.primaryScreen().availableGeometry()
        w = screen.width() // 2
        h = screen.height() // 2
        x = screen.x()
        y = screen.y() + h
        self.setGeometry(x, y, w, h)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 0)
        self._btn_refresh_all = QPushButton("Aggiorna lista moduli")
        self._btn_refresh_all.clicked.connect(self._on_refresh_all)
        toolbar.addWidget(self._btn_refresh_all)
        toolbar.addStretch()
        self._btn_shutdown = QPushButton("Spegni sistema")
        self._btn_shutdown.setStyleSheet(
            "QPushButton { color: #cc3333; font-weight: bold; }"
            "QPushButton:hover { background-color: #3a1a1a; }"
        )
        self._btn_shutdown.setMinimumHeight(32)
        self._btn_shutdown.clicked.connect(self._on_shutdown_clicked)
        toolbar.addWidget(self._btn_shutdown)
        root.addLayout(toolbar)

        self._tab_widget = QTabWidget()
        root.addWidget(self._tab_widget, stretch=1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("In attesa di system.start...")

    @pyqtSlot(str)
    def set_status(self, message: str) -> None:
        self._status.showMessage(message)

    @pyqtSlot(str, int, str)
    def add_or_update_module_tab(self, name: str, pid: int, status: str) -> None:
        if name in self._tabs:
            self._tabs[name].update_status(pid, status)
            return
        tab = ModuleConfigTab(name, pid, status)
        self._tabs[name] = tab
        self._tab_widget.addTab(tab, name)
        _request_config(name)

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

    def _on_refresh_all(self) -> None:
        bus.publish("system.get_modules", {})
        self.set_status("Aggiornamento lista moduli...")

    def _on_shutdown_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "Conferma spegnimento",
            "Vuoi davvero spegnere il sistema?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log.info("Shutdown requested by user")
        bus.publish("system.shutdown", {})
        self.set_status("Segnale di spegnimento inviato...")


# ---------------------------------------------------------------------------
# Module-level window reference
# ---------------------------------------------------------------------------

_window: "ConfigWindow | None" = None
_app:    "QApplication | None" = None


def _invoke(slot: str, *args) -> None:
    if _window is None:
        return
    q_args = [Q_ARG(type(a), a) for a in args]
    QMetaObject.invokeMethod(_window, slot, Qt.ConnectionType.QueuedConnection, *q_args)


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received - announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} - requesting module list")
    _invoke("set_status", "Sistema pronto. Recupero lista moduli...")
    bus.publish("system.get_modules", {})
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published - config_ui online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received")
    _invoke("set_status", "Sistema in arresto...")
    bus.stop()
    if _app:
        QMetaObject.invokeMethod(_app, "quit", Qt.ConnectionType.QueuedConnection)


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
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    global _app, _window

    bus.subscribe("system.readytostart",     on_system_readytostart)
    bus.subscribe("system.start",            on_system_start)
    bus.subscribe("system.stop",             on_system_stop)
    bus.subscribe("system.modules_response", on_modules_response)
    bus.subscribe("config.response",         on_config_response)
    bus.subscribe("config.error",            on_config_error)

    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()

    _app    = QApplication(sys.argv)
    _window = ConfigWindow()
    _window.apply_default_geometry(_app)
    _window.show()

    log.info("config_ui window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
