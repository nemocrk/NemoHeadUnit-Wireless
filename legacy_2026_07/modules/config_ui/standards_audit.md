# Standards Audit Report — modules/config_ui

## Coding Conventions
All standards met.

## UI Architecture & Design System
- **Violating File Path**: `modules/config_ui/main.py`
  - **Details of Violations**:
    - **Line 88 (Visual Surface)**: `ConfigWindow` is built as an opaque desktop application window complete with OS window decorations, omitting required transparent, frameless flags.
    - **Line 101 (Geometry Control)**: Explicitly positions itself using self-determined layouts via `self.setGeometry(...)` rather than acting as a layout receiver from `ui_shell`.
    - **Layout Engine Bypass (Architectural)**: Fails to notify `ui_shell` of its presence via `ui.widget.register` or respond to layout reflows on `ui.widget.geometry`.
    - **Centralized Input Bypass (Architectural)**: Bypasses the central compositor input event routing pipeline.
    - **Integration Flow Lifecycle**: Spawns a standard GUI window on application startup rather than registering as an on-demand overlay. This violates `docs/UI_ARCHITECTURE.md` which specifies that `config_ui` should register as an `on_request` widget.
  - **Concrete Recommendations/Fixes**:
    - Refactor `ConfigWindow` to apply `FramelessWindowHint`, `WA_TranslucentBackground`, and `Tool` window attributes.
    - Alter the boot behavior so that it registers as an `on_request` component on the `ui.widget.register` topic.
    - Remove the hardcoded startup window instantiation logic. Ensure the window is drawn and positioned only in response to `ui.widget.geometry` layout messages sent by the compositor.
    - Connect input handlers to the routed `input.event.config_ui` bus messages.

## Test Suite & Coverage
- **Violating File Path**: `tests/unit/modules/config_ui/`
  - **Details**: No unit tests exist for the configuration user interface module.
  - **Current Coverage**: 0% (Violates the 80% coverage mandate).
  - **Concrete Recommendations/Fixes**:
    - Build a unit test suite under `tests/unit/modules/config_ui/`.
    - Test the dynamic schema-driven widget generators (`_FieldWidget`, `_OneofWidget`, etc.) using sample schemas to ensure forms render fields with matching data-type validation logic.
    - Write UI tests that simulate modification of configuration values via forms and verify that appropriate ZMQ configurations updates are published.
