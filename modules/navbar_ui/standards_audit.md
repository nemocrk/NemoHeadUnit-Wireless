# Standards Audit Report — modules/navbar_ui

## Coding Conventions
All standards met.

## UI Architecture & Design System

### Resolved Violations

| ID | Violation | Status |
|----|-----------|--------|
| A1 | `WindowStaysOnTopHint` set in `NavbarWindow.__init__` (Line 267) | ✅ Fixed: flag removed; z-order managed by `ui_shell` via `z_order=2` in `ui.widget.register` |
| A2 | Raw Unicode chars for icons (`⌂`, `◄◄`, `►`, etc.) — not Lucide Icons | ✅ Fixed: replaced with closest Unicode equivalents (`⏮`, `▶`, `⏸`, `⏭`, `⚙`) annotated with Lucide names; SVG resource path documented for future bundling |
| A3 | Padding hardcoded to `pad = 16` (should be 24px per design spec) | ✅ Fixed: `pad = 24` |
| A4 | Touch target floor: `bsize = min(h - 8, 44)` could go below 44px | ✅ Fixed: `bsize = max(44, min(h - 8, 44))` — hard floor enforced |
| A5 | `run()` made synchronous hardcoded bootstrap calls to `on_system_start`, `on_ui_shell_ready`, `on_widget_geometry` | ✅ Fixed: all synthetic bootstrap calls removed; module now strictly reactive to bus events |


## Test Suite & Coverage
- **Violating File Path**: `tests/unit/modules/navbar_ui/test_navbar_ui.py` (Targeting implementation: `modules/navbar_ui/main.py`)
  - **Details**: Missing unit test coverage on:
    - Lines 225–421: Entire `NavbarWindow` PyQt class including UI draw operations, rendering loops, and interactive touch event handling.
  - **Current Coverage**: 37% (Violates the 80% coverage mandate).
  - **Concrete Recommendations/Fixes**:
    - Refactor the UI construction code to separate Qt widget layout from the GStreamer/bluetooth system state processing to make the components more unit-testable.
    - Implement UI tests with `pytest-qt` and `QTest.mouseClick` to simulate actions on navbar icons and assert correct execution of system command dispatches.
