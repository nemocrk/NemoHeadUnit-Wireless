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
                                          schema: {key: {type, ...}, ...} (optional)}
                                          ← only processed when requester == "config_ui"
                config.error             {module, key, value, reason}
  Publishes   : system.module_ready       {name, priority}
                system.ready              {name, priority}
                system.get_modules       {}
                config.get               {module: str, requester: "config_ui"}
                config.set               {module: str, key: str, value: any}
                system.shutdown          {}  ← triggered by the shutdown button

Flow:
  1. system.readytostart → publish system.module_ready
  2. system.start (priority==2) → publish system.ready + system.get_modules
  3. system.modules_response → build one tab per module,
                               publish config.get {module, requester} for each
  4. config.response (requester=="config_ui") → populate the tab with typed widgets
  5. User edits + clicks Save → publish config.set for each changed key
  6. config.error → show inline error badge next to the offending field
  7. User clicks Shutdown → publish system.shutdown {}

Widget selection by schema type
--------------------------------
  string              → QLineEdit
  int  (no bounds)    → QLineEdit + −/+ buttons
  int  (with bounds)  → QSlider (horizontal) + value label
  float (no bounds)   → QLineEdit + −/+ buttons (step 0.1)
  float (with bounds) → QSlider (horizontal, ×100 int mapping) + value label
  enum                → QComboBox
  bool                → QCheckBox
  message/list/oneof  → QLineEdit (read-only, raw value; deep editing not yet supported)
  (no schema)         → QLineEdit  (backward-compatible fallback)
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
    QFormLayout, QStatusBar, QFrame, QMessageBox, QComboBox,
    QSlider, QSpinBox, QDoubleSpinBox, QCheckBox,
)

from shared.bus_client import BusClient              # noqa: E402
from shared.logger import get_logger                 # noqa: E402
from shared.config_schema import (                   # noqa: E402
    ConfigFieldSchema,
    schema_from_dict,
)

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "config_ui"
PRIORITY    = 2  # UI level

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)


def _request_config(module: str):
    bus.publish("config.get", {"module": module, "requester": MODULE_NAME})


# ---------------------------------------------------------------------------
# Typed field widget factory
# ---------------------------------------------------------------------------

_BOOL_TRUE = {"true", "1", "yes", "on"}


class _FieldWidget(QWidget):
    """
    Container that wraps the appropriate Qt widget for a config field and
    exposes a uniform get_value() / set_value() interface.

    Accepts only ConfigFieldSchema (scalar) or None for field_schema.
    Structured nodes (ConfigFieldMessage / ConfigFieldList / ConfigFieldOneof)
    must be normalised to None by the caller — they are rendered as a
    read-only QLineEdit showing the raw value until deep editing is supported.
    """

    def __init__(self, key: str, raw_value, field_schema: "ConfigFieldSchema | None"):
        super().__init__()
        self._key    = key
        self._schema = field_schema
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._error_lbl = QLabel()
        self._error_lbl.setStyleSheet("color: #cc3333; font-size: 11px;")
        self._error_lbl.setVisible(False)

        if field_schema is None:
            self._build_string(str(raw_value) if raw_value is not None else "")
        elif field_schema.type == "bool":
            self._build_bool(raw_value)
        elif field_schema.type == "enum":
            self._build_enum(raw_value, field_schema.choices)
        elif field_schema.type == "int":
            self._build_int(raw_value, field_schema.min, field_schema.max)
        elif field_schema.type == "float":
            self._build_float(raw_value, field_schema.min, field_schema.max)
        else:  # string or unknown scalar
            self._build_string(str(raw_value) if raw_value is not None else "")

        self._layout.addWidget(self._error_lbl)

    # ---- builders --------------------------------------------------------

    def _build_string(self, value: str):
        self._widget_type = "lineedit"
        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText("(vuoto)")
        self._layout.addWidget(self._edit)

    def _build_bool(self, value):
        self._widget_type = "checkbox"
        # Accept bool, int, or string truthy values
        if isinstance(value, bool):
            checked = value
        elif isinstance(value, int):
            checked = bool(value)
        else:
            checked = str(value).strip().lower() in _BOOL_TRUE
        self._checkbox = QCheckBox()
        self._checkbox.setChecked(checked)
        self._layout.addWidget(self._checkbox)

    def _build_enum(self, value, choices: list[str]):
        self._widget_type = "combobox"
        self._combo = QComboBox()
        self._combo.addItems(choices)
        if str(value) in choices:
            self._combo.setCurrentText(str(value))
        self._layout.addWidget(self._combo)

    def _build_int(self, value, min_v, max_v):
        try:
            int_val = int(value)
        except (TypeError, ValueError):
            int_val = 0

        if min_v is not None and max_v is not None:
            # Slider mode
            self._widget_type = "int_slider"
            self._slider = QSlider(Qt.Orientation.Horizontal)
            self._slider.setMinimum(int(min_v))
            self._slider.setMaximum(int(max_v))
            self._slider.setValue(int_val)
            self._val_lbl = QLabel(str(int_val))
            self._val_lbl.setMinimumWidth(36)
            self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._slider.valueChanged.connect(
                lambda v: self._val_lbl.setText(str(v))
            )
            self._layout.addWidget(self._slider, stretch=1)
            self._layout.addWidget(self._val_lbl)
        else:
            # LineEdit + −/+ buttons
            self._widget_type = "int_step"
            self._edit = QLineEdit(str(int_val))
            self._edit.setFixedWidth(80)
            btn_minus = QPushButton("−")
            btn_plus  = QPushButton("+")
            for btn in (btn_minus, btn_plus):
                btn.setFixedWidth(28)
            btn_minus.clicked.connect(lambda: self._step_int(-1))
            btn_plus.clicked.connect(lambda:  self._step_int(+1))
            self._layout.addWidget(btn_minus)
            self._layout.addWidget(self._edit)
            self._layout.addWidget(btn_plus)

    def _build_float(self, value, min_v, max_v):
        try:
            float_val = float(value)
        except (TypeError, ValueError):
            float_val = 0.0

        if min_v is not None and max_v is not None:
            # Slider mode (×100 mapping for 2-decimal precision)
            self._widget_type = "float_slider"
            self._float_min = float(min_v)
            self._float_max = float(max_v)
            self._slider = QSlider(Qt.Orientation.Horizontal)
            self._slider.setMinimum(0)
            self._slider.setMaximum(100)
            self._slider.setValue(self._float_to_slider(float_val))
            self._val_lbl = QLabel(f"{float_val:.2f}")
            self._val_lbl.setMinimumWidth(44)
            self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._slider.valueChanged.connect(
                lambda v: self._val_lbl.setText(f"{self._slider_to_float(v):.2f}")
            )
            self._layout.addWidget(self._slider, stretch=1)
            self._layout.addWidget(self._val_lbl)
        else:
            # LineEdit + −/+ buttons (step 0.1)
            self._widget_type = "float_step"
            self._edit = QLineEdit(f"{float_val:.2f}")
            self._edit.setFixedWidth(80)
            btn_minus = QPushButton("−")
            btn_plus  = QPushButton("+")
            for btn in (btn_minus, btn_plus):
                btn.setFixedWidth(28)
            btn_minus.clicked.connect(lambda: self._step_float(-0.1))
            btn_plus.clicked.connect(lambda:  self._step_float(+0.1))
            self._layout.addWidget(btn_minus)
            self._layout.addWidget(self._edit)
            self._layout.addWidget(btn_plus)

    # ---- slider <-> float helpers ----------------------------------------

    def _float_to_slider(self, v: float) -> int:
        span = self._float_max - self._float_min
        if span == 0:
            return 0
        return round((v - self._float_min) / span * 100)

    def _slider_to_float(self, pos: int) -> float:
        span = self._float_max - self._float_min
        return self._float_min + pos / 100 * span

    # ---- step helpers ----------------------------------------------------

    def _step_int(self, delta: int):
        try:
            v = int(self._edit.text()) + delta
        except ValueError:
            v = delta
        self._edit.setText(str(v))

    def _step_float(self, delta: float):
        try:
            v = round(float(self._edit.text()) + delta, 10)
        except ValueError:
            v = delta
        self._edit.setText(f"{v:.2f}")

    # ---- public interface ------------------------------------------------

    def get_value(self):
        wt = self._widget_type
        if wt == "checkbox":
            return self._checkbox.isChecked()  # returns Python bool
        if wt == "combobox":
            return self._combo.currentText()
        if wt == "int_slider":
            return self._slider.value()
        if wt == "float_slider":
            return self._slider_to_float(self._slider.value())
        # lineedit, int_step, float_step
        return self._edit.text()

    def set_error(self, message: str | None):
        if message:
            self._error_lbl.setText(f"⚠ {message}")
            self._error_lbl.setVisible(True)
        else:
            self._error_lbl.setVisible(False)


# ---------------------------------------------------------------------------
# Per-module tab widget
# ---------------------------------------------------------------------------

def _schema_type_badge(field_schema) -> str:
    """Return a human-readable HTML badge string for a schema node.

    Works for both ConfigFieldSchema (scalar) and structured nodes
    (ConfigFieldMessage / ConfigFieldList / ConfigFieldOneof) which only
    expose a class name, not a .type attribute.
    """
    if field_schema is None:
        return ""
    if isinstance(field_schema, ConfigFieldSchema):
        label = field_schema.type.upper()
    else:
        # Structured node — use the class name, strip "ConfigField" prefix
        label = type(field_schema).__name__.replace("ConfigField", "").upper()
    return f" <span style='color:#888; font-size:10px'>[{label}]</span>"


class ModuleConfigTab(QWidget):
    def __init__(self, module_name: str, pid: int, status: str):
        super().__init__()
        self._module_name = module_name
        self._original: dict = {}
        self._fields:   dict[str, _FieldWidget] = {}
        self._schema:   dict = {}   # key → AnyFieldSchema (or empty)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

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

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

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

        self._btn_save = QPushButton("💾  Salva modifiche")
        self._btn_save.setMinimumHeight(36)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save)
        root.addWidget(self._btn_save)

    def populate(self, config: dict, schema_raw: dict | None = None):
        """
        Rebuild the form with typed widgets.

        Parameters
        ----------
        config     : {key: value} dict from config.response
        schema_raw : optional plain-dict schema from config.response["schema"]

        Structured schema nodes (ConfigFieldMessage / ConfigFieldList /
        ConfigFieldOneof) are detected via isinstance(ConfigFieldSchema) check.
        They receive field_schema=None so _FieldWidget falls back to a plain
        QLineEdit, and their badge is derived from the class name instead of
        the .type attribute (which only ConfigFieldSchema exposes).
        """
        while self._form.rowCount():
            self._form.removeRow(0)
        self._fields.clear()
        self._original = dict(config)

        # Parse schema if provided
        if schema_raw:
            try:
                self._schema = schema_from_dict(schema_raw)
            except Exception as exc:
                log.warning(f"Failed to parse schema for '{self._module_name}': {exc}")
                self._schema = {}
        else:
            self._schema = {}

        if not config:
            self._form.addRow(QLabel("Nessuna configurazione trovata per questo modulo."))
            self._btn_save.setEnabled(False)
            return

        for key, value in sorted(config.items()):
            raw_schema = self._schema.get(key)  # may be None or any schema node

            # _FieldWidget only understands scalar ConfigFieldSchema.
            # Structured nodes (Message / List / Oneof) fall back to QLineEdit.
            scalar_schema = raw_schema if isinstance(raw_schema, ConfigFieldSchema) else None

            widget = _FieldWidget(key, value, scalar_schema)
            self._fields[key] = widget

            badge = _schema_type_badge(raw_schema)
            label = QLabel(f"{key}{badge}")
            label.setTextFormat(Qt.TextFormat.RichText)
            self._form.addRow(label, widget)

        self._btn_save.setEnabled(True)

    def mark_error(self, key: str, reason: str):
        """Show inline error badge on the field that failed validation."""
        widget = self._fields.get(key)
        if widget:
            widget.set_error(reason)

    def update_status(self, pid: int, status: str):
        self._lbl_pid.setText(f"PID: {pid}")
        self._lbl_status.setText(f"Stato: {status}")

    def _on_refresh(self):
        _request_config(self._module_name)

    def _on_save(self):
        # Clear previous errors
        for fw in self._fields.values():
            fw.set_error(None)

        changed = {}
        for key, fw in self._fields.items():
            new_val = fw.get_value()
            orig    = self._original.get(key)
            # Bool: compare as bool to avoid True vs "True" mismatch
            if isinstance(new_val, bool):
                orig_bool = orig if isinstance(orig, bool) else str(orig).strip().lower() in _BOOL_TRUE
                if new_val != orig_bool:
                    changed[key] = new_val
            elif str(new_val) != str(orig if orig is not None else ""):
                changed[key] = new_val

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
        self._tabs: dict[str, ModuleConfigTab] = {}
        self._build_ui()

    def apply_default_geometry(self, app: QApplication) -> None:
        """Bottom-left quarter of the primary screen."""
        screen = app.primaryScreen().availableGeometry()
        w = screen.width() // 2
        h = screen.height() // 2
        x = screen.x()
        y = screen.y() + h
        self.setGeometry(x, y, w, h)

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

        self._btn_shutdown = QPushButton("⏻  Spegni sistema")
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
        self._status.showMessage("In attesa di system.start…")

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

    @pyqtSlot(str, str, str)
    def populate_module_config(self, module: str, config_json: str, schema_json: str):
        import json
        tab = self._tabs.get(module)
        if tab is None:
            return
        config     = json.loads(config_json)
        schema_raw = json.loads(schema_json) if schema_json else None
        tab.populate(config, schema_raw)
        self.set_status(f"Configurazione caricata per '{module}'")

    @pyqtSlot(str, str, str)
    def show_config_error(self, module: str, key: str, reason: str):
        tab = self._tabs.get(module)
        if tab:
            tab.mark_error(key, reason)
        self.set_status(f"Errore di validazione: '{module}'.{key} — {reason}")

    def _on_refresh_all(self):
        bus.publish("system.get_modules", {})
        self.set_status("Aggiornamento lista moduli…")

    def _on_shutdown_clicked(self):
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
        self.set_status("Segnale di spegnimento inviato…")


# ---------------------------------------------------------------------------
# Module-level window reference
# ---------------------------------------------------------------------------

_window: ConfigWindow | None = None
_app:    QApplication | None = None


def _invoke(slot: str, *args):
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
    if payload.get("requester", "") != MODULE_NAME:
        return
    module     = payload.get("module", "")
    config     = payload.get("config", {})
    schema_raw = payload.get("schema")   # may be None
    log.info(f"config.response for '{module}': {len(config)} chiavi, schema={'sì' if schema_raw else 'no'}")
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

    _app = QApplication(sys.argv)
    _window = ConfigWindow()
    _window.apply_default_geometry(_app)  # bottom-left quarter
    _window.show()

    log.info("config_ui window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
