"""
NemoHeadUnit-Wireless — ui_shell

Layout engine, widget geometry orchestrator and input_trap co-process.

  Name        : ui_shell
  Priority    : 2
  Subscribes  : system.readytostart
                system.start
                system.stop
                ui.widget.register  → {name, z_order, dock, width, min_width,
                                        max_width, height, min_height, max_height,
                                        aspect_ratio, on_request, menu_order, icon}
                ui.widget.update    → {name, [height], [width], ...}
                ui.widget.unregister→ {name}
                input.raw           → {type, x_global, y_global, timestamp}
  Publishes   : system.module_ready → {name, priority}
                system.ready       → {name, priority}
                ui.shell.ready     → {}
                ui.widget.geometry → {name, x, y, w, h, dpi_factor}
                ui.focus.changed   → {name}
                input.event.<name> → {type, x, y, x_global, y_global, ...}
  Config keys : fullscreen  bool    True      start in fullscreen mode
                screen_w    int     1024      logical screen width  (ignored in fullscreen)
                screen_h    int     600       logical screen height (ignored in fullscreen)
                dpi_factor  float   1.0       DPI scaling factor (1.0 = 160dpi base)
  State       : private

Path layout:
  root/
  ├── shared/
  └── modules/
      └── ui_shell/
          └── main.py
"""

import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent   # modules/ui_shell/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_schema import field_bool, field_int, field_float  # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "ui_shell"
PRIORITY: int = 2

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "fullscreen": field_bool(default=True),
    "screen_w":   field_int(default=1024, min=320, max=7680),
    "screen_h":   field_int(default=600,  min=240, max=4320),
    "dpi_factor": field_float(default=1.0, min=0.5, max=4.0),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

# ---------------------------------------------------------------------------
# Layout engine data model
# ---------------------------------------------------------------------------

VALID_DOCKS = {
    "top", "bottom", "left", "right",
    "top-left", "top-right", "bottom-left", "bottom-right",
    "center",
}


@dataclass
class WidgetConstraints:
    """Registration constraints for a single widget process."""

    name: str
    z_order: int
    dock: str
    width: Optional[int] = None
    min_width: Optional[int] = None
    max_width: Optional[int] = None
    height: Optional[int] = None
    min_height: Optional[int] = None
    max_height: Optional[int] = None
    aspect_ratio: Optional[float] = None
    on_request: bool = False
    menu_order: int = 99
    icon: str = ""


@dataclass
class WidgetGeometry:
    """Last computed absolute geometry for a widget."""

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


@dataclass
class WidgetRecord:
    constraints: WidgetConstraints
    geometry: WidgetGeometry = field(default_factory=WidgetGeometry)


# Active widget registry: name → WidgetRecord
_registry: dict[str, WidgetRecord] = {}
_registry_lock = threading.Lock()

# Screen dimensions (updated when ui_shell window resizes)
_screen_w: int = 1024
_screen_h: int = 600

# ---------------------------------------------------------------------------
# Layout engine
# ---------------------------------------------------------------------------

def _clamp(value: int, lo: Optional[int], hi: Optional[int]) -> int:
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return value


def _resolve_size(
    available: int,
    fixed: Optional[int],
    min_v: Optional[int],
    max_v: Optional[int],
) -> int:
    """Return the resolved dimension respecting fixed/min/max constraints."""
    base = fixed if fixed is not None else available
    return _clamp(base, min_v, max_v)


def _compute_geometry(record: WidgetRecord, sw: int, sh: int) -> WidgetGeometry:
    """Compute absolute geometry for a widget given screen dimensions."""
    c = record.constraints
    w = _resolve_size(sw, c.width, c.min_width, c.max_width)
    h = _resolve_size(sh, c.height, c.min_height, c.max_height)

    if c.aspect_ratio is not None:
        if c.width is None and c.height is not None:
            w = int(h * c.aspect_ratio)
        elif c.height is None and c.width is not None:
            h = int(w / c.aspect_ratio)

    w = max(1, w)
    h = max(1, h)

    dock = c.dock
    x = 0
    y = 0

    if dock == "bottom":
        x = 0
        y = sh - h
        w = sw
    elif dock == "top":
        x = 0
        y = 0
        w = sw
    elif dock == "left":
        x = 0
        y = 0
        h = sh
    elif dock == "right":
        x = sw - w
        y = 0
        h = sh
    elif dock == "top-left":
        x = 0
        y = 0
    elif dock == "top-right":
        x = sw - w
        y = 0
    elif dock == "bottom-left":
        x = 0
        y = sh - h
    elif dock == "bottom-right":
        x = sw - w
        y = sh - h
    elif dock == "center":
        x = (sw - w) // 2
        y = (sh - h) // 2

    return WidgetGeometry(x=x, y=y, w=w, h=h)


def _reflow() -> dict[str, WidgetGeometry]:
    """Recompute geometry for ALL registered widgets."""
    sw = _screen_w
    sh = _screen_h

    layers: dict[int, list[WidgetRecord]] = {}
    for rec in _registry.values():
        z = rec.constraints.z_order
        layers.setdefault(z, []).append(rec)

    new_geometries: dict[str, WidgetGeometry] = {}

    top_offset    = 0
    bottom_offset = 0
    left_offset   = 0
    right_offset  = 0

    for z in sorted(layers):
        layer_records = layers[z]

        by_dock: dict[str, list[WidgetRecord]] = {}
        for rec in layer_records:
            by_dock.setdefault(rec.constraints.dock, []).append(rec)

        for rec in by_dock.get("top", []):
            c = rec.constraints
            h = _resolve_size(sh, c.height, c.min_height, c.max_height)
            w = sw - left_offset - right_offset
            g = WidgetGeometry(x=left_offset, y=top_offset, w=w, h=h)
            new_geometries[c.name] = g
            top_offset += h

        for rec in by_dock.get("bottom", []):
            c = rec.constraints
            h = _resolve_size(sh, c.height, c.min_height, c.max_height)
            w = sw - left_offset - right_offset
            y = sh - bottom_offset - h
            g = WidgetGeometry(x=left_offset, y=y, w=w, h=h)
            new_geometries[c.name] = g
            bottom_offset += h

        for rec in by_dock.get("left", []):
            c = rec.constraints
            w = _resolve_size(sw, c.width, c.min_width, c.max_width)
            h = sh - top_offset - bottom_offset
            g = WidgetGeometry(x=left_offset, y=top_offset, w=w, h=h)
            new_geometries[c.name] = g
            left_offset += w

        for rec in by_dock.get("right", []):
            c = rec.constraints
            w = _resolve_size(sw, c.width, c.min_width, c.max_width)
            h = sh - top_offset - bottom_offset
            x = sw - right_offset - w
            g = WidgetGeometry(x=x, y=top_offset, w=w, h=h)
            new_geometries[c.name] = g
            right_offset += w

        for dock in ("top-left", "top-right", "bottom-left", "bottom-right"):
            for rec in by_dock.get(dock, []):
                g = _compute_geometry(rec, sw, sh)
                new_geometries[rec.constraints.name] = g

        for rec in by_dock.get("center", []):
            c = rec.constraints
            avail_w = sw - left_offset - right_offset
            avail_h = sh - top_offset - bottom_offset
            w = _resolve_size(avail_w, c.width, c.min_width, c.max_width)
            h = _resolve_size(avail_h, c.height, c.min_height, c.max_height)
            x = left_offset + (avail_w - w) // 2
            y = top_offset  + (avail_h - h) // 2
            new_geometries[c.name] = WidgetGeometry(x=x, y=y, w=w, h=h)

    return new_geometries


def _publish_geometries(new_geometries: dict[str, WidgetGeometry]) -> None:
    """Publish ui.widget.geometry for every changed widget and update registry."""
    dpi_factor = float(_config.get("dpi_factor", 1.0))
    with _registry_lock:
        for name, geom in new_geometries.items():
            if name not in _registry:
                continue
            old = _registry[name].geometry
            if (old.x, old.y, old.w, old.h) != (geom.x, geom.y, geom.w, geom.h):
                _registry[name].geometry = geom
                bus.publish(
                    "ui.widget.geometry",
                    {
                        "name": name,
                        "x": geom.x,
                        "y": geom.y,
                        "w": geom.w,
                        "h": geom.h,
                        "dpi_factor": dpi_factor,
                    },
                )
                log.debug(
                    f"ui.widget.geometry → {name}: "
                    f"x={geom.x} y={geom.y} w={geom.w} h={geom.h} dpi={dpi_factor}"
                )


# ---------------------------------------------------------------------------
# Input routing
# ---------------------------------------------------------------------------

def _hit_test(x_global: int, y_global: int) -> Optional[str]:
    with _registry_lock:
        sorted_names = sorted(
            _registry.keys(),
            key=lambda n: (_registry[n].constraints.z_order, list(_registry.keys()).index(n)),
            reverse=True,
        )
        for name in sorted_names:
            g = _registry[name].geometry
            if g.x <= x_global < g.x + g.w and g.y <= y_global < g.y + g.h:
                return name
    return None


def on_input_raw(topic: str, payload: dict) -> None:
    x_global = payload.get("x_global", 0)
    y_global = payload.get("y_global", 0)
    target = _hit_test(x_global, y_global)
    if target is None:
        return

    with _registry_lock:
        g = _registry[target].geometry if target in _registry else None
    if g is None:
        return

    routed = dict(payload)
    routed["x"] = x_global - g.x
    routed["y"] = y_global - g.y
    bus.publish(f"input.event.{target}", routed)


# ---------------------------------------------------------------------------
# Widget lifecycle handlers
# ---------------------------------------------------------------------------

def on_widget_register(topic: str, payload: dict) -> None:
    name = payload.get("name")
    if not name:
        log.warning("ui.widget.register: missing 'name' field — ignored")
        return

    dock = payload.get("dock", "center")
    if dock not in VALID_DOCKS:
        log.warning(f"ui.widget.register: invalid dock '{dock}' for '{name}' — ignored")
        return

    constraints = WidgetConstraints(
        name         = name,
        z_order      = int(payload.get("z_order", 2)),
        dock         = dock,
        width        = payload.get("width"),
        min_width    = payload.get("min_width"),
        max_width    = payload.get("max_width"),
        height       = payload.get("height"),
        min_height   = payload.get("min_height"),
        max_height   = payload.get("max_height"),
        aspect_ratio = payload.get("aspect_ratio"),
        on_request   = bool(payload.get("on_request", False)),
        menu_order   = int(payload.get("menu_order", 99)),
        icon         = str(payload.get("icon", "")),
    )

    with _registry_lock:
        _registry[name] = WidgetRecord(constraints=constraints)

    log.info(f"Widget registered: '{name}' dock={dock} z={constraints.z_order} on_request={constraints.on_request}")
    new_geometries = _reflow()
    _publish_geometries(new_geometries)


def on_widget_update(topic: str, payload: dict) -> None:
    name = payload.get("name")
    if not name:
        log.warning("ui.widget.update: missing 'name' — ignored")
        return

    with _registry_lock:
        if name not in _registry:
            log.warning(f"ui.widget.update: unknown widget '{name}' — ignored")
            return
        c = _registry[name].constraints
        for attr in ("width", "min_width", "max_width", "height", "min_height", "max_height", "aspect_ratio"):
            if attr in payload:
                setattr(c, attr, payload[attr])

    log.debug(f"Widget updated: '{name}'")
    new_geometries = _reflow()
    _publish_geometries(new_geometries)


def on_widget_unregister(topic: str, payload: dict) -> None:
    name = payload.get("name")
    if not name:
        log.warning("ui.widget.unregister: missing 'name' — ignored")
        return

    with _registry_lock:
        removed = _registry.pop(name, None)

    if removed:
        log.info(f"Widget unregistered: '{name}'")
        new_geometries = _reflow()
        _publish_geometries(new_geometries)
    else:
        log.warning(f"ui.widget.unregister: unknown widget '{name}' — ignored")


# ---------------------------------------------------------------------------
# Screen resize
# ---------------------------------------------------------------------------

def on_screen_resize(new_w: int, new_h: int) -> None:
    global _screen_w, _screen_h
    _screen_w = new_w
    _screen_h = new_h
    log.info(f"Screen resized → {new_w}×{new_h}")
    new_geometries = _reflow()
    _publish_geometries(new_geometries)


# ---------------------------------------------------------------------------
# PyQt6 UI — ui_shell window + input_trap
# ---------------------------------------------------------------------------

_qt_app = None
_qt_thread: Optional[threading.Thread] = None


def _run_qt() -> None:
    try:
        from PyQt6.QtWidgets import QApplication, QWidget
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QPainter, QColor
    except ImportError:
        log.warning("PyQt6 not available — ui_shell running in headless mode")
        return

    global _qt_app

    _qt_app = QApplication.instance() or QApplication(sys.argv)

    class ShellWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("NemoHeadUnit")
            self.setStyleSheet("background: #141414;")
            if _config.get("fullscreen", True):
                self.showFullScreen()
            else:
                w = _config.get("screen_w", 1024)
                h = _config.get("screen_h", 600)
                self.setGeometry(0, 0, w, h)
                self.show()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            on_screen_resize(self.width(), self.height())
            if hasattr(self, "_trap") and self._trap is not None:
                self._trap.setGeometry(self.geometry())

        def paintEvent(self, event):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0x14, 0x14, 0x14))

    class InputTrap(QWidget):
        def __init__(self, shell: ShellWindow):
            super().__init__()
            self._shell = shell
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint       |
                Qt.WindowType.WindowStaysOnTopHint      |
                Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setGeometry(shell.geometry())
            self.show()

        def _publish_raw(self, event_type: str, x: int, y: int) -> None:
            bus.publish("input.raw", {
                "type":      event_type,
                "x_global":  x,
                "y_global":  y,
                "timestamp": int(time.time() * 1000),
            })

        def mousePressEvent(self, ev):
            self._publish_raw("press", int(ev.globalPosition().x()), int(ev.globalPosition().y()))

        def mouseMoveEvent(self, ev):
            self._publish_raw("move", int(ev.globalPosition().x()), int(ev.globalPosition().y()))

        def mouseReleaseEvent(self, ev):
            self._publish_raw("release", int(ev.globalPosition().x()), int(ev.globalPosition().y()))

        def keyPressEvent(self, ev):
            bus.publish("input.raw", {
                "type":           "key",
                "key":            ev.key(),
                "text":           ev.text(),
                "is_auto_repeat": ev.isAutoRepeat(),
                "x_global":       0,
                "y_global":       0,
                "timestamp":      int(time.time() * 1000),
            })

    shell = ShellWindow()
    trap  = InputTrap(shell)
    shell._trap = trap

    bus.publish("ui.shell.ready", {})
    log.info("ui.shell.ready published — shell window active")

    _qt_app.exec()


# ---------------------------------------------------------------------------
# Config callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config, _screen_w, _screen_h
    if not config:
        log.info("No persisted config — using defaults.")
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged
    _screen_w = _config.get("screen_w", 1024)
    _screen_h = _config.get("screen_h", 600)
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    if isinstance(value, (dict, list)):
        log.warning(f"config.changed: structural value for '{key}' rejected")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — launching ui_shell")
    cfg.get(schema=_SCHEMA)

    global _screen_w, _screen_h
    _screen_w = _config.get("screen_w", 1024)
    _screen_h = _config.get("screen_h", 600)

    global _qt_thread
    _qt_thread = threading.Thread(target=_run_qt, name="ui_shell-qt", daemon=True)
    _qt_thread.start()

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info(f"system.ready published (priority={PRIORITY})")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down ui_shell")
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

    bus.subscribe("ui.widget.register",   on_widget_register)
    bus.subscribe("ui.widget.update",     on_widget_update)
    bus.subscribe("ui.widget.unregister", on_widget_unregister)
    bus.subscribe("input.raw",            on_input_raw)

    log.info("ui_shell started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
