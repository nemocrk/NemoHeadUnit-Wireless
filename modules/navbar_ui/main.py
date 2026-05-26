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
_HERE      = Path(__file__).parent   # modules/navbar_ui/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

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
    """ui_shell is up: publish our registration."""
    global _shell_ready
    _shell_ready = True
    log.info("ui.shell.ready received — registering widget")
    _register()


def on_widget_geometry(topic: str, payload: dict) -> None:
    """Apply geometry when ui_shell responds with our coordinates."""
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
    """Route bus input events into the Qt window."""
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
    """Build and run the navbar_ui PyQt6 window.

    Window is transparent, frameless, Tool-type (hidden from taskbar).
    It never positions itself — geometry is applied exclusively via
    apply_geometry() which is called from on_widget_geometry().
    """
    try:
        from PyQt6.QtWidgets import QApplication, QWidget, QHBoxLayout, QPushButton, QLabel
        from PyQt6.QtCore import Qt, QTimer, QPointF
        from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFont, QMouseEvent, QCursor
    except ImportError:
        log.warning("PyQt6 not available — navbar_ui running in headless mode")
        return

    global _qt_app, _qt_window

    _qt_app = QApplication.instance() or QApplication(sys.argv)

    # ------------------------------------------------------------------
    # Design tokens (Scandinavian dark, from UI_DESIGN_SYSTEM.md)
    # ------------------------------------------------------------------
    BG_COLOR   = QColor(28, 28, 28, 245)   # rgba(28,28,28,0.96) frosted glass base
    TEXT_COLOR = QColor(240, 236, 228)       # warm-white #f0ece4
    ACCENT     = QColor(200, 184, 154)       # sand #c8b89a
    RADIUS     = 0                           # full-width bar, no rounding at bottom

    FONT_BODY = QFont("DM Sans", -1)
    FONT_BODY.setPixelSize(14)
    FONT_BODY.setWeight(QFont.Weight.Normal)

    ICON_PREV  = "◄◄"   # U+25C4 x2  — prev track
    ICON_PLAY  = "►"     # U+25BA     — play
    ICON_PAUSE = "▌▌"   # U+258C x2  — pause (thin bars)
    ICON_NEXT  = "►►"   # U+25BA x2  — next track
    ICON_BT_ON = "BT•"   # connected
    ICON_BT_OFF= "BT◦"   # disconnected

    # ------------------------------------------------------------------
    # Navbar window
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

            # Internal state mirrors
            self._media_state = "stopped"
            self._bt_connected = False
            self._bt_device = ""

            # Track pressed button for visual feedback
            self._pressed_btn: Optional[str] = None

            # Button hit areas: {name: (x, y, w, h)} — computed in _layout_buttons
            self._buttons: dict[str, tuple] = {}

            # Will be set to actual dimensions after apply_geometry
            self._bar_w = 1024
            self._bar_h = 60

        # ------ public API called from bus thread ------

        def apply_geometry(self, x: int, y: int, w: int, h: int) -> None:
            """Apply absolute geometry and show the window."""
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
            self._bt_device = device
            self.update()

        def handle_input(self, payload: dict) -> None:
            """Process routed input from ui_shell."""
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

        # ------ private helpers ------

        def _layout_buttons(self) -> dict[str, tuple]:
            """Compute button hit areas based on current bar dimensions.

            Layout (left-to-right, centered vertically):
              [prev]  [play/pause]  [next]     ...(center gap)...  [bt_status]

            Returns dict: name → (x, y, w, h)
            """
            w, h = self._bar_w, self._bar_h
            btn_size = min(h - 8, 44)   # square, min touch target 44px
            pad_x    = 16
            gap      = 8
            y_offset = (h - btn_size) // 2

            x = pad_x
            buttons = {}

            for name in ("prev", "play_pause", "next"):
                buttons[name] = (x, y_offset, btn_size, btn_size)
                x += btn_size + gap

            # BT status — right-aligned
            bt_w = 48
            buttons["bt"] = (w - pad_x - bt_w, y_offset, bt_w, btn_size)

            return buttons

        def _hit_button(self, x: int, y: int) -> Optional[str]:
            for name, (bx, by, bw, bh) in self._buttons.items():
                if bx <= x < bx + bw and by <= y < by + bh:
                    return name
            return None

        def _on_button_tap(self, btn: str) -> None:
            log.debug(f"Button tapped: {btn}")
            if btn == "play_pause":
                bus.publish("media.command", {"action": "play_pause"})
            elif btn == "prev":
                bus.publish("media.command", {"action": "prev"})
            elif btn == "next":
                bus.publish("media.command", {"action": "next"})
            # bt: no action for now (reserved for future bt_ui toggle)

        # ------ painting ------

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)

            w, h = self.width(), self.height()
            self._bar_w = w
            self._bar_h = h

            # Recompute button layout every frame (cheap)
            self._buttons = self._layout_buttons()

            # Background — frosted glass simulation (opaque for now,
            # blur requires compositor support unavailable in all targets)
            p.fillRect(0, 0, w, h, BG_COLOR)

            # Subtle top separator line
            p.setPen(QColor(255, 255, 255, 18))
            p.drawLine(0, 0, w, 0)

            # Draw buttons
            self._draw_media_buttons(p)
            self._draw_bt_indicator(p)

        def _draw_media_buttons(self, p: "QPainter") -> None:
            """Draw prev / play-pause / next buttons."""
            from PyQt6.QtCore import Qt
            p.setFont(FONT_BODY)

            play_icon = ICON_PAUSE if self._media_state == "playing" else ICON_PLAY

            icons = {
                "prev":      ICON_PREV,
                "play_pause": play_icon,
                "next":      ICON_NEXT,
            }

            for name, icon in icons.items():
                bx, by, bw, bh = self._buttons.get(name, (0, 0, 0, 0))
                if bw == 0:
                    continue

                is_pressed = self._pressed_btn == name
                bg = QColor(255, 255, 255, 30 if is_pressed else 12)
                p.fillRect(bx, by, bw, bh, bg)

                color = ACCENT if is_pressed else TEXT_COLOR
                p.setPen(color)
                p.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, icon)

        def _draw_bt_indicator(self, p: "QPainter") -> None:
            """Draw Bluetooth status indicator (right side)."""
            from PyQt6.QtCore import Qt
            bx, by, bw, bh = self._buttons.get("bt", (0, 0, 0, 0))
            if bw == 0:
                return

            icon  = ICON_BT_ON if self._bt_connected else ICON_BT_OFF
            color = ACCENT if self._bt_connected else QColor(255, 255, 255, 80)
            p.setPen(color)
            p.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, icon)

    # Instantiate and keep global reference so bus handlers can call methods
    _qt_window = NavbarWindow()

    # Apply initial state from bus if already received before Qt started
    _qt_window._media_state  = _media_state
    _qt_window._bt_connected = _bt_connected
    _qt_window._bt_device    = _bt_device

    # Do NOT call show() yet — wait for ui.widget.geometry
    _qt_app.exec()


# ---------------------------------------------------------------------------
# Config callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        return
    if isinstance(value, (dict, list)):
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")
    # Re-register with updated height constraints
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

    # Start Qt in a background thread
    global _qt_thread
    _qt_thread = threading.Thread(target=_run_qt, name="navbar_ui-qt", daemon=True)
    _qt_thread.start()

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info(f"system.ready published (priority={PRIORITY})")

    # If ui_shell was already ready before we subscribed, register immediately.
    # (Race guard: ui.shell.ready might have fired between priority 2 and 4.)
    # In practice the orchestrator guarantees ordering, but defensive is better.
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

    bus.subscribe("ui.shell.ready",               on_ui_shell_ready)
    bus.subscribe("ui.widget.geometry",           on_widget_geometry)
    bus.subscribe(f"input.event.{MODULE_NAME}",   on_input_event)
    bus.subscribe("media.state",                  on_media_state)
    bus.subscribe("bt.state",                     on_bt_state)

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
