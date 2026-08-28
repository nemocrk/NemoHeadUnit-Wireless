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
import urllib.request

try:
    from PyQt6.QtCore import QTimer, QThread, pyqtSignal
    from PyQt6.QtWidgets import QApplication
    HAS_PYQT6 = True
except ImportError as err:
    HAS_PYQT6 = False
    QTimer = None
    QThread = object
    pyqtSignal = lambda *a: None
    QApplication = None
    logging.warning(
        "Failed to import PyQt6 modules on %s: %s. "
        "Fix for Windows: pip install --force-reinstall PyQt6 PyQt6-Qt6",
        sys.platform,
        err,
    )



from backend.modules.qt6_gui.media.shm_media_engine import QtSHMMediaEngine
from backend.modules.qt6_gui.media.audio_handler import QtAudioEngine
from backend.modules.qt6_gui.ui.main_window import MainWindow

logger = logging.getLogger("qt6_gui")





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
        self._sse_tasks: list[asyncio.Task] = []
        self._last_toast_message = ""



    def get_default_config(self) -> dict[str, Any]:
        return {
            "fullscreen": False,
            "theme": "dark",
            "enable_mic": True,
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "fullscreen": field_bool(default=False),
            "theme": field_enum(default="dark", choices=["dark", "light"]),
            "enable_mic": field_bool(default=True),
        }

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

        self.log.info("⏱ [Boot Trace 4g/7] Instantiating QtAudioEngine...")
        self.audio_engine = QtAudioEngine()
        self.audio_engine.mic_data_captured.connect(self._on_mic_data_captured)
        self.log.info(f"⏱ [Boot Trace 5/7] SHM & Audio engines initialized in {(time.time()-t4)*1000:.1f}ms")

        # Connect Window Signals
        t5 = time.time()
        self.main_window.video_focus_toggled.connect(self._on_video_focus_toggled)
        self.log.info(f"⏱ [Boot Trace 6/7] Window signals connected in {(time.time()-t5)*1000:.1f}ms")

        # ZMQ Topic Subscriptions
        self.log.info("⏱ [Boot Trace 6a/7] Subscribing to ZMQ topics...")
        self.subscribe("media.video.transport_frame_shm", self._on_shm_video_notify)
        self.subscribe("media.audio.frame_shm", self._on_shm_audio_notify)
        self.subscribe("media.audio.mic_control", self._on_mic_control_notify)
        self.subscribe("video.stream_start", self._on_stream_start)
        self.subscribe("video.stream_stop", self._on_stream_stop)

        # Request video focus from channel_manager / media_server on setup
        self.log.info("⏱ [Boot Trace 6b/7] Publishing media.video.request_focus...")
        self.publish("media.video.request_focus", {"sender": "qt6_gui"})

        # Show Window & Process Initial Render Events
        self.log.info("⏱ [Boot Trace 6c/7] Calling main_window.show()...")
        if self.config.get("fullscreen", False):
            self.main_window.showFullScreen()
        else:
            self.main_window.show()
        self.log.info("⏱ [Boot Trace 6d/7] main_window.show() returned!")

        if self.app:
            self.log.info("⏱ [Boot Trace 6e/7] Processing initial Qt events via app.processEvents()...")
            self.app.processEvents()
            self.log.info("⏱ [Boot Trace 6f/7] app.processEvents() completed!")

        self.log.info(f"⏱ [Boot Trace 7/7] Total setup() completed cleanly in {(time.time()-t0)*1000:.1f}ms")

    async def run(self) -> None:
        """Run asyncio loop processing Qt events periodically and SSE status streams."""
        self.log.info("Qt6 GUI Module running...")

        # Spawn pure Python asyncio SSE stream background tasks
        task1 = asyncio.create_task(self._read_sse_loop("/api/connectivity/stream_status", self._on_connectivity_status_updated))
        task2 = asyncio.create_task(self._read_sse_loop("/api/channels/stream_status", self._on_channel_status_updated))
        self._sse_tasks = [task1, task2]

        while self._running:
            if self.app:
                self.app.processEvents()
            await asyncio.sleep(0.016)  # ~60 FPS Qt event processing cycle

    async def _read_sse_loop(self, path: str, callback: Callable[[dict], None]) -> None:
        """Pure Python asyncio loop reading SSE streams without C++ QThread locks."""
        await asyncio.sleep(1.0)  # Wait for web servers to bind
        url = f"http://127.0.0.1:8000{path}"
        while self._running:
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                # Run blocking line reading in asyncio executor thread
                def _fetch():
                    lines = []
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        for line in resp:
                            if not self._running:
                                break
                            lines.append(line.decode("utf-8", errors="ignore").strip())
                            if len(lines) >= 5:
                                break
                    return lines

                lines = await asyncio.to_thread(_fetch)
                for line_str in lines:
                    if line_str.startswith("data:"):
                        json_str = line_str[5:].strip()
                        if json_str:
                            data = json.loads(json_str)
                            callback(data)
            except Exception:
                await asyncio.sleep(2.0)

    async def teardown(self) -> None:
        """Clean up Qt window, SHM engine, and audio resources."""
        self.log.info("Tearing down Qt6 GUI Module...")
        self.publish("system.shutdown", {"sender": "qt6_gui", "reason": "gui_teardown"})
        self.publish("system.stop", {"sender": "qt6_gui", "reason": "gui_teardown"})

        for t in self._sse_tasks:
            t.cancel()
        self._sse_tasks.clear()

        if self.audio_engine:
            self.audio_engine.close()
        if self.shm_engine:
            self.shm_engine.close()
        if self.main_window:
            self.main_window.close()
            self.main_window = None


    # ------------------------------------------------------------------
    # Handlers & Callbacks
    # ------------------------------------------------------------------

    def _on_connectivity_status_updated(self, data: dict):

        toast_msg = data.get("toast_message")
        pairing_pin = data.get("pairing_pin")
        if pairing_pin:
            dev_addr = data.get("pairing_device", "Phone")
            toast_msg = f"🔑 Pairing Request from {dev_addr}: PIN {pairing_pin}"

        if toast_msg and toast_msg != self._last_toast_message and self.main_window:
            self._last_toast_message = toast_msg
            stage_idx = data.get("stage_index", 0)
            toast_type = "success" if stage_idx == 10 else "warning"
            self.main_window.toast_widget.show_toast(toast_msg, toast_type)

    def _on_video_focus_toggled(self, mode: str):
        self.log.info(f"Video Focus Toggled by user -> Requesting focus mode: {mode}")
        self.publish("media.video.request_focus", {"mode": mode, "sender": "qt6_gui"})

    def _on_channel_status_updated(self, data: dict):
        is_connected = data.get("connected", False)
        if self.main_window:
            self.main_window.set_connected_state(is_connected)

    async def _on_shm_video_notify(self, payload: dict) -> None:
        offset = payload.get("shm_offset", -1)
        if offset >= 0 and self.shm_engine:
            self.shm_engine.process_downstream_frame(offset)

    async def _on_shm_audio_notify(self, payload: dict) -> None:
        offset = payload.get("shm_offset", -1)
        if offset >= 0 and self.shm_engine:
            self.shm_engine.process_downstream_frame(offset)

    async def _on_mic_control_notify(self, payload: dict) -> None:
        enabled = payload.get("enabled", False)
        if enabled and self.config.get("enable_mic", True):
            self.audio_engine.start_microphone()
        else:
            self.audio_engine.stop_microphone()

    async def _on_stream_start(self, payload: dict) -> None:
        self.log.info("Video stream started -> Switching Qt GUI to connected state")
        if self.main_window:
            self.main_window.set_connected_state(True)
        self.publish("media.video.request_focus", {"sender": "qt6_gui"})

    async def _on_stream_stop(self, payload: dict) -> None:
        self.log.info("Video stream stopped -> Switching Qt GUI to disconnected clock state")
        if self.main_window:
            self.main_window.set_connected_state(False)

    def _on_video_frame_from_shm(self, rgba_bytes: bytes, w: int, h: int, ts_us: int):
        if self.main_window:
            if self.main_window.isVideoFocused and self.main_window.disconnected_screen.isVisible():
                self.main_window.disconnected_screen.hide()
                self.main_window.disconnected_screen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.main_window.video_viewport.update_frame(rgba_bytes, w, h)


    def _on_audio_frame_from_shm(self, pcm_bytes: bytes, ts_us: int):

        if self.audio_engine:
            self.audio_engine.play_pcm_frame(pcm_bytes)

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

        for t in self._sse_tasks:
            t.cancel()
        self._sse_tasks.clear()


        if self.audio_engine:
            self.audio_engine.close()
        if self.shm_engine:
            self.shm_engine.close()
        if self.app:
            self.app.quit()




if __name__ == "__main__":
    run_module(Qt6GuiModule)

