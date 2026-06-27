import os
from PyQt6.QtWidgets import QPushButton, QWidget, QListWidget, QLineEdit, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore import pyqtSignal, Qt, QPoint
from PyQt6.QtGui import QPainter, QColor, QBrush, QPolygon

def is_keyboard_attached() -> bool:
    """Detect if a physical keyboard is attached by scanning /proc/bus/input/devices."""
    if os.getenv("FORCE_VIRTUAL_KEYBOARD") == "1":
        return False
    if not os.path.exists("/proc/bus/input/devices"):
        return False
    try:
        with open("/proc/bus/input/devices", "r") as f:
            content = f.read().lower()
        devices = content.split("\n\n")
        for dev in devices:
            if "handlers=" in dev and "kbd" in dev:
                # Filter out standard non-keyboard devices like power buttons or lid switches
                name_line = ""
                for line in dev.split("\n"):
                    if line.startswith("n:"):
                        name_line = line
                        break
                if any(x in name_line for x in ["power button", "sleep button", "video bus", "gpio", "volume"]):
                    continue
                return True
    except Exception:
        pass
    return False


class TouchComboBox(QPushButton):
    currentTextChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._popup = None
        self.clicked.connect(self.show_popup)
        self.setProperty("class", "combo_btn")

    def addItems(self, texts: list[str]) -> None:
        for t in texts:
            self.addItem(t)

    def addItem(self, text: str) -> None:
        self._items.append(text)
        if len(self._items) == 1 and not self.text():
            self.setText(text)

    def setCurrentText(self, text: str) -> None:
        if text in self._items:
            if self.text() != text:
                self.setText(text)
                self.currentTextChanged.emit(text)
        else:
            self.setText(text)
            self.currentTextChanged.emit(text)

    def currentText(self) -> str:
        return self.text()

    def show_popup(self) -> None:
        win = self.window()
        if not win:
            return
        
        # Create and display popup overlay inside the same window frame
        self._popup = TouchComboBoxPopup(self, self._items, win)
        self._popup.setGeometry(0, 0, win.width(), win.height())
        self._popup.show()
        self._popup.raise_()
        self._popup.list_widget.setFocus()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        
        # Paint dropdown arrow overlay on the button
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        df = 1.0
        win = self.window()
        if win and hasattr(win, "_dpi_factor"):
            df = win._dpi_factor
            
        arrow_w = int(8 * df)
        arrow_h = int(5 * df)
        
        rect = self.rect()
        margin_right = int(12 * df)
        cx = rect.right() - margin_right - arrow_w // 2
        cy = rect.center().y()
        
        arrow_color = QColor(25, 118, 210)  # Material Blue
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(arrow_color))
        
        poly = QPolygon([
            QPoint(cx - arrow_w // 2, cy - arrow_h // 2),
            QPoint(cx + arrow_w // 2, cy - arrow_h // 2),
            QPoint(cx, cy + arrow_h // 2)
        ])
        painter.drawPolygon(poly)
        painter.end()


class TouchComboBoxPopup(QWidget):
    def __init__(self, button: TouchComboBox, items: list[str], parent: QWidget):
        super().__init__(parent)
        self.button = button
        self.items = items
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.list_widget = QListWidget(self)
        self.list_widget.addItems(items)
        
        current_text = button.currentText()
        if current_text in items:
            idx = items.index(current_text)
            self.list_widget.setCurrentRow(idx)
            
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        
        df = getattr(parent, "_dpi_factor", 1.0)
        font_size = int(14 * df)
        input_radius = int(8 * df)
        item_h = int(32 * df)
        
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.12);
                border-radius: {input_radius}px;
                padding: {int(4 * df)}px;
                font-family: 'DM Sans', sans-serif;
                font-size: {font_size}px;
                color: #121212;
            }}
            QListWidget::item {{
                min-height: {item_h}px;
                padding: {int(4 * df)}px;
                border-radius: {int(4 * df)}px;
                color: #121212;
            }}
            QListWidget::item:selected {{
                background-color: #1976d2;
                color: #ffffff;
            }}
            QListWidget::item:hover {{
                background-color: rgba(0, 0, 0, 0.06);
            }}
        """)
        
        # Position the list widget relatively
        btn_pos = button.mapTo(parent, QPoint(0, button.height()))
        popup_width = max(button.width(), int(150 * df))
        
        row_height = int(36 * df)
        popup_height = min(int(200 * df), len(items) * row_height + int(10 * df))
        
        y = btn_pos.y()
        if y + popup_height > parent.height():
            y = button.mapTo(parent, QPoint(0, 0)).y() - popup_height
            if y < 0:
                y = 10
                
        x = btn_pos.x()
        if x + popup_width > parent.width():
            x = parent.width() - popup_width - 10
            if x < 0:
                x = 10
                
        self.list_widget.setGeometry(x, y, popup_width, popup_height)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if not self.list_widget.geometry().contains(pos):
            self.close()
        else:
            super().mousePressEvent(event)

    def on_item_clicked(self, item):
        self.button.setCurrentText(item.text())
        self.close()


class TouchLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._keyboard_shown = False

    def _request_keyboard(self) -> None:
        """Show the on-screen keyboard via the parent window if needed."""
        if is_keyboard_attached():
            return
        win = self.window()
        if win and hasattr(win, "show_keyboard"):
            win.show_keyboard(self)
            self._keyboard_shown = True

    def mousePressEvent(self, event) -> None:
        """Show keyboard on every tap — the reliable path for offscreen windows.

        In WA_DontShowOnScreen rendering, the OS window-manager never activates
        the window, which means QLineEdit.focusInEvent() may never fire even after
        setFocus() is called.  The QMouseEvent, however, IS always delivered by
        inject_input_event() via QApplication.sendEvent(), so overriding
        mousePressEvent is the guaranteed trigger for showing the keyboard.
        """
        super().mousePressEvent(event)
        self._request_keyboard()

    def focusInEvent(self, event) -> None:
        """Secondary path — fires when focus works normally (e.g. native windows)."""
        super().focusInEvent(event)
        self._request_keyboard()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._keyboard_shown = False
        win = self.window()
        if win and hasattr(win, "hide_keyboard"):
            win.hide_keyboard()


class TouchKeyboardOverlay(QWidget):
    def __init__(self, parent: QWidget, line_edit: QLineEdit):
        super().__init__(parent)
        self.line_edit = line_edit
        self.shift_active = False
        
        df = getattr(parent, "_dpi_factor", 1.0)
        bg_color = "#f5f5f5"
        border_color = "rgba(0, 0, 0, 0.12)"
        
        self.setObjectName("TouchKeyboard")
        self.setStyleSheet(f"""
            QWidget#TouchKeyboard {{
                background-color: {bg_color};
                border-top: 1px solid {border_color};
            }}
            QPushButton {{
                background-color: #ffffff;
                color: #121212;
                border: 1px solid {border_color};
                border-radius: {int(6 * df)}px;
                font-family: 'DM Sans', sans-serif;
                font-size: {int(16 * df)}px;
                font-weight: bold;
            }}
            QPushButton:pressed {{
                background-color: #e0e0e0;
            }}
            QPushButton[special="true"] {{
                background-color: #e9e9e9;
            }}
            QPushButton[action="close"] {{
                background-color: #d32f2f;
                color: #ffffff;
                border-color: #d32f2f;
            }}
            QPushButton[action="close"]:pressed {{
                background-color: #b71c1c;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(8 * df), int(8 * df), int(8 * df), int(8 * df))
        layout.setSpacing(int(6 * df))
        
        self.row1_keys = ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"]
        self.row2_keys = ["a", "s", "d", "f", "g", "h", "j", "k", "l"]
        self.row3_keys = ["Shift", "z", "x", "c", "v", "b", "n", "m", "⌫"]
        self.row4_keys = ["Close", "Space", "Enter"]
        
        self.buttons = []
        
        self._build_row(self.row1_keys, layout, df)
        self._build_row(self.row2_keys, layout, df)
        self._build_row(self.row3_keys, layout, df)
        self._build_row(self.row4_keys, layout, df)

    def _build_row(self, keys: list[str], parent_layout: QVBoxLayout, df: float) -> None:
        row_layout = QHBoxLayout()
        row_layout.setSpacing(int(6 * df))
        row_layout.setContentsMargins(0, 0, 0, 0)
        
        for key in keys:
            btn = QPushButton(key)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setMinimumHeight(int(42 * df))
            
            # Dynamic stretch for special keys
            stretch = 1
            if key in ["Shift", "⌫", "Close", "Enter", "Space"]:
                btn.setProperty("special", "true")
                
            if key == "Close":
                btn.setProperty("action", "close")
                btn.clicked.connect(self.close_keyboard)
                stretch = 2
            elif key == "Shift":
                btn.clicked.connect(self.toggle_shift)
                stretch = 2
            elif key == "⌫":
                btn.clicked.connect(self.backspace)
                stretch = 2
            elif key == "Space":
                btn.setText(" ")
                btn.clicked.connect(lambda: self.insert_char(" "))
                stretch = 4
            elif key == "Enter":
                btn.clicked.connect(self.enter_pressed)
                stretch = 2
            else:
                btn.clicked.connect(lambda checked, k=key: self.insert_char(k))
                
            self.buttons.append(btn)
            row_layout.addWidget(btn, stretch=stretch)
            
        parent_layout.addLayout(row_layout)

    def insert_char(self, char: str) -> None:
        if self.line_edit:
            text = char.upper() if self.shift_active else char.lower()
            self.line_edit.insert(text)
            if self.shift_active:
                self.toggle_shift()

    def backspace(self) -> None:
        if self.line_edit:
            self.line_edit.backspace()

    def toggle_shift(self) -> None:
        self.shift_active = not self.shift_active
        for btn in self.buttons:
            text = btn.text()
            if len(text) == 1 and text.isalpha():
                btn.setText(text.upper() if self.shift_active else text.lower())

    def close_keyboard(self) -> None:
        win = self.window()
        if win and hasattr(win, "hide_keyboard"):
            win.hide_keyboard()

    def enter_pressed(self) -> None:
        if self.line_edit:
            self.line_edit.returnPressed.emit()
        self.close_keyboard()
