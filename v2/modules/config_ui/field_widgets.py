"""
config_ui — field_widgets

Scalar field widget (_FieldWidget) and scalar-list editor (_ScalarListEditor).

_FieldWidget
    Renders a single scalar config value using the appropriate Qt widget
    depending on ConfigFieldSchema.type (or falls back to QLineEdit).
    Exposes:
        get_value() -> any
        set_error(message | None)

_ScalarListEditor
    Renders a list of scalar values (int / float / string / enum / bool)
    as a compact vertical list of _FieldWidget rows with add / delete buttons.
    Exposes:
        get_value() -> list

_OneofWidget
    Renders a ConfigFieldOneof field as a QComboBox branch selector +
    collapsible body for the active branch sub-form.
    Exposes:
        get_value() -> scalar  (the value of the active branch directly,
                                NOT wrapped in a {branch: value} dict)

_OptionalMessageWidget
    Renders a ConfigFieldMessage with optional=True as a checkbox that
    enables/disables the nested sub-form.  When unchecked the field is
    omitted from the payload (returns None).
    Exposes:
        get_value() -> dict | None
        validate()  -> list[str]   (empty when unchecked or all fields valid)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from shared.config_schema import (
        ConfigFieldList,
        ConfigFieldMessage,
        ConfigFieldOneof,
        ConfigFieldSchema,
    )

_BOOL_TRUE = {"true", "1", "yes", "on"}


# ---------------------------------------------------------------------------
# _FieldWidget — single scalar field
# ---------------------------------------------------------------------------

class _FieldWidget(QWidget):
    """
    Container for a single scalar config field.
    Accepts ConfigFieldSchema or None (falls back to QLineEdit).
    Structured nodes must NOT be passed here.
    """

    def __init__(
        self,
        key: str,
        raw_value,
        field_schema: "ConfigFieldSchema | None",
    ):
        super().__init__()
        self._key        = key
        self._schema     = field_schema
        self._widget_type: str = "lineedit"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._layout = layout

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

        layout.addWidget(self._error_lbl)

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_string(self, value: str) -> None:
        self._widget_type = "lineedit"
        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText("(vuoto)")
        self._layout.addWidget(self._edit)

    def _build_bool(self, value) -> None:
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

    def _build_enum(self, value, choices: list[str]) -> None:
        self._widget_type = "combobox"
        self._combo = QComboBox()
        self._combo.addItems(choices)
        if str(value) in choices:
            self._combo.setCurrentText(str(value))
        self._layout.addWidget(self._combo)

    def _build_int(self, value, min_v, max_v) -> None:
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

    def _build_float(self, value, min_v, max_v) -> None:
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

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------

    def _float_to_slider(self, v: float) -> int:
        span = self._float_max - self._float_min
        return 0 if span == 0 else round((v - self._float_min) / span * 100)

    def _slider_to_float(self, pos: int) -> float:
        return self._float_min + pos / 100 * (self._float_max - self._float_min)

    def _step_int(self, delta: int) -> None:
        try:
            v = int(self._edit.text()) + delta
        except ValueError:
            v = delta
        self._edit.setText(str(v))

    def _step_float(self, delta: float) -> None:
        try:
            v = round(float(self._edit.text()) + delta, 10)
        except ValueError:
            v = delta
        self._edit.setText(f"{v:.2f}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self):
        wt = self._widget_type
        if wt == "checkbox":     return self._checkbox.isChecked()
        if wt == "combobox":     return self._combo.currentText()
        if wt == "int_slider":   return self._slider.value()
        if wt == "float_slider": return self._slider_to_float(self._slider.value())
        return self._edit.text()

    # Convenience alias used by tests that access .text() directly on a
    # QLineEdit-backed _FieldWidget (backward compatible).
    def text(self) -> str:
        if hasattr(self, "_edit"):
            return self._edit.text()
        return str(self.get_value())

    def setText(self, value: str) -> None:
        if hasattr(self, "_edit"):
            self._edit.setText(value)

    def set_error(self, message: "str | None") -> None:
        if message:
            self._error_lbl.setText(f"! {message}")
            self._error_lbl.setVisible(True)
        else:
            self._error_lbl.setVisible(False)


# ---------------------------------------------------------------------------
# _ScalarListEditor — compact list of scalar values
# ---------------------------------------------------------------------------

class _ScalarListEditor(QWidget):
    """
    Compact editor for list<scalar> fields (int, float, string, enum, bool).

    Renders as a vertical stack of (_FieldWidget + delete button) rows,
    with an "+ Aggiungi" button at the bottom.

    get_value() -> list
    """

    def __init__(
        self,
        field_schema: "ConfigFieldList | None",
        initial_value: list,
        parent: "QWidget | None" = None,
    ):
        super().__init__(parent)
        self._field_schema = field_schema
        self._rows: list[_FieldWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(2)

        self._rows_container = QWidget()
        self._rows_vbox = QVBoxLayout(self._rows_container)
        self._rows_vbox.setContentsMargins(0, 0, 0, 0)
        self._rows_vbox.setSpacing(2)
        root.addWidget(self._rows_container)

        btn_add = QPushButton("+ Aggiungi")
        btn_add.setStyleSheet(
            "QPushButton { color: #4caf50; background: transparent;"
            " border: 1px dashed #4caf50; border-radius: 4px; padding: 2px 8px; }"
            "QPushButton:hover { background: #1a2e1a; }"
        )
        btn_add.clicked.connect(self._on_add)
        root.addWidget(btn_add)

        for val in initial_value:
            self._append_row(val)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _item_schema(self):
        if self._field_schema is not None:
            return self._field_schema.item_schema
        return None

    def _default_value(self):
        schema = self._item_schema()
        if schema is None:
            return ""
        from shared.config_schema import ConfigFieldSchema
        if isinstance(schema, ConfigFieldSchema):
            return schema.default
        return ""

    def _append_row(self, value) -> None:
        schema = self._item_schema()
        from shared.config_schema import ConfigFieldSchema
        scalar_schema = schema if isinstance(schema, ConfigFieldSchema) else None
        fw = _FieldWidget("", value, scalar_schema)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        row_layout.addWidget(fw, stretch=1)

        btn_del = QPushButton("×")
        btn_del.setFixedSize(22, 22)
        btn_del.setStyleSheet(
            "QPushButton { color: #cc4444; background: transparent;"
            " border: none; font-weight: bold; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        row_layout.addWidget(btn_del)

        self._rows.append(fw)
        self._rows_vbox.addWidget(row)

        btn_del.clicked.connect(lambda _checked=False, w=row, f=fw: self._delete_row(w, f))

    def _delete_row(self, row_widget: QWidget, fw: "_FieldWidget") -> None:
        if fw in self._rows:
            self._rows.remove(fw)
        self._rows_vbox.removeWidget(row_widget)
        row_widget.hide()
        row_widget.deleteLater()

    def _on_add(self) -> None:
        self._append_row(self._default_value())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self) -> list:
        return [fw.get_value() for fw in self._rows]


# ---------------------------------------------------------------------------
# _OneofWidget — renders a ConfigFieldOneof as branch selector + body
# ---------------------------------------------------------------------------

class _OneofWidget(QWidget):
    """
    Widget for a ConfigFieldOneof field.

    Shows a QComboBox to select the active branch; below it renders the
    sub-form for that branch (rebuilt on every branch change).

    get_value() returns the scalar or dict value of the active branch
    directly — NOT wrapped in {branch_name: value}.

    Parameters
    ----------
    field_schema : ConfigFieldOneof
    raw_value    : the current value (scalar or dict matching the active branch)
    """

    def __init__(
        self,
        field_schema: "ConfigFieldOneof",
        raw_value,
        parent: "QWidget | None" = None,
    ):
        super().__init__(parent)
        self._field_schema   = field_schema
        self._active_collect = [None]   # list so closure can mutate

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # Branch selector
        self._combo = QComboBox()
        # First item is an empty placeholder so no branch is pre-selected
        self._combo.addItem("— seleziona tipo —")
        branch_names = list(field_schema.branches.keys())
        self._combo.addItems(branch_names)
        root.addWidget(self._combo)

        # Body host
        self._body_host = QWidget()
        body_layout = QVBoxLayout(self._body_host)
        body_layout.setContentsMargins(12, 4, 0, 4)
        body_layout.setSpacing(2)
        self._body_layout = body_layout
        root.addWidget(self._body_host)

        # Determine initial branch from raw_value — leave placeholder if None/empty
        initial_branch = None
        if raw_value is not None:
            if isinstance(raw_value, dict):
                for bn in branch_names:
                    if bn in raw_value:
                        initial_branch = bn
                        break
            # scalar value: cannot map to a branch name, leave placeholder
        if initial_branch is None and field_schema.active_branch in branch_names:
            # Only pre-select if there's an actual value
            if raw_value is not None:
                initial_branch = field_schema.active_branch

        self._raw_value   = raw_value
        self._branch_names = branch_names

        if initial_branch:
            self._combo.setCurrentText(initial_branch)
            self._rebuild_body(initial_branch)
        # else stays on placeholder, body stays empty

        self._combo.currentTextChanged.connect(self._on_branch_changed)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._active_collect[0] = None

    def _rebuild_body(self, branch_name: str) -> None:
        from config_ui.form_builder import build_form_for_schema

        self._clear_body()
        if branch_name not in self._branch_names:
            return

        branch_schema = self._field_schema.branches[branch_name]

        # Extract branch value from raw_value
        if isinstance(self._raw_value, dict):
            branch_val = self._raw_value.get(branch_name)
        else:
            branch_val = self._raw_value  # scalar branch

        body_widget = build_form_for_schema(branch_schema, branch_val)
        self._body_layout.addWidget(body_widget)
        self._active_collect[0] = body_widget

    def _on_branch_changed(self, branch_name: str) -> None:
        if branch_name == "— seleziona tipo —":
            self._clear_body()
            return
        # When the user switches branch, forget the old raw value
        self._raw_value = None
        self._rebuild_body(branch_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self):
        """Return the value of the active branch as a plain scalar/dict."""
        widget = self._active_collect[0]
        if widget is None:
            return None
        if hasattr(widget, "get_value"):
            return widget.get_value()
        return None

    def set_error(self, message: "str | None") -> None:
        # No-op — errors on oneof fields are handled at the parent level
        pass


# ---------------------------------------------------------------------------
# _OptionalMessageWidget — checkbox-gated nested message
# ---------------------------------------------------------------------------

class _OptionalMessageWidget(QWidget):
    """
    Widget for a ConfigFieldMessage with optional=True.

    Renders as:
        [ ✓ ] fieldname    ← QCheckBox toggles the sub-form visibility
        ┌─────────────────┐
        │  sub-form fields │   ← built by form_builder, visible only when checked
        └─────────────────┘

    get_value() returns:
        dict   — when checkbox is checked (the sub-form values)
        None   — when unchecked (field is omitted from payload)

    validate() returns:
        []              — when unchecked (nothing to validate)
        list[str]       — human-readable errors for required-but-empty sub-fields
    """

    def __init__(
        self,
        field_schema: "ConfigFieldMessage",
        raw_value,
        parent: "QWidget | None" = None,
    ):
        super().__init__(parent)
        self._field_schema = field_schema

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Checkbox row
        chk_row = QWidget()
        chk_layout = QHBoxLayout(chk_row)
        chk_layout.setContentsMargins(0, 2, 0, 2)
        chk_layout.setSpacing(6)
        self._checkbox = QCheckBox("attivo")
        self._checkbox.setStyleSheet("color: #aaa; font-size: 11px;")
        chk_layout.addWidget(self._checkbox)
        chk_layout.addStretch()
        root.addWidget(chk_row)

        # Body frame
        self._frame = QFrame()
        self._frame.setStyleSheet(
            "QFrame { border: 1px solid #2d3f50; border-radius: 4px;"
            " background: #111820; }"
        )
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(10, 6, 10, 6)
        frame_layout.setSpacing(4)

        from config_ui.form_builder import build_form_for_schema
        self._body = build_form_for_schema(field_schema, raw_value or {})
        frame_layout.addWidget(self._body)
        root.addWidget(self._frame)

        # Initial state: active if raw_value is not None
        is_active = raw_value is not None
        self._checkbox.setChecked(is_active)
        self._frame.setVisible(is_active)

        self._checkbox.toggled.connect(self._on_toggle)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_toggle(self, checked: bool) -> None:
        self._frame.setVisible(checked)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self):
        if not self._checkbox.isChecked():
            return None
        if hasattr(self._body, "get_value"):
            return self._body.get_value()
        return None

    def validate(self) -> list[str]:
        """
        Returns a list of human-readable error strings for required-but-empty
        sub-fields.  Always returns [] when the widget is unchecked — the
        whole message is absent and there is nothing to validate.
        """
        if not self._checkbox.isChecked():
            return []
        # Delegate to the body widget if it exposes validate()
        if hasattr(self._body, "validate"):
            return self._body.validate()
        return []

    def set_error(self, message: "str | None") -> None:
        pass
