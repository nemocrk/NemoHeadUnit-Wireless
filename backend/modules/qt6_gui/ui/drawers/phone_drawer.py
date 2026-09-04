"""
phone_drawer.py — Phone Drawer with Recents, Favorites, Contacts & Dialer Keypad.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QGridLayout,
)
from ..svg_utils import make_svg_icon


class PhoneDrawerWidget(QWidget):
    """
    Phone Drawer Slide-Over Card for Contacts, Recent Calls, Favorites, and Dialer.
    """

    close_clicked = pyqtSignal()
    call_requested = pyqtSignal(str)  # Phone number or contact name to dial
    call_action_triggered = pyqtSignal(str)
    dtmf_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(380)

        self.is_in_call = False
        self._all_contacts: list[dict] = []
        self._all_favorites: list[dict] = []
        self._all_recents: list[dict] = []

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)

        # 1. Header
        header_layout = QHBoxLayout()
        title_label = QLabel("Phone", self)
        title_label.setProperty("class", "drawer-title")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #f0f6fc;")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("×", self)
        close_btn.setProperty("class", "close-btn")
        close_btn.setStyleSheet("""
            QPushButton {
                font-size: 22px;
                color: #8b949e;
                background: transparent;
                border: none;
                padding: 4px 8px;
            }
            QPushButton:hover { color: #f0f6fc; }
        """)
        close_btn.clicked.connect(self.close_clicked.emit)
        header_layout.addWidget(close_btn)
        self.layout.addLayout(header_layout)

        # 2. Tabs: Recents, Favorites, Contacts, Keypad
        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                background: #0d1117;
            }
            QTabBar::tab {
                background: #161b22;
                color: #8b949e;
                font-weight: 600;
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #21262d;
                color: #58a6ff;
                border-bottom: 2px solid #58a6ff;
            }
        """)

        # Tab 1: Recents
        self.recents_list = QListWidget()
        self.recents_list.setStyleSheet("background: transparent; border: none; color: #c9d1d9;")
        self.recents_list.itemClicked.connect(self._on_item_clicked)
        self.tabs.addTab(self.recents_list, "Recents")

        # Tab 2: Favorites
        self.favorites_list = QListWidget()
        self.favorites_list.setStyleSheet("background: transparent; border: none; color: #c9d1d9;")
        self.favorites_list.itemClicked.connect(self._on_item_clicked)
        self.tabs.addTab(self.favorites_list, "Favorites")

        # Tab 3: Contacts with Search Bar
        contacts_tab_widget = QWidget()
        contacts_tab_layout = QVBoxLayout(contacts_tab_widget)
        contacts_tab_layout.setContentsMargins(4, 8, 4, 4)
        contacts_tab_layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search contacts...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: #161b22;
                color: #f0f6fc;
                font-size: 13px;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QLineEdit:focus {
                border-color: #58a6ff;
            }
        """)
        self.search_input.textChanged.connect(self._filter_contacts)
        contacts_tab_layout.addWidget(self.search_input)

        self.contacts_list = QListWidget()
        self.contacts_list.setStyleSheet("background: transparent; border: none; color: #c9d1d9;")
        self.contacts_list.itemClicked.connect(self._on_item_clicked)
        contacts_tab_layout.addWidget(self.contacts_list)

        self.tabs.addTab(contacts_tab_widget, "Contacts")

        # Tab 4: Keypad / Dialer
        keypad_widget = self._create_keypad()
        self.tabs.addTab(keypad_widget, "Keypad")

        self.layout.addWidget(self.tabs)

        # Load initial data from PBAP cache or synthetics
        self._load_initial_pbap_data()

    def _load_initial_pbap_data(self):
        try:
            from shared.hardware.bluez_pbap import BlueZPBAPClient
            client = BlueZPBAPClient()
            self.set_contacts(client.get_contacts())
            self.set_favorites(client.get_favorites())
            self.set_recents(client.get_recents())
        except Exception:
            # Fallback
            self.set_contacts([
                {"name": "Alice Rossi", "primary_phone": "+39 333 111 2222", "favorite": False},
                {"name": "Bob Bianchi", "primary_phone": "+39 340 333 4444", "favorite": False},
                {"name": "Emergency Roadside", "primary_phone": "+39 800 123 456", "favorite": True},
            ])

    def set_contacts(self, contacts: list[dict]):
        self._all_contacts = list(contacts)
        self._filter_contacts(self.search_input.text() if hasattr(self, "search_input") else "")

    def _filter_contacts(self, query: str = ""):
        self.contacts_list.clear()
        q = (query or "").lower().strip()
        for c in self._all_contacts:
            name = c.get("name", "Unknown")
            phone = c.get("primary_phone", "")
            if not q or q in name.lower() or q in phone:
                item = QListWidgetItem(f"👤 {name}\n   {phone}")
                item.setData(Qt.ItemDataRole.UserRole, phone)
                self.contacts_list.addItem(item)

    def set_favorites(self, favorites: list[dict]):
        self._all_favorites = list(favorites)
        self.favorites_list.clear()
        for c in self._all_favorites:
            name = c.get("name", "Unknown")
            phone = c.get("primary_phone", "")
            item = QListWidgetItem(f"★ {name}\n   {phone}")
            item.setData(Qt.ItemDataRole.UserRole, phone)
            self.favorites_list.addItem(item)

    def set_recents(self, recents: list[dict]):
        self._all_recents = list(recents)
        self.recents_list.clear()
        for r in self._all_recents:
            name = r.get("name", "Unknown")
            num = r.get("number", "")
            ts = r.get("timestamp", "")
            kind = r.get("call_type", "CALL").capitalize()
            item = QListWidgetItem(f"{name} ({num})\n   {ts} • {kind}")
            item.setData(Qt.ItemDataRole.UserRole, num)
            self.recents_list.addItem(item)

    def set_in_call(self, in_call: bool):
        self.is_in_call = in_call

    def _on_item_clicked(self, item: QListWidgetItem):
        number = item.data(Qt.ItemDataRole.UserRole)
        if number:
            self.call_requested.emit(number)

    def _create_keypad(self) -> QWidget:
        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(10)

        self.dial_display = QLineEdit()
        self.dial_display.setReadOnly(True)
        self.dial_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dial_display.setStyleSheet("""
            QLineEdit {
                background: #161b22;
                color: #f0f6fc;
                font-size: 22px;
                font-weight: bold;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        vbox.addWidget(self.dial_display)

        grid = QGridLayout()
        grid.setSpacing(8)

        buttons = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("*", 3, 0), ("0", 3, 1), ("#", 3, 2),
        ]

        btn_style = """
            QPushButton {
                background: #21262d;
                color: #f0f6fc;
                font-size: 18px;
                font-weight: bold;
                border-radius: 8px;
                min-height: 44px;
            }
            QPushButton:hover { background: #30363d; }
            QPushButton:pressed { background: #388bfd; }
        """

        for label, r, c in buttons:
            btn = QPushButton(label)
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda _, ch=label: self._append_digit(ch))
            grid.addWidget(btn, r, c)

        vbox.addLayout(grid)

        # Call & Backspace bottom row
        bottom_row = QHBoxLayout()
        btn_back = QPushButton("⌫")
        btn_back.setStyleSheet(btn_style)
        btn_back.clicked.connect(self._backspace_digit)
        bottom_row.addWidget(btn_back)

        btn_call = QPushButton(" Call")
        btn_call.setIcon(make_svg_icon("phone", color="#ffffff", size=18))
        btn_call.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: #ffffff;
                font-size: 16px;
                font-weight: bold;
                border-radius: 8px;
                min-height: 44px;
            }
            QPushButton:hover { background: #2ea043; }
        """)
        btn_call.clicked.connect(self._trigger_call)
        bottom_row.addWidget(btn_call)

        vbox.addLayout(bottom_row)
        return widget

    def _append_digit(self, char: str):
        self.dial_display.setText(self.dial_display.text() + char)
        if self.is_in_call:
            self.dtmf_requested.emit(char)

    def _backspace_digit(self):
        txt = self.dial_display.text()
        if txt:
            self.dial_display.setText(txt[:-1])

    def _trigger_call(self):
        num = self.dial_display.text().strip()
        if num:
            self.call_requested.emit(num)
            self.call_action_triggered.emit(f"dial:{num}")
