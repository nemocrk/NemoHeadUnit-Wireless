"""
settings_drawer.py — System Settings Slide-Over Drawer with Schema-Aware Dynamic Form Builder.

Dynamic system configuration editor interacting with config_manager via REST / ZMQ APIs.
"""

import copy
import json
import urllib.request
import urllib.parse
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QEvent, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

MODULE_NAMES = {
    "channel_manager": "⚙ Channel Manager & SDR",
    "media_server": "🎬 Media Server & Video/Audio",
    "tcp_server": "⚡ TCP Server",
    "connectivity_manager": "📶 Connectivity & AP",
    "proxy": "🌐 Gateway Proxy",
    "qt6_gui": "🖥 Qt6 GUI",
}


class DragScrollArea(QScrollArea):
    """
    Touch and Mouse-drag enabled QScrollArea with wheel support.
    Captures mouse drag even when initiated on top of clickable child buttons.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._dragging = False
        self._last_pos = None
        self._start_pos = None
        self._drag_threshold = 6
        self.viewport().installEventFilter(self)
        scroller = QScroller.scroller(self.viewport())
        props = scroller.scrollerProperties()
        props.setScrollMetric(QScrollerProperties.ScrollMetric.DragStartDistance, 0.005)
        props.setScrollMetric(QScrollerProperties.ScrollMetric.MousePressEventDelay, 0.0)
        scroller.setScrollerProperties(props)
        QScroller.grabGesture(self.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture)

    def register_child(self, child: QWidget):
        """Install drag filter on interactive child widgets like QPushButton."""
        child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._start_pos = event.globalPosition().toPoint()
            self._last_pos = self._start_pos
            self._dragging = False
        elif event.type() == QEvent.Type.MouseMove and self._last_pos is not None:
            current_pos = event.globalPosition().toPoint()
            delta = current_pos - self._last_pos
            if not self._dragging and (current_pos - self._start_pos).manhattanLength() > self._drag_threshold:
                self._dragging = True
            if self._dragging:
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
                self._last_pos = current_pos
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            self._last_pos = None
            self._start_pos = None
            self._dragging = False
            if was_dragging:
                return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if self.verticalScrollBar().maximum() > 0:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta)
        else:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
        event.accept()


class ConfigFetchThread(QThread):
    """Asynchronous thread for fetching /api/config/all and /api/media/audio_devices without blocking UI loop."""
    config_loaded = pyqtSignal(dict, dict)
    fetch_failed = pyqtSignal(str)

    def __init__(self, host_port="127.0.0.1:8000"):
        super().__init__()
        self.host_port = host_port

    def run(self):
        data = {}
        audio_devices = {}
        try:
            url = f"http://{self.host_port}/api/config/all"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            data = {}

        try:
            dev_url = f"http://{self.host_port}/api/media/audio_devices"
            dev_req = urllib.request.Request(dev_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(dev_req, timeout=3.0) as dev_resp:
                audio_devices = json.loads(dev_resp.read().decode("utf-8"))
        except Exception:
            pass

        if data:
            self.config_loaded.emit(data, audio_devices)
        else:
            # Fallback to local default skeleton if backend REST is not yet reachable
            fallback = {
                "media_server": {"config": {"transport_mode": "auto", "jpeg_quality": 75, "audio_output_sink": "default", "audio_input_source": "default"}},
                "channel_manager": {"config": {}},
                "tcp_server": {"config": {"port": 5000}},
                "connectivity_manager": {"config": {}},
                "proxy": {"config": {"public_port": 8000}},
                "qt6_gui": {"config": {"fullscreen": False}},
            }
            self.config_loaded.emit(fallback, audio_devices)


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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("class", "drawer-card")
        self.setMinimumWidth(380)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)

        self.all_config = {}
        self.audio_devices = {}
        self.active_module = "channel_manager"
        self.field_inputs = {}  # key -> (widget, type_str)

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

        # Module Category Horizontal Navigation Bar with Left/Right Arrows
        tabs_nav_wrapper = QHBoxLayout()
        tabs_nav_wrapper.setSpacing(4)
        tabs_nav_wrapper.setContentsMargins(0, 0, 0, 0)

        self.btn_scroll_left = QPushButton("‹", self)
        self.btn_scroll_left.setFixedSize(24, 28)
        self.btn_scroll_left.setStyleSheet("""
            QPushButton {
                background-color: #161b22;
                color: #8b949e;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #21262d;
                color: #58a6ff;
            }
        """)
        self.btn_scroll_left.clicked.connect(lambda: self.tabs_scroll.horizontalScrollBar().setValue(
            self.tabs_scroll.horizontalScrollBar().value() - 100
        ))
        tabs_nav_wrapper.addWidget(self.btn_scroll_left)

        self.tabs_scroll = DragScrollArea(self)
        self.tabs_scroll.setWidgetResizable(True)
        self.tabs_scroll.setFixedHeight(44)
        self.tabs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs_scroll.setStyleSheet("background: transparent; border: none;")

        tabs_container = QWidget()
        tabs_container.setStyleSheet("background: transparent;")
        self.tabs_layout = QHBoxLayout(tabs_container)
        self.tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs_layout.setSpacing(8)
        self.tabs_scroll.setWidget(tabs_container)
        tabs_nav_wrapper.addWidget(self.tabs_scroll, 1)

        self.btn_scroll_right = QPushButton("›", self)
        self.btn_scroll_right.setFixedSize(24, 28)
        self.btn_scroll_right.setStyleSheet("""
            QPushButton {
                background-color: #161b22;
                color: #8b949e;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #21262d;
                color: #58a6ff;
            }
        """)
        self.btn_scroll_right.clicked.connect(lambda: self.tabs_scroll.horizontalScrollBar().setValue(
            self.tabs_scroll.horizontalScrollBar().value() + 100
        ))
        tabs_nav_wrapper.addWidget(self.btn_scroll_right)

        self.layout.addLayout(tabs_nav_wrapper)

        # Dynamic Settings Form (Scrollable)
        self.form_scroll = DragScrollArea(self)
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setStyleSheet("background: transparent; border: none;")
        self.form_container = QWidget()
        self.form_container.setStyleSheet("background: transparent;")
        self.form_layout = QVBoxLayout(self.form_container)
        self.form_layout.setContentsMargins(0, 4, 0, 4)
        self.form_layout.setSpacing(8)
        self.form_scroll.setWidget(self.form_container)
        self.layout.addWidget(self.form_scroll, 1)

        # Status Notice
        self.lbl_status = QLabel("", self)
        self.lbl_status.setStyleSheet("color: #8b949e; font-size: 12px;")
        self.layout.addWidget(self.lbl_status)

        # Action Buttons
        self.save_btn = QPushButton("💾 Save && Apply Settings", self)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #238636;
                color: #ffffff;
                border: 1px solid rgba(240, 246, 252, 0.1);
                border-radius: 6px;
                padding: 10px 16px;
                font-weight: 600;
                font-size: 13px;
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

    def _on_config_loaded(self, data: dict, audio_devices: dict = None):
        self.all_config = data
        self.audio_devices = audio_devices or {}
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

        modules = list(self.all_config.keys()) if self.all_config else ["channel_manager", "media_server", "tcp_server", "connectivity_manager", "proxy", "qt6_gui"]
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
            self.tabs_scroll.register_child(btn)
            self.tabs_layout.addWidget(btn)
            if is_active:
                QTimer.singleShot(20, lambda b=btn: self.tabs_scroll.ensureWidgetVisible(b))

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

            if key == "audio_output_sink":
                cb = QComboBox(self)
                cb.setStyleSheet("background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
                sinks = self.audio_devices.get("sinks", [])
                if not sinks:
                    sinks = [{"id": "default", "name": "System Default Output"}]
                for s in sinks:
                    sid = s.get("id", "default")
                    sname = s.get("name", sid)
                    cb.addItem(f"🔊 {sname}", sid)
                if val and cb.findData(val) < 0:
                    cb.addItem(f"🔊 {val}", val)
                idx = cb.findData(val)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
                self.field_inputs[key] = (cb, "enum")
                form.addRow(label, cb)
            elif key == "audio_input_source":
                cb = QComboBox(self)
                cb.setStyleSheet("background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
                sources = self.audio_devices.get("sources", [])
                if not sources:
                    sources = [{"id": "default", "name": "System Default Input"}]
                for src in sources:
                    sid = src.get("id", "default")
                    sname = src.get("name", sid)
                    cb.addItem(f"🎤 {sname}", sid)
                if val and cb.findData(val) < 0:
                    cb.addItem(f"🎤 {val}", val)
                idx = cb.findData(val)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
                self.field_inputs[key] = (cb, "enum")
                form.addRow(label, cb)
            elif field_type == "enum" and "choices" in field_def:
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
            self.channels_data = copy.deepcopy(mod_config["channels"])
            ch_hdr = QLabel(f"🔌 Active Protocol Channel Descriptors ({len(self.channels_data)})", self)
            ch_hdr.setStyleSheet("color: #3fb950; font-weight: 600; font-size: 14px; margin-top: 10px; margin-bottom: 4px;")
            self.form_layout.addWidget(ch_hdr)

            for idx, ch in enumerate(self.channels_data):
                card = self._render_channel_card(ch, idx)
                self.form_layout.addWidget(card)

    def _render_channel_card(self, ch: dict, index: int) -> QFrame:
        card = QFrame(self)
        card.setStyleSheet("""
            QFrame {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 6px;
                margin-bottom: 4px;
            }
        """)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(8, 8, 8, 8)
        card_lay.setSpacing(6)

        ch_id = ch.get("channel_id", index + 1)
        name, color = self._get_channel_info(ch)

        # Header with channel tag and title
        hdr_lay = QHBoxLayout()
        hdr_lay.setContentsMargins(0, 0, 0, 0)
        ch_tag = QLabel(f"CH {ch_id}", card)
        ch_tag.setStyleSheet(f"""
            background-color: {color}22;
            color: {color};
            border: 1px solid {color}66;
            border-radius: 4px;
            padding: 2px 6px;
            font-size: 11px;
            font-weight: bold;
        """)
        hdr_lay.addWidget(ch_tag)

        t_lbl = QLabel(name, card)
        t_lbl.setStyleSheet("color: #e6edf3; font-weight: 600; font-size: 13px;")
        hdr_lay.addWidget(t_lbl)
        hdr_lay.addStretch(1)
        card_lay.addLayout(hdr_lay)

        # Content based on channel type
        if "av_channel" in ch and "VIDEO" in ch.get("av_channel", {}).get("codec", ""):
            vcfgs = ch.get("av_channel", {}).get("video_configs", [])
            if not vcfgs:
                vcfgs.append({})
                ch["av_channel"]["video_configs"] = vcfgs
            vcfg = vcfgs[0]

            form = QFormLayout()
            form.setSpacing(6)

            # Resolution preset
            res_cb = QComboBox(card)
            res_cb.setStyleSheet("background-color: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
            res_opts = [
                ("1280x720 (720p)", "VIDEO_1280x720"),
                ("1920x1080 (1080p)", "VIDEO_1920x1080"),
                ("800x480 (480p)", "VIDEO_800x480"),
            ]
            current_res = vcfg.get("video_resolution", "VIDEO_1280x720")
            for label, val in res_opts:
                res_cb.addItem(label, val)
            idx = res_cb.findData(current_res)
            if idx >= 0:
                res_cb.setCurrentIndex(idx)

            def _on_res_changed(i, cfg=vcfg, cb=res_cb):
                cfg["video_resolution"] = cb.itemData(i)
            res_cb.currentIndexChanged.connect(_on_res_changed)
            form.addRow(self._styled_label("Resolution:"), res_cb)

            # FPS
            fps_cb = QComboBox(card)
            fps_cb.setStyleSheet("background-color: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")
            fps_opts = [("30 FPS", "_30"), ("60 FPS", "_60")]
            current_fps = vcfg.get("video_fps", "_30")
            for label, val in fps_opts:
                fps_cb.addItem(label, val)
            idx = fps_cb.findData(current_fps)
            if idx >= 0:
                fps_cb.setCurrentIndex(idx)

            def _on_fps_changed(i, cfg=vcfg, cb=fps_cb):
                cfg["video_fps"] = cb.itemData(i)
            fps_cb.currentIndexChanged.connect(_on_fps_changed)
            form.addRow(self._styled_label("Frame Rate:"), fps_cb)

            # DPI
            dpi_sb = QSpinBox(card)
            dpi_sb.setRange(80, 400)
            dpi_sb.setSingleStep(10)
            dpi_sb.setValue(int(vcfg.get("dpi", 140)))
            dpi_sb.setStyleSheet("background-color: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 4px; border-radius: 4px;")

            def _on_dpi_changed(v, cfg=vcfg):
                cfg["dpi"] = v
            dpi_sb.valueChanged.connect(_on_dpi_changed)
            form.addRow(self._styled_label("Display DPI:"), dpi_sb)

            card_lay.addLayout(form)

        elif "av_channel" in ch:
            av = ch.get("av_channel", {})
            audio_type = av.get("audio_type", "MEDIA")
            sample_rate = av.get("sample_rate", 48000)
            channels_cnt = av.get("number_of_channels", 2)

            form = QFormLayout()
            form.setSpacing(6)

            type_lbl = QLabel(str(audio_type), card)
            type_lbl.setStyleSheet("color: #e6edf3; font-weight: bold;")
            form.addRow(self._styled_label("Audio Stream:"), type_lbl)

            sr_lbl = QLabel(f"{sample_rate} Hz (16-bit PCM)", card)
            sr_lbl.setStyleSheet("color: #8b949e;")
            form.addRow(self._styled_label("Sample Rate:"), sr_lbl)

            ch_mode = "Stereo (2 ch)" if channels_cnt == 2 else "Mono (1 ch)"
            mode_lbl = QLabel(ch_mode, card)
            mode_lbl.setStyleSheet("color: #8b949e;")
            form.addRow(self._styled_label("Channels:"), mode_lbl)

            card_lay.addLayout(form)

        elif "input_channel" in ch:
            inp = ch.get("input_channel", {})
            ts_cfgs = inp.get("touch_screen_configs", [])
            ts_cfg = ts_cfgs[0] if ts_cfgs else {}

            form = QFormLayout()
            form.setSpacing(6)

            w = ts_cfg.get("width", 1280)
            h = ts_cfg.get("height", 720)
            res_lbl = QLabel(f"{w} × {h} (Touchscreen Digitizer)", card)
            res_lbl.setStyleSheet("color: #e6edf3;")
            form.addRow(self._styled_label("Touch Area:"), res_lbl)

            key_cnt = len(inp.get("supported_keycodes", []))
            key_lbl = QLabel(f"{key_cnt} Media & Navigation Keys Configured", card)
            key_lbl.setStyleSheet("color: #8b949e;")
            form.addRow(self._styled_label("Supported Keys:"), key_lbl)

            card_lay.addLayout(form)

        elif "sensor_channel" in ch:
            sensors = ch.get("sensor_channel", {}).get("sensors", [])
            s_lay = QHBoxLayout()
            s_lay.setSpacing(6)
            for s in sensors:
                stype = s.get("type", "UNKNOWN") if isinstance(s, dict) else str(s)
                badge = QLabel(stype, card)
                badge.setStyleSheet("""
                    background-color: #21262d;
                    color: #bc8cff;
                    border: 1px solid #30363d;
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-size: 11px;
                """)
                s_lay.addWidget(badge)
            s_lay.addStretch(1)
            card_lay.addLayout(s_lay)

        elif "av_input_channel" in ch:
            form = QFormLayout()
            form.setSpacing(6)
            mic_lbl = QLabel("16000 Hz Mono (Voice Recognition Input)", card)
            mic_lbl.setStyleSheet("color: #8b949e;")
            form.addRow(self._styled_label("Format:"), mic_lbl)
            card_lay.addLayout(form)

        else:
            keys_info = [f"{k}: {v}" for k, v in ch.items() if k != "channel_id"]
            info_str = " | ".join(keys_info) if keys_info else "Active channel descriptor"
            d_lbl = QLabel(info_str, card)
            d_lbl.setWordWrap(True)
            d_lbl.setStyleSheet("color: #8b949e; font-size: 12px;")
            card_lay.addWidget(d_lbl)

        return card

    def _styled_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setStyleSheet("color: #8b949e; font-size: 12px; font-weight: 500;")
        return lbl

    def _get_channel_info(self, ch: dict) -> tuple[str, str]:
        if "input_channel" in ch:
            return "Touch Screen Input", "#58a6ff"
        if "sensor_channel" in ch:
            return "Vehicle Sensors", "#bc8cff"
        if "av_channel" in ch:
            av = ch.get("av_channel", {})
            codec = av.get("codec", "")
            audio_type = av.get("audio_type", "")
            if "VIDEO" in codec:
                return "H.264 Video Stream", "#3fb950"
            return f"Audio Stream [{audio_type or 'MEDIA'}]", "#f0883e"
        if "av_input_channel" in ch:
            return "Microphone Voice Input", "#db61a2"
        if "bluetooth_channel" in ch:
            return "Bluetooth Handshake", "#79c0ff"
        if "wifi_channel" in ch:
            return "Wi-Fi Projection", "#56d364"
        if "navigation_channel" in ch:
            return "Navigation TBT Status", "#e3b341"
        if "media_info_channel" in ch:
            return "Media Playback Status", "#f0883e"
        return f"Channel {ch.get('channel_id', '?')}", "#8b949e"

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

        if self.active_module == "channel_manager" and hasattr(self, "channels_data") and self.channels_data:
            new_config["channels"] = self.channels_data

        self.lbl_status.setText("Saving settings...")
        self.save_thread = ConfigSaveThread(self.active_module, new_config)
        self.save_thread.save_finished.connect(self._on_save_finished)
        self.save_thread.start()

    def _on_save_finished(self, success: bool, msg: str):
        if success:
            self.lbl_status.setText("✅ Settings saved!")
        else:
            self.lbl_status.setText(f"❌ Save error: {msg}")
