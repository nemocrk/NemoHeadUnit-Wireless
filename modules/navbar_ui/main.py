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
_REPO_ROOT = _MODULES.parent

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

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

# ---------------------------------------------------------------------------
# PyQt6 window — set after Qt starts
# ---------------------------------------------------------------------------

_qt_app    = None
_qt_window = None
_qt_thread: Optional[threading.Thread] = None

# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

_shell_ready  = False
_geometry_set = False


def _register() -> None:
    """Publish ui.widget.register with current config constraints."""
    bus.publish("ui.widget.register", {
        "name":       MODULE_NAME,
        "z_order":    2,
        "dock":       "bottom",
        "height":     _config.get("height",     60),
        "min_height": _config.get("min_height", 48),
        "max_height": _config.get("max_height", 80),
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
    global _geometry_set
    if payload.get("name") != MODULE_NAME:
        return
    x, y, w, h = payload["x"], payload["y"], payload["w"], payload["h"]
    log.info(f"Geometry received: x={x} y={y} w={w} h={h}")
    if _qt_window is not None:
        try:
            _qt_window.apply_geometry(x, y, w, h)
        except Exception as exc:
            log.warning(f"apply_geometry failed: {exc}")
    _geometry_set = True


def on_input_event(topic: str, payload: dict) -> None:
    if _qt_window is None:
        return
    try:
        _qt_window.handle_input(payload)
    except Exception as exc:
        log.warning(f"handle_input failed: {exc}")


def on_media_state(topic: str, payload: dict) -> None:
    global _media_state
    _media_state = payload.get("state", "stopped")
    if _qt_window is not None:
        try:
            _qt_window.set_media_state(_media_state)
        except Exception:
            pass


def on_bt_state(topic: str, payload: dict) -> None:
    global _bt_connected, _bt_device
    _bt_connected = bool(payload.get("connected", False))
    _bt_device    = payload.get("device_name", "")
    if _qt_window is not None:
        try:
            _qt_window.set_bt_state(_bt_connected, _bt_device)
        except Exception:
            pass


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
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor, QPainter, QFont
    except ImportError:
        log.warning("PyQt6 not available — navbar_ui running in headless mode")
        return

    global _qt_app, _qt_window

    _qt_app = QApplication.instance() or QApplication(sys.argv)

    # ------------------------------------------------------------------
    # Design tokens
    # ------------------------------------------------------------------
    BG_COLOR    = QColor(28, 28, 28, 245)
    TEXT_COLOR  = QColor(240, 236, 228)
    ACCENT      = QColor(200, 184, 154)
    BT_OFF_CLR  = QColor(255, 255, 255, 60)

    FONT_BODY = QFont("DM Sans", -1)
    FONT_BODY.setPixelSize(14)
    FONT_BODY.setWeight(QFont.Weight.Normal)

    FONT_ICON = QFont("DM Sans", -1)
    FONT_ICON.setPixelSize(16)
    FONT_ICON.setWeight(QFont.Weight.Normal)

    # Unicode glyphs used as icon stand-ins until Phosphor/Lucide fonts land
    ICON_HOME     = "⌂"
    ICON_PREV     = "◄◄"
    ICON_PLAY     = "►"
    ICON_PAUSE    = "▌▌"
    ICON_NEXT     = "►►"
    ICON_SETTINGS = "⚙"

    # ------------------------------------------------------------------
    class NavbarWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFont(FONT_BODY)

            self._media_state  = "stopped"
            self._bt_connected = False
            self._bt_device    = ""
            self._pressed_btn: Optional[str] = None
            self._buttons: dict[str, tuple]  = {}
            self._bar_w = 1024
            self._bar_h = 60

        # ------ public API (called from bus thread) ------

        def apply_geometry(self, x: int, y: int, w: int, h: int) -> None:
            self._bar_w = w
            self._bar_h = h
            self.setGeometry(x, y, w, h)
            self.show()
            self.update()

        def set_media_state(self, state: str) -> None:
            self._media_state = state
            self.update()

        def set_bt_state(self, connected: bool, device: str) -> None:
            self._bt_connected = connected
            self._bt_device    = device
            self.update()

        def handle_input(self, payload: dict) -> None:
            ev_type = payload.get("type")
            x = int(payload.get("x", 0))
            y = int(payload.get("y", 0))
            if ev_type == "press":
                self._pressed_btn = self._hit_button(x, y)
                self.update()
            elif ev_type == "release":
                btn = self._hit_button(x, y)
                if btn and btn == self._pressed_btn:
                    self._on_button_tap(btn)
                self._pressed_btn = None
                self.update()

        # ------ layout ------

        def _layout_buttons(self) -> dict[str, tuple]:
            """
            Returns {name: (x, y, w, h)} for every tappable button.

            Left cluster  : home | prev | play_pause | next
            Right cluster : settings
            Passive dot   : bt_dot (not in returned dict — drawn separately)
            """
            w, h  = self._bar_w, self._bar_h
            bsize = min(h - 8, 44)          # square touch target
            pad   = 16
            gap   = 8
            yo    = (h - bsize) // 2

            buttons: dict[str, tuple] = {}

            # Left cluster
            x = pad
            for name in ("home", "prev", "play_pause", "next"):
                buttons[name] = (x, yo, bsize, bsize)
                x += bsize + gap

            # Right cluster — settings right-aligned
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

            # Background
            p.fillRect(0, 0, w, h, BG_COLOR)

            # Top separator
            p.setPen(QColor(255, 255, 255, 18))
            p.drawLine(0, 0, w, 0)

            # BT passive dot (right side, left of settings)
            self._draw_bt_dot(p)

            # Buttons
            icons = {
                "home":      ICON_HOME,
                "prev":      ICON_PREV,
                "play_pause": ICON_PAUSE if self._media_state == "playing" else ICON_PLAY,
                "next":      ICON_NEXT,
                "settings":  ICON_SETTINGS,
            }
            p.setFont(FONT_ICON)
            for name, icon in icons.items():
                bx, by, bw, bh = self._buttons.get(name, (0, 0, 0, 0))
                if bw == 0:
                    continue
                is_pressed = self._pressed_btn == name
                bg = QColor(255, 255, 255, 30 if is_pressed else 12)
                p.fillRect(bx, by, bw, bh, bg)
                p.setPen(ACCENT if is_pressed else TEXT_COLOR)
                p.drawText(bx, by, bw, bh,
                           Qt.AlignmentFlag.AlignCenter, icon)

        def _draw_bt_dot(self, p: "QPainter") -> None:
            """Passive 8px circle BT indicator, left of settings button."""
            settings_x = self._buttons.get("settings", (0, 0, 0, 0))[0]
            dot_r  = 4
            dot_x  = settings_x - 16 - dot_r
            dot_y  = self._bar_h // 2
            color  = ACCENT if self._bt_connected else BT_OFF_CLR
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)

    _qt_window = NavbarWindow()
    _qt_window._media_state  = _media_state
    _qt_window._bt_connected = _bt_connected
    _qt_window._bt_device    = _bt_device

    _qt_app.exec()


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

    global _qt_thread
    _qt_thread = threading.Thread(target=_run_qt, name="navbar_ui-qt", daemon=True)
    _qt_thread.start()

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
            _qt_app.quit()
        except Exception:
            pass
    bus.stop()


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

    log.info("navbar_ui started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
