"""
config_ui — module_tab

ModuleConfigTab
    One QWidget per module, shown as a tab inside ConfigWindow.
    Knows how to:
      - populate() itself from a config dict + optional schema dict
      - mark_error() an individual field
      - update_status() the pid / status labels
      - _on_save() publish config.set for every changed key
      - _on_refresh() re-request config from the bus

Depends on:
    field_widgets._FieldWidget
    main._ListFieldInlineEditor  (legacy inline accordion — still in main.py)
    main._request_config         (bus helper)
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

for _p in (str(_V2), str(_MODULES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from shared.logger import get_logger
from shared.config_schema import (
    ConfigFieldList,
    ConfigFieldMessage,
    ConfigFieldOneof,
    ConfigFieldSchema,
    schema_from_dict,
)
from v2.modules.config_ui.field_widgets import _FieldWidget

_BOOL_TRUE = {"true", "1", "yes", "on"}

_STRUCTURED_TYPES = (ConfigFieldList, ConfigFieldMessage, ConfigFieldOneof)

log = get_logger("config_ui")


# ---------------------------------------------------------------------------
# Schema badge helpers
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


# ---------------------------------------------------------------------------
# ModuleConfigTab
# ---------------------------------------------------------------------------

class ModuleConfigTab(QWidget):
    """
    One tab per module.

    Public API
    ----------
    populate(config, schema_raw)   — fill the form from bus data
    mark_error(key, reason)        — show inline error badge
    update_status(pid, status)     — refresh metadata labels
    """

    def __init__(self, module_name: str, pid: int, status: str):
        super().__init__()
        self._module_name = module_name
        self._original:      dict = {}
        self._fields:        dict[str, _FieldWidget] = {}
        self._list_editors:  dict = {}                  # key -> _ListFieldInlineEditor
        self._schema:        dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # --- metadata row ---
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

        # --- scrollable form ---
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

        # --- save button ---
        self._btn_save = QPushButton("Salva modifiche")
        self._btn_save.setMinimumHeight(36)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save)
        root.addWidget(self._btn_save)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self, config: dict, schema_raw: "dict | None" = None) -> None:
        """Clear and rebuild the form from *config* + optional *schema_raw*."""
        # Import here to avoid circular dependency at module load time.
        from v2.modules.config_ui.main import _ListFieldInlineEditor

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

                initial_val  = list(value) if isinstance(value, list) else []
                field_schema = raw_schema if is_list_schema else None
                editor = _ListFieldInlineEditor(field_schema, initial_val, self)
                self._list_editors[key] = editor

                self._form.addRow(key_lbl)
                self._form.addRow(editor)
                continue

            # ---- Other structured (message/oneof/bare dict): read-only summary ----
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

    def mark_error(self, key: str, reason: str) -> None:
        widget = self._fields.get(key)
        if widget:
            widget.set_error(reason)

    def update_status(self, pid: int, status: str) -> None:
        self._lbl_pid.setText(f"PID: {pid}")
        self._lbl_status.setText(f"Stato: {status}")

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _on_refresh(self) -> None:
        from v2.modules.config_ui.main import _request_config
        _request_config(self._module_name)

    def _on_save(self) -> None:
        from v2.modules.config_ui.main import bus

        for fw in self._fields.values():
            fw.set_error(None)

        changed: dict = {}

        # Scalar fields
        for key, fw in self._fields.items():
            new_val = fw.get_value()
            orig    = self._original.get(key)
            if isinstance(new_val, bool):
                orig_bool = orig if isinstance(orig, bool) else (
                    str(orig).strip().lower() in _BOOL_TRUE
                )
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
