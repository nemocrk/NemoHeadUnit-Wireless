"""
config_ui — form_builder

Single public entry-point:

    build_form_for_schema(schema, value) -> _FormWidget

Where _FormWidget exposes get_value() -> dict | scalar.

The builder is fully recursive:
    - scalar         -> _FieldWidget
    - list<scalar>   -> _ScalarListEditor
    - list<struct>   -> _ListEditor
    - struct/message -> _FormWidget (recursive)
    - unknown / None -> plain QLineEdit wrapped in _FormWidget
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QFormLayout,
    QLabel,
    QWidget,
)

from v2.modules.config_ui.field_widgets import _FieldWidget, _ScalarListEditor

if TYPE_CHECKING:
    from shared.config_schema import (
        ConfigFieldList,
        ConfigFieldMessage,
        ConfigFieldSchema,
    )


# ---------------------------------------------------------------------------
# _FormWidget — a widget that holds a dict of sub-widgets
# ---------------------------------------------------------------------------

class _FormWidget(QWidget):
    """
    A QFormLayout-based widget that maps field keys to sub-widgets.

    Each sub-widget must expose get_value() -> any.
    _FormWidget.get_value() returns the dict of all sub-values.
    """

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent)
        self._sub: dict[str, QWidget] = {}
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setVerticalSpacing(6)
        layout.setHorizontalSpacing(10)
        self._form_layout = layout

    def add_field(
        self,
        key: str,
        label: str,
        widget: QWidget,
        description: str = "",
    ) -> None:
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #cdd6f4; font-weight: 500;")
        if description:
            lbl.setToolTip(description)
            widget.setToolTip(description)
        self._form_layout.addRow(lbl, widget)
        self._sub[key] = widget

    def get_value(self) -> dict:
        return {
            k: (w.get_value() if hasattr(w, "get_value") else w.text())
            for k, w in self._sub.items()
        }


# ---------------------------------------------------------------------------
# build_form_for_schema — recursive
# ---------------------------------------------------------------------------

def build_form_for_schema(
    schema,
    value,
    parent: "QWidget | None" = None,
) -> QWidget:
    """
    Build a Qt form widget for *schema* pre-populated with *value*.

    Returns a _FormWidget (get_value()->dict) or a _FieldWidget/list editor
    depending on schema type.

    Parameters
    ----------
    schema : ConfigFieldMessage | ConfigFieldSchema | ConfigFieldList | None
    value  : the current config value (dict, list, scalar, or JSON string)
    """
    # Lazy imports to avoid circular dependency at module load time
    try:
        from shared.config_schema import (
            ConfigFieldList,
            ConfigFieldMessage,
            ConfigFieldSchema,
        )
        _HAS_SCHEMA = True
    except ImportError:
        _HAS_SCHEMA = False
        ConfigFieldList    = None
        ConfigFieldMessage = None
        ConfigFieldSchema  = None

    # ------------------------------------------------------------------
    # Coerce JSON string value to dict / list
    # ------------------------------------------------------------------
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass

    # ------------------------------------------------------------------
    # No schema — fall back based on value type
    # ------------------------------------------------------------------
    if schema is None or not _HAS_SCHEMA:
        if isinstance(value, dict):
            form = _FormWidget(parent)
            for k, v in value.items():
                sub = _build_any(None, k, v)
                form.add_field(k, k, sub)
            return form
        if isinstance(value, list):
            return _ScalarListEditor(None, value, parent)
        return _FieldWidget("", value, None)

    # ------------------------------------------------------------------
    # ConfigFieldMessage (struct)
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldMessage):
        if not isinstance(value, dict):
            value = {}
        form = _FormWidget(parent)
        for field_key, field_schema in schema.fields.items():
            raw = value.get(field_key, getattr(field_schema, "default", None))
            sub = _build_any(field_schema, field_key, raw)
            desc = getattr(field_schema, "description", "") or ""
            form.add_field(field_key, field_key, sub, desc)
        return form

    # ------------------------------------------------------------------
    # ConfigFieldList
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldList):
        if not isinstance(value, list):
            value = []
        item_schema = schema.item_schema
        # list<struct>
        if isinstance(item_schema, ConfigFieldMessage):
            from v2.modules.config_ui.list_editor import _ListEditor
            return _ListEditor(schema, value, parent)
        # list<scalar>
        return _ScalarListEditor(schema, value, parent)

    # ------------------------------------------------------------------
    # ConfigFieldSchema (scalar)
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldSchema):
        return _FieldWidget("", value, schema)

    # ------------------------------------------------------------------
    # Fallback — treat as plain string
    # ------------------------------------------------------------------
    return _FieldWidget("", str(value) if value is not None else "", None)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _build_any(field_schema, key: str, value) -> QWidget:
    """
    Dispatch a single (schema, value) pair to the right widget.
    Called recursively by build_form_for_schema for message fields.
    """
    try:
        from shared.config_schema import (
            ConfigFieldList,
            ConfigFieldMessage,
            ConfigFieldSchema,
        )
        _HAS_SCHEMA = True
    except ImportError:
        _HAS_SCHEMA = False
        ConfigFieldList    = None
        ConfigFieldMessage = None
        ConfigFieldSchema  = None

    if field_schema is None:
        if isinstance(value, dict):
            return build_form_for_schema(None, value)
        if isinstance(value, list):
            return _ScalarListEditor(None, value)
        return _FieldWidget(key, value, None)

    if _HAS_SCHEMA:
        if isinstance(field_schema, ConfigFieldMessage):
            return build_form_for_schema(field_schema, value)
        if isinstance(field_schema, ConfigFieldList):
            return build_form_for_schema(field_schema, value)
        if isinstance(field_schema, ConfigFieldSchema):
            return _FieldWidget(key, value, field_schema)

    return _FieldWidget(key, value, None)
