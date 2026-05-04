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
  list                → read-only QLabel + "Modifica" button → opens _StructuredFieldDialog
  message/oneof       → rendered recursively inside _StructuredFieldDialog
  (no schema, scalar) → QLineEdit  (backward-compatible fallback)
  (no schema, list)   → read-only QLabel summary + "Modifica" button
"""

import sys
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
    QSlider, QCheckBox, QGroupBox, QDialog, QDialogButtonBox,
    QSizePolicy,
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

_STRUCTURED_TYPES = (ConfigFieldList, ConfigFieldMessage, ConfigFieldOneof)

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
# Typed field widget factory  (scalar only)
# ---------------------------------------------------------------------------

_BOOL_TRUE = {"true", "1", "yes", "on"}


class _FieldWidget(QWidget):
    """
    Container for a single scalar config field.
    Accepts ConfigFieldSchema or None (falls back to QLineEdit).
    Structured nodes must NOT be passed here.
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
        else:
            self._build_string(str(raw_value) if raw_value is not None else "")

        self._layout.addWidget(self._error_lbl)

    def _build_string(self, value: str):
        self._widget_type = "lineedit"
        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText("(vuoto)")
        self._layout.addWidget(self._edit)

    def _build_bool(self, value):
        self._widget_type = "checkbox"
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
            self._widget_type = "int_slider"
            self._slider = QSlider(Qt.Orientation.Horizontal)
            self._slider.setMinimum(int(min_v))
            self._slider.setMaximum(int(max_v))
            self._slider.setValue(int_val)
            self._val_lbl = QLabel(str(int_val))
            self._val_lbl.setMinimumWidth(36)
            self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._slider.valueChanged.connect(lambda v: self._val_lbl.setText(str(v)))
            self._layout.addWidget(self._slider, stretch=1)
            self._layout.addWidget(self._val_lbl)
        else:
            self._widget_type = "int_step"
            self._edit = QLineEdit(str(int_val))
            self._edit.setFixedWidth(80)
            btn_minus = QPushButton("−")
            btn_plus  = QPushButton("+")
            for btn in (btn_minus, btn_plus):
                btn.setFixedWidth(28)
            btn_minus.clicked.connect(lambda: self._step_int(-1))
            btn_plus.clicked.connect(lambda: self._step_int(+1))
            self._layout.addWidget(btn_minus)
            self._layout.addWidget(self._edit)
            self._layout.addWidget(btn_plus)

    def _build_float(self, value, min_v, max_v):
        try:
            float_val = float(value)
        except (TypeError, ValueError):
            float_val = 0.0
        if min_v is not None and max_v is not None:
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
            self._widget_type = "float_step"
            self._edit = QLineEdit(f"{float_val:.2f}")
            self._edit.setFixedWidth(80)
            btn_minus = QPushButton("−")
            btn_plus  = QPushButton("+")
            for btn in (btn_minus, btn_plus):
                btn.setFixedWidth(28)
            btn_minus.clicked.connect(lambda: self._step_float(-0.1))
            btn_plus.clicked.connect(lambda: self._step_float(+0.1))
            self._layout.addWidget(btn_minus)
            self._layout.addWidget(self._edit)
            self._layout.addWidget(btn_plus)

    def _float_to_slider(self, v: float) -> int:
        span = self._float_max - self._float_min
        return 0 if span == 0 else round((v - self._float_min) / span * 100)

    def _slider_to_float(self, pos: int) -> float:
        return self._float_min + pos / 100 * (self._float_max - self._float_min)

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

    def get_value(self):
        wt = self._widget_type
        if wt == "checkbox":    return self._checkbox.isChecked()
        if wt == "combobox":    return self._combo.currentText()
        if wt == "int_slider":  return self._slider.value()
        if wt == "float_slider": return self._slider_to_float(self._slider.value())
        return self._edit.text()

    def set_error(self, message: str | None):
        if message:
            self._error_lbl.setText(f"⚠ {message}")
            self._error_lbl.setVisible(True)
        else:
            self._error_lbl.setVisible(False)


# ---------------------------------------------------------------------------
# Recursive message editor  (_MessageForm)
# ---------------------------------------------------------------------------

def _build_message_form(parent_layout: QVBoxLayout, schema: "ConfigFieldMessage", value: dict):
    """
    Populate *parent_layout* with widgets for all fields in a ConfigFieldMessage.
    Returns a callable collect() -> dict that harvests current values.
    Special handling for ConfigFieldOneof: renders a branch-selector QComboBox
    and shows/hides the active branch sub-form dynamically.
    """
    form   = QFormLayout()
    form.setSpacing(4)
    form.setContentsMargins(0, 0, 0, 0)
    collectors: list  = []   # list of (key, callable) — callable returns value
    value = value or {}

    for field_name, field_node in schema.fields.items():
        field_val = value.get(field_name)

        if isinstance(field_node, ConfigFieldSchema):
            w = _FieldWidget(field_name, field_val, field_node)
            form.addRow(QLabel(field_name), w)
            collectors.append((field_name, w.get_value))

        elif isinstance(field_node, ConfigFieldOneof):
            # Branch selector
            branch_combo = QComboBox()
            branch_names = list(field_node.branches.keys())
            branch_combo.addItems(branch_names)

            # Determine current branch from value keys
            current_branch = field_node.active_branch
            if isinstance(field_val, dict):
                for bn in branch_names:
                    if bn in field_val:
                        current_branch = bn
                        break
            if current_branch in branch_names:
                branch_combo.setCurrentText(current_branch)

            form.addRow(QLabel(f"{field_name} (tipo)"), branch_combo)

            # Sub-form container for the active branch
            branch_container = QWidget()
            branch_vbox = QVBoxLayout(branch_container)
            branch_vbox.setContentsMargins(12, 0, 0, 0)
            branch_vbox.setSpacing(2)

            branch_collectors: dict[str, list] = {}   # branch_name -> collectors
            branch_widgets:    dict[str, QWidget] = {}

            for bn, branch_schema in field_node.branches.items():
                bw = QWidget()
                bv = QVBoxLayout(bw)
                bv.setContentsMargins(0, 0, 0, 0)
                bv.setSpacing(2)
                if isinstance(branch_schema, ConfigFieldMessage):
                    branch_val = (field_val or {}).get(bn, {})
                    bc = _build_message_form(bv, branch_schema, branch_val)
                    branch_collectors[bn] = bc
                else:
                    # Scalar oneof branch
                    sv = (field_val or {}).get(bn)
                    scalar_w = _FieldWidget(bn, sv, branch_schema if isinstance(branch_schema, ConfigFieldSchema) else None)
                    bv.addWidget(scalar_w)
                    branch_collectors[bn] = scalar_w.get_value
                bw.setVisible(bn == current_branch)
                branch_vbox.addWidget(bw)
                branch_widgets[bn] = bw

            def _on_branch_changed(idx, _branch_widgets=branch_widgets, _branch_combo=branch_combo):
                selected = _branch_combo.currentText()
                for bn2, bw2 in _branch_widgets.items():
                    bw2.setVisible(bn2 == selected)

            branch_combo.currentIndexChanged.connect(_on_branch_changed)
            form.addRow("", branch_container)

            def _collect_oneof(_branch_combo=branch_combo, _branch_collectors=branch_collectors):
                bn = _branch_combo.currentText()
                c  = _branch_collectors.get(bn)
                if c is None:
                    return {}
                val = c() if callable(c) else c
                return {bn: val}

            collectors.append((field_name, _collect_oneof))

        elif isinstance(field_node, ConfigFieldMessage):
            # Nested message — recurse into a sub-group
            grp = QGroupBox(field_name)
            grp_vbox = QVBoxLayout(grp)
            grp_vbox.setContentsMargins(6, 6, 6, 6)
            sub_collect = _build_message_form(grp_vbox, field_node, field_val or {})
            form.addRow(grp)
            collectors.append((field_name, sub_collect))

        elif isinstance(field_node, ConfigFieldList):
            # Nested repeated field — show count label (deep nesting not yet supported)
            count = len(field_val) if isinstance(field_val, list) else 0
            lbl = QLabel(f"{count} elementi")
            lbl.setStyleSheet("color: #888;")
            form.addRow(QLabel(field_name), lbl)
            _frozen = field_val
            collectors.append((field_name, lambda fv=_frozen: fv))

        else:
            # Unknown node — show as plain string
            w = _FieldWidget(field_name, str(field_val) if field_val is not None else "", None)
            form.addRow(QLabel(field_name), w)
            collectors.append((field_name, w.get_value))

    parent_layout.addLayout(form)

    def collect() -> dict:
        result = {}
        for key, fn in collectors:
            result[key] = fn() if callable(fn) else fn
        return result

    return collect


# ---------------------------------------------------------------------------
# Structured field dialog  (_StructuredFieldDialog)
# ---------------------------------------------------------------------------

class _StructuredFieldDialog(QDialog):
    """
    Modal dialog for editing a ConfigFieldList value.

    Each list item is displayed in a collapsible QGroupBox.
    The item schema (ConfigFieldMessage) is used to render typed sub-widgets.
    On Accept, get_value() returns the updated list of dicts.
    """

    def __init__(self, key: str, value: list, field_schema: "ConfigFieldList", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Modifica: {key}")
        self.setMinimumSize(640, 480)
        self.setSizeGripEnabled(True)

        self._key          = key
        self._value        = list(value) if isinstance(value, list) else []
        self._field_schema = field_schema
        self._item_collectors: list = []   # one callable per list item

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        root.addWidget(QLabel(
            f"<b>{key}</b> — {len(self._value)} elementi. "
            "Le modifiche sono applicate al salvataggio."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self._items_layout = QVBoxLayout(scroll_widget)
        self._items_layout.setSpacing(6)
        self._items_layout.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(scroll_widget)
        root.addWidget(scroll, stretch=1)

        self._populate_items()

        # Add stretch so items pack to top
        self._items_layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _populate_items(self):
        self._item_collectors.clear()
        item_schema = self._field_schema.item_schema

        for idx, item_value in enumerate(self._value):
            # Build a meaningful group title
            title = self._item_title(idx, item_value)
            grp = QGroupBox(title)
            grp.setCheckable(False)
            grp_vbox = QVBoxLayout(grp)
            grp_vbox.setContentsMargins(8, 8, 8, 8)
            grp_vbox.setSpacing(4)

            if isinstance(item_schema, ConfigFieldMessage):
                collect = _build_message_form(grp_vbox, item_schema, item_value or {})
            else:
                # Scalar list
                w = _FieldWidget(str(idx), item_value, item_schema if isinstance(item_schema, ConfigFieldSchema) else None)
                grp_vbox.addWidget(w)
                collect = w.get_value

            self._item_collectors.append(collect)
            self._items_layout.addWidget(grp)

    def _item_title(self, idx: int, item: dict) -> str:
        """Build a short human-readable title for a list item group box."""
        if not isinstance(item, dict):
            return f"Elemento {idx}"
        ch_id = item.get("channel_id")
        if ch_id is not None:
            # Detect channel type from oneof key
            type_keys = [
                k for k in item
                if k not in ("channel_id",) and isinstance(item.get(k), dict)
            ]
            ch_type = type_keys[0].replace("_channel", "").replace("_", " ").title() if type_keys else ""
            return f"Canale {ch_id}  —  {ch_type}" if ch_type else f"Canale {ch_id}"
        return f"Elemento {idx}"

    def get_value(self) -> list:
        """Return the current (possibly edited) list value."""
        result = []
        for collect in self._item_collectors:
            result.append(collect() if callable(collect) else collect)
        return result


# ---------------------------------------------------------------------------
# Per-module tab widget
# ---------------------------------------------------------------------------

def _schema_type_badge(field_schema) -> str:
    if field_schema is None:
        return ""
    if isinstance(field_schema, ConfigFieldSchema):
        label = field_schema.type.upper()
    else:
        label = type(field_schema).__name__.replace("ConfigField", "").upper()
    return f" <span style='color:#888; font-size:10px'>[{label}]</span>"


def _structured_summary(value, field_schema) -> str:
    if isinstance(value, list):
        badge = _schema_type_badge(field_schema)
        return f"{len(value)} elementi{badge}"
    if isinstance(value, dict):
        badge = _schema_type_badge(field_schema)
        return f"{len(value)} campi{badge}"
    return str(value)


class ModuleConfigTab(QWidget):
    def __init__(self, module_name: str, pid: int, status: str):
        super().__init__()
        self._module_name = module_name
        self._original: dict = {}
        self._fields:   dict[str, _FieldWidget] = {}
        self._structured_values: dict[str, list] = {}  # key -> current list/dict value
        self._structured_schemas: dict[str, "ConfigFieldList"] = {}
        self._schema:   dict = {}

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
        while self._form.rowCount():
            self._form.removeRow(0)
        self._fields.clear()
        self._structured_values.clear()
        self._structured_schemas.clear()
        self._original = dict(config)

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
            raw_schema = self._schema.get(key)

            is_list_schema   = isinstance(raw_schema, ConfigFieldList)
            is_bare_list     = raw_schema is None and isinstance(value, list)
            is_other_struct  = isinstance(raw_schema, (ConfigFieldMessage, ConfigFieldOneof))
            is_bare_dict     = raw_schema is None and isinstance(value, dict)

            if is_list_schema or is_bare_list:
                # Editable list: summary label + "Modifica" button
                summary_text = _structured_summary(value, raw_schema)
                summary_lbl  = QLabel(summary_text)
                summary_lbl.setTextFormat(Qt.TextFormat.RichText)
                summary_lbl.setStyleSheet("color: #888;")

                btn_edit = QPushButton("Modifica…")
                btn_edit.setFixedWidth(90)

                if is_list_schema:
                    self._structured_schemas[key] = raw_schema

                self._structured_values[key] = list(value) if isinstance(value, list) else []

                def _make_edit_handler(_key=key, _summary_lbl=summary_lbl):
                    def _open_editor():
                        schema = self._structured_schemas.get(_key)
                        current_val = self._structured_values.get(_key, [])
                        if schema is None:
                            # No schema — show raw JSON editor
                            import json
                            dlg = _RawJsonDialog(_key, current_val, self)
                        else:
                            dlg = _StructuredFieldDialog(_key, current_val, schema, self)
                        if dlg.exec() == QDialog.DialogCode.Accepted:
                            new_val = dlg.get_value()
                            self._structured_values[_key] = new_val
                            count = len(new_val) if isinstance(new_val, list) else 0
                            _summary_lbl.setText(f"{count} elementi")
                    return _open_editor

                btn_edit.clicked.connect(_make_edit_handler(key, summary_lbl))

                row_widget = QWidget()
                row_hbox   = QHBoxLayout(row_widget)
                row_hbox.setContentsMargins(0, 0, 0, 0)
                row_hbox.setSpacing(6)
                row_hbox.addWidget(summary_lbl, stretch=1)
                row_hbox.addWidget(btn_edit)

                badge = _schema_type_badge(raw_schema)
                lbl_key = QLabel(f"{key}{badge}")
                lbl_key.setTextFormat(Qt.TextFormat.RichText)
                self._form.addRow(lbl_key, row_widget)
                continue

            if is_other_struct or is_bare_dict:
                # Non-editable structured node — read-only summary
                summary_lbl = QLabel(_structured_summary(value, raw_schema))
                summary_lbl.setTextFormat(Qt.TextFormat.RichText)
                summary_lbl.setStyleSheet("color: #888;")
                badge = _schema_type_badge(raw_schema)
                self._form.addRow(QLabel(f"{key}{badge}"), summary_lbl)
                continue

            # Scalar field
            scalar_schema = raw_schema if isinstance(raw_schema, ConfigFieldSchema) else None
            widget = _FieldWidget(key, value, scalar_schema)
            self._fields[key] = widget
            badge = _schema_type_badge(raw_schema)
            label = QLabel(f"{key}{badge}")
            label.setTextFormat(Qt.TextFormat.RichText)
            self._form.addRow(label, widget)

        self._btn_save.setEnabled(True)

    def mark_error(self, key: str, reason: str):
        widget = self._fields.get(key)
        if widget:
            widget.set_error(reason)

    def update_status(self, pid: int, status: str):
        self._lbl_pid.setText(f"PID: {pid}")
        self._lbl_status.setText(f"Stato: {status}")

    def _on_refresh(self):
        _request_config(self._module_name)

    def _on_save(self):
        for fw in self._fields.values():
            fw.set_error(None)

        changed = {}

        # Scalar fields
        for key, fw in self._fields.items():
            new_val = fw.get_value()
            orig    = self._original.get(key)
            if isinstance(new_val, bool):
                orig_bool = orig if isinstance(orig, bool) else str(orig).strip().lower() in _BOOL_TRUE
                if new_val != orig_bool:
                    changed[key] = new_val
            elif str(new_val) != str(orig if orig is not None else ""):
                changed[key] = new_val

        # Structured list fields
        for key, current_val in self._structured_values.items():
            orig = self._original.get(key)
            if current_val != orig:
                changed[key] = current_val

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
# Raw JSON fallback dialog (list fields without schema)
# ---------------------------------------------------------------------------

class _RawJsonDialog(QDialog):
    """Fallback editor for list values that have no ConfigFieldList schema."""

    def __init__(self, key: str, value, parent=None):
        super().__init__(parent)
        import json
        self.setWindowTitle(f"Modifica raw: {key}")
        self.setMinimumSize(480, 320)
        self._value = value
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"<b>{key}</b> — JSON raw (modifica con cautela)"))
        self._edit = QLineEdit()
        self._edit.setText(json.dumps(value, ensure_ascii=False))
        root.addWidget(self._edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def get_value(self):
        import json
        try:
            return json.loads(self._edit.text())
        except Exception:
            return self._value


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
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — requesting module list")
    _invoke("set_status", "Sistema pronto. Recupero lista moduli…")
    bus.publish("system.get_modules", {})
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published — config_ui online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received")
    _invoke("set_status", "Sistema in arresto…")
    bus.stop()
    if _app:
        QMetaObject.invokeMethod(_app, "quit", Qt.ConnectionType.QueuedConnection)


def on_modules_response(topic: str, payload: dict) -> None:
    modules = payload.get("modules", [])
    log.info(f"system.modules_response: {len(modules)} moduli")
    for m in modules:
        _invoke("add_or_update_module_tab", m.get("name", ""), int(m.get("pid", 0)), m.get("status", "unknown"))
    _invoke("set_status", f"{len(modules)} modulo/i trovato/i.")


def on_config_response(topic: str, payload: dict) -> None:
    import json
    if payload.get("requester", "") != MODULE_NAME:
        return
    module     = payload.get("module", "")
    config     = payload.get("config", {})
    schema_raw = payload.get("schema")
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
    _window.apply_default_geometry(_app)
    _window.show()

    log.info("config_ui window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
