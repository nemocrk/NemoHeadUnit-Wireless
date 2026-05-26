"""
NemoHeadUnit-Wireless — floating_menu_ui

Arc-shaped floating menu anchored at bottom-right corner.
Discovers on_request modules via ui.widget.register, manages mutual
exclusivity, and routes open/close commands.

  Name        : floating_menu_ui
  Priority    : 3   (after ui_shell=2, before on_request widgets=4)
  Subscribes  : system.readytostart
                system.start
                system.stop
                ui.shell.ready          → {} (triggers registration)
                ui.widget.register      → {name, on_request, menu_order, icon, ...}
                ui.widget.geometry      → {name, x, y, w, h, dpi_factor}
                input.event.floating_menu_ui → {type, x, y, ...}
                ui.settings.toggle      → {} (show/hide menu)
                ui.home.pressed         → {} (close all + hide menu)
  Publishes   : system.module_ready     → {name, priority}
                system.ready            → {name, priority}
                ui.widget.register      → registration payload
                ui.widget.update        → {name, width, height} when visible
                ui.widget.unregister    → {name}
                ui.module.open          → {name}
                ui.module.close         → {name}

  Arc geometry:
    Anchored at bottom-right corner.
    Arc sweeps from bottom-right (270°) to top-right (0°), i.e. 90° quarter-circle.
    Radius base : 120px * dpi_factor
    Icon size   : 52px * dpi_factor
    Gap         : 8px  * dpi_factor
    Max visible : 8 icons (swipe/drag tangentially to scroll when N > 8)
    Icon active : inverted color scheme

  Z-order  : 3  (always above navbar=2 and on_request modules=2)
  Dock     : bottom-right

  Config keys : radius_base   int   120   base arc radius in logical px
                icon_size     int   52    icon diameter in logical px
                icon_gap      int   8     gap between icons in logical px

Path layout:
  root/
  ├── shared/
  └── modules/
      └── floating_menu_ui/
          └── main.py
"""

import math
import sys
import time
import threading
from dataclasses import dataclass
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

MODULE_NAME = "floating_menu_ui"
PRIORITY: int = 3

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "radius_base": field_int(default=120, min=60,  max=300),
    "icon_size":   field_int(default=52,  min=32,  max=96),
    "icon_gap":    field_int(default=8,   min=0,   max=32),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

# ---------------------------------------------------------------------------
# On-request module registry
# ---------------------------------------------------------------------------

@dataclass
class OnRequestEntry:
    name: str
    menu_order: int
    icon: str


_on_request_modules: dict[str, OnRequestEntry] = {}  # name → entry
_on_request_lock = threading.Lock()

_active_module: Optional[str] = None   # currently open on_request module
_dpi_factor: float = 1.0
_screen_h: int = 600
_navbar_h: int = 60                     # updated from ui.widget.geometry of navbar_ui

# ---------------------------------------------------------------------------
# Menu visibility state
# ---------------------------------------------------------------------------

_menu_visible: bool = False

# ---------------------------------------------------------------------------
# PyQt6 window
# ---------------------------------------------------------------------------

_qt_app    = None
_qt_window = None
_qt_thread: Optional[threading.Thread] = None

# ---------------------------------------------------------------------------
# Shell readiness gate
# ---------------------------------------------------------------------------

_shell_ready: bool  = False
_geometry_set: bool = False


def _register() -> None:
    """Register as a hidden widget at bottom-right; dimensions are 0,0 until visible."""
    bus.publish("ui.widget.register", {
        "name":       MODULE_NAME,
        "z_order":    3,
        "dock":       "bottom-right",
        "width":      0,
        "height":     0,
        "on_request": False,
    })
    log.info("ui.widget.register published (hidden)")


def _update_geometry(width: int, height: int) -> None:
    """Notify ui_shell of current bounding-box size."""
    bus.publish("ui.widget.update", {
        "name":   MODULE_NAME,
        "width":  width,
        "height": height,
    })


# ---------------------------------------------------------------------------
# Arc geometry helpers
# ---------------------------------------------------------------------------

def _arc_params() -> tuple[float, float, float, float]:
    """
    Returns (radius, icon_sz, gap, arc_len) all in logical pixels (pre-dpi).
    arc_len is the available arc length in pixels (quarter circle).
    """
    radius   = float(_config.get("radius_base", 120)) * _dpi_factor
    icon_sz  = float(_config.get("icon_size",   52))  * _dpi_factor
    gap      = float(_config.get("icon_gap",    8))   * _dpi_factor
    arc_len  = (math.pi / 2) * radius          # 90° = π/2 radians
    return radius, icon_sz, gap, arc_len


def _visible_count() -> int:
    """
    Number of icons that fit on the arc, capped at min(8, registered).
    arc_len / (icon_sz + gap) gives theoretical max slots.
    """
    radius, icon_sz, gap, arc_len = _arc_params()
    slots = int(arc_len / (icon_sz + gap))
    with _on_request_lock:
        total = len(_on_request_modules)
    return min(slots, 8, total)


def _icon_center(index: int, total_visible: int) -> tuple[float, float]:
    """
    (cx, cy) in window-local coordinates for icon at position `index`
    within the arc.

    The arc is a quarter-circle from angle 270° (bottom) to 0° (right)
    in standard math convention, i.e. from
      start_angle = -math.pi / 2  (pointing down from right-edge anchor)
    to
      end_angle   = 0             (pointing right, i.e. top of visible arc)

    The window is positioned at the screen’s bottom-right corner.
    Its local origin (0,0) is the top-left of the bounding box.
    The arc pivot is at local (radius, window_h), i.e. bottom-right of the
    bounding-box, which corresponds to the screen corner.
    """
    radius, icon_sz, gap, arc_len = _arc_params()
    if total_visible <= 1:
        step = 0.0
    else:
        step = (math.pi / 2) / (total_visible - 1)

    # angle 0 = bottom-right corner direction; we sweep counter-clockwise
    angle = (math.pi / 2) * (index / max(total_visible - 1, 1))

    # pivot is at bottom-right of our bounding box
    pivot_x = radius
    pivot_y = _bounding_h()

    cx = pivot_x - radius * math.cos(angle)
    cy = pivot_y - radius * math.sin(angle)
    return cx, cy


def _bounding_w() -> int:
    radius, _, _, _ = _arc_params()
    return int(radius)


def _bounding_h() -> int:
    """Arc can extend up to screen height minus navbar."""
    radius, _, _, _ = _arc_params()
    available = _screen_h - _navbar_h
    return int(min(radius, available))


# ---------------------------------------------------------------------------
# Sorted icon list
# ---------------------------------------------------------------------------

def _sorted_entries() -> list[OnRequestEntry]:
    with _on_request_lock:
        entries = list(_on_request_modules.values())
    return sorted(entries, key=lambda e: (e.menu_order, e.name))


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_ui_shell_ready(topic: str, payload: dict) -> None:
    global _shell_ready
    _shell_ready = True
    log.info("ui.shell.ready received — registering floating_menu_ui")
    _register()


def on_widget_register(topic: str, payload: dict) -> None:
    """Intercept registrations from on_request modules."""
    if not payload.get("on_request", False):
        return
    name = payload.get("name")
    if not name or name == MODULE_NAME:
        return

    entry = OnRequestEntry(
        name       = name,
        menu_order = int(payload.get("menu_order", 99)),
        icon       = str(payload.get("icon", "")),
    )
    with _on_request_lock:
        _on_request_modules[name] = entry
    log.info(f"on_request module discovered: '{name}' icon='{entry.icon}' order={entry.menu_order}")

    if _qt_window is not None:
        try:
            _qt_window.refresh()
        except Exception:
            pass


def on_widget_unregister(topic: str, payload: dict) -> None:
    name = payload.get("name")
    if not name:
        return
    with _on_request_lock:
        removed = _on_request_modules.pop(name, None)
    if removed:
        log.info(f"on_request module removed: '{name}'")
        if _qt_window is not None:
            try:
                _qt_window.refresh()
            except Exception:
                pass


def on_widget_geometry(topic: str, payload: dict) -> None:
    global _geometry_set, _dpi_factor, _screen_h, _navbar_h
    name = payload.get("name", "")
    _dpi_factor = float(payload.get("dpi_factor", 1.0))

    if name == "navbar_ui":
        _navbar_h = int(payload.get("h", 60))

    if name == MODULE_NAME:
        _geometry_set = True
        if _qt_window is not None:
            try:
                x = int(payload["x"])
                y = int(payload["y"])
                w = int(payload["w"])
                h = int(payload["h"])
                _qt_window.apply_geometry(x, y, w, h)
            except Exception as exc:
                log.warning(f"apply_geometry failed: {exc}")


def on_input_event(topic: str, payload: dict) -> None:
    if _qt_window is None:
        return
    try:
        _qt_window.handle_input(payload)
    except Exception as exc:
        log.warning(f"handle_input error: {exc}")


def on_settings_toggle(topic: str, payload: dict) -> None:
    """Show or hide the arc menu."""
    global _menu_visible
    _menu_visible = not _menu_visible
    log.info(f"settings.toggle — menu_visible={_menu_visible}")
    if _qt_window is not None:
        try:
            _qt_window.set_visible(_menu_visible)
        except Exception as exc:
            log.warning(f"set_visible failed: {exc}")
    if _menu_visible:
        w = _bounding_w()
        h = _bounding_h()
        _update_geometry(w, h)
    else:
        _update_geometry(0, 0)


def on_home_pressed(topic: str, payload: dict) -> None:
    """Close all on_request modules and hide the menu."""
    global _menu_visible, _active_module
    log.info("ui.home.pressed — closing all modules and hiding menu")
    _close_active_module()
    _active_module = None
    _menu_visible  = False
    if _qt_window is not None:
        try:
            _qt_window.set_visible(False)
        except Exception:
            pass
    _update_geometry(0, 0)


# ---------------------------------------------------------------------------
# Module open/close logic
# ---------------------------------------------------------------------------

def _close_active_module() -> None:
    """Send ui.module.close to the currently active module (if any)."""
    global _active_module
    if _active_module:
        bus.publish("ui.module.close", {"name": _active_module})
        log.info(f"ui.module.close → {_active_module}")
        _active_module = None


def _open_module(name: str) -> None:
    """Close active module (if different), then open the requested one."""
    global _active_module
    if _active_module == name:
        # already open — no-op (toggle-off not in spec)
        return
    _close_active_module()
    _active_module = name
    bus.publish("ui.module.open", {"name": name})
    log.info(f"ui.module.open → {name}")


# ---------------------------------------------------------------------------
# PyQt6 window implementation
# ---------------------------------------------------------------------------

def _run_qt() -> None:
    """
    Build and run the floating_menu_ui PyQt6 window.

    The window is initially hidden (0×0). When visible:
      - Draws a quarter-circle arc of circular icon buttons
      - Handles tangential drag gestures for scrolling when N > 8
      - Animates open/close via QPropertyAnimation on the window opacity
        and geometry (slide from bottom-right corner)
    """
    try:
        from PyQt6.QtWidgets import QApplication, QWidget
        from PyQt6.QtCore import (
            Qt, QPropertyAnimation, QEasingCurve,
            QRect, QPoint, pyqtProperty,
        )
        from PyQt6.QtGui import QColor, QPainter, QFont, QPen, QBrush
    except ImportError:
        log.warning("PyQt6 not available — floating_menu_ui running in headless mode")
        return

    global _qt_app, _qt_window

    _qt_app = QApplication.instance() or QApplication(sys.argv)

    # Design tokens
    COLOR_BG      = QColor(28, 28, 28, 220)
    COLOR_ICON    = QColor(240, 236, 228)
    COLOR_ICON_BG = QColor(50, 50, 50, 200)
    COLOR_ACTIVE_BG   = QColor(240, 236, 228, 240)
    COLOR_ACTIVE_ICON = QColor(28, 28, 28)

    FONT_ICON = QFont("DM Sans", -1)
    FONT_ICON.setPixelSize(18)

    class ArcMenuWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.hide()

            self._is_visible   = False
            self._scroll_offset = 0     # icon index of first visible icon
            self._drag_start_y: Optional[int] = None
            self._drag_start_offset: int = 0
            self._anim: Optional[QPropertyAnimation] = None
            self._opacity_val: float = 0.0

        # ---- public API ----

        def apply_geometry(self, x: int, y: int, w: int, h: int) -> None:
            if w > 0 and h > 0:
                self.setGeometry(x, y, w, h)
                self.show()
            else:
                self.hide()

        def set_visible(self, visible: bool) -> None:
            self._is_visible = visible
            if visible:
                self._animate_in()
            else:
                self._animate_out()

        def refresh(self) -> None:
            self.update()

        def handle_input(self, payload: dict) -> None:
            ev   = payload.get("type")
            x    = int(payload.get("x", 0))
            y    = int(payload.get("y", 0))

            if ev == "press":
                self._drag_start_y      = y
                self._drag_start_offset = self._scroll_offset

            elif ev == "move":
                if self._drag_start_y is not None:
                    delta = y - self._drag_start_y
                    icon_sz = float(_config.get("icon_size", 52)) * _dpi_factor
                    gap     = float(_config.get("icon_gap", 8))   * _dpi_factor
                    step    = icon_sz + gap
                    entries = _sorted_entries()
                    total   = len(entries)
                    vis     = _visible_count()
                    new_off = self._drag_start_offset - int(delta / step)
                    self._scroll_offset = max(0, min(new_off, total - vis))
                    self.update()

            elif ev == "release":
                if self._drag_start_y is not None:
                    delta = abs(y - self._drag_start_y)
                    if delta < 8:   # tap, not drag
                        hit = self._hit_icon(x, y)
                        if hit is not None:
                            _open_module(hit)
                            self.update()
                self._drag_start_y = None

        # ---- animation ----

        def _animate_in(self) -> None:
            from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
            if self._anim:
                self._anim.stop()
            self._anim = QPropertyAnimation(self, b"windowOpacity")
            self._anim.setDuration(220)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.setWindowOpacity(0.0)
            self.show()
            self._anim.start()

        def _animate_out(self) -> None:
            from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
            if self._anim:
                self._anim.stop()
            self._anim = QPropertyAnimation(self, b"windowOpacity")
            self._anim.setDuration(180)
            self._anim.setStartValue(1.0)
            self._anim.setEndValue(0.0)
            self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
            self._anim.finished.connect(self.hide)
            self._anim.start()

        # ---- hit testing ----

        def _icon_rects(self) -> list[tuple[str, int, int, int, int]]:
            """
            Returns [(name, cx-r, cy-r, 2r, 2r), ...] for visible icons.
            All coordinates are window-local.
            """
            entries  = _sorted_entries()
            vis      = _visible_count()
            start    = self._scroll_offset
            visible  = entries[start:start + vis]
            icon_sz  = int(float(_config.get("icon_size", 52)) * _dpi_factor)
            rects    = []
            for i, entry in enumerate(visible):
                cx, cy = _icon_center(i, vis)
                r = icon_sz // 2
                rects.append((entry.name, int(cx) - r, int(cy) - r, icon_sz, icon_sz))
            return rects

        def _hit_icon(self, x: int, y: int) -> Optional[str]:
            for name, rx, ry, rw, rh in self._icon_rects():
                if rx <= x < rx + rw and ry <= y < ry + rh:
                    return name
            return None

        # ---- painting ----

        def paintEvent(self, _event) -> None:
            if not self._is_visible:
                return

            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setFont(FONT_ICON)

            entries = _sorted_entries()
            vis     = _visible_count()
            start   = self._scroll_offset
            visible = entries[start:start + vis]

            for i, entry in enumerate(visible):
                cx, cy   = _icon_center(i, vis)
                icon_sz  = int(float(_config.get("icon_size", 52)) * _dpi_factor)
                r        = icon_sz // 2
                is_active = (entry.name == _active_module)

                # Background circle
                bg = COLOR_ACTIVE_BG if is_active else COLOR_ICON_BG
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawEllipse(int(cx) - r, int(cy) - r, icon_sz, icon_sz)

                # Icon label (Unicode glyph; Lucide SVG rendering TBD)
                icon_color = COLOR_ACTIVE_ICON if is_active else COLOR_ICON
                p.setPen(icon_color)
                p.drawText(
                    int(cx) - r, int(cy) - r, icon_sz, icon_sz,
                    Qt.AlignmentFlag.AlignCenter,
                    entry.icon or "?",
                )

            # Scroll hint dots when there are more icons than visible
            total = len(entries)
            if total > vis:
                self._draw_scroll_hint(p, total, vis)

        def _draw_scroll_hint(self, p: "QPainter", total: int, vis: int) -> None:
            """Draw small indicator dots near the arc edge."""
            from PyQt6.QtGui import QColor as QC
            dot_r    = 3
            dot_gap  = 8
            dots     = min(total, 5)
            start_x  = self.width() - 12
            start_y  = self.height() // 2 - (dots * (dot_r * 2 + dot_gap)) // 2

            for i in range(dots):
                frac   = (self._scroll_offset + vis / 2) / max(total - vis, 1)
                active = abs(i / (dots - 1) - frac) < 0.3 if dots > 1 else True
                clr    = QC(240, 236, 228, 200 if active else 80)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(clr)
                iy = start_y + i * (dot_r * 2 + dot_gap)
                p.drawEllipse(start_x - dot_r, iy, dot_r * 2, dot_r * 2)

    _qt_window = ArcMenuWindow()
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


# ---------------------------------------------------------------------------
# Boot protocol
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — launching floating_menu_ui")
    cfg.get(schema=_SCHEMA)

    global _qt_thread
    _qt_thread = threading.Thread(target=_run_qt, name="floating_menu_ui-qt", daemon=True)
    _qt_thread.start()

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info(f"system.ready published (priority={PRIORITY})")

    if _shell_ready:
        log.info("ui.shell.ready already received — registering immediately")
        _register()


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down floating_menu_ui")
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

    bus.subscribe("system.readytostart",          on_system_readytostart)
    bus.subscribe("system.start",                 on_system_start)
    bus.subscribe("system.stop",                  on_system_stop)

    bus.subscribe("ui.shell.ready",               on_ui_shell_ready)
    bus.subscribe("ui.widget.register",           on_widget_register)
    bus.subscribe("ui.widget.unregister",         on_widget_unregister)
    bus.subscribe("ui.widget.geometry",           on_widget_geometry)
    bus.subscribe(f"input.event.{MODULE_NAME}",   on_input_event)
    bus.subscribe("ui.settings.toggle",           on_settings_toggle)
    bus.subscribe("ui.home.pressed",              on_home_pressed)

    log.info("floating_menu_ui started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
