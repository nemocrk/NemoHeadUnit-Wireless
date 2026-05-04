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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from shared.config_schema import ConfigFieldSchema, ConfigFieldList

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
            for btn in (btn_minus, btn_plus)
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

        # Capture index at creation time; rewire on delete
        btn_del.clicked.connect(lambda _checked=False, w=row, f=fw: self._delete_row(w, f))

    def _delete_row(self, row_widget: QWidget, fw: "_FieldWidget") -> None:
        if fw in self._rows:
            self._rows.remove(fw)
        self._rows_vbox.removeWidget(row_widget)
        row_widget.deleteLater()

    def _on_add(self) -> None:
        self._append_row(self._default_value())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self) -> list:
        return [fw.get_value() for fw in self._rows]
