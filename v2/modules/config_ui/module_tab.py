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
    form_builder.build_form_for_schema  (all structured fields)
    main._request_config                (bus helper)
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
from v2.modules.config_ui.form_builder import build_form_for_schema

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
        self._original:       dict = {}
        self._fields:         dict[str, _FieldWidget] = {}
        # All structured editors (list / message / oneof) keyed by field name.
        # Each value exposes get_value().
        self._struct_editors: dict[str, QWidget] = {}
        self._schema:         dict = {}

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

        # --- validation error banner (hidden until needed) ---
        self._error_banner = QLabel()
        self._error_banner.setStyleSheet(
            "color: #cc3333; background: #2a1010; border: 1px solid #cc3333;"
            " border-radius: 4px; padding: 4px 8px; font-size: 11px;"
        )
        self._error_banner.setWordWrap(True)
        self._error_banner.setVisible(False)
        root.addWidget(self._error_banner)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self, config: dict, schema_raw: "dict | None" = None) -> None:
        """Clear and rebuild the form from *config* + optional *schema_raw*."""
        while self._form.rowCount():
            self._form.removeRow(0)
        self._fields.clear()
        self._struct_editors.clear()
        self._error_banner.setVisible(False)
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

            is_structured = isinstance(raw_schema, _STRUCTURED_TYPES)
            is_bare_list  = raw_schema is None and isinstance(value, list)
            is_bare_dict  = raw_schema is None and isinstance(value, dict)

            # ---- Structured field (list / message / oneof / bare list|dict) ----
            if is_structured or is_bare_list or is_bare_dict:
                badge   = _schema_type_badge(raw_schema)
                key_lbl = QLabel(f"<b>{key}</b>{badge}")
                key_lbl.setTextFormat(Qt.TextFormat.RichText)

                editor = build_form_for_schema(
                    raw_schema if is_structured else None,
                    value,
                    self._form_container,
                )
                self._struct_editors[key] = editor

                self._form.addRow(key_lbl)
                self._form.addRow(editor)
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

    def _collect_all_values(self) -> dict:
        """
        Merge scalar fields and struct editors into a single dict.
        Optional fields whose widget returns None are excluded.
        """
        result: dict = {}
        for key, fw in self._fields.items():
            val = fw.get_value()
            if val is not None:
                result[key] = val
        for key, editor in self._struct_editors.items():
            val = editor.get_value() if hasattr(editor, "get_value") else None
            if val is not None:
                result[key] = val
        return result

    def _validate(self, values: dict) -> list[str]:
        """
        Check required scalar fields for empty values.
        Returns a list of human-readable error strings (empty = valid).
        """
        errors: list[str] = []
        for key, fw in self._fields.items():
            raw_schema = self._schema.get(key)
            is_optional = getattr(raw_schema, "optional", False)
            if is_optional:
                continue
            val = fw.get_value()
            if val is None or str(val).strip() == "":
                fw.set_error("Campo obbligatorio")
                errors.append(f"'{key}': campo obbligatorio")
        return errors

    def _on_save(self) -> None:
        from v2.modules.config_ui.main import bus

        # Clear previous errors
        for fw in self._fields.values():
            fw.set_error(None)
        self._error_banner.setVisible(False)

        current = self._collect_all_values()

        # Inline validation
        errors = self._validate(current)
        if errors:
            self._error_banner.setText(
                "Salvataggio bloccato. Campi obbligatori mancanti:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )
            self._error_banner.setVisible(True)
            return

        changed: dict = {}

        for key, new_val in current.items():
            orig = self._original.get(key)
            if isinstance(new_val, bool):
                orig_bool = orig if isinstance(orig, bool) else (
                    str(orig).strip().lower() in _BOOL_TRUE
                )
                if new_val != orig_bool:
                    changed[key] = new_val
            elif new_val != orig:
                changed[key] = new_val

        # Also detect keys removed (optional fields now unchecked)
        for key in list(self._original.keys()):
            if key not in current and key in self._original:
                changed[key] = None  # signal removal to backend

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
