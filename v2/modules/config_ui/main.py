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
                                          <- only processed when requester == "config_ui"
                config.error             {module, key, value, reason}
  Publishes   : system.module_ready       {name, priority}
                system.ready              {name, priority}
                system.get_modules       {}
                config.get               {module: str, requester: "config_ui"}
                config.set               {module: str, key: str, value: any}
                system.shutdown          {}  <- triggered by the shutdown button

Flow:
  1. system.readytostart -> publish system.module_ready
  2. system.start (priority==2) -> publish system.ready + system.get_modules
  3. system.modules_response -> build one tab per module,
                               publish config.get {module, requester} for each
  4. config.response (requester=="config_ui") -> populate the tab with typed widgets
  5. User edits + clicks Save -> publish config.set for each changed key
  6. config.error -> show inline error badge next to the offending field
  7. User clicks Shutdown -> publish system.shutdown {}

Widget selection by schema type
--------------------------------
  string              -> QLineEdit
  int  (no bounds)    -> QLineEdit +  -/+ buttons
  int  (with bounds)  -> QSlider (horizontal) + value label
  float (no bounds)   -> QLineEdit + -/+ buttons (step 0.1)
  float (with bounds) -> QSlider (horizontal, x100 int mapping) + value label
  enum                -> QComboBox
  bool                -> QCheckBox
  list (with schema)  -> _ListFieldInlineEditor (accordion inline)
  list (no schema)    -> _ListFieldInlineEditor with raw string items
  message/oneof       -> rendered recursively inside accordion items
  (no schema, scalar) -> QLineEdit  (backward-compatible fallback)
"""

import copy
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
    QSlider, QCheckBox, QGroupBox, QSizePolicy,
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
            btn_minus = QPushButton("-")
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
            btn_minus = QPushButton("-")
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
        if wt == "checkbox":     return self._checkbox.isChecked()
        if wt == "combobox":     return self._combo.currentText()
        if wt == "int_slider":   return self._slider.value()
        if wt == "float_slider": return self._slider_to_float(self._slider.value())
        return self._edit.text()

    def set_error(self, message: str | None):
        if message:
            self._error_lbl.setText(f"! {message}")
            self._error_lbl.setVisible(True)
        else:
            self._error_lbl.setVisible(False)


# ---------------------------------------------------------------------------
# _build_message_form  (recursive, returns collect callable)
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
      - On branch change the old body is destroyed and a fresh one is built,
        so no stale fields from the previous branch ever linger.
    """
    form = QFormLayout()
    form.setSpacing(4)
    form.setContentsMargins(0, 0, 0, 0)
    collectors: list = []   # list of (key, callable)
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
            branch_names   = list(field_node.branches.keys())
            branch_combo   = QComboBox()
            branch_combo.addItems(branch_names)

            # Detect current branch from value
            current_branch = field_node.active_branch
            if isinstance(field_val, dict):
                for bn in branch_names:
                    if bn in field_val:
                        current_branch = bn
                        break
            if current_branch in branch_names:
                branch_combo.setCurrentText(current_branch)

            form.addRow(QLabel(f"{field_name} (tipo)"), branch_combo)

            # Container that holds the currently-visible branch body
            branch_host = QWidget()
            branch_host_layout = QVBoxLayout(branch_host)
            branch_host_layout.setContentsMargins(12, 0, 0, 0)
            branch_host_layout.setSpacing(2)
            form.addRow("", branch_host)

            # Mutable cell so the closure can replace the collector
            active_collector: list = [None]  # active_collector[0] = callable

            def _rebuild_branch(
                branch_name,
                _host_layout=branch_host_layout,
                _branches=field_node.branches,
                _field_val=field_val,
                _active_collector=active_collector,
            ):
                # Remove all existing widgets from the host
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

            # Build initial branch
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
            # Oneof returns a dict {branch_name: {...}} — merge into parent
            if isinstance(val, dict) and key in schema.fields and isinstance(schema.fields[key], ConfigFieldOneof):
                result[key] = val
            else:
                result[key] = val
        return result

    return collect


# ---------------------------------------------------------------------------
# _AccordionItem  — single collapsible list item
# ---------------------------------------------------------------------------

class _AccordionItem(QWidget):
    """
    A collapsible row representing one element of a ConfigFieldList.

    Header row: [v] Elemento N          [x]
    Body:       form fields (hidden by default)

    The parent _ListFieldInlineEditor is responsible for deletion;
    it connects the delete_requested signal.
    """

    def __init__(
        self,
        index: int,
        item_value,
        item_schema,
        on_delete_cb,
        parent=None,
    ):
        super().__init__(parent)
        self._index      = index
        self._collect_fn = None
        self._expanded   = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(0)

        # ---- header ----
        header = QWidget()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setStyleSheet(
            "QWidget { background: #2a2a2a; border-radius: 4px; }"
            "QWidget:hover { background: #333; }"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 4, 8, 4)
        h_layout.setSpacing(6)

        self._arrow = QLabel("v")
        self._arrow.setFixedWidth(14)
        self._arrow.setStyleSheet("color: #888; font-size: 10px; background: transparent;")
        self._title_lbl = QLabel(f"Elemento {index}")
        self._title_lbl.setStyleSheet("font-weight: bold; background: transparent;")

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

        # ---- body ----
        self._body = QWidget()
        self._body.setStyleSheet(
            "QWidget { background: #1e1e1e; border: 1px solid #333;"
            " border-top: none; border-radius: 0 0 4px 4px; }"
        )
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 8, 12, 8)
        body_layout.setSpacing(4)

        # Build form inside body
        if isinstance(item_schema, ConfigFieldMessage):
            self._collect_fn = _build_message_form(body_layout, item_schema, item_value or {})
        elif isinstance(item_schema, ConfigFieldSchema):
            w = _FieldWidget(str(index), item_value, item_schema)
            body_layout.addWidget(w)
            self._collect_fn = w.get_value
        else:
            # No schema — raw string
            import json as _json
            raw = item_value if isinstance(item_value, str) else _json.dumps(item_value, ensure_ascii=False)
            w = _FieldWidget(str(index), raw, None)
            body_layout.addWidget(w)
            self._collect_fn = w.get_value

        self._body.setVisible(False)
        root.addWidget(self._body)

        # Toggle on header click
        header.mousePressEvent = lambda _e: self._toggle()

    def set_index(self, index: int):
        self._index = index
        self._title_lbl.setText(f"Elemento {index}")

    def _toggle(self):
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
    Inline widget rendered directly in the module config form for a
    ConfigFieldList key.

    Layout (inside the form scroll area):
      [Accordion item 0]  (collapsed)
      [Accordion item 1]  (collapsed)
      ...
      [+ Aggiungi elemento]

    Each item has a [x] delete button in its header.
    get_value() -> list  is called by _on_save().
    """

    def __init__(self, field_schema: "ConfigFieldList | None", initial_value: list, parent=None):
        super().__init__(parent)
        self._field_schema = field_schema
        self._items: list[_AccordionItem] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(2)

        # Items container
        self._items_container = QWidget()
        self._items_vbox = QVBoxLayout(self._items_container)
        self._items_vbox.setContentsMargins(0, 0, 0, 0)
        self._items_vbox.setSpacing(2)
        root.addWidget(self._items_container)

        # Add button
        self._btn_add = QPushButton("+ Aggiungi elemento")
        self._btn_add.setStyleSheet(
            "QPushButton { color: #4caf50; background: transparent;"
            " border: 1px dashed #4caf50; border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background: #1a2e1a; }"
        )
        self._btn_add.clicked.connect(self._on_add)
        root.addWidget(self._btn_add)

        # Populate with initial values
        for item_val in initial_value:
            self._append_item(item_val)

    def _item_schema(self):
        if self._field_schema is not None:
            return self._field_schema.item_schema
        return None

    def _default_item_value(self):
        """Return a blank default value for a new item."""
        if self._field_schema is None:
            return ""
        schema = self._field_schema.item_schema
        if isinstance(schema, ConfigFieldMessage):
            # Build defaults from scalar leaves
            result = {}
            for fname, fnode in schema.fields.items():
                if isinstance(fnode, ConfigFieldSchema):
                    result[fname] = fnode.default
                elif isinstance(fnode, ConfigFieldOneof):
                    result[fname] = {}
                elif isinstance(fnode, ConfigFieldMessage):
                    result[fname] = {}
                else:
                    result[fname] = None
            return result
        if isinstance(schema, ConfigFieldSchema):
            return schema.default
        return {}

    def _append_item(self, item_value):
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

    def _delete_item(self, idx: int):
        if idx >= len(self._items):
            return
        item = self._items.pop(idx)
        self._items_vbox.removeWidget(item)
        item.deleteLater()
        # Re-index remaining items
        for i, it in enumerate(self._items):
            it.set_index(i)
            # Reconnect delete button with updated index
            # The old closure still fires _delete_item with the old index,
            # so we rebuild all delete handlers.
        self._reconnect_delete_handlers()

    def _reconnect_delete_handlers(self):
        """Re-wire delete buttons after a deletion so indices stay correct."""
        for i, item in enumerate(self._items):
            # QPushButton is the last widget in the header h_layout
            # We access it via the header child widget
            header = item.layout().itemAt(0).widget()  # first child of root VBox
            h_layout = header.layout()
            btn_del = h_layout.itemAt(h_layout.count() - 1).widget()
            try:
                btn_del.clicked.disconnect()
            except Exception:
                pass
            _i = i
            btn_del.clicked.connect(lambda checked=False, idx=_i: self._delete_item(idx))

    def _on_add(self):
        self._append_item(self._default_item_value())
        self._reconnect_delete_handlers()

    def get_value(self) -> list:
        return [item.get_value() for item in self._items]


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
        self._fields:       dict[str, _FieldWidget]            = {}
        self._list_editors: dict[str, _ListFieldInlineEditor]  = {}
        self._schema:       dict = {}

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

        btn_refresh = QPushButton("Ricarica")
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

        self._placeholder = QLabel("In attesa dei dati di configurazione...")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._form.addRow(self._placeholder)

        self._btn_save = QPushButton("Salva modifiche")
        self._btn_save.setMinimumHeight(36)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save)
        root.addWidget(self._btn_save)

    def populate(self, config: dict, schema_raw: dict | None = None):
        while self._form.rowCount():
            self._form.removeRow(0)
        self._fields.clear()
        self._list_editors.clear()
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

            is_list_schema  = isinstance(raw_schema, ConfigFieldList)
            is_bare_list    = raw_schema is None and isinstance(value, list)
            is_other_struct = isinstance(raw_schema, (ConfigFieldMessage, ConfigFieldOneof))
            is_bare_dict    = raw_schema is None and isinstance(value, dict)

            # ---- List field: inline accordion editor ----
            if is_list_schema or is_bare_list:
                badge   = _schema_type_badge(raw_schema)
                key_lbl = QLabel(f"<b>{key}</b>{badge}")
                key_lbl.setTextFormat(Qt.TextFormat.RichText)

                initial_val = list(value) if isinstance(value, list) else []
                field_schema = raw_schema if is_list_schema else None
                editor = _ListFieldInlineEditor(field_schema, initial_val, self)
                self._list_editors[key] = editor

                # Span full width: add key label as a section header, then editor below
                self._form.addRow(key_lbl)
                self._form.addRow(editor)
                continue

            # ---- Other structured (message/oneof/bare dict): read-only ----
            if is_other_struct or is_bare_dict:
                summary_lbl = QLabel(_structured_summary(value, raw_schema))
                summary_lbl.setTextFormat(Qt.TextFormat.RichText)
                summary_lbl.setStyleSheet("color: #888;")
                badge = _schema_type_badge(raw_schema)
                self._form.addRow(QLabel(f"{key}{badge}"), summary_lbl)
                continue

            # ---- Scalar field ----
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

        # List editors
        for key, editor in self._list_editors.items():
            new_val = editor.get_value()
            orig    = self._original.get(key)
            if new_val != orig:
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

    def _build_ui(self):
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
        self.set_status(f"Errore di validazione: '{module}'.{key} - {reason}")

    def _on_refresh_all(self):
        bus.publish("system.get_modules", {})
        self.set_status("Aggiornamento lista moduli...")

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
        self.set_status("Segnale di spegnimento inviato...")


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
        _invoke("add_or_update_module_tab", m.get("name", ""), int(m.get("pid", 0)), m.get("status", "unknown"))
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

    _app = QApplication(sys.argv)
    _window = ConfigWindow()
    _window.apply_default_geometry(_app)
    _window.show()

    log.info("config_ui window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
