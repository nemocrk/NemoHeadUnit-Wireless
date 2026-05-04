"""
config_ui — list_editor

_ListEditor
    Accordion-based editor for list<struct> fields.
    Each item is a collapsible _AccordionItem containing a mini form built
    by form_builder.build_form_for_schema().

    Exposes:
        get_value() -> list[dict]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from shared.config_schema import ConfigFieldList


# ---------------------------------------------------------------------------
# _AccordionItem
# ---------------------------------------------------------------------------

class _AccordionItem(QWidget):
    """
    A collapsible row in the accordion list editor.

    header_text : displayed in the toggle button
    body_widget  : the mini form rendered by form_builder
    on_delete    : callable called when the user clicks "× Rimuovi"
    """

    def __init__(
        self,
        header_text: str,
        body_widget: QWidget,
        on_delete,
        parent: "QWidget | None" = None,
    ):
        super().__init__(parent)
        self._body = body_widget
        self._expanded = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(0)

        # --- header bar ---
        header = QWidget()
        header.setStyleSheet(
            "background: #1e2a36; border-radius: 4px;"
        )
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(6, 4, 6, 4)
        hbox.setSpacing(4)

        self._toggle_btn = QPushButton(f"▼  {header_text}")
        self._toggle_btn.setStyleSheet(
            "QPushButton { color: #cdd6f4; background: transparent;"
            " border: none; text-align: left; font-size: 12px; }"
        )
        self._toggle_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._toggle_btn.clicked.connect(self._toggle)
        hbox.addWidget(self._toggle_btn)

        btn_del = QPushButton("× Rimuovi")
        btn_del.setStyleSheet(
            "QPushButton { color: #cc4444; background: transparent;"
            " border: none; font-size: 11px; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        btn_del.clicked.connect(on_delete)
        hbox.addWidget(btn_del)

        root.addWidget(header)

        # --- body ---
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { border: 1px solid #2d3f50; border-top: none;"
            " border-radius: 0 0 4px 4px; background: #141c23; }"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.addWidget(body_widget)
        self._frame = frame
        root.addWidget(frame)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._frame.setVisible(self._expanded)
        arrow = "▼" if self._expanded else "▶"
        current = self._toggle_btn.text()
        # Replace only the arrow prefix
        label = current.lstrip("▼▶ ")
        self._toggle_btn.setText(f"{arrow}  {label}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self) -> dict:
        """
        Delegate to the body widget’s get_value().
        form_builder forms expose get_value() -> dict.
        """
        return self._body.get_value()


# ---------------------------------------------------------------------------
# _ListEditor
# ---------------------------------------------------------------------------

class _ListEditor(QWidget):
    """
    Accordion editor for list<struct> config fields.

    Parameters
    ----------
    field_schema  : ConfigFieldList (item_schema is a ConfigFieldMessage)
    initial_value : list[dict]
    """

    def __init__(
        self,
        field_schema: "ConfigFieldList | None",
        initial_value: list,
        parent: "QWidget | None" = None,
    ):
        super().__init__(parent)
        self._field_schema = field_schema
        self._items: list[_AccordionItem] = []
        self._item_count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(4)

        self._items_container = QWidget()
        self._items_vbox = QVBoxLayout(self._items_container)
        self._items_vbox.setContentsMargins(0, 0, 0, 0)
        self._items_vbox.setSpacing(4)
        root.addWidget(self._items_container)

        btn_add = QPushButton("+ Aggiungi elemento")
        btn_add.setStyleSheet(
            "QPushButton { color: #4caf50; background: transparent;"
            " border: 1px dashed #4caf50; border-radius: 4px;"
            " padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #1a2e1a; }"
        )
        btn_add.clicked.connect(self._on_add)
        root.addWidget(btn_add)

        for val in initial_value:
            self._append_item(val)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _item_schema(self):
        if self._field_schema is not None:
            return self._field_schema.item_schema
        return None

    def _default_item(self) -> dict:
        schema = self._item_schema()
        if schema is None:
            return {}
        try:
            from shared.config_schema import ConfigFieldMessage
            if isinstance(schema, ConfigFieldMessage):
                return {k: f.default for k, f in schema.fields.items()}
        except Exception:  # pragma: no cover
            pass
        return {}

    def _append_item(self, value: dict) -> None:
        from v2.modules.config_ui.form_builder import build_form_for_schema

        self._item_count += 1
        idx   = self._item_count
        schema = self._item_schema()
        body   = build_form_for_schema(schema, value)

        item = _AccordionItem(
            header_text=f"Elemento {idx}",
            body_widget=body,
            on_delete=lambda _checked=False, w=None: None,  # replaced below
        )
        # Wire delete after creation to capture the correct `item` reference
        item._toggle_btn  # noqa — ensure widget exists before closure
        # Re-wire delete button properly
        self._rewire_delete(item)

        self._items.append(item)
        self._items_vbox.addWidget(item)

    def _rewire_delete(self, item: _AccordionItem) -> None:
        """
        Find the × Rimuovi button in item’s header and reconnect its signal.
        """
        # The button was created inside _AccordionItem.__init__ with a no-op.
        # Find it by text and reconnect.
        for btn in item.findChildren(QPushButton):
            if "×" in btn.text() and "Rimuovi" in btn.text():
                try:
                    btn.clicked.disconnect()
                except RuntimeError:
                    pass
                btn.clicked.connect(
                    lambda _checked=False, i=item: self._delete_item(i)
                )
                break

    def _delete_item(self, item: _AccordionItem) -> None:
        if item in self._items:
            self._items.remove(item)
        self._items_vbox.removeWidget(item)
        item.hide()
        item.deleteLater()

    def _on_add(self) -> None:
        self._append_item(self._default_item())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_value(self) -> list:
        return [item.get_value() for item in self._items]
