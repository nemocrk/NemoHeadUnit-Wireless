"""
config_ui — form_builder

Single public entry-point:

    build_form_for_schema(schema, value) -> QWidget

Where the returned widget exposes get_value() -> dict | scalar.

The builder is fully recursive:
    - scalar                    -> _FieldWidget
    - list<scalar>              -> _ScalarListEditor
    - list<struct>              -> _ListEditor
    - oneof                     -> _OneofWidget
    - message (optional=False)  -> _FormWidget  (recursive)
    - message (optional=True)   -> _OptionalMessageWidget (checkbox-gated)
    - unknown / None            -> plain QLineEdit wrapped in _FormWidget

Additional public helper:

    build_default_value(schema) -> any
        Returns a Python value (scalar / dict / list) suitable as the
        default for a new item of the given schema type.
        Used by _ListEditor._default_item() for recursive defaults.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QLabel,
    QWidget,
)

from config_ui.field_widgets import (
    _FieldWidget,
    _OneofWidget,
    _OptionalMessageWidget,
    _ScalarListEditor,
)

if TYPE_CHECKING:
    from shared.config_schema import (
        ConfigFieldList,
        ConfigFieldMessage,
        ConfigFieldOneof,
        ConfigFieldSchema,
    )


# ---------------------------------------------------------------------------
# _FormWidget — a widget that holds a dict of sub-widgets
# ---------------------------------------------------------------------------

class _FormWidget(QWidget):
    """
    A QFormLayout-based widget that maps field keys to sub-widgets.

    Each sub-widget must expose get_value() -> any.
    _FormWidget.get_value() returns the dict of all sub-values,
    omitting keys whose widget returns None (optional fields not active).
    """

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent)
        self._sub: dict[str, QWidget] = {}
        self._labels: dict[str, tuple[QLabel, str, bool]] = {}
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
        optional: bool = False,
    ) -> None:
        lbl_text = label
        if optional:
            lbl_text = f"{label} <span style='color:#666; font-size:10px'>(opz.)</span>"
        lbl = QLabel(lbl_text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet("font-weight: 500;")
        if description:
            lbl.setToolTip(description)
            widget.setToolTip(description)
        self._form_layout.addRow(lbl, widget)
        self._sub[key] = widget
        self._labels[key] = (lbl, label, optional)

    def get_value(self) -> dict:
        result = {}
        for k, w in self._sub.items():
            val = w.get_value() if hasattr(w, "get_value") else w.text()
            if val is not None:          # None = optional field not active, omit
                result[k] = val
        return result

    def scale_layouts(self, df: float) -> None:
        if self.layout():
            self.layout().setContentsMargins(0, 0, 0, 0)
            self.layout().setVerticalSpacing(int(6 * df))
            self.layout().setHorizontalSpacing(int(10 * df))
        for key, (lbl, label, optional) in self._labels.items():
            if optional:
                lbl.setText(f"{label} <span style='color:#666; font-size:{int(10 * df)}px'>(opz.)</span>")
        for w in self._sub.values():
            if hasattr(w, "scale_layouts"):
                w.scale_layouts(df)


# ---------------------------------------------------------------------------
# build_default_value — produce a default Python value for any schema node
# ---------------------------------------------------------------------------

def build_default_value(schema) -> object:
    """
    Return a sensible default Python value for *schema*.

    - ConfigFieldSchema  -> schema.default (or type-appropriate zero)
    - ConfigFieldMessage -> {} (sub-fields filled lazily when the widget opens)
    - ConfigFieldList    -> []
    - ConfigFieldOneof   -> None  (no branch pre-selected)
    - None               -> ""
    """
    if schema is None:
        return ""

    try:
        from shared.config_schema import (
            ConfigFieldList,
            ConfigFieldMessage,
            ConfigFieldOneof,
            ConfigFieldSchema,
        )
    except ImportError:
        return ""

    if isinstance(schema, ConfigFieldSchema):
        default = getattr(schema, "default", None)
        if default is not None:
            return default
        type_defaults = {"int": 0, "float": 0.0, "bool": False, "string": "", "enum": ""}
        return type_defaults.get(schema.type, "")

    if isinstance(schema, ConfigFieldMessage):
        return {}

    if isinstance(schema, ConfigFieldList):
        return []

    if isinstance(schema, ConfigFieldOneof):
        return None

    return ""


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

    Returns a _FormWidget (get_value()->dict) or a leaf widget
    depending on schema type.

    Parameters
    ----------
    schema : ConfigFieldMessage | ConfigFieldOneof | ConfigFieldList |
             ConfigFieldSchema | None
    value  : the current config value (dict, list, scalar, or JSON string)
    """
    try:
        from shared.config_schema import (
            ConfigFieldList,
            ConfigFieldMessage,
            ConfigFieldOneof,
            ConfigFieldSchema,
        )
        _HAS_SCHEMA = True
    except ImportError:
        _HAS_SCHEMA = False
        ConfigFieldList    = None  # type: ignore
        ConfigFieldMessage = None  # type: ignore
        ConfigFieldOneof   = None  # type: ignore
        ConfigFieldSchema  = None  # type: ignore

    # Coerce JSON string value to dict / list
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
    # ConfigFieldOneof
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldOneof):
        return _OneofWidget(schema, value, parent)

    # ------------------------------------------------------------------
    # ConfigFieldMessage (struct)
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldMessage):
        is_optional = getattr(schema, "optional", False)

        if is_optional:
            # Checkbox-gated widget: shows body only when active
            return _OptionalMessageWidget(schema, value, parent)

        if not isinstance(value, dict):
            value = {}
        form = _FormWidget(parent)
        for field_key, field_schema in schema.fields.items():
            raw = value.get(field_key, build_default_value(field_schema))
            sub = _build_any(field_schema, field_key, raw)
            desc     = getattr(field_schema, "description", "") or ""
            optional = getattr(field_schema, "optional", False)
            form.add_field(field_key, field_key, sub, desc, optional=optional)
        return form

    # ------------------------------------------------------------------
    # ConfigFieldList
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldList):
        if not isinstance(value, list):
            value = []
        item_schema = schema.item_schema
        if isinstance(item_schema, (ConfigFieldMessage, ConfigFieldOneof)):
            from config_ui.list_editor import _ListEditor
            return _ListEditor(schema, value, parent)
        return _ScalarListEditor(schema, value, parent)

    # ------------------------------------------------------------------
    # ConfigFieldSchema (scalar)
    # ------------------------------------------------------------------
    if _HAS_SCHEMA and isinstance(schema, ConfigFieldSchema):
        return _FieldWidget("", value, schema)

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------
    return _FieldWidget("", str(value) if value is not None else "", None)


# ---------------------------------------------------------------------------
# Internal helpers
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
            ConfigFieldOneof,
            ConfigFieldSchema,
        )
        _HAS_SCHEMA = True
    except ImportError:
        _HAS_SCHEMA = False
        ConfigFieldList    = None  # type: ignore
        ConfigFieldMessage = None  # type: ignore
        ConfigFieldOneof   = None  # type: ignore
        ConfigFieldSchema  = None  # type: ignore

    if field_schema is None:
        if isinstance(value, dict):
            return build_form_for_schema(None, value)
        if isinstance(value, list):
            return _ScalarListEditor(None, value)
        return _FieldWidget(key, value, None)

    if _HAS_SCHEMA:
        if isinstance(field_schema, ConfigFieldOneof):
            return _OneofWidget(field_schema, value)
        if isinstance(field_schema, ConfigFieldMessage):
            is_optional = getattr(field_schema, "optional", False)
            if is_optional:
                return _OptionalMessageWidget(field_schema, value)
            return build_form_for_schema(field_schema, value)
        if isinstance(field_schema, ConfigFieldList):
            return build_form_for_schema(field_schema, value)
        if isinstance(field_schema, ConfigFieldSchema):
            return _FieldWidget(key, value, field_schema)

    return _FieldWidget(key, value, None)
