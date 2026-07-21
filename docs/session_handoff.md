# Session Handoff — NemoHeadUnit-Wireless

> **Purpose**: Continuity document for subsequent AI agent sessions.  
> **Last Updated**: 2026-06-11 — V2 promotion completed, documentation suite refreshed.

---

## Current Status Summary

The V2 multi-process architecture is promoted to the repository root. Legacy V1 code (`app/`) and obsolete document files have been physically removed. A comprehensive, refreshed English documentation suite has been written, covering system architecture, design patterns, and functional targets.

---

## Architectural Constants & Contracts

### 1. Boot Priority Convention
To prevent race conditions during startup, modules follow a reactive priority order:
- **Priority 0**: `config_manager` (loads configuration database)
- **Priority 1**: `bluetooth_manager`, `audio_manager`, `tcp_server` (system background services)
- **Priority 2**: `ui_shell` (starts screen layout engine and global input trap)
- **Priority 3**: `floating_menu_ui` (listens for widget registration announcements)
- **Priority 4**: `navbar_ui`, `video_ui`, `bluetooth_ui`, `config_ui` (individual visual widgets)

### 2. Widget Registration Contract
To dock inside the `ui_shell` compositor layout, widget modules publish to `ui.widget.register` upon receiving `ui.shell.ready`:

#### Always-Visible Widgets (e.g., navbar, video)
```json
{
  "name": "navbar_ui",
  "z_order": 2,
  "dock": "bottom",
  "height": 60,
  "min_height": 48,
  "max_height": 80
}
```

#### On-Demand Widgets (e.g., bluetooth_ui, config_ui)
```json
{
  "name": "config_ui",
  "z_order": 2,
  "dock": "center",
  "width": 400,
  "height": 500,
  "on_request": true,
  "menu_order": 1,
  "icon": "⚙️"
}
```

### 3. Bootstrap Path Pattern
Each module process boosts its import path to resolve imports from the repository root:
```python
from pathlib import Path
import sys

_HERE = Path(__file__).parent
_MODULES = _HERE.parent
_REPO_ROOT = _MODULES.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

---

## Essential Commands

### Running Unit Tests & Coverage
```bash
# Run tests for specific modules
pytest tests/unit/modules/floating_menu_ui/ -v --cov=modules/floating_menu_ui --cov-report=term-missing
pytest tests/unit/modules/navbar_ui/ -v --cov=modules/navbar_ui --cov-report=term-missing
pytest tests/unit/modules/ui_shell/ -v --cov=modules/ui_shell --cov-report=term-missing

# Run entire test suite (excluding services)
pytest --cov=. --cov-report=html --cov-report=term-missing --ignore=services
```

---

## Active Development Guidelines

- **No Monoliths**: All features must live inside isolated, standalone module processes.
- **Pure ZMQ IPC**: Never cross process boundaries using imports or direct object references. Always use the `BusClient` wrapper.
- **Thread Safety**: When implementing PyQt6 interfaces, always dispatch ZMQ callback events to the Qt main thread via Qt custom signals (`pyqtSignal`).
- **No V1 Paths**: Ensure that all scripts, deb packaging specs, and deployment logs point to root paths rather than obsolete `/v2/` prefixes.
