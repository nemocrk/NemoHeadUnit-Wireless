# Standards Audit Report — modules/ui_shell

## Coding Conventions
All standards met.

## UI Architecture & Design System
All standards met.

## Test Suite & Coverage
- **Violating File Path**: `tests/unit/modules/ui_shell/test_ui_shell.py` (Targeting implementation: `modules/ui_shell/main.py`)
  - **Details**: Missing unit test coverage on:
    - Lines 142–205: Layout calculations and geometry update logic (`_clamp`, `_resolve_size`, `_compute_geometry`, `_reflow`).
    - Lines 321–354: Centralized input event mapping and hit testing (`_hit_test`, `on_input_raw`).
    - Lines 470–524: Window painting, event handling, and screen size retrieval in `ShellWindow` and `InputTrap` (such as `resizeEvent`, `paintEvent`, and `mousePressEvent`).
  - **Current Coverage**: 65% (Violates the 80% coverage mandate).
  - **Concrete Recommendations/Fixes**:
    - Expand the unit test suite under `tests/unit/modules/ui_shell/test_ui_shell.py` to instantiate and interact with `ShellWindow` and `InputTrap` headlessly using a testing library such as `pytest-qt`.
    - Implement tests that mock the ZMQ bus client to publish raw input events (`input.raw`) and verify that they are correctly mapped to targeted widgets with coordinate transformations via `on_input_raw`.
    - Directly test layout helper functions (`_clamp`, `_resolve_size`, `_compute_geometry`, `_reflow`) with edge case widget registration data (various docks, aspect ratios, and constraints) to fully cover geometry-calculating paths.
