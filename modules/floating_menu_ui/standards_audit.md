# Standards Audit Report — modules/floating_menu_ui

## Coding Conventions
All standards met.

## UI Architecture & Design System

### Resolved Violations

| ID | Violation | Status |
|----|-----------|--------|
| A1 | `WindowStaysOnTopHint` set in `ArcMenuWindow.__init__` (Line 423) — bypassed `ui_shell` z-order management | ✅ Fixed: flag removed; z_order now declared via `z_order=3` in `ui.widget.register` payload and managed exclusively by `ui_shell` |

Z-order management now fully delegated to `ui_shell` per `UI_ARCHITECTURE.md` §Compositor.

## Test Suite & Coverage
- **Violating File Path**: `tests/unit/modules/floating_menu_ui/test_floating_menu_ui.py` (Targeting implementation: `modules/floating_menu_ui/main.py`)
  - **Details**: Missing unit test coverage on:
    - Lines 395–599: Entire PyQt GUI class `ArcMenuWindow` including custom paint events, layout math, and circular arc rendering.
    - Lines 638–660 and 669–692: Lifecycle execution functions (`run()`, event loops, system start/stop handlers).
  - **Current Coverage**: 39% (Violates the 80% coverage mandate).
  - **Concrete Recommendations/Fixes**:
    - Introduce `pytest-qt` testing helpers to instantiate `ArcMenuWindow` headlessly.
    - Implement tests using a mock QPainter or assert geometry outputs to verify coordinate calculations for rendering the arc menu items.
    - Mock the ZMQ bus callbacks for `system.start` and `system.stop` to exercise startup and cleanup sequences, verifying standard PyQt application setup and shutdown thread safety.
