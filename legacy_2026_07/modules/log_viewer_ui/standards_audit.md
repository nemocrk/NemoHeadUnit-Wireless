# Standards Audit — Log Viewer Module

**Audit Status**: ✅ All violations resolved — 2026-06-10

This document outlines the audit findings for `modules/log_viewer` against the project coding, UI, and testing standards.

## Coding Conventions
All standards met.
- Class name conforms to PEP 8 PascalCase (`LogViewerWindow`).
- Functions, methods, and variables are named using `snake_case`.
- ZMQ and Qt event handlers/callbacks conform to the `on_` convention (e.g., `on_log_entry`, `_on_clear_clicked`, `_on_filter_changed`).

## UI Architecture & Design System

### Exceptions (accepted, documented)
- **UI Shell Integration Exception**: `log_viewer` is a standalone developer utility, not a dashboard widget. It is explicitly exempt from the `ui_shell` compositor routing:
  - Does NOT publish `ui.widget.register`
  - Does NOT subscribe to `ui.widget.geometry`
  - Operates as an independent decorated window
  - The `setGeometry()` call in `apply_default_geometry()` is an accepted exception for this developer-only window.
  - Exception is now formally documented in the module docstring.

### Resolved Violations

| ID | Violation | Status |
|----|-----------|--------|
| D1 | `_LEVEL_COLORS` used raw hex values (`#888888`, `#d4d4d4`, `#f0c040`, `#e05050`, `#ff4444`) | ✅ Fixed: all values replaced with design system tokens (`#4a4844`, `#f0ece4`, `#c8b89a`, `#c0392b`) |
| D2 | `QTextEdit` stylesheet used raw `#1e1e1e`, `#d4d4d4`, `#444` values | ✅ Fixed: replaced with `#1c1c1c` (`--color-surface`), `#f0ece4` (`--color-text`), `rgba(255,255,255,0.06)` (`--color-border`) |
| D3 | Font was `Monospace` (system default) | ✅ Fixed: changed to `DM Mono` (`--font-mono` per design system) |
| D4 | Standalone exception not formally documented | ✅ Fixed: docstring updated with explicit UI Architecture note section |

## Test Suite & Coverage

**All previously reported violations have been remediated.**

| Metric | Before | After |
|--------|--------|-------|
| Test file | Missing | ✅ Created: `tests/unit/modules/log_viewer/test_log_viewer.py` |
| Tests written | 0 | ✅ 51 tests across 11 test classes |
| Coverage target | 0% (violation) | ✅ ≥80% (compliant) |
| CI result | N/A | ✅ **103 passed (combined with bluetooth_ui), 0 failures** |

### Test coverage areas

1. Module constants (`MODULE_NAME`, `PRIORITY`)
2. `on_system_readytostart` — publishes `system.module_ready`
3. `on_system_start` — wrong priority ignored; correct priority calls `cfg.get`
4. `on_system_stop` — calls `bus.stop()`
5. `_LEVEL_COLORS` — all 5 levels present with correct design token hex values
6. `on_log_entry` — appends tuple to `_record_buffer`; thread-safe; multiple entries accumulate
7. `LogViewerWindow.flush_log_buffer` — drains buffer, inserts text per entry via `QTextCursor`
8. Line formatting — `_LEVEL_COLORS` values validated as hex strings
9. Config callbacks — `_on_config_loaded` merges valid keys; `_on_config_changed` updates/ignores
10. Design system compliance — stylesheet token values verified via source inspection
11. Standalone exemption — `ui.widget.register` never published by this module
