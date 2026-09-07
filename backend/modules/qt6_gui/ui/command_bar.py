"""
command_bar.py — Floating Command Bar Widget for Qt6.

Bottom command bar featuring Home, Status Dot, Play/Pause, Volume, Exit,
Compact Phone Status Pill (Signal + Battery), and Menu Drawer buttons.
"""

from typing import Optional
from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget

from .svg_utils import make_svg_icon


class PhoneStatusPill(QFrame):
    """
    Compact status pill in the command bar showing cellular signal and battery level.
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("phone-status-pill")
        self.setFixedHeight(42)
        self.setFixedWidth(108)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame#phone-status-pill {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 21px;
            }
            QFrame#phone-status-pill:hover {
                background-color: rgba(255, 255, 255, 0.15);
                border-color: rgba(56, 189, 248, 0.4);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. Cellular Signal Icon
        self.icon_signal = QLabel(self)
        self.icon_signal.setPixmap(make_svg_icon("signal", color="#38bdf8", size=15).pixmap(15, 15))
        layout.addWidget(self.icon_signal)

        # 2. Battery Icon
        self.icon_battery = QLabel(self)
        self.icon_battery.setPixmap(make_svg_icon("battery", color="#3fb950", size=16).pixmap(16, 16))
        layout.addWidget(self.icon_battery)

        # 3. Battery Percentage Text
        self.lbl_battery = QLabel("--%", self)
        self.lbl_battery.setStyleSheet("""
            color: #f0f6fc;
            font-size: 12px;
            font-weight: 700;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        """)
        layout.addWidget(self.lbl_battery)
        self._battery: int = -1
        self._signal: int = -1
        self._operator: str = ""
        self._is_roaming: bool = False
        self._is_charging: bool = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def update_status(
        self,
        signal: Optional[int] = None,
        battery: Optional[int] = None,
        is_charging: bool = False,
        operator_name: str = "",
        is_roaming: bool = False,
    ):
        if battery is not None and battery >= 0:
            self._battery = max(0, min(100, battery))
        if signal is not None and signal >= 0:
            self._signal = max(0, min(5, signal))
        if operator_name:
            self._operator = operator_name
        self._is_charging = is_charging
        self._is_roaming = is_roaming

        op_prefix = f"📶 {self._operator} {'(Roaming) ' if self._is_roaming else ''}| " if self._operator else ""
        sig_str = f"Signal: {self._signal}/5" if self._signal >= 0 else "Signal: --"
        bat_str = f"Battery: {self._battery}% {'(Charging)' if self._is_charging else ''}" if self._battery >= 0 else "Battery: --"
        self.setToolTip(f"{op_prefix}{sig_str} | {bat_str}")

        if self._battery >= 0:
            bat_color = "#3fb950" if self._battery > 20 else "#f85149"
            self.icon_battery.setPixmap(make_svg_icon("battery", color=bat_color, size=16).pixmap(16, 16))
            self.lbl_battery.setText(f"{self._battery}%")
        else:
            self.icon_battery.setPixmap(make_svg_icon("battery", color="#8b949e", size=16).pixmap(16, 16))
            self.lbl_battery.setText("--%")

    def reset(self):
        self._battery = -1
        self._signal = -1
        self._operator = ""
        self._is_roaming = False
        self._is_charging = False
        self.icon_battery.setPixmap(make_svg_icon("battery", color="#8b949e", size=16).pixmap(16, 16))
        self.lbl_battery.setText("--%")
        self.setToolTip("Signal: -- | Battery: --")


class AudioBufferPill(QFrame):
    """
    Compact status pill in the command bar showing audio buffer health.
    Tracks both Buffer 1 (App Jitter Pre-buffer) and Buffer 2 (QAudioSink Hardware Buffer).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("audio-buffer-pill")
        self.setFixedHeight(42)
        self.setStyleSheet("""
            QFrame#audio-buffer-pill {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 21px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. Audio Icon
        self.icon_audio = QLabel(self)
        self.icon_audio.setPixmap(make_svg_icon("volume", color="#8b949e", size=15).pixmap(15, 15))
        layout.addWidget(self.icon_audio)

        # 2. Buffer Status Label (App & Sink ms)
        self.lbl_status = QLabel("Audio Idle", self)
        self.lbl_status.setStyleSheet("""
            color: #8b949e;
            font-size: 11px;
            font-weight: 700;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
        """)
        layout.addWidget(self.lbl_status)

        self.setToolTip("Audio Buffer Monitor (App Prebuffer & QAudioSink Buffer)")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tooltip_visible = False

    def mousePressEvent(self, event):
        """Toggle tooltip/hover on touchscreen tap or click."""
        tip_text = self.toolTip()
        if not tip_text:
            return

        if self._tooltip_visible:
            QToolTip.hideText()
            self._tooltip_visible = False
        else:
            # Show tooltip positioned directly above the pill
            global_pos = self.mapToGlobal(QPoint(self.width() // 2, -10))
            QToolTip.showText(global_pos, tip_text, self)
            self._tooltip_visible = True
        event.accept()

    def update_status(self, metrics: dict, video_metrics: Optional[dict] = None):
        """Update buffer and sync pill state from QtAudioEngine and video metrics."""
        v_metrics = video_metrics or {}
        v_lag = v_metrics.get("lag_ms", 0)
        v_fps = v_metrics.get("fps", 0.0)
        has_video = (v_fps > 0 or v_lag > 0)
        fps_str = f"{int(v_fps)}fps " if v_fps > 0 else ""

        # Find active streaming channel
        active_ch = None
        if metrics:
            for ch in metrics.values():
                if ch.get("is_streaming") and not ch.get("app_buffer", {}).get("is_paused"):
                    active_ch = ch
                    break
            if not active_ch:
                for ch in metrics.values():
                    if ch.get("app_buffer", {}).get("is_paused") or ch.get("app_buffer", {}).get("buffered_bytes", 0) > 0:
                        active_ch = ch
                        break
            if not active_ch:
                for ch in metrics.values():
                    if ch.get("is_started"):
                        active_ch = ch
                        break

        ch_id = active_ch.get("channel_id", 0) if active_ch else 0
        app_buf = active_ch.get("app_buffer", {}) if active_ch else {}
        sink_buf = active_ch.get("sink_buffer", {}) if active_ch else {}

        app_ms = app_buf.get("buffered_ms", 0)
        app_target_ms = app_buf.get("prebuffer_ms", 150)
        is_buffering = app_buf.get("is_buffering", False)
        underruns = app_buf.get("underruns", 0)
        a_lag = active_ch.get("lag_ms", 0) if active_ch else 0

        sink_ms = sink_buf.get("queued_ms", 0)
        sink_state = sink_buf.get("state", "None")
        sink_err = sink_buf.get("error", "None")

        is_streaming = active_ch.get("is_streaming", False) if active_ch else False
        is_paused = app_buf.get("is_paused", False)

        # Determine pill state and color strictly from actual audio & video status
        if not is_streaming:
            status_color = "#8b949e"  # Gray / Neutral for inactive audio
            if is_paused:
                state_text = f"{fps_str}V:+{v_lag}ms PAUSED" if has_video else "PAUSED"
            elif has_video:
                state_text = f"{fps_str}V:+{v_lag}ms"
            else:
                state_text = "Audio Idle"
        elif underruns > 0 and is_buffering:
            status_color = "#f85149"  # Red (Starved / Underrun)
            state_text = f"UNDERRUN ({underruns})"
        elif is_buffering and app_ms < app_target_ms:
            status_color = "#d29922"  # Yellow (Prebuffering)
            state_text = f"BUF {app_ms}/{app_target_ms}ms"
        else:
            av_drift = v_lag - a_lag
            if abs(av_drift) > 100 or v_lag > 250 or a_lag > 250:
                status_color = "#f85149"  # Red (Significant Lag / Drift)
                state_text = f"V:+{v_lag}ms A:+{a_lag}ms"
            elif abs(av_drift) > 40 or v_lag > 100 or a_lag > 100:
                status_color = "#d29922"  # Yellow (Moderate Lag / Drift)
                state_text = f"V:+{v_lag}ms A:+{a_lag}ms"
            else:
                status_color = "#3fb950"  # Green (Healthy Playback & Sync)
                state_text = f"{fps_str}V:+{v_lag}ms A:+{a_lag}ms"

        self.lbl_status.setText(state_text)
        self.lbl_status.setStyleSheet(f"color: {status_color}; font-size: 11px; font-weight: 700; font-family: monospace;")
        self.icon_audio.setPixmap(make_svg_icon("volume", color=status_color, size=15).pixmap(15, 15))

        audio_stream_state = "ACTIVE (Streaming)" if is_streaming else ("PAUSED" if is_paused else "IDLE / STOPPED")
        tooltip_lines = [
            "⏱ Stream Latency & A/V Sync Diagnostics:",
            f"• Video Stream: +{v_lag}ms lag | {v_fps:.1f} fps" if has_video else "• Video Stream: IDLE",
            f"• Audio Stream (Ch{ch_id}): {audio_stream_state}" + (f" | +{a_lag}ms lag" if is_streaming else ""),
        ]
        if is_streaming:
            av_drift = v_lag - a_lag
            tooltip_lines.extend([
                f"• A/V Sync Drift: {av_drift:+d}ms ({'In Sync' if abs(av_drift) < 40 else 'Drifting'})",
                f"• Buffer 1 (App Prebuffer): {app_ms}ms / target {app_target_ms}ms ({app_buf.get('buffered_bytes', 0)}B)",
                f"  - Buffering State: {'WAITING (Prebuffering)' if is_buffering else 'STREAMING (Active)'}",
                f"  - Underrun Count: {underruns}",
                f"• Buffer 2 (QAudioSink): {sink_ms}ms queued / {sink_buf.get('buffer_size', 0)}B total",
                f"  - Free Space: {sink_buf.get('bytes_free', 0)}B",
                f"  - Sink State: {sink_state} (Error: {sink_err})",
                f"• Stream: {active_ch.get('sample_rate', 48000)}Hz {active_ch.get('channel_count', 2)}ch | In: {active_ch.get('total_bytes_in', 0) // 1024}KB",
            ])
        tip_text = "\n".join(tooltip_lines)
        self.setToolTip(tip_text)

        # If user opened the tooltip via touch tap, refresh it live
        if self._tooltip_visible:
            global_pos = self.mapToGlobal(QPoint(self.width() // 2, -10))
            QToolTip.showText(global_pos, tip_text, self)


class InCallControlPill(QFrame):
    """
    Interactive in-call control pill on the command bar with Material icons.
    """

    action_triggered = pyqtSignal(str)  # "answer", "hangup", "mute"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("in-call-control-pill")
        self.setFixedHeight(48)
        self.setStyleSheet("""
            QFrame#in-call-control-pill {
                background-color: rgba(15, 23, 42, 0.95);
                border: 1.5px solid rgba(56, 189, 248, 0.50);
                border-radius: 24px;
            }
        """)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 0, 10, 0)
        self.layout.setSpacing(10)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. Phone In Talk Material Icon
        self.icon_call = QLabel(self)
        self.icon_call.setPixmap(make_svg_icon("phone_in_talk", color="#3fb950", size=18).pixmap(18, 18))
        self.layout.addWidget(self.icon_call)

        # 2. Text Container (Name & Timer)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(0)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.lbl_caller = QLabel("Call Active", self)
        self.lbl_caller.setStyleSheet("color: #f0f6fc; font-size: 12px; font-weight: 700; max-width: 150px;")
        text_layout.addWidget(self.lbl_caller)

        self.lbl_timer = QLabel("00:00", self)
        self.lbl_timer.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 600; font-family: monospace;")
        text_layout.addWidget(self.lbl_timer)

        self.layout.addLayout(text_layout)

        # 3. Answer Button (Material Green)
        self.btn_answer = QPushButton(self)
        self.btn_answer.setIcon(make_svg_icon("phone", color="#ffffff", size=18))
        self.btn_answer.setIconSize(QSize(18, 18))
        self.btn_answer.setFixedSize(36, 36)
        self.btn_answer.setToolTip("Answer Call")
        self.btn_answer.setStyleSheet("""
            QPushButton {
                background-color: #238636;
                border: none;
                border-radius: 18px;
            }
            QPushButton:hover { background-color: #2ea043; }
        """)
        self.btn_answer.clicked.connect(lambda: self.action_triggered.emit("answer"))
        self.layout.addWidget(self.btn_answer)

        # 4. Mute Mic Button (Material Mic Toggle)
        self.is_mic_muted = False
        self.btn_mute = QPushButton(self)
        self.btn_mute.setIcon(make_svg_icon("mic", color="#e6edf3", size=18))
        self.btn_mute.setIconSize(QSize(18, 18))
        self.btn_mute.setFixedSize(36, 36)
        self.btn_mute.setToolTip("Mute Microphone")
        self.btn_mute.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 18px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.22); }
        """)
        self.btn_mute.clicked.connect(self._on_mute_clicked)
        self.layout.addWidget(self.btn_mute)

        # 5. Hangup / Reject Button (Material Call End Red)
        self.btn_hangup = QPushButton(self)
        self.btn_hangup.setIcon(make_svg_icon("call_end", color="#ffffff", size=18))
        self.btn_hangup.setIconSize(QSize(18, 18))
        self.btn_hangup.setFixedSize(36, 36)
        self.btn_hangup.setToolTip("End Call")
        self.btn_hangup.setStyleSheet("""
            QPushButton {
                background-color: #da3633;
                border: none;
                border-radius: 18px;
            }
            QPushButton:hover { background-color: #f85149; }
        """)
        self.btn_hangup.clicked.connect(lambda: self.action_triggered.emit("hangup"))
        self.layout.addWidget(self.btn_hangup)

        self.hide()

    def _on_mute_clicked(self):
        self.is_mic_muted = not self.is_mic_muted
        self.set_mic_muted(self.is_mic_muted)
        self.action_triggered.emit("mute")

    def set_mic_muted(self, is_muted: bool):
        self.is_mic_muted = is_muted
        if is_muted:
            self.btn_mute.setIcon(make_svg_icon("mic_off", color="#f0883e", size=18))
            self.btn_mute.setStyleSheet("""
                QPushButton {
                    background-color: rgba(240, 136, 62, 0.25);
                    border: 1px solid #f0883e;
                    border-radius: 18px;
                }
            """)
            self.btn_mute.setToolTip("Unmute Microphone")
        else:
            self.btn_mute.setIcon(make_svg_icon("mic", color="#e6edf3", size=18))
            self.btn_mute.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.12);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 18px;
                }
                QPushButton:hover { background-color: rgba(255, 255, 255, 0.22); }
            """)
            self.btn_mute.setToolTip("Mute Microphone")

    def update_call_state(
        self,
        is_in_call: bool,
        call_state: str = "IDLE",
        caller_name: str = "",
        caller_number: str = "",
        duration_seconds: int = 0,
    ):
        if not is_in_call or call_state in ("IDLE", "DISCONNECTED"):
            self.hide()
            return

        display_name = caller_name or caller_number or "In Call"
        if len(display_name) > 18:
            display_name = display_name[:16] + "…"
        self.lbl_caller.setText(display_name)

        if call_state == "RINGING":
            self.lbl_timer.setText("Incoming Call...")
            self.icon_call.setPixmap(make_svg_icon("phone", color="#38bdf8", size=18).pixmap(18, 18))
            self.btn_answer.show()
            self.btn_mute.hide()
            self.btn_hangup.setToolTip("Decline Call")
        else:
            mins = duration_seconds // 60
            secs = duration_seconds % 60
            self.lbl_timer.setText(f"{mins:02d}:{secs:02d}")
            self.icon_call.setPixmap(make_svg_icon("phone_in_talk", color="#3fb950", size=18).pixmap(18, 18))
            self.btn_answer.hide()
            self.btn_mute.show()
            self.btn_hangup.setToolTip("End Call")

        self.show()


class CommandBarWidget(QWidget):
    """
    Floating Bottom Control Bar Widget.
    """

    home_clicked = pyqtSignal()
    playpause_clicked = pyqtSignal()
    volume_clicked = pyqtSignal()
    exit_clicked = pyqtSignal()
    menu_clicked = pyqtSignal()
    call_action_triggered = pyqtSignal(str)  # "answer", "hangup", "mute"
    phone_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("command-bar")

        self.setFixedHeight(64)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(20, 6, 20, 6)
        self.layout.setSpacing(12)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. Home / Clock Button
        self.btn_home = QPushButton(self)
        self.btn_home.setIcon(make_svg_icon("home", color="#f0f6fc", size=22))
        self.btn_home.setIconSize(QSize(22, 22))
        self.btn_home.setObjectName("btn-home")
        self.btn_home.setProperty("class", "cmd-btn")
        self.btn_home.setToolTip("Home / Clock View")
        self.btn_home.clicked.connect(self.home_clicked.emit)
        self.layout.addWidget(self.btn_home)

        # 2. Status Dot
        self.status_dot = QWidget(self)
        self.status_dot.setObjectName("status-dot-offline")
        self.status_dot.setToolTip("System Connection Status")
        self.layout.addWidget(self.status_dot)

        # 3. Play / Pause Button
        self.btn_playpause = QPushButton(self)
        self.btn_playpause.setIcon(make_svg_icon("play", color="#f0f6fc", size=22))
        self.btn_playpause.setIconSize(QSize(22, 22))
        self.btn_playpause.setObjectName("btn-playpause")
        self.btn_playpause.setProperty("class", "cmd-btn")
        self.btn_playpause.setToolTip("Play / Pause")
        self.btn_playpause.clicked.connect(self.playpause_clicked.emit)
        self.layout.addWidget(self.btn_playpause)

        # 4. Volume Controls Button
        self.btn_volume = QPushButton(self)
        self.btn_volume.setIcon(make_svg_icon("volume", color="#f0f6fc", size=22))
        self.btn_volume.setIconSize(QSize(22, 22))
        self.btn_volume.setObjectName("btn-volume")
        self.btn_volume.setProperty("class", "cmd-btn")
        self.btn_volume.setToolTip("Volume Controls")
        self.btn_volume.clicked.connect(self.volume_clicked.emit)
        self.layout.addWidget(self.btn_volume)

        # 5. Exit Button
        self.btn_close = QPushButton(self)
        self.btn_close.setIcon(make_svg_icon("close", color="#f85149", size=22))
        self.btn_close.setIconSize(QSize(22, 22))
        self.btn_close.setObjectName("btn-close")
        self.btn_close.setProperty("class", "cmd-btn")
        self.btn_close.setToolTip("Close / Exit")
        self.btn_close.clicked.connect(self.exit_clicked.emit)
        self.layout.addWidget(self.btn_close)

        # 6. Audio Buffer Status Pill (App Prebuffer & QAudioSink Buffer)
        self.audio_pill = AudioBufferPill(self)
        self.layout.addWidget(self.audio_pill)

        # 7. Phone Status Pill (Signal & Battery)
        self.phone_pill = PhoneStatusPill(self)
        self.phone_pill.clicked.connect(self.phone_clicked.emit)
        self.layout.addWidget(self.phone_pill)

        # 8. Interactive In-Call Control Pill (Dynamic Material Icons)
        self.in_call_pill = InCallControlPill(self)
        self.in_call_pill.action_triggered.connect(self.call_action_triggered.emit)
        self.layout.addWidget(self.in_call_pill)

        # 9. Menu Drawer Button
        self.btn_menu = QPushButton(self)
        self.btn_menu.setIcon(make_svg_icon("menu", color="#38bdf8", size=22))
        self.btn_menu.setIconSize(QSize(22, 22))
        self.btn_menu.setObjectName("btn-menu")
        self.btn_menu.setProperty("class", "cmd-btn")
        self.btn_menu.setToolTip("Main Menu")
        self.btn_menu.clicked.connect(self.menu_clicked.emit)
        self.layout.addWidget(self.btn_menu)

    def set_online_status(self, is_online: bool):
        if is_online:
            self.status_dot.setObjectName("status-dot-online")
        else:
            self.status_dot.setObjectName("status-dot-offline")
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    def update_playback_state(self, is_playing: bool):
        """Update play/pause toggle button icon and tooltip."""
        if is_playing:
            self.btn_playpause.setIcon(make_svg_icon("pause", color="#f0f6fc", size=22))
            self.btn_playpause.setToolTip("Pause")
        else:
            self.btn_playpause.setIcon(make_svg_icon("play", color="#f0f6fc", size=22))
            self.btn_playpause.setToolTip("Play")

    def update_audio_status(self, metrics: dict, video_metrics: Optional[dict] = None):
        """Update audio buffer & A/V sync pill with current app, hardware sink, and video metrics."""
        self.audio_pill.update_status(metrics, video_metrics=video_metrics)

    def update_phone_status(self, signal: Optional[int] = None, battery: Optional[int] = None, is_charging: bool = False, operator_name: str = "", is_roaming: bool = False, is_connected: Optional[bool] = None):
        """Update signal bars, battery indicator, and operator tooltip."""
        if is_connected is False:
            self.phone_pill.reset()
        else:
            self.phone_pill.update_status(signal, battery, is_charging, operator_name, is_roaming)

    def update_call_state(
        self,
        is_in_call: bool,
        call_state: str = "IDLE",
        caller_name: str = "",
        caller_number: str = "",
        duration_seconds: int = 0,
    ):
        """Update in-call control pill on the command bar."""
        self.in_call_pill.update_call_state(
            is_in_call=is_in_call,
            call_state=call_state,
            caller_name=caller_name,
            caller_number=caller_number,
            duration_seconds=duration_seconds,
        )
