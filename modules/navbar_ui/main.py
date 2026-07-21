"""
NemoHeadUnit-Wireless — navbar_ui

Navigation bar widget: always-visible frosted-glass bottom bar.
Runs as a separate process. Transparent frameless PyQt6 window
positioned exclusively via ui.widget.geometry from ui_shell.

  Name        : navbar_ui
  Priority    : 4   (ui_shell at priority 2 is guaranteed ready)
  Subscribes  : system.readytostart
                system.start
                system.stop
                ui.shell.ready         → {} (triggers registration)
                ui.widget.geometry     → {name, x, y, w, h}
                input.event.navbar_ui  → {type, x, y, ...}
                media.state            → {state}  (play/pause icon)
                bt.state               → {connected, device_name}
  Publishes   : system.module_ready    → {name, priority}
                system.ready           → {name, priority}
                ui.widget.register     → registration constraints
                ui.widget.unregister   → {name}
                media.command          → {action}   (play_pause, next, prev)
                ui.home.pressed        → {}
                ui.settings.toggle     → {}

  Button layout (left → right):
    [home]  [prev]  [play_pause]  [next]  ···  [settings]

  Config keys : height      int   60   bar height in px
                min_height  int   48
                max_height  int   80
  State       : private

Path layout:
  root/
  ├── shared/
  └── modules/
      └── navbar_ui/
          └── main.py
"""

import signal
import sys
import time
import threading
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent
_MODULES   = _HERE.parent
_REPO_ROOT    = _MODULES.parent         # root
_PROTO_ROOT   = _REPO_ROOT / "protos"  # root/protos

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))
if str(_PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROTO_ROOT))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_schema import field_int     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "navbar_ui"
PRIORITY: int = 4

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "height":     field_int(default=60,  min=48,  max=80),
    "min_height": field_int(default=48,  min=24,  max=80),
    "max_height": field_int(default=80,  min=48,  max=160),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_media_state: str   = "stopped"   # stopped | playing | paused
_bt_connected: bool = False
_bt_device: str     = ""
_active_module: Optional[str] = None
_dpi_factor: float = 1.0

# ---------------------------------------------------------------------------
# PyQt6 window — set after Qt starts
# ---------------------------------------------------------------------------

_qt_app    = None
_qt_window = None
_system_start_event = threading.Event()

# Geometry received before Qt window is ready is stored here and applied
# once the window is initialised (via QTimer inside _run_qt).
_pending_geometry: Optional[tuple[int, int, int, int]] = None  # (x, y, w, h)
_pending_geometry_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

_shell_ready  = False
_geometry_set = False


def _register() -> None:
    """Publish ui.widget.register with current config constraints."""
    bus.publish("ui.widget.register", {
        "name":          MODULE_NAME,
        "z_order":       1,
        "dock":          "bottom",
        "width":         None,
        "min_width":     None,
        "max_width":     None,
        "height":        int(_config.get("height",     60) * _dpi_factor),
        "min_height":    int(_config.get("min_height", 48) * _dpi_factor),
        "max_height":    int(_config.get("max_height", 80) * _dpi_factor),
        "aspect_ratio":  None,
        "on_request":    False,
        "menu_order":    99,
        "icon":          "",
    })
    log.info("ui.widget.register published")


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_ui_shell_ready(topic: str, payload: dict) -> None:
    global _shell_ready
    _shell_ready = True
    log.info("ui.shell.ready received — registering widget")
    _register()


def on_widget_geometry(topic: str, payload: dict) -> None:
    """Called from the bus thread — must NOT touch Qt objects directly."""
    global _geometry_set, _dpi_factor
    if payload.get("name") != MODULE_NAME:
        return
    df = float(payload.get("dpi_factor", 1.0))
    old_df = _dpi_factor
    if df > 0:
        _dpi_factor = df
    x, y, w, h = int(payload["x"]), int(payload["y"]), int(payload["w"]), int(payload["h"])
    log.info(f"Geometry received: x={x} y={y} w={w} h={h} dpi={df}")
    _geometry_set = True

    if _qt_window is not None:
        # Qt window exists: schedule apply_geometry on the Qt thread.
        _qt_invoke_geometry(x, y, w, h)
    else:
        # Qt thread not ready yet: store for pickup by QTimer in _run_qt.
        with _pending_geometry_lock:
            global _pending_geometry  # noqa: PLW0603
            _pending_geometry = (x, y, w, h)
            log.debug("Geometry queued (Qt not ready yet)")
            
    if old_df != _dpi_factor and _shell_ready:
        _register()


def _qt_invoke_geometry(x: int, y: int, w: int, h: int) -> None:
    """Thread-safe geometry dispatch: store args, then poke the Qt event loop."""
    with _pending_geometry_lock:
        global _pending_geometry  # noqa: PLW0603
        _pending_geometry = (x, y, w, h)
    if _qt_window is not None:
        try:
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(_qt_window, "apply_pending_geometry",
                                     Qt.ConnectionType.QueuedConnection)
        except Exception as exc:
            log.warning(f"_qt_invoke_geometry failed: {exc}")


def _invoke(obj, slot: str, *args):
    if obj is None:
        return
    try:
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        q_args = [Q_ARG(type(a), a) for a in args]
        QMetaObject.invokeMethod(obj, slot, Qt.ConnectionType.QueuedConnection, *q_args)
    except Exception as exc:
        log.warning(f"_invoke({slot}) failed: {exc}")


def on_input_event(topic: str, payload: dict) -> None:
    if _qt_window is None:
        return
    _invoke(_qt_window, "handle_input", payload)


def on_media_state(topic: str, payload: dict) -> None:
    global _media_state
    _media_state = payload.get("state", "stopped")
    if _qt_window is not None:
        _invoke(_qt_window, "set_media_state", _media_state)


def on_bt_state(topic: str, payload: dict) -> None:
    global _bt_connected, _bt_device
    _bt_connected = bool(payload.get("connected", False))
    _bt_device    = payload.get("device_name", "")
    if _qt_window is not None:
        _invoke(_qt_window, "set_bt_state", _bt_connected, _bt_device)


def on_module_open(topic: str, payload: dict) -> None:
    global _active_module
    _active_module = payload.get("name")
    if _qt_window is not None:
        _invoke(_qt_window, "set_active_module", _active_module or "")


def on_module_close(topic: str, payload: dict) -> None:
    global _active_module
    if _active_module == payload.get("name"):
        _active_module = None
    if _qt_window is not None:
        _invoke(_qt_window, "set_active_module", _active_module or "")


# ---------------------------------------------------------------------------
# PyQt6 window implementation
# ---------------------------------------------------------------------------

def _run_qt() -> None:
    """
    Build and run the navbar_ui PyQt6 window.

    Button layout (left → right):
      [home]  [prev]  [play_pause]  [next]  ···(flex gap)···  [bt_dot]  [settings]

    home     → publishes ui.home.pressed {}
    settings → publishes ui.settings.toggle {}
    bt_dot   → passive indicator (no tap action), 8px circle
    """
    try:
        from PyQt6.QtWidgets import QApplication, QWidget
        from PyQt6.QtCore import Qt, QTimer, pyqtSlot
        from PyQt6.QtGui import QColor, QPainter, QFont
    except ImportError:
        log.warning("PyQt6 not available — navbar_ui running in headless mode")
        return

    global _qt_app, _qt_window

    _qt_app = QApplication.instance() or QApplication(sys.argv)

    # ------------------------------------------------------------------
    # Design tokens
    # ------------------------------------------------------------------
    BG_COLOR    = QColor(245, 245, 245, 245)
    TEXT_COLOR  = QColor(18, 18, 18)
    ACCENT      = QColor(25, 118, 210)
    BT_OFF_CLR  = QColor(0, 0, 0, 60)

    FONT_BODY = QFont("DM Sans", -1)
    FONT_BODY.setPixelSize(14)
    FONT_BODY.setWeight(QFont.Weight.Normal)

    FONT_ICON = QFont("DM Sans", -1)
    FONT_ICON.setPixelSize(16)
    FONT_ICON.setWeight(QFont.Weight.Normal)

    # Navbar icon set per UI_DESIGN_SYSTEM.md §Iconography
    # Lucide Icons thin stroke (stroke-width: 1.5).
    # Rendered via DM Sans glyphs — SVG rendering can be added via QtSvg.
    # Using Unicode Private Use / closest readable approximation until
    # SVG resources are bundled; names match the Lucide navbar icon table.
    ICON_HOME     = "○"  # Lucide: circle (filled via CSS; Home button)
    ICON_PREV     = "⏮"  # Lucide: skip-back
    ICON_PLAY     = "▶"  # Lucide: play
    ICON_PAUSE    = "⏸"  # Lucide: pause
    ICON_NEXT     = "⏭"  # Lucide: skip-forward
    ICON_SETTINGS = "⚙"  # Lucide: settings (gear)

    # ------------------------------------------------------------------
    class NavbarWindow(QWidget):
        def __init__(self):
            super().__init__()
            # Z-order managed exclusively by ui_shell via ui.widget.register
            # (z_order=2). Never set WindowStaysOnTopHint directly.
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            if hasattr(Qt.WidgetAttribute, "WA_DontShowOnScreen"):
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self.setFont(FONT_BODY)

            self._media_state  = "stopped"
            self._bt_connected = False
            self._bt_device    = ""
            self._active_module = None
            self._pressed_btn: Optional[str] = None
            self._buttons: dict[str, tuple]  = {}
            self._bar_w = 1024
            self._bar_h = 60
            self._shm_engine = None

        # ------ thread-safe public API ------

        @pyqtSlot()
        def apply_pending_geometry(self) -> None:
            """No-arg slot: reads latest geometry from _pending_geometry and applies it."""
            with _pending_geometry_lock:
                pending = _pending_geometry
            if pending is not None:
                self.apply_geometry(*pending)

        @pyqtSlot(int, int, int, int)
        def apply_geometry_slot(self, x: int, y: int, w: int, h: int) -> None:
            """Called on the Qt thread via invokeMethod."""
            self.apply_geometry(x, y, w, h)

        def apply_geometry(self, x: int, y: int, w: int, h: int) -> None:
            self._bar_w = w
            self._bar_h = h
            self.setGeometry(x, y, w, h)

            needs_rebuild = (
                self._shm_engine is None
                or w > self._shm_engine.max_width
                or h > self._shm_engine.max_height
            )
            if needs_rebuild:
                if self._shm_engine is not None:
                    self._shm_engine.cleanup()
                from shared.shm_helper import OffscreenWidgetEngine
                self._shm_engine = OffscreenWidgetEngine(
                    MODULE_NAME, w, h, bus=bus, max_width=w, max_height=h
                )
            else:
                self._shm_engine.resize(w, h)

            self.render_to_shm()

        def render_to_shm(self) -> None:
            if self._shm_engine is None:
                return
            self._shm_engine.render_and_swap(self)

        @pyqtSlot()
        def handle_frame_ack(self) -> None:
            if self._shm_engine is not None:
                self._shm_engine.on_swap_ack()
                if self._shm_engine.needs_redraw:
                    self.render_to_shm()

        @pyqtSlot(str)
        def set_media_state(self, state: str) -> None:
            self._media_state = state
            self.render_to_shm()

        @pyqtSlot(bool, str)
        def set_bt_state(self, connected: bool, device: str) -> None:
            self._bt_connected = connected
            self._bt_device    = device
            self.render_to_shm()

        @pyqtSlot(str)
        def set_active_module(self, name: str) -> None:
            self._active_module = name if name else None
            self.render_to_shm()

        @pyqtSlot(dict)
        def handle_input(self, payload: dict) -> None:
            from shared.shm_helper import inject_input_event
            inject_input_event(self, payload)

        def mousePressEvent(self, ev) -> None:
            self._pressed_btn = self._hit_button(int(ev.position().x()), int(ev.position().y()))
            self.render_to_shm()

        def mouseReleaseEvent(self, ev) -> None:
            btn = self._hit_button(int(ev.position().x()), int(ev.position().y()))
            if btn and btn == self._pressed_btn:
                self._on_button_tap(btn)
            self._pressed_btn = None
            self.render_to_shm()

        # ------ layout ------

        def _layout_buttons(self) -> dict[str, tuple]:
            w, h  = self._bar_w, self._bar_h
            df    = _dpi_factor
            
            # Scale touch target and spacing with dpi_factor
            bsize = int(44 * df)
            pad   = int(24 * df)
            gap   = int(8 * df)
            
            # Ensure buttons fit within navbar height
            if bsize > h:
                bsize = h
            yo    = (h - bsize) // 2

            buttons: dict[str, tuple] = {}
            x = pad
            for name in ("home", "prev", "play_pause", "next"):
                buttons[name] = (x, yo, bsize, bsize)
                x += bsize + gap

            buttons["settings"] = (w - pad - bsize, yo, bsize, bsize)
            return buttons

        def _hit_button(self, x: int, y: int) -> Optional[str]:
            for name, (bx, by, bw, bh) in self._buttons.items():
                if bx <= x < bx + bw and by <= y < by + bh:
                    return name
            return None

        def _on_button_tap(self, btn: str) -> None:
            log.debug(f"Button tapped: {btn}")
            if btn == "home":
                bus.publish("ui.home.pressed", {})
            elif btn == "settings":
                bus.publish("ui.settings.toggle", {})
            elif btn == "play_pause":
                bus.publish("media.command", {"action": "play_pause"})
            elif btn == "prev":
                bus.publish("media.command", {"action": "prev"})
            elif btn == "next":
                bus.publish("media.command", {"action": "next"})

        # ------ painting ------

        def paintEvent(self, _event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)

            w, h = self.width(), self.height()
            self._bar_w = w
            self._bar_h = h
            self._buttons = self._layout_buttons()

            p.fillRect(0, 0, w, h, BG_COLOR)
            p.setPen(QColor(0, 0, 0, 30))
            p.drawLine(0, 0, w, 0)

            self._draw_bt_dot(p)

            icons = {
                "home":      ICON_HOME,
                "prev":      ICON_PREV,
                "play_pause": ICON_PAUSE if self._media_state == "playing" else ICON_PLAY,
                "next":      ICON_NEXT,
                "settings":  ICON_SETTINGS,
            }
            
            # Scale font size dynamically with dpi_factor
            scaled_font = QFont("DM Sans", -1)
            scaled_font.setPixelSize(int(18 * _dpi_factor))
            p.setFont(scaled_font)
 
            for name, icon in icons.items():
                bx, by, bw, bh = self._buttons.get(name, (0, 0, 0, 0))
                if bw == 0:
                    continue
 
                is_pressed = (self._pressed_btn == name)
                is_active = False
                if name == "settings" and self._active_module == "config_ui":
                    is_active = True
                elif name == "home" and self._active_module is None:
                    is_active = True
 
                if is_active:
                    bg = ACCENT
                    fg = QColor(255, 255, 255)  # White text/icon on blue circle
                elif is_pressed:
                    bg = QColor(0, 0, 0, 30)
                    fg = TEXT_COLOR
                else:
                    bg = QColor(0, 0, 0, 10)
                    fg = TEXT_COLOR
 
                # Render fully circular MD3 button
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawEllipse(bx, by, bw, bh)

                p.setPen(fg)
                p.drawText(bx, by, bw, bh,
                           Qt.AlignmentFlag.AlignCenter, icon)

        def _draw_bt_dot(self, p: "QPainter") -> None:
            settings_x = self._buttons.get("settings", (0, 0, 0, 0))[0]
            df = _dpi_factor
            dot_r  = int(4 * df)
            dot_x  = settings_x - int(16 * df) - dot_r
            dot_y  = self._bar_h // 2
            color  = ACCENT if self._bt_connected else BT_OFF_CLR
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)

    # ------------------------------------------------------------------
    # Instantiate window and expose globally BEFORE exec() so that the
    # bus thread can call invokeMethod on it immediately.
    # ------------------------------------------------------------------
    _qt_window = NavbarWindow()
    _qt_window._media_state  = _media_state
    _qt_window._bt_connected = _bt_connected
    _qt_window._bt_device    = _bt_device
    _qt_window._active_module = _active_module

    # Apply any geometry that arrived before this thread was ready.
    def _apply_pending() -> None:
        with _pending_geometry_lock:
            pending = _pending_geometry
        if pending is not None:
            log.debug(f"Applying pending geometry: {pending}")
            if _qt_window is not None:
                _qt_window.apply_geometry(*pending)

    QTimer.singleShot(0, _apply_pending)

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _qt_app.exec()

    log.info("Qt event loop exited, cleaning up navbar UI resources...")
    if _qt_window is not None:
        if _qt_window._shm_engine is not None:
            _qt_window._shm_engine.cleanup()
        _qt_window.close()
        _qt_window = None
    _qt_app = None


# ---------------------------------------------------------------------------
# Config callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({
        k: v for k, v in config.items()
        if k in _SCHEMA and not isinstance(v, (dict, list))
    })
    _config = merged
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA or isinstance(value, (dict, list)):
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")
    if _shell_ready:
        _register()
    if _qt_window is not None:
        _invoke(_qt_window, "render_to_shm")


# ---------------------------------------------------------------------------
# Boot protocol
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — launching navbar_ui")
    cfg.get(schema=_SCHEMA)

    _system_start_event.set()

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info(f"system.ready published (priority={PRIORITY})")

    if _shell_ready:
        log.info("ui.shell.ready already received — registering immediately")
        _register()


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down navbar_ui")
    bus.publish("ui.widget.unregister", {"name": MODULE_NAME})
    if _qt_app is not None:
        try:
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(_qt_app, "quit", Qt.ConnectionType.QueuedConnection)
        except Exception:
            pass
    bus.stop()


def on_widget_frame_ack(topic: str, payload: dict) -> None:
    if _qt_window is not None:
        _invoke(_qt_window, "handle_frame_ack")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    bus.subscribe("ui.shell.ready",             on_ui_shell_ready)
    bus.subscribe("ui.widget.geometry",         on_widget_geometry)
    bus.subscribe(f"input.event.{MODULE_NAME}", on_input_event)
    bus.subscribe("media.state",                on_media_state)
    bus.subscribe("bt.state",                   on_bt_state)
    bus.subscribe("ui.module.open",             on_module_open)
    bus.subscribe("ui.module.close",            on_module_close)
    bus.subscribe(f"ui.widget.frame_ack.{MODULE_NAME}", on_widget_frame_ack)

    log.info("navbar_ui started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        _system_start_event.wait()
        _run_qt()
        if bus_thread is not None:
            bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
