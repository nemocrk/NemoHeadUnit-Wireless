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
                                          requester: str}  ← only processed when
                                                              requester == "config_ui"
  Publishes   : system.module_ready       {name, priority}
                system.ready              {name, priority}
                system.get_modules       {}
                config.get               {module: str, requester: "config_ui"}
                config.set               {module: str, key: str, value: any}

Flow:
  1. system.readytostart → publish system.module_ready
  2. system.start (priority==2) → publish system.ready + system.get_modules
  3. system.modules_response → build one tab per module,
                               publish config.get {module, requester} for each
  4. config.response (requester=="config_ui") → populate the tab
  5. User edits + clicks Save → publish config.set for each changed key
"""

import sys
import threading
from pathlib import Path
import time

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

for _p in (str(_V2), str(_MODULES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot           # noqa: E402
from PyQt6.QtWidgets import (                                         # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTabWidget, QLabel, QLineEdit, QScrollArea,
    QFormLayout, QStatusBar, QFrame,
)

from shared.bus_client import BusClient   # noqa: E402
from shared.logger import get_logger      # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "config_ui"
PRIORITY    = 2  # UI level

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)


def _request_config(module: str):
    """Publish config.get with requester tag so we only handle our own responses."""
    bus.publish("config.get", {"module": module, "requester": MODULE_NAME})


# ---------------------------------------------------------------------------
# Per-module tab widget
# ---------------------------------------------------------------------------

class ModuleConfigTab(QWidget):
    """
    One tab per module.
    Shows module metadata at the top, then an editable key/value form.
    """

    def __init__(self, module_name: str, pid: int, status: str):
        super().__init__()
        self._module_name = module_name
        self._original: dict = {}   # last values received from config.response
        self._fields:   dict[str, QLineEdit] = {}  # key → input widget

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # — metadata bar
        meta = QHBoxLayout()
        meta.addWidget(QLabel(f"<b>Modulo:</b> {module_name}"))
        meta.addSpacing(24)
        self._lbl_pid    = QLabel(f"PID: {pid}")
        self._lbl_status = QLabel(f"Stato: {status}")
        meta.addWidget(self._lbl_pid)
        meta.addSpacing(12)
        meta.addWidget(self._lbl_status)
        meta.addStretch()

        btn_refresh = QPushButton("↻ Ricarica")
        btn_refresh.setFixedWidth(90)
        btn_refresh.clicked.connect(self._on_refresh)
        meta.addWidget(btn_refresh)
        root.addLayout(meta)

        # separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        # — scrollable form area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._form_container = QWidget()
        self._form = QFormLayout(self._form_container)
        self._form.setContentsMargins(4, 4, 4, 4)
        self._form.setSpacing(6)
        scroll.setWidget(self._form_container)
        root.addWidget(scroll, stretch=1)

        self._placeholder = QLabel("In attesa dei dati di configurazione…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._form.addRow(self._placeholder)

        # — save button
        self._btn_save = QPushButton("💾  Salva modifiche")
        self._btn_save.setMinimumHeight(36)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save)
        root.addWidget(self._btn_save)

    # -----------------------------------------------------------------------
    # Public slot — called from main thread via _invoke
    # -----------------------------------------------------------------------

    def populate(self, config: dict):
        """Replace the form with editable rows from the received config dict."""
        while self._form.rowCount():
            self._form.removeRow(0)
        self._fields.clear()
        self._original = dict(config)

        if not config:
            self._form.addRow(QLabel("Nessuna configurazione trovata per questo modulo."))
            self._btn_save.setEnabled(False)
            return

        for key, value in sorted(config.items()):
            edit = QLineEdit(str(value))
            edit.setPlaceholderText("(vuoto)")
            self._fields[key] = edit
            self._form.addRow(QLabel(key), edit)

        self._btn_save.setEnabled(True)

    def update_status(self, pid: int, status: str):
        self._lbl_pid.setText(f"PID: {pid}")
        self._lbl_status.setText(f"Stato: {status}")

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _on_refresh(self):
        _request_config(self._module_name)

    def _on_save(self):
        changed = {
            key: edit.text()
            for key, edit in self._fields.items()
            if edit.text() != str(self._original.get(key, ""))
        }
        if not changed:
            return
        for key, value in changed.items():
            bus.publish("config.set", {
                "module": self._module_name,
                "key":    key,
                "value":  value,
            })
        log.info(f"Saved {len(changed)} key(s) for '{self._module_name}'")
        self._original.update(changed)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ConfigWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Configurazione moduli — NemoHeadUnit v2")
        self.setMinimumSize(640, 480)

        self._tabs: dict[str, ModuleConfigTab] = {}  # module_name → tab

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 0)
        self._btn_refresh_all = QPushButton("↻ Aggiorna lista moduli")
        self._btn_refresh_all.clicked.connect(self._on_refresh_all)
        toolbar.addWidget(self._btn_refresh_all)
        toolbar.addStretch()
        root.addLayout(toolbar)

        self._tab_widget = QTabWidget()
        root.addWidget(self._tab_widget, stretch=1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("In attesa di system.start…")

    # -----------------------------------------------------------------------
    # Qt slots (main-thread safe)
    # -----------------------------------------------------------------------

    @pyqtSlot(str)
    def set_status(self, message: str):
        self._status.showMessage(message)

    @pyqtSlot(str, int, str)
    def add_or_update_module_tab(self, name: str, pid: int, status: str):
        if name in self._tabs:
            self._tabs[name].update_status(pid, status)
            return
        tab = ModuleConfigTab(name, pid, status)
        self._tabs[name] = tab
        self._tab_widget.addTab(tab, name)
        _request_config(name)

    @pyqtSlot(str, str)
    def populate_module_config(self, module: str, config_json: str):
        import json
        tab = self._tabs.get(module)
        if tab is None:
            return
        config = json.loads(config_json)
        tab.populate(config)
        self.set_status(f"Configurazione caricata per '{module}'")

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _on_refresh_all(self):
        bus.publish("system.get_modules", {})
        self.set_status("Aggiornamento lista moduli…")


# ---------------------------------------------------------------------------
# Module-level window reference
# ---------------------------------------------------------------------------

_window: ConfigWindow | None = None
_app:    QApplication | None = None


def _invoke(slot: str, *args):
    """Thread-safe dispatch from ZMQ thread to Qt main thread."""
    if _window is None:
        return
    q_args = [Q_ARG(type(a), a) for a in args]
    QMetaObject.invokeMethod(_window, slot, Qt.ConnectionType.QueuedConnection, *q_args)


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return

    log.info(f"system.start priority={PRIORITY} — requesting module list")
    _invoke("set_status", "Sistema pronto. Recupero lista moduli…")
    bus.publish("system.get_modules", {})

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — config_ui online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received")
    _invoke("set_status", "Sistema in arresto…")
    bus.stop()
    if _app:
        _app.quit()


def on_modules_response(topic: str, payload: dict) -> None:
    modules = payload.get("modules", [])
    log.info(f"system.modules_response: {len(modules)} moduli")
    for m in modules:
        name   = m.get("name", "")
        pid    = int(m.get("pid", 0))
        status = m.get("status", "unknown")
        _invoke("add_or_update_module_tab", name, pid, status)
    _invoke("set_status", f"{len(modules)} modulo/i trovato/i.")


def on_config_response(topic: str, payload: dict) -> None:
    import json
    # Ignore responses not directed at this module
    if payload.get("requester", "") != MODULE_NAME:
        return
    module = payload.get("module", "")
    config = payload.get("config", {})
    log.info(f"config.response for '{module}': {len(config)} chiavi")
    _invoke("populate_module_config", module, json.dumps(config))


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


    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()

    _app = QApplication(sys.argv)
    _window = ConfigWindow()
    _window.show()

    log.info("config_ui window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
