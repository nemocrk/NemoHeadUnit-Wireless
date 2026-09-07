"""
phone_drawer.py — Phone Drawer with Recents, Favorites, Contacts & Dialer Keypad.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
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
    QFrame,
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
    sync_requested = pyqtSignal()

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

        header_layout.addStretch()

        self.sync_btn = QPushButton(" Sync", self)
        self.sync_btn.setIcon(make_svg_icon("sync", color="#8b949e", size=14))
        self.sync_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                color: #8b949e;
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                color: #f0f6fc;
                border-color: #58a6ff;
            }
            QPushButton:disabled {
                color: #484f58;
                border-color: #21262d;
            }
        """)
        self.sync_btn.setToolTip("Sync contacts and recents from connected Bluetooth phone")
        self.sync_btn.clicked.connect(self._on_sync_clicked)
        header_layout.addWidget(self.sync_btn)

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

        # 2. Active Call Banner (hidden until call is active/ringing)
        self.active_call_banner = self._create_active_call_banner()
        self.layout.addWidget(self.active_call_banner)
        self.active_call_banner.hide()

        # 3. Tabs: Recents, Favorites, Contacts, Keypad
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

        self.contacts_tab = contacts_tab_widget
        self.tabs.addTab(contacts_tab_widget, "Contacts")

        # Tab 4: Keypad / Dialer
        self.keypad_tab = self._create_keypad()
        self.tabs.addTab(self.keypad_tab, "Keypad")

        self.layout.addWidget(self.tabs)

        # Load initial data from PBAP cache and set tab visibility
        self._load_initial_pbap_data()
        self._update_pbap_tabs_visibility()

    def _update_pbap_tabs_visibility(self):
        """Hide PBAP tabs (Recents, Favorites, Contacts) when empty/not loaded."""
        has_pbap = bool(self._all_contacts or self._all_favorites or self._all_recents)
        self.tabs.setTabVisible(self.tabs.indexOf(self.recents_list), has_pbap)
        self.tabs.setTabVisible(self.tabs.indexOf(self.favorites_list), has_pbap)
        self.tabs.setTabVisible(self.tabs.indexOf(self.contacts_tab), has_pbap)
        if not has_pbap:
            self.tabs.setCurrentWidget(self.keypad_tab)

    def _create_active_call_banner(self) -> QWidget:
        banner = QFrame(self)
        banner.setStyleSheet("""
            QFrame {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 6px;
            }
        """)
        layout = QVBoxLayout(banner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Top row: status + timer
        top_row = QHBoxLayout()
        self.call_status_label = QLabel("Call in Progress", banner)
        self.call_status_label.setStyleSheet("font-weight: bold; color: #3fb950; font-size: 13px;")
        top_row.addWidget(self.call_status_label)

        self.call_timer_label = QLabel("00:00", banner)
        self.call_timer_label.setStyleSheet("color: #8b949e; font-size: 12px; font-family: monospace;")
        top_row.addWidget(self.call_timer_label, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top_row)

        # Contact info
        self.call_contact_label = QLabel("Unknown Caller", banner)
        self.call_contact_label.setStyleSheet("color: #f0f6fc; font-size: 15px; font-weight: 600;")
        layout.addWidget(self.call_contact_label)

        self.call_number_label = QLabel("", banner)
        self.call_number_label.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(self.call_number_label)

        # Action buttons: Answer, Mute, Hangup
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_answer = QPushButton(" Answer", banner)
        self.btn_answer.setIcon(make_svg_icon("phone", color="#ffffff", size=14))
        self.btn_answer.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #2ea043; }
        """)
        self.btn_answer.clicked.connect(lambda: self.call_action_triggered.emit("answer"))
        btn_row.addWidget(self.btn_answer)
        self.btn_answer.hide()

        self.btn_mute = QPushButton(" Mute", banner)
        self.btn_mute.setIcon(make_svg_icon("mic", color="#8b949e", size=14))
        self.btn_mute.setStyleSheet("""
            QPushButton {
                background: #21262d;
                color: #c9d1d9;
                font-weight: 500;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #30363d; color: #f0f6fc; }
        """)
        self.btn_mute.clicked.connect(lambda: self.call_action_triggered.emit("mute"))
        btn_row.addWidget(self.btn_mute)

        self.btn_hangup = QPushButton(" End Call", banner)
        self.btn_hangup.setIcon(make_svg_icon("call_end", color="#ffffff", size=14))
        self.btn_hangup.setStyleSheet("""
            QPushButton {
                background: #da3633;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #f85149; }
        """)
        self.btn_hangup.clicked.connect(lambda: self.call_action_triggered.emit("hangup"))
        btn_row.addWidget(self.btn_hangup)

        layout.addLayout(btn_row)
        return banner

    def _on_sync_clicked(self):
        if hasattr(self, "sync_btn"):
            self.sync_btn.setEnabled(False)
            self.sync_btn.setText(" Syncing...")
            QTimer.singleShot(6000, self._reset_sync_button)
        self.sync_requested.emit()

    def _reset_sync_button(self):
        if hasattr(self, "sync_btn"):
            self.sync_btn.setEnabled(True)
            self.sync_btn.setText(" Sync")

    def _load_initial_pbap_data(self):
        try:
            from shared.hardware.bluez_pbap import BlueZPBAPClient
            client = BlueZPBAPClient()
            self.set_contacts(client.get_contacts())
            self.set_favorites(client.get_favorites())
            self.set_recents(client.get_recents())
        except Exception:
            self.set_contacts([])
            self.set_favorites([])
            self.set_recents([])

    def set_contacts(self, contacts: list[dict]):
        self._all_contacts = list(contacts)
        self._filter_contacts(self.search_input.text() if hasattr(self, "search_input") else "")
        self._update_pbap_tabs_visibility()
        self._reset_sync_button()

    def _filter_contacts(self, query: str = ""):
        self.contacts_list.clear()
        q = (query or "").lower().strip()
        matched = 0
        for c in self._all_contacts:
            name = c.get("name", "Unknown")
            phone = c.get("primary_phone", "")
            if not q or q in name.lower() or q in phone:
                item = QListWidgetItem(f"👤 {name}\n   {phone}")
                item.setData(Qt.ItemDataRole.UserRole, phone)
                self.contacts_list.addItem(item)
                matched += 1

        if matched == 0:
            if not self._all_contacts:
                item = QListWidgetItem("No contacts synced.\nTap Sync to import from phone.")
            else:
                item = QListWidgetItem(f"No contacts matching '{query}'.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
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

        if not self._all_favorites:
            item = QListWidgetItem("No favorites found.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.favorites_list.addItem(item)
        self._update_pbap_tabs_visibility()

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

        if not self._all_recents:
            item = QListWidgetItem("No recent calls.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recents_list.addItem(item)
        self._update_pbap_tabs_visibility()
        self._reset_sync_button()

    def set_in_call(self, in_call: bool):
        self.is_in_call = in_call
        if not in_call:
            self.active_call_banner.hide()

    def update_call_state(
        self,
        is_in_call: bool,
        call_state: str = "IDLE",
        caller_name: str = "",
        caller_number: str = "",
        duration_seconds: int = 0,
    ):
        self.is_in_call = is_in_call
        if not is_in_call or call_state in ("IDLE", "TERMINATED", "DISCONNECTED"):
            self.active_call_banner.hide()
            return

        self.active_call_banner.show()
        mins = duration_seconds // 60
        secs = duration_seconds % 60
        time_str = f"{mins:02d}:{secs:02d}"

        if call_state in ("RINGING", "INCOMING"):
            self.call_status_label.setText("Incoming Call...")
            self.call_status_label.setStyleSheet("font-weight: bold; color: #d29922; font-size: 13px;")
            self.btn_answer.show()
            self.call_timer_label.setText("")
        elif call_state in ("DIALING", "ALERTING"):
            self.call_status_label.setText("Calling...")
            self.call_status_label.setStyleSheet("font-weight: bold; color: #58a6ff; font-size: 13px;")
            self.btn_answer.hide()
            self.call_timer_label.setText("")
        else:
            self.call_status_label.setText("Active Call")
            self.call_status_label.setStyleSheet("font-weight: bold; color: #3fb950; font-size: 13px;")
            self.btn_answer.hide()
            self.call_timer_label.setText(time_str)

        display_name = caller_name or caller_number or "Unknown"
        self.call_contact_label.setText(display_name)
        self.call_number_label.setText(caller_number if caller_name else "")
        self.call_number_label.setVisible(bool(caller_name and caller_number))

    def _on_item_clicked(self, item: QListWidgetItem):
        number = item.data(Qt.ItemDataRole.UserRole)
        if number:
            clean_number = str(number).strip()
            self.dial_display.setText(clean_number)
            self.tabs.setCurrentWidget(self.keypad_tab)

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
            self.call_action_triggered.emit(f"dtmf:{char}")

    def _backspace_digit(self):
        txt = self.dial_display.text()
        if txt:
            self.dial_display.setText(txt[:-1])

    def _trigger_call(self):
        num = self.dial_display.text().strip()
        if num:
            self.update_call_state(is_in_call=True, call_state="DIALING", caller_number=num)
            self.call_requested.emit(num)
            self.call_action_triggered.emit(f"dial:{num}")
