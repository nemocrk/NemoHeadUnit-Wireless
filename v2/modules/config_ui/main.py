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

Internal layout (post-refactor)
-------------------------------
  field_widgets.py   _FieldWidget, _ScalarListEditor
  list_editor.py     _ListEditor (accordion list<struct>), _AccordionItem
  form_builder.py    build_form_for_schema() recursive builder
  module_tab.py      ModuleConfigTab
  main.py            _AccordionItem (legacy), _ListFieldInlineEditor (legacy),
                     _build_message_form (legacy),
                     ConfigWindow, bus handlers, entrypoint

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
    QPushButton, QTabWidget, QLabel, QScrollArea,
    QFormLayout, QStatusBar, QFrame, QMessageBox, QComboBox,
    QGroupBox, QSizePolicy,
)

from shared.bus_client import BusClient              # noqa: E402
from shared.logger import get_logger                 # noqa: E402
from shared.config_schema import (                   # noqa: E402
    ConfigFieldList,
    ConfigFieldMessage,
    ConfigFieldOneof,
    ConfigFieldSchema,
    schema_from_dict,
)
from v2.modules.config_ui.field_widgets import _FieldWidget  # noqa: E402
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
# _build_message_form  (recursive, returns collect callable)
# Legacy helper used by _AccordionItem / _ListFieldInlineEditor below.
# ---------------------------------------------------------------------------

def _build_message_form(
    parent_layout: QVBoxLayout,
    schema: "ConfigFieldMessage",
    value: dict,
) -> "callable":
    """
    Populate *parent_layout* with widgets for all fields in a ConfigFieldMessage.
    Returns collect() -> dict.

    ConfigFieldOneof handling:
      - A QComboBox selects the active branch.
      - Only the active branch body widget is visible at any time.
      - On branch change the old body is destroyed and a fresh one is built.
    """
    form = QFormLayout()
    form.setSpacing(4)
    form.setContentsMargins(0, 0, 0, 0)
    collectors: list = []
    value = value or {}

    for field_name, field_node in schema.fields.items():
        field_val = value.get(field_name)

        # --- Scalar ---
        if isinstance(field_node, ConfigFieldSchema):
            w = _FieldWidget(field_name, field_val, field_node)
            form.addRow(QLabel(field_name), w)
            collectors.append((field_name, w.get_value))

        # --- Oneof ---
        elif isinstance(field_node, ConfigFieldOneof):
            branch_names = list(field_node.branches.keys())
            branch_combo = QComboBox()
            branch_combo.addItems(branch_names)

            current_branch = field_node.active_branch
            if isinstance(field_val, dict):
                for bn in branch_names:
                    if bn in field_val:
                        current_branch = bn
                        break
            if current_branch in branch_names:
                branch_combo.setCurrentText(current_branch)

            form.addRow(QLabel(f"{field_name} (tipo)"), branch_combo)

            branch_host = QWidget()
            branch_host_layout = QVBoxLayout(branch_host)
            branch_host_layout.setContentsMargins(12, 0, 0, 0)
            branch_host_layout.setSpacing(2)
            form.addRow("", branch_host)

            active_collector: list = [None]

            def _rebuild_branch(
                branch_name,
                _host_layout=branch_host_layout,
                _branches=field_node.branches,
                _field_val=field_val,
                _active_collector=active_collector,
            ):
                while _host_layout.count():
                    item = _host_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()

                branch_schema = _branches.get(branch_name)
                if branch_schema is None:
                    _active_collector[0] = lambda: {}
                    return

                bw = QWidget()
                bv = QVBoxLayout(bw)
                bv.setContentsMargins(0, 0, 0, 0)
                bv.setSpacing(2)

                branch_val = (_field_val or {}).get(branch_name, {})

                if isinstance(branch_schema, ConfigFieldMessage):
                    bc = _build_message_form(bv, branch_schema, branch_val)
                    _active_collector[0] = lambda _bc=bc, _bn=branch_name: {_bn: _bc()}
                elif isinstance(branch_schema, ConfigFieldSchema):
                    sv = (_field_val or {}).get(branch_name)
                    scalar_w = _FieldWidget(branch_name, sv, branch_schema)
                    bv.addWidget(scalar_w)
                    _active_collector[0] = lambda _sw=scalar_w, _bn=branch_name: {_bn: _sw.get_value()}
                else:
                    _active_collector[0] = lambda: {}

                _host_layout.addWidget(bw)

            _rebuild_branch(current_branch)
            branch_combo.currentTextChanged.connect(_rebuild_branch)

            def _collect_oneof(_ac=active_collector):
                return _ac[0]() if _ac[0] else {}

            collectors.append((field_name, _collect_oneof))

        # --- Nested message ---
        elif isinstance(field_node, ConfigFieldMessage):
            grp = QGroupBox(field_name)
            grp_vbox = QVBoxLayout(grp)
            grp_vbox.setContentsMargins(6, 6, 6, 6)
            sub_collect = _build_message_form(grp_vbox, field_node, field_val or {})
            form.addRow(grp)
            collectors.append((field_name, sub_collect))

        # --- Nested list (frozen count) ---
        elif isinstance(field_node, ConfigFieldList):
            count = len(field_val) if isinstance(field_val, list) else 0
            lbl = QLabel(f"{count} elementi")
            lbl.setStyleSheet("color: #888;")
            form.addRow(QLabel(field_name), lbl)
            _frozen = field_val
            collectors.append((field_name, lambda fv=_frozen: fv))

        # --- Fallback ---
        else:
            w = _FieldWidget(field_name, str(field_val) if field_val is not None else "", None)
            form.addRow(QLabel(field_name), w)
            collectors.append((field_name, w.get_value))

    parent_layout.addLayout(form)

    def collect() -> dict:
        result = {}
        for key, fn in collectors:
            val = fn() if callable(fn) else fn
            if isinstance(val, dict) and key in schema.fields and isinstance(schema.fields[key], ConfigFieldOneof):
                result[key] = val
            else:
                result[key] = val
        return result

    return collect


# ---------------------------------------------------------------------------
# _AccordionItem  — single collapsible list item
# ---------------------------------------------------------------------------

_ACCORDION_HEADER_LABEL_STYLE = "color: #e0e0e0; background: transparent;"


class _AccordionItem(QWidget):
    """
    Collapsible row representing one element of a ConfigFieldList.
    Used by _ListFieldInlineEditor.
    """

    def __init__(self, index: int, item_value, item_schema, on_delete_cb, parent=None):
        super().__init__(parent)
        self._index      = index
        self._collect_fn = None
        self._expanded   = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(0)

        header = QWidget()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setStyleSheet(
            "QWidget { background: #2a2a2a; border-radius: 4px; }"
            "QWidget:hover { background: #333; }"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 4, 8, 4)
        h_layout.setSpacing(6)

        self._arrow     = QLabel("v")
        self._arrow.setFixedWidth(14)
        self._arrow.setStyleSheet(f"{_ACCORDION_HEADER_LABEL_STYLE} font-size: 10px;")

        self._title_lbl = QLabel(f"Canale {index}")
        self._title_lbl.setStyleSheet(f"{_ACCORDION_HEADER_LABEL_STYLE} font-weight: bold;")

        btn_del = QPushButton("x")
        btn_del.setFixedSize(22, 22)
        btn_del.setStyleSheet(
            "QPushButton { color: #cc4444; background: transparent; border: none; font-weight: bold; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        btn_del.clicked.connect(on_delete_cb)

        h_layout.addWidget(self._arrow)
        h_layout.addWidget(self._title_lbl, stretch=1)
        h_layout.addWidget(btn_del)
        root.addWidget(header)

        self._body = QWidget()
        self._body.setStyleSheet(
            "QWidget { border: 1px solid #333; border-top: none; border-radius: 0 0 4px 4px; }"
        )
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 8, 12, 8)
        body_layout.setSpacing(4)

        if isinstance(item_schema, ConfigFieldMessage):
            self._collect_fn = _build_message_form(body_layout, item_schema, item_value or {})
        elif isinstance(item_schema, ConfigFieldSchema):
            w = _FieldWidget(str(index), item_value, item_schema)
            body_layout.addWidget(w)
            self._collect_fn = w.get_value
        else:
            import json as _json
            raw = item_value if isinstance(item_value, str) else _json.dumps(item_value, ensure_ascii=False)
            w = _FieldWidget(str(index), raw, None)
            body_layout.addWidget(w)
            self._collect_fn = w.get_value

        self._body.setVisible(False)
        root.addWidget(self._body)
        header.mousePressEvent = lambda _e: self._toggle()

    def set_index(self, index: int) -> None:
        self._index = index
        self._title_lbl.setText(f"Canale {index}")

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow.setText("^" if self._expanded else "v")

    def get_value(self):
        if self._collect_fn is None:
            return {}
        return self._collect_fn() if callable(self._collect_fn) else self._collect_fn


# ---------------------------------------------------------------------------
# _ListFieldInlineEditor  — inline accordion list widget
# ---------------------------------------------------------------------------

class _ListFieldInlineEditor(QWidget):
    """
    Inline widget for a ConfigFieldList key inside the module config form.
    """

    def __init__(self, field_schema: "ConfigFieldList | None", initial_value: list, parent=None):
        super().__init__(parent)
        self._field_schema = field_schema
        self._items: list[_AccordionItem] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(2)

        self._items_container = QWidget()
        self._items_vbox = QVBoxLayout(self._items_container)
        self._items_vbox.setContentsMargins(0, 0, 0, 0)
        self._items_vbox.setSpacing(2)
        root.addWidget(self._items_container)

        self._btn_add = QPushButton("+ Aggiungi canale")
        self._btn_add.setStyleSheet(
            "QPushButton { color: #4caf50; background: transparent;"
            " border: 1px dashed #4caf50; border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background: #1a2e1a; }"
        )
        self._btn_add.clicked.connect(self._on_add)
        root.addWidget(self._btn_add)

        for item_val in initial_value:
            self._append_item(item_val)

    def _item_schema(self):
        if self._field_schema is not None:
            return self._field_schema.item_schema
        return None

    def _default_item_value(self):
        if self._field_schema is None:
            return ""
        schema = self._field_schema.item_schema
        if isinstance(schema, ConfigFieldMessage):
            result = {}
            for fname, fnode in schema.fields.items():
                if isinstance(fnode, ConfigFieldSchema):
                    result[fname] = fnode.default
                elif isinstance(fnode, (ConfigFieldOneof, ConfigFieldMessage)):
                    result[fname] = {}
                else:
                    result[fname] = None
            return result
        if isinstance(schema, ConfigFieldSchema):
            return schema.default
        return {}

    def _append_item(self, item_value) -> None:
        idx = len(self._items)

        def _on_delete(_idx=idx):
            self._delete_item(_idx)

        item = _AccordionItem(
            index=idx,
            item_value=item_value,
            item_schema=self._item_schema(),
            on_delete_cb=_on_delete,
        )
        self._items.append(item)
        self._items_vbox.addWidget(item)

    def _delete_item(self, idx: int) -> None:
        if idx >= len(self._items):
            return
        item = self._items.pop(idx)
        self._items_vbox.removeWidget(item)
        item.deleteLater()
        for i, it in enumerate(self._items):
            it.set_index(i)
        self._reconnect_delete_handlers()

    def _reconnect_delete_handlers(self) -> None:
        for i, item in enumerate(self._items):
            header   = item.layout().itemAt(0).widget()
            h_layout = header.layout()
            btn_del  = h_layout.itemAt(h_layout.count() - 1).widget()
            try:
                btn_del.clicked.disconnect()
            except Exception:
                pass
            _i = i
            btn_del.clicked.connect(lambda checked=False, idx=_i: self._delete_item(idx))

    def _on_add(self) -> None:
        self._append_item(self._default_item_value())
        self._reconnect_delete_handlers()

    def get_value(self) -> list:
        return [item.get_value() for item in self._items]


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
