# UI Architecture — NemoHeadUnit-Wireless

## Overview

This document defines the target multi-process UI architecture for NemoHeadUnit-Wireless. The design solves the GIL contention problem between touch input processing and video rendering by isolating each concern in a dedicated OS process, while maintaining a unified visual surface through a layered window composition strategy.

---

## Core Design Principles

- **One visual surface**: `ui_shell` owns the only opaque window; all widget processes render on transparent frameless windows positioned above it.
- **GIL isolation**: every UI module runs in a separate Python process with its own GIL — video rendering is never blocked by input handling or panel logic.
- **Bus-only coupling**: processes communicate exclusively via ZeroMQ bus; no direct imports, no shared memory, no OS-level window embedding.
- **Declarative layout**: widget processes declare their layout constraints to `ui_shell`; the shell computes absolute geometry and pushes coordinates down.
- **Centralised input**: a transparent always-on-top `input_trap` window (co-process of `ui_shell`) captures all touch and keyboard events and routes them via bus to the correct widget.

---

## Process Map

| Process | Z-order | Background | Resizeable | Receives input directly |
|---|---|---|---|---|
| `ui_shell` | 1 | ✅ opaque black | ✅ yes (manages fullscreen) | ❌ no |
| `widget_*` | 2 … N | ❌ transparent, no border | ❌ no | ❌ no |
| `input_trap` | ∞ | ❌ transparent, no border | follows `ui_shell` | ✅ captures everything |

`input_trap` runs inside the same process as `ui_shell`, sharing the same Qt event loop and window coordinate space.

Widget processes never overlap `ui_shell` visually because `ui_shell` is always the lowest visible layer with an opaque black background. All transparency in widget windows reveals `ui_shell`, not the desktop.

---

## Window Configuration

### `ui_shell` window

```python
window.setWindowFlags(Qt.WindowType.Window)
window.setStyleSheet("background: black;")
window.showFullScreen()   # or setGeometry() for windowed dev mode
```

### Widget window (every `widget_*` process)

```python
window.setWindowFlags(
    Qt.WindowType.FramelessWindowHint |
    Qt.WindowType.Tool                 # hidden from taskbar
)
window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
window.setFixedSize(w, h)             # set by ui_shell via bus
# position set exclusively via ui.widget.geometry messages
```

### `input_trap` window (co-process of `ui_shell`)

```python
trap.setWindowFlags(
    Qt.WindowType.FramelessWindowHint      |
    Qt.WindowType.WindowStaysOnTopHint     |
    Qt.WindowType.Tool
)
trap.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
trap.setGeometry(ui_shell.geometry())   # always mirrors ui_shell
# trap resizes whenever ui_shell resizes
```

---

## Widget Registration Protocol

### Registration message

Every widget process publishes `ui.widget.register` once it is ready:

```json
{
  "name":         "navbar",
  "z_order":      2,
  "dock":         "bottom",
  "width":        null,
  "min_width":    null,
  "max_width":    null,
  "height":       60,
  "min_height":   48,
  "max_height":   80,
  "aspect_ratio": null
}
```

**Field reference:**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✅ | Unique widget identifier, matches module name |
| `z_order` | int | ✅ | Layer index; widgets with equal `z_order` are placed on the same layer and arranged side-by-side |
| `dock` | enum | ✅ | `top`, `bottom`, `left`, `right`, `top-left`, `top-right`, `bottom-left`, `bottom-right`, `center` |
| `width` | int \| null | — | Fixed width in px; null = fill available |
| `min_width` | int \| null | — | Minimum width constraint |
| `max_width` | int \| null | — | Maximum width constraint |
| `height` | int \| null | — | Fixed height in px; null = fill available |
| `min_height` | int \| null | — | Minimum height constraint |
| `max_height` | int \| null | — | Maximum height constraint |
| `aspect_ratio` | float \| null | — | width/height ratio; overrides free dimension if set |

### Runtime geometry update

A widget that changes its size at runtime (e.g., a panel expanding on user interaction) publishes:

```json
{
  "name":   "settings_button",
  "height": 320
}
```

`ui_shell` recomputes the geometry of all widgets on the same layer and pushes updated `ui.widget.geometry` messages to every affected widget.

### Geometry response

`ui_shell` responds to registration and updates by publishing:

```json
{
  "name": "navbar",
  "x": 0,
  "y": 540,
  "w": 1024,
  "h": 60
}
```

The widget applies this immediately:

```python
self.setGeometry(x, y, w, h)
self.show()
```

### Deregistration

When a widget process shuts down it publishes:

```json
{ "name": "navbar" }
```

`ui_shell` removes the widget shadow and reflows the affected layer.

---

## Layout Engine

`ui_shell` maintains an internal **layout registry**: a dictionary keyed by `name`, storing the registration constraints and the last computed geometry for every active widget.

### Layer model

- Widgets with the same `z_order` form a **layer**.
- Within a layer, widgets are arranged according to their `dock` value using a flex-like algorithm:
  - `top` / `bottom` widgets share horizontal space (arranged left-to-right by registration order).
  - `left` / `right` widgets share vertical space (arranged top-to-bottom).
  - `center` widgets occupy remaining space after edge-docked widgets are placed.
- Layers are stacked vertically by `z_order`; higher values appear in front.

### Reflow triggers

Layout is recomputed when:

1. A new widget registers (`ui.widget.register`)
2. A widget updates its constraints (`ui.widget.update`)
3. `ui_shell` is resized or toggled to fullscreen
4. A widget deregisters (`ui.widget.unregister`)

On reflow, `ui_shell` publishes `ui.widget.geometry` to every widget whose computed position or size has changed.

---

## Input Routing

### Capture

`input_trap` receives all `QMouseEvent`, `QTouchEvent`, and `QKeyEvent` instances from Qt before any other window. It serialises each event and publishes it on the bus.

### Raw event topic

```
input.raw → {
  "type":      "press" | "move" | "release" | "key",
  "x_global":  412,
  "y_global":  230,
  "timestamp": 1748257012345
}
```

### Hit-test and routing

`ui_shell` subscribes to `input.raw`, performs a hit-test against its layout registry (iterating layers from highest `z_order` downward), identifies the target widget, and publishes:

```
input.event.<name> → {
  "type":         "press" | "move" | "release" | "key",

  // pointer events
  "x":            412,          // relative to widget origin
  "y":            190,
  "x_global":     412,
  "y_global":     230,
  "button":       1,            // Qt.MouseButton int value
  "buttons":      1,            // bitmask of currently held buttons
  "modifiers":    0,            // Qt.KeyboardModifier bitmask

  // key events (type == "key" only)
  "key":          16777220,     // Qt.Key int value
  "text":         "\r",
  "is_auto_repeat": false,

  "timestamp":    1748257012345
}
```

### Widget-side reconstruction

The widget process deserialises the message and constructs a synthetic Qt event:

```python
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtCore import Qt, QPointF

def on_input_event(payload: dict) -> None:
    if payload["type"] in ("press", "move", "release"):
        event_type = {
            "press":   QEvent.Type.MouseButtonPress,
            "move":    QEvent.Type.MouseMove,
            "release": QEvent.Type.MouseButtonRelease,
        }[payload["type"]]
        ev = QMouseEvent(
            event_type,
            QPointF(payload["x"], payload["y"]),
            QPointF(payload["x_global"], payload["y_global"]),
            Qt.MouseButton(payload["button"]),
            Qt.MouseButton(payload["buttons"]),
            Qt.KeyboardModifier(payload["modifiers"]),
        )
        QApplication.sendEvent(target_widget, ev)
```

---

## Bus Topic Reference

| Topic | Publisher | Subscriber | Payload |
|---|---|---|---|
| `ui.widget.register` | `widget_*` | `ui_shell` | Registration constraints (see above) |
| `ui.widget.update` | `widget_*` | `ui_shell` | `{name, [height], [width], …}` |
| `ui.widget.unregister` | `widget_*` | `ui_shell` | `{name}` |
| `ui.widget.geometry` | `ui_shell` | `widget_*` | `{name, x, y, w, h}` |
| `ui.focus.changed` | `ui_shell` | `widget_*` | `{name}` — frontmost visible widget |
| `ui.shell.ready` | `ui_shell` | all | `{}` |
| `input.raw` | `input_trap` | `ui_shell` | Raw pointer/key event |
| `input.event.<name>` | `ui_shell` | `widget_<name>` | Routed event (see above) |

---

## Boot Sequence

```
Priority 0  config_manager

Priority 1  bluetooth_manager
            tcp_server
            audio_manager

Priority 2  ui_shell          → spawns input_trap co-process
                              → publishes ui.shell.ready
            video_ui          → registers ui.widget.register
            navbar_ui         → registers ui.widget.register
            bt_ui             → registers ui.widget.register (hidden until needed)
            config_ui         → registers ui.widget.register (hidden until needed)
```

Widget processes at priority 2 may start in parallel. Each waits for `ui.shell.ready` before publishing `ui.widget.register`. `ui_shell` responds with `ui.widget.geometry` and the widget calls `show()`.

---

## Module Naming Convention

| Module folder | Role | Priority |
|---|---|---|
| `modules/ui_shell/` | Orchestrator, layout engine, input_trap host | 2 |
| `modules/video_ui/` | GStreamer H.264 decode + GL render | 2 |
| `modules/navbar_ui/` | Navigation bar, always visible | 2 |
| `modules/bt_ui/` | Bluetooth status panel | 2 |
| `modules/config_ui/` | Settings panel | 2 |

All modules follow the standard boot protocol defined in `modules/_template/main.py`.

---

## Key Constraints

- `ui_shell` is the **only process** allowed to call `setGeometry()` on widget windows (via bus messages). Widgets must never reposition themselves autonomously.
- Widget processes must **never** set `WindowStaysOnTopHint` — z-order is managed exclusively by the compositor and `ui_shell` window stacking.
- All touch and keyboard input must flow through `input_trap` → `ui_shell` → `input.event.<name>`. Widgets must not install native event filters for raw input.
- `input_trap` must be recreated (or re-raised) whenever `ui_shell` gains focus, to guarantee it remains at `z=∞`.
