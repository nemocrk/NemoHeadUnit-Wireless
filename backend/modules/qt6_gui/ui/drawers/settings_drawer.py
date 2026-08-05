"""
settings_drawer.py — System Settings Slide-Over Drawer with Schema-Aware Dynamic Form Builder.

Dynamic system configuration editor interacting with config_manager via REST / ZMQ APIs.
"""

import json
import urllib.request
import urllib.parse
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

MODULE_NAMES = {
    "channel_manager": "⚙ Channel Manager & SDR",
    "tcp_server": "⚡ TCP Server",
    "connectivity_manager": "📶 Connectivity & AP",
    "proxy": "🌐 Gateway Proxy",
    "qt6_gui": "🖥 Qt6 GUI",
}


class ConfigFetchThread(QThread):
    """Asynchronous thread for fetching /api/config/all without blocking UI loop."""
    config_loaded = pyqtSignal(dict)
    fetch_failed = pyqtSignal(str)

    def __init__(self, host_port="127.0.0.1:8000"):
        super().__init__()
        self.host_port = host_port

    def run(self):
        try:
            url = f"http://{self.host_port}/api/config/all"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.config_loaded.emit(data)
        except Exception as exc:
            self.fetch_failed.emit(str(exc))


class ConfigSaveThread(QThread):
    """Asynchronous thread for posting config update to /api/config/{module}."""
    save_finished = pyqtSignal(bool, str)

    def __init__(self, module_name: str, config_dict: dict, host_port="127.0.0.1:8000"):
        super().__init__()
        self.module_name = module_name
        self.config_dict = config_dict
        self.host_port = host_port

    def run(self):
        try:
            url = f"http://{self.host_port}/api/config/{self.module_name}"
            payload = json.dumps(self.config_dict).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status in (200, 201):
                    self.save_finished.emit(True, f"Settings saved for {self.module_name}")
                else:
                    self.save_finished.emit(False, f"HTTP Error {resp.status}")
        except Exception as exc:
            self.save_finished.emit(False, str(exc))


class SettingsDrawerWidget(QWidget):
    """
    System Settings Drawer with Schema-Aware Dynamic Form Controls.
    """

    close_clicked = pyqtSignal()
    save_config_requested = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(380)

        self.all_config = {}
        self.active_module = "channel_manager"
        self.field_inputs = {}

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)

        # Drawer Header
        header_layout = QHBoxLayout()
        title_label = QLabel("System Settings", self)
        title_label.setProperty("class", "drawer-title")
        header_layout.addWidget(title_label)

        close_btn = QPushButton("×", self)
        close_btn.setProperty("class", "close-btn")
        close_btn.clicked.connect(self.close_clicked.emit)
        header_layout.addWidget(close_btn)
        self.layout.addLayout(header_layout)

        # Module Tabs Container
        self.tabs_layout = QHBoxLayout()
        self.tabs_layout.setSpacing(6)
        self.tabs_widget = QWidget(self)
        self.tabs_widget.setLayout(self.tabs_layout)
        
        tabs_scroll = QScrollArea(self)
        tabs_scroll.setFixedHeight(44)
        tabs_scroll.setWidgetResizable(True)
        tabs_scroll.setWidget(self.tabs_widget)
        tabs_scroll.setStyleSheet("border: none; background-color: transparent;")
        self.layout.addWidget(tabs_scroll)

        # Form Scroll Area
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("border: none; background-color: transparent;")

        self.form_widget = QWidget()
        self.form_layout = QVBoxLayout(self.form_widget)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(12)
        self.scroll_area.setWidget(self.form_widget)
        self.layout.addWidget(self.scroll_area)

        # Status & Save Button
        self.lbl_status = QLabel("", self)
        self.lbl_status.setStyleSheet("color: #3fb950; font-weight: 500;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.lbl_status)

        self.save_btn = QPushButton("💾 Save & Apply Settings", self)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #238636;
                color: #ffffff;
                border: 1px solid #2ea043;
                border-radius: 6px;
                padding: 10px;
                font-weight: 600;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2ea043;
            }
        """)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.layout.addWidget(self.save_btn)

        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_all_config()

    def refresh_all_config(self):
        """Fetch /api/config/all asynchronously."""
        self.lbl_status.setText("Loading live settings...")
        self.fetch_thread = ConfigFetchThread()
        self.fetch_thread.config_loaded.connect(self._on_config_loaded)
        self.fetch_thread.fetch_failed.connect(self._on_config_failed)
        self.fetch_thread.start()

    def _on_config_loaded(self, data: dict):
        self.all_config = data
        self.lbl_status.setText("")
        self._build_module_tabs()
        self.render_active_module_form()

    def _on_config_failed(self, err_msg: str):
        self.lbl_status.setText(f"Load notice: {err_msg}")

    def _build_module_tabs(self):
        while self.tabs_layout.count():
            item = self.tabs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        modules = list(self.all_config.keys()) if self.all_config else ["channel_manager", "tcp_server", "connectivity_manager", "proxy", "qt6_gui"]
        if self.active_module not in modules:
            self.active_module = modules[0]

        for mod in modules:
            btn = QPushButton(MODULE_NAMES.get(mod, mod), self)
            is_active = (mod == self.active_module)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {'#1f6feb' if is_active else '#21262d'};
                    color: {'#ffffff' if is_active else '#8b949e'};
                    border: 1px solid {'#388bfd' if is_active else '#30363d'};
                    border-radius: 14px;
                    padding: 6px 12px;
                    font-weight: {'600' if is_active else 'normal'};
                }}
            """)
            btn.clicked.connect(lambda checked, m=mod: self._select_module(m))
            self.tabs_layout.addWidget(btn)

    def _select_module(self, mod_name: str):
        self.active_module = mod_name
        self._build_module_tabs()
        self.render_active_module_form()

    def render_active_module_form(self):
        """Build dynamic form for active module using schema & config dicts."""
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.field_inputs.clear()

        mod_entry = self.all_config.get(self.active_module, {})
        if isinstance(mod_entry, dict) and "config" in mod_entry:
            mod_config = mod_entry.get("config", {})
            mod_schema = mod_entry.get("schema", {}) or {}
        elif isinstance(mod_entry, dict):
            mod_config = mod_entry
            mod_schema = {}
        else:
            mod_config = {}
            mod_schema = {}

        # Section Header
        sec_label = QLabel(f"⚙ {MODULE_NAMES.get(self.active_module, self.active_module)}", self)
        sec_label.setStyleSheet("color: #58a6ff; font-weight: 600; font-size: 15px; margin-bottom: 4px;")
        self.form_layout.addWidget(sec_label)

        grid_frame = QFrame(self)
        grid_frame.setStyleSheet("background-color: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 8px;")
        form = QFormLayout(grid_frame)
        form.setSpacing(10)

        for key, val in mod_config.items():
            if key in ("channels", "_schema"):
                continue

            field_def = mod_schema.get(key, {}) if isinstance(mod_schema, dict) else {}
            field_type = field_def.get("type", "")

            label = QLabel(key.replace("_", " ").title(), self)
            label.setStyleSheet("color: #8b949e; font-weight: 500;")

            if field_type == "enum" and "choices" in field_def:
                cb = QComboBox(self)
                cb.setStyleSheet("background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
                for choice in field_def["choices"]:
                    cb.addItem(str(choice), choice)
                idx = cb.findData(val)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
                self.field_inputs[key] = (cb, "enum")
                form.addRow(label, cb)
            elif field_type == "bool" or isinstance(val, bool):
                cb = QComboBox(self)
                cb.setStyleSheet("background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
                cb.addItem("True", True)
                cb.addItem("False", False)
                cb.setCurrentIndex(0 if val else 1)
                self.field_inputs[key] = (cb, "bool")
                form.addRow(label, cb)
            elif field_type == "int" or isinstance(val, int):
                sb = QSpinBox(self)
                min_val = field_def.get("min", 0)
                max_val = field_def.get("max", 100000000)
                sb.setRange(int(min_val), int(max_val))
                sb.setValue(int(val))
                sb.setStyleSheet("background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
                self.field_inputs[key] = (sb, "int")
                form.addRow(label, sb)
            else:
                le = QLineEdit(str(val) if val is not None else "", self)
                le.setStyleSheet("background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
                self.field_inputs[key] = (le, "str")
                form.addRow(label, le)

        self.form_layout.addWidget(grid_frame)

        # Active Protocol Channel Descriptors (for channel_manager)
        if "channels" in mod_config and isinstance(mod_config["channels"], list):
            ch_hdr = QLabel("🔌 Active Protocol Channel Descriptors", self)
            ch_hdr.setStyleSheet("color: #3fb950; font-weight: 600; font-size: 14px; margin-top: 8px;")
            self.form_layout.addWidget(ch_hdr)

            for ch in mod_config["channels"]:
                ch_id = ch.get("channel_id", "?")
                ch_name = ch.get("name", f"Channel {ch_id}")
                card = QFrame(self)
                card.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 8px; margin-bottom: 4px;")
                card_lay = QVBoxLayout(card)
                card_lay.setContentsMargins(6, 6, 6, 6)
                
                t_lbl = QLabel(f"Channel {ch_id}: {ch_name}", card)
                t_lbl.setStyleSheet("color: #e6edf3; font-weight: 600;")
                card_lay.addWidget(t_lbl)

                details = json.dumps(ch, indent=2)
                d_lbl = QLabel(details, card)
                d_lbl.setStyleSheet("color: #8b949e; font-family: monospace; font-size: 11px;")
                card_lay.addWidget(d_lbl)
                self.form_layout.addWidget(card)

    def _on_save_clicked(self):
        new_config = {}
        for key, (widget, f_type) in self.field_inputs.items():
            if f_type == "enum":
                new_config[key] = widget.currentData()
            elif f_type == "bool":
                new_config[key] = widget.currentData()
            elif f_type == "int":
                new_config[key] = widget.value()
            else:
                new_config[key] = widget.text()

        self.lbl_status.setText("Saving settings...")
        self.save_thread = ConfigSaveThread(self.active_module, new_config)
        self.save_thread.save_finished.connect(self._on_save_finished)
        self.save_thread.start()

    def _on_save_finished(self, success: bool, msg: str):
        if success:
            self.lbl_status.setText("✅ Settings saved!")
        else:
            self.lbl_status.setText(f"❌ Save error: {msg}")
