#!/usr/bin/env python3
"""
Qt6 GUI Frontend Module — Priority 5 Native Qt Interface.

Extends BaseBackendModule. Graphically identical to HTML5 WebClient,
rendering video/audio zero-copy from `nemo_media_shm_down` and capturing
microphone input to `nemo_media_shm_up`.
"""

import sys
import time
import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Optional



# Bootstrap PYTHONPATH/sys.path
backend_dir = Path(__file__).resolve().parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

try:
    from shared.base_module import BaseBackendModule, run_module
    from shared.config_schema import field_bool, field_enum, field_string
    from shared.media_shm import BidirectionalMediaSHM
except ImportError:
    from backend.shared.base_module import BaseBackendModule, run_module
    from backend.shared.config_schema import field_bool, field_enum, field_string
    from backend.shared.media_shm import BidirectionalMediaSHM


import os


def _setup_windows_dll_path():
    """
    Prepend PyQt6 and Qt6/bin directories to os.environ['PATH'] and os.add_dll_directory()
    on Windows to prevent DLL load conflicts (ERROR_PROC_NOT_FOUND code 127).
    """
    if sys.platform != "win32":
        return
    try:
        import site
        prefix = sys.prefix
        qt6_dirs = []
        site_dirs = []
        try:
            site_dirs.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            site_dirs.append(site.getusersitepackages())
        except Exception:
            pass
        for path_entry in sys.path:
            if "site-packages" in str(path_entry):
                site_dirs.append(str(path_entry))

        for d in site_dirs:
            qt6_bin = os.path.join(d, "PyQt6", "Qt6", "bin")
            qt6_root = os.path.join(d, "PyQt6")
            if os.path.exists(qt6_bin):
                qt6_dirs.append(qt6_bin)
            if os.path.exists(qt6_root):
                qt6_dirs.append(qt6_root)

        priority_dirs = qt6_dirs + [
            os.path.join(prefix, "DLLs"),
            os.path.join(prefix, "Library", "bin"),
        ]

        for d in priority_dirs:
            if os.path.exists(d):
                if d not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(d)
                    except Exception:
                        pass

        # Force pre-loading Qt6Core.dll using LOAD_WITH_ALTERED_SEARCH_PATH
        import ctypes
        LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
        for d in qt6_dirs:
            qt6_core = os.path.join(d, "Qt6Core.dll")
            if os.path.exists(qt6_core):
                try:
                    ctypes.windll.kernel32.LoadLibraryExW(qt6_core, None, LOAD_WITH_ALTERED_SEARCH_PATH)
                except Exception:
                    pass
    except Exception as exc:
        logging.debug("Windows DLL path preloading notice: %s", exc)





_setup_windows_dll_path()

import json
import threading
import urllib.request

try:
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, pyqtSlot, QObject, qInstallMessageHandler, QtMsgType
    from PyQt6.QtWidgets import QApplication
    HAS_PYQT6 = True
except ImportError as err:
    HAS_PYQT6 = False
    Qt = None
    QTimer = None
    QThread = object
    QObject = object
    pyqtSignal = lambda *a: None
    pyqtSlot = lambda *a, **k: (lambda f: f)
    QApplication = None
    qInstallMessageHandler = lambda *a: None
    QtMsgType = None
    logging.warning(
        "Failed to import PyQt6 modules on %s: %s. "
        "Fix for Windows: pip install --force-reinstall PyQt6 PyQt6-Qt6",
        sys.platform,
        err,
    )


if HAS_PYQT6 and qInstallMessageHandler:
    import traceback

    def _qt_diagnostic_message_handler(msg_type, context, message):
        try:
            if "QBasicTimer" in message or "Timers cannot be started" in message:
                cur_thread = threading.current_thread()
                stack = "".join(traceback.format_stack())
                logging.error(
                    f"\n🔥 [DETERMINISTIC QTIMER TRACE]\n"
                    f"  Message: {message}\n"
                    f"  Thread: {cur_thread.name} (ident={cur_thread.ident}, is_main={cur_thread is threading.main_thread()})\n"
                    f"  Qt Context: file={context.file}:{context.line} func={context.function}\n"
                    f"  Python Stack:\n{stack}"
                )
            elif msg_type == QtMsgType.QtWarningMsg:
                logging.warning(f"[QtWarning] {message} ({context.file}:{context.line})")
            elif msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                logging.error(f"[QtCritical] {message} ({context.file}:{context.line})")
        except Exception:
            pass

    try:
        qInstallMessageHandler(_qt_diagnostic_message_handler)
    except Exception as exc:
        logging.debug(f"Failed to install Qt message handler: {exc}")



from backend.modules.qt6_gui.media.shm_media_engine import QtSHMMediaEngine
from backend.modules.qt6_gui.media.audio_handler import QtAudioEngine
from backend.modules.qt6_gui.ui.main_window import MainWindow

logger = logging.getLogger("qt6_gui")


class GuiEventBridge(QObject):
    """
    Qt Signal Bridge for thread-safe cross-thread event dispatching.
    Emits PyQt signals from ZMQ background subscriber threads so slots execute
    strictly on the Qt GUI main event loop via Qt.ConnectionType.QueuedConnection.
    """
    connectivity_updated = pyqtSignal(dict)
    channel_status_updated = pyqtSignal(dict)
    shm_video_notify = pyqtSignal(dict)
    shm_audio_notify = pyqtSignal(dict)
    audio_channel_configured = pyqtSignal(dict)
    audio_sink_changed = pyqtSignal(dict)
    mic_control_notify = pyqtSignal(dict)
    nav_turn_notify = pyqtSignal(dict)
    nav_dist_notify = pyqtSignal(dict)
    media_metadata_notify = pyqtSignal(dict)
    media_playback_status_notify = pyqtSignal(dict)
    phone_status_notify = pyqtSignal(dict)
    notification_post = pyqtSignal(dict)
    notification_dismiss = pyqtSignal(dict)
    media_tree_updated = pyqtSignal(dict)
    diagnostic_audio_test = pyqtSignal(dict)

    def __init__(self, module: "Qt6GuiModule", parent=None):
        super().__init__(parent)
        self._module = module

    @pyqtSlot(dict)
    def on_connectivity_updated(self, data: dict):
        self._module._on_connectivity_status_updated(data)

    @pyqtSlot(dict)
    def on_channel_status_updated(self, data: dict):
        self._module._on_channel_status_updated(data)

    @pyqtSlot(dict)
    def on_shm_video_notify(self, data: dict):
        self._module._on_shm_video_notify(data)

    @pyqtSlot(dict)
    def on_shm_audio_notify(self, data: dict):
        self._module._on_shm_audio_notify(data)

    @pyqtSlot(dict)
    def on_audio_channel_configured(self, data: dict):
        self._module._on_audio_channel_configured(data)

    @pyqtSlot(dict)
    def on_audio_sink_changed(self, data: dict):
        self._module._on_audio_sink_changed(data)

    @pyqtSlot(dict)
    def on_mic_control_notify(self, data: dict):
        self._module._on_mic_control_notify(data)

    @pyqtSlot(dict)
    def on_nav_turn_notify(self, data: dict):
        self._module._on_nav_turn_notify(data)

    @pyqtSlot(dict)
    def on_nav_dist_notify(self, data: dict):
        self._module._on_nav_dist_notify(data)

    @pyqtSlot(dict)
    def on_media_metadata_notify(self, data: dict):
        self._module._on_media_metadata_notify(data)

    @pyqtSlot(dict)
    def on_media_playback_status_notify(self, data: dict):
        self._module._on_media_playback_status_notify(data)

    @pyqtSlot(dict)
    def on_phone_status_notify(self, data: dict):
        self._module._on_phone_status_notify(data)

    @pyqtSlot(dict)
    def on_notification_post(self, data: dict):
        self._module._on_notification_post_notify(data)

    @pyqtSlot(dict)
    def on_notification_dismiss(self, data: dict):
        self._module._on_notification_dismiss_notify(data)

    @pyqtSlot(dict)
    def on_media_tree_updated(self, data: dict):
        self._module._on_media_tree_updated_notify(data)

    @pyqtSlot(dict)
    def on_diagnostic_audio_test(self, data: dict):
        self._module._on_diagnostic_audio_test(data)


class Qt6GuiModule(BaseBackendModule):
    """
    Priority 5 Qt6 GUI Frontend Module.
    """

    def __init__(self):
        super().__init__(
            name="qt6_gui",
            priority=5,
            path_prefix="/api/qt6",
        )
        self.app: Optional[QApplication] = None
        self.main_window: Optional[MainWindow] = None
        self.shm_engine: Optional[QtSHMMediaEngine] = None
        self.audio_engine: Optional[QtAudioEngine] = None
        self.bridge: Optional[GuiEventBridge] = None
        self._sse_tasks: list[asyncio.Task] = []
        self._last_toast_message = ""



    def get_default_config(self) -> dict[str, Any]:
        return {
            "fullscreen": True,
            "theme": "dark",
            "enable_mic": True,
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "fullscreen": field_bool(default=True),
            "theme": field_enum(default="dark", choices=["dark", "light"]),
            "enable_mic": field_bool(default=True),
        }

    def on_config_updated(self, new_config: dict[str, Any]) -> None:
        super().on_config_updated(new_config)
        if self.main_window and "fullscreen" in new_config:
            is_fs = bool(new_config["fullscreen"])
            self.main_window.fullscreen_change_requested.emit(is_fs)

    async def setup(self) -> None:
        """Initialize QApplication, UI components, ZMQ bus subscriptions, and SHM engine."""
        t0 = time.time()
        self.log.info("⏱ [Boot Trace 1/7] Initializing Qt6 GUI Frontend Module...")

        if not HAS_PYQT6 or QApplication is None:
            self.log.warning("PyQt6 C++ DLL or module unavailable — skipping Qt GUI main window initialization")
            return

        # Create QApplication if not created
        t1 = time.time()
        if not QApplication.instance():
            os.environ.setdefault("QT_WIDGETS_RHI", "0")
            try:
                from PyQt6.QtGui import QSurfaceFormat
                fmt = QSurfaceFormat()
                fmt.setDepthBufferSize(24)
                fmt.setStencilBufferSize(8)
                QSurfaceFormat.setDefaultFormat(fmt)
            except Exception as exc:
                self.log.debug(f"QSurfaceFormat setup notice: {exc}")
            self.app = QApplication(sys.argv)
        else:
            self.app = QApplication.instance()
        self.log.info(f"⏱ [Boot Trace 2/7] QApplication initialized in {(time.time()-t1)*1000:.1f}ms")

        # Load QSS Theme
        t2 = time.time()
        try:
            from pathlib import Path
            theme_path = Path(__file__).parent / "styles" / "theme.qss"
            if theme_path.exists():
                with open(theme_path, "r", encoding="utf-8") as f:
                    self.app.setStyleSheet(f.read())
        except Exception as exc:
            self.log.warning("Failed to load QSS stylesheet: %s", exc)
        self.log.info(f"⏱ [Boot Trace 3/7] Theme QSS loaded in {(time.time()-t2)*1000:.1f}ms")

        # Initialize Main Window
        t3 = time.time()
        self.log.info("⏱ [Boot Trace 4a/7] Initializing MainWindow widget hierarchy...")
        self.main_window = MainWindow()
        self.main_window.close_app_requested.connect(self._on_close_requested)
        self.main_window.video_viewport.touch_input_event.connect(self._on_touch_input_event)
        self.log.info(f"⏱ [Boot Trace 4c/7] MainWindow signals connected in {(time.time()-t3)*1000:.1f}ms")

        # Initialize SHM & Audio Engines
        t4 = time.time()
        self.log.info("⏱ [Boot Trace 4d/7] Instantiating QtSHMMediaEngine...")
        self.shm_engine = QtSHMMediaEngine()
        self.shm_engine.on_video_frame = self._on_video_frame_from_shm
        self.shm_engine.on_audio_frame = self._on_audio_frame_from_shm
        self.log.info("⏱ [Boot Trace 4e/7] Connecting to BidirectionalMediaSHM...")
        self.shm_engine.connect_shm()
        self.log.info(f"⏱ [Boot Trace 4f/7] SHM engine connected (is_connected={self.shm_engine.is_connected})!")

        self.audio_engine = QtAudioEngine()
        self.audio_engine.mic_data_captured.connect(self._on_mic_data_captured)
        self.log.info(f"⏱ [Boot Trace 5/7] SHM & Audio engines initialized in {(time.time()-t4)*1000:.1f}ms")

        # Connect Window Signals
        t5 = time.time()
        self.main_window.focus_toggle_requested.connect(self._on_video_focus_toggled)
        self.main_window.phone_action_requested.connect(self._on_phone_action_requested)
        self.main_window.media_playpause_requested.connect(self._on_media_playpause_requested)
        self.log.info(f"⏱ [Boot Trace 6/7] Window signals connected in {(time.time()-t5)*1000:.1f}ms")

        # Initialize GuiEventBridge and wire slots onto main GUI thread with QueuedConnection
        self.bridge = GuiEventBridge(self)
        self.bridge.connectivity_updated.connect(self.bridge.on_connectivity_updated, Qt.ConnectionType.QueuedConnection)
        self.bridge.channel_status_updated.connect(self.bridge.on_channel_status_updated, Qt.ConnectionType.QueuedConnection)
        self.bridge.shm_video_notify.connect(self.bridge.on_shm_video_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.shm_audio_notify.connect(self.bridge.on_shm_audio_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.audio_channel_configured.connect(self.bridge.on_audio_channel_configured, Qt.ConnectionType.QueuedConnection)
        self.bridge.audio_sink_changed.connect(self.bridge.on_audio_sink_changed, Qt.ConnectionType.QueuedConnection)
        self.bridge.mic_control_notify.connect(self.bridge.on_mic_control_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.nav_turn_notify.connect(self.bridge.on_nav_turn_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.nav_dist_notify.connect(self.bridge.on_nav_dist_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.media_metadata_notify.connect(self.bridge.on_media_metadata_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.media_playback_status_notify.connect(self.bridge.on_media_playback_status_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.phone_status_notify.connect(self.bridge.on_phone_status_notify, Qt.ConnectionType.QueuedConnection)
        self.bridge.notification_post.connect(self.bridge.on_notification_post, Qt.ConnectionType.QueuedConnection)
        self.bridge.notification_dismiss.connect(self.bridge.on_notification_dismiss, Qt.ConnectionType.QueuedConnection)
        self.bridge.media_tree_updated.connect(self.bridge.on_media_tree_updated, Qt.ConnectionType.QueuedConnection)
        self.bridge.diagnostic_audio_test.connect(self.bridge.on_diagnostic_audio_test, Qt.ConnectionType.QueuedConnection)

        def _bridge_emit(sig, top_or_pay, pay=None):
            data = pay if pay is not None else top_or_pay
            if isinstance(data, dict):
                sig.emit(data)

        # ZMQ Topic Subscriptions (bridged thread-safely into Qt main thread)
        self.log.info("⏱ [Boot Trace 6a/7] Subscribing to ZMQ topics...")
        self.subscribe("media.video.transport_frame_shm", lambda top, pay=None: _bridge_emit(self.bridge.shm_video_notify, top, pay))
        self.subscribe("media.audio.frame_shm", lambda top, pay=None: _bridge_emit(self.bridge.shm_audio_notify, top, pay))
        self.subscribe("media.audio.channel_configured", lambda top, pay=None: _bridge_emit(self.bridge.audio_channel_configured, top, pay))
        self.subscribe("media.audio.mic_control", lambda top, pay=None: _bridge_emit(self.bridge.mic_control_notify, top, pay))
        self.subscribe("video.stream_start", self._on_stream_start)
        self.subscribe("video.stream_stop", self._on_stream_stop)
        self.subscribe("navigation.turn_event", lambda top, pay=None: _bridge_emit(self.bridge.nav_turn_notify, top, pay))
        self.subscribe("navigation.distance_event", lambda top, pay=None: _bridge_emit(self.bridge.nav_dist_notify, top, pay))
        self.subscribe("media.metadata", lambda top, pay=None: _bridge_emit(self.bridge.media_metadata_notify, top, pay))
        self.subscribe("media.playback_status", lambda top, pay=None: _bridge_emit(self.bridge.media_playback_status_notify, top, pay))
        self.subscribe("phone.status", lambda top, pay=None: _bridge_emit(self.bridge.phone_status_notify, top, pay))
        self.subscribe("connectivity.status", lambda top, pay=None: _bridge_emit(self.bridge.connectivity_updated, top, pay))
        self.subscribe("channel.status", lambda top, pay=None: _bridge_emit(self.bridge.channel_status_updated, top, pay))
        self.subscribe("notification.post", lambda top, pay=None: _bridge_emit(self.bridge.notification_post, top, pay))
        self.subscribe("notification.dismiss", lambda top, pay=None: _bridge_emit(self.bridge.notification_dismiss, top, pay))
        self.subscribe("media.browser.tree_updated", lambda top, pay=None: _bridge_emit(self.bridge.media_tree_updated, top, pay))
        self.subscribe("media.audio.sink_changed", lambda top, pay=None: _bridge_emit(self.bridge.audio_sink_changed, top, pay))
        self.subscribe("diagnostic.audio.in_process_test", lambda top, pay=None: _bridge_emit(self.bridge.diagnostic_audio_test, top, pay))

        # Request video focus from channel_manager / media_server on setup
        self.log.info("⏱ [Boot Trace 6b/7] Publishing media.video.request_focus...")
        self.publish("media.video.request_focus", {"sender": "qt6_gui"})

        # Show Window & Process Initial Render Events
        is_fs = self.config.get("fullscreen", True)
        if is_fs:
            self.log.info("⏱ [Boot Trace 6c/7] Showing Frameless Fullscreen Window...")
        else:
            self.log.info("⏱ [Boot Trace 6c/7] Calling main_window.show() in windowed mode...")
        self.main_window.set_fullscreen(is_fs)
        self.log.info("⏱ [Boot Trace 6d/7] main_window display initialized!")

        if self.app:
            self.log.info("⏱ [Boot Trace 6e/7] Processing initial Qt events via app.processEvents()...")
            self.app.processEvents()
            self.log.info("⏱ [Boot Trace 6f/7] app.processEvents() completed!")

        self.log.info(f"⏱ [Boot Trace 7/7] Total setup() completed cleanly in {(time.time()-t0)*1000:.1f}ms")

    async def run(self) -> None:
        """Run asyncio loop processing Qt events smoothly."""
        self.log.info("Qt6 GUI Module running...")

        while self._running:
            if self.app:
                self.app.processEvents()
            await asyncio.sleep(0.016)  # ~60 FPS Qt event processing cycle

    async def teardown(self) -> None:
        """Clean up Qt window, SHM engine, and audio resources."""
        self.log.info("Tearing down Qt6 GUI Module...")
        self.publish("system.shutdown", {"sender": "qt6_gui", "reason": "gui_teardown"})
        self.publish("system.stop", {"sender": "qt6_gui", "reason": "gui_teardown"})

        if self.audio_engine:
            self.audio_engine.close()
        if self.shm_engine:
            self.shm_engine.close()
        if self.main_window:
            if hasattr(self.main_window, "video_viewport") and hasattr(self.main_window.video_viewport, "cleanupGL"):
                self.main_window.video_viewport.cleanupGL()
            try:
                self.main_window.close()
                self.main_window.deleteLater()
            except Exception:
                pass
            self.main_window = None

        if self.app:
            try:
                self.app.processEvents()
            except Exception:
                pass


    # ------------------------------------------------------------------
    # Handlers & Callbacks
    # ------------------------------------------------------------------

    def _on_connectivity_status_updated(self, data: dict):
        stage_idx = data.get("stage_index", 0)
        is_online = (stage_idx == 10) or data.get("connected", False) or (data.get("state") == "CONNECTED")
        if self.main_window:
            self.main_window.command_bar.set_online_status(is_online)
            if is_online and not self.main_window._is_connected:
                self.main_window.set_connected_state(True)

        toast_msg = data.get("toast_message")
        pairing_pin = data.get("pairing_pin")
        if pairing_pin:
            dev_addr = data.get("pairing_device", "Phone")
            toast_msg = f"🔑 Pairing Request from {dev_addr}: PIN {pairing_pin}"

        if toast_msg and toast_msg != self._last_toast_message and self.main_window:
            self._last_toast_message = toast_msg
            toast_type = "success" if stage_idx == 10 else "warning"
            self.main_window.toast_widget.show_toast(toast_msg, toast_type)

    def _on_video_focus_toggled(self, mode: str):
        self.log.info(f"Video Focus Toggled by user -> Requesting focus mode: {mode}")
        self.publish("media.video.request_focus", {"mode": mode, "sender": "qt6_gui"})

    def _on_channel_status_updated(self, data: dict):
        is_connected = data.get("connected", False)
        if self.main_window:
            self.main_window.command_bar.set_online_status(is_connected)
            if is_connected:
                self.main_window.set_connected_state(True)

    def _on_shm_video_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        """Synchronous callback invoked directly on ZMQ sub thread to avoid asyncio loop drag stalls."""
        data = payload if payload is not None else topic_or_payload
        offset = data.get("shm_offset", -1) if isinstance(data, dict) else -1
        if offset >= 0 and self.shm_engine:
            self.shm_engine.process_downstream_video(offset)

    def _on_shm_audio_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        """Synchronous callback invoked directly on ZMQ sub thread to avoid asyncio loop drag stalls."""
        data = payload if payload is not None else topic_or_payload
        offset = data.get("shm_offset", -1) if isinstance(data, dict) else -1
        channel_id = data.get("channel_id") if isinstance(data, dict) else None
        if offset >= 0 and self.shm_engine:
            self.shm_engine.process_downstream_audio(offset, channel_id=channel_id)

    def _on_audio_channel_configured(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        """Handle dynamic audio channel codec configuration from AVChannelSetupRequest."""
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.audio_engine:
            channel_id = data.get("channel_id", 0)
            codec = data.get("codec", "")
            sample_rate = data.get("sample_rate", 48000)
            channel_count = data.get("channel_count", 2)
            bit_depth = data.get("bit_depth", 16)
            self.audio_engine.configure_channel_codec(
                channel_id=channel_id,
                codec=codec,
                sample_rate=sample_rate,
                channel_count=channel_count,
                bit_depth=bit_depth,
            )

    def _on_audio_sink_changed(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        """Handle audio output sink or input source changes broadcast from media_server."""
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.audio_engine:
            sink = data.get("sink")
            source = data.get("source")
            if sink is not None:
                self.audio_engine.set_output_sink(sink)
            if source is not None:
                self.audio_engine.set_input_source(source)

    def _on_diagnostic_audio_test(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if not isinstance(data, dict):
            return
        freq = float(data.get("freq", 440.0))
        duration_sec = float(data.get("duration", 2.0))
        push_mode = bool(data.get("push", False))
        device_name = str(data.get("device", ""))
        self.log.info(f"🔊 Executing In-Process Direct Audio Test on Qt Main Thread ({freq}Hz, {duration_sec}s, push={push_mode})...")
        try:
            import pathlib
            scripts_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "scripts"
            if not scripts_dir.exists():
                scripts_dir = pathlib.Path("/opt/nemo-headunit/scripts")
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            import test_audio_cli
            test_audio_cli.test_qaudiosink(device_name, freq, duration_sec, push_mode)
        except Exception as e:
            self.log.error(f"In-process audio test failed on main thread: {e}", exc_info=True)

    def _on_nav_turn_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window and self.main_window.nav_widget:
            road = data.get("road", "")
            dist = data.get("distance_meters", -1.0)
            maneuver = data.get("maneuver_type", 0)
            side = data.get("turn_side", 0)
            icon_b64 = data.get("turn_icon", "")
            self.main_window.nav_widget.update_navigation(
                road=road,
                distance_meters=dist,
                maneuver_type=maneuver,
                turn_side=side,
                turn_icon_b64=icon_b64,
            )
            has_nav = bool(road or dist >= 0)
            has_media = self.main_window.has_active_media
            self.main_window.update_dashboard_state(has_nav=has_nav, has_media=has_media)

    def _on_nav_dist_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window and self.main_window.nav_widget:
            dist = data.get("distance_meters", -1.0)
            road = data.get("road", "")
            eta_sec = data.get("eta_seconds", 0)
            maneuver = data.get("maneuver_type", 0)
            side = data.get("turn_side", 0)
            self.main_window.nav_widget.update_navigation(
                road=road,
                distance_meters=dist,
                maneuver_type=maneuver,
                turn_side=side,
                eta_seconds=eta_sec,
            )
            has_nav = bool(road or dist >= 0)
            has_media = self.main_window.has_active_media
            self.main_window.update_dashboard_state(has_nav=has_nav, has_media=has_media)

    def _on_media_metadata_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window and self.main_window.media_widget:
            title = data.get("title", "")
            artist = data.get("artist", "")
            album = data.get("album", "")
            art_b64 = data.get("album_art", "")
            self.main_window.media_widget.update_metadata(
                title=title,
                artist=artist,
                album=album,
                album_art_b64=art_b64,
            )
            has_media = bool(title or artist)
            has_nav = self.main_window.has_active_nav
            self.main_window.update_dashboard_state(has_nav=has_nav, has_media=has_media)

    def _on_media_playback_status_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window and self.main_window.media_widget:
            state = data.get("playback_state", 0)
            source = data.get("media_source", "")
            pos = data.get("position_seconds", 0)
            self.main_window.media_widget.update_metadata(
                playback_state=state,
                media_source=source,
                position_seconds=pos,
            )

    def _on_phone_status_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window:
            is_in_call = data.get("is_in_call", False)
            call_state = data.get("call_state", "IDLE")
            name = data.get("caller_name", "")
            number = data.get("caller_number", "")
            duration = data.get("call_duration_seconds", 0)
            signal = data.get("signal_strength", 4)
            battery = data.get("battery_level", 85)
            charging = data.get("is_charging", False)

            operator = data.get("operator_name", "")
            roaming = bool(data.get("is_roaming", False))

            # Update command bar signal & battery indicators and in-call control pill
            self.main_window.command_bar.update_phone_status(
                signal=signal,
                battery=battery,
                is_charging=charging,
                operator_name=operator,
                is_roaming=roaming,
            )
            self.main_window.command_bar.update_call_state(
                is_in_call=is_in_call,
                call_state=call_state,
                caller_name=name,
                caller_number=number,
                duration_seconds=duration,
            )

            # Update call widget
            self.main_window.phone_call_widget.update_call_state(
                is_in_call=is_in_call,
                call_state=call_state,
                caller_name=name,
                caller_number=number,
                duration_seconds=duration,
            )

            has_nav = self.main_window.has_active_nav
            has_media = self.main_window.has_active_media
            self.main_window.update_dashboard_state(has_nav=has_nav, has_media=has_media, has_call=is_in_call)

    def _on_notification_post_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window:
            nid = data.get("id", "")
            app_name = data.get("app_name", "Alert")
            title = data.get("title", "")
            text = data.get("text", "")

            # Show floating toast
            self.main_window.notification_toast.show_notification(
                notif_id=nid,
                title=title,
                text=text,
                app_name=app_name,
            )
            # Add to persistent card
            self.main_window.notification_card.add_notification(
                notif_id=nid,
                title=title,
                text=text,
                app_name=app_name,
            )

    def _on_notification_dismiss_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window:
            nid = data.get("id", "")
            self.main_window.notification_card.remove_notification(nid)

    def _on_media_tree_updated_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        if isinstance(data, dict) and self.main_window and self.main_window.media_widget:
            path = data.get("path", "/")
            items = data.get("items", [])
            self.main_window.media_widget.set_browser_items(path, items)

    def _on_phone_action_requested(self, action: str):
        self.log.info(f"Phone action requested: {action}")
        asyncio.create_task(self._send_phone_action(action))

    async def _send_phone_action(self, action: str):
        try:
            import urllib.request, json
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/channels/phone/action",
                data=json.dumps({"action": action}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                pass
        except Exception as exc:
            self.log.debug(f"Failed to post phone action: {exc}")

    def _on_media_playpause_requested(self):
        self.log.info("Media Play/Pause key pressed")
        asyncio.create_task(self._send_media_key(85))

    async def _send_media_key(self, key_code: int = 85):
        try:
            import urllib.request, json
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/channels/input/media",
                data=json.dumps({"key_code": key_code}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                self.log.info(f"🎮 Dispatched media key {key_code} -> HTTP {resp.status}")
        except Exception as exc:
            self.log.warning(f"Failed to post media key {key_code}: {exc}")

    def _on_mic_control_notify(self, topic_or_payload: Any, payload: Optional[dict] = None) -> None:
        data = payload if payload is not None else topic_or_payload
        enabled = data.get("enabled", False) if isinstance(data, dict) else False
        if enabled and self.config.get("enable_mic", True):
            if self.audio_engine:
                self.audio_engine.start_microphone()
        else:
            if self.audio_engine:
                self.audio_engine.stop_microphone()

    async def _on_stream_start(self, payload: dict) -> None:
        self.log.info("Video stream started -> Switching Qt GUI to projected state")
        if self.main_window:
            self.main_window.isVideoFocused = True
            self.main_window.set_connected_state(True)
        self.publish("media.video.request_focus", {"sender": "qt6_gui"})

    async def _on_stream_stop(self, payload: dict) -> None:
        self.log.info("Video stream temporarily stopped/paused by phone")

    def _on_video_frame_from_shm(self, rgba_bytes: bytes, w: int, h: int, ts_us: int):
        if self.main_window:
            if self.main_window.isVideoFocused and self.main_window.disconnected_screen.isVisible():
                self.main_window.disconnected_screen.hide()
                self.main_window.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.main_window.video_viewport.update_frame(rgba_bytes, w, h)


    def _on_audio_frame_from_shm(self, pcm_bytes: bytes, channel_id: int, ts_us: int):
        if self.audio_engine:
            self.audio_engine.play_pcm_frame(pcm_bytes, channel_id=channel_id)

    def _on_mic_data_captured(self, pcm_chunk: bytes):
        if self.shm_engine:
            offset = self.shm_engine.write_upstream_mic(pcm_chunk)
            if offset >= 0:
                self.publish("media.audio.mic_shm", {"shm_offset": offset, "len": len(pcm_chunk)})

    def _on_touch_input_event(self, touch_data: dict):
        self.publish("input.event", touch_data)

    def _on_user_input_event(self, ev_type: str, x: int, y: int, button: int):
        self.publish("input.event", {
            "type": ev_type,
            "x": x,
            "y": y,
            "button": button,
        })

    def _on_close_requested(self):
        self.log.info("Close requested by user — shutting down Qt6 GUI module...")
        self.publish("system.shutdown", {"sender": "qt6_gui", "reason": "gui_close"})
        self.publish("system.stop", {"sender": "qt6_gui", "reason": "gui_close"})
        self._running = False

        if self.audio_engine:
            self.audio_engine.close()
        if self.shm_engine:
            self.shm_engine.close()

        if self.main_window:
            if hasattr(self.main_window, "video_viewport") and hasattr(self.main_window.video_viewport, "cleanupGL"):
                self.main_window.video_viewport.cleanupGL()
            try:
                self.main_window.close()
                self.main_window.deleteLater()
            except Exception:
                pass
            self.main_window = None

        if self.app:
            try:
                self.app.processEvents()
                self.app.quit()
            except Exception:
                pass




if __name__ == "__main__":
    run_module(Qt6GuiModule)

