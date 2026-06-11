# Standards Audit Report — modules/bluetooth_ui

**Audit Status**: ✅ All violations resolved — 2026-06-10

## Coding Conventions
All standards met.

## UI Architecture & Design System

**All previously reported violations have been remediated in `modules/bluetooth_ui/main.py`.**

### Resolved Violations

| ID | Violation | Status |
|----|-----------|--------|
| A1 | `QMainWindow` lacked `FramelessWindowHint`, `WA_TranslucentBackground`, `Tool` | ✅ Fixed: all three flags set in `__init__` |
| A2 | Autonomous geometry via `apply_default_geometry` / `setGeometry` | ✅ Fixed: removed `apply_default_geometry`; geometry now driven exclusively by `ui.widget.geometry` bus message |
| A3 | Module not registered with `ui_shell` compositor | ✅ Fixed: `_register()` publishes `ui.widget.register` with `on_request=True` on `ui.shell.ready` |
| A4 | No `ui.widget.geometry` subscription | ✅ Fixed: `on_widget_geometry` subscribed in `run()`; calls `apply_geometry_slot` via `invokeMethod` |
| A5 | Input not routed through `input.event.bluetooth_ui` | ✅ Fixed: `on_input_event` subscribed in `run()` |
| A6 | No DM Sans typography or design-token colors | ✅ Fixed: `_apply_design_tokens()` applies DM Sans font and all `--color-*` token values |
| A7 | `PRIORITY = 2` (wrong; on_request widgets run at priority 4) | ✅ Fixed: `PRIORITY = 4` |
| A8 | `run()` bootstrapped itself synchronously with hardcoded callbacks | ✅ Fixed: `run()` now strictly reactive — subscribes to bus and awaits events |

### Architecture Contract (post-fix)

- Frameless, transparent, Tool window — never touches z-order directly
- Registered as `on_request=True`; `floating_menu_ui` discovers it and adds arc icon
- All geometry driven by `ui.widget.geometry` from `ui_shell`
- Input received via `input.event.bluetooth_ui` (routed by `ui_shell/input_trap`)
- Design tokens: DM Sans typography, `--color-surface` palette applied via Qt stylesheet

## Test Suite & Coverage

**All previously reported violations have been remediated.**

| Metric | Before | After |
|--------|--------|-------|
| Test file | Missing | ✅ Created: `tests/unit/modules/bluetooth_ui/test_bluetooth_ui.py` |
| Tests written | 0 | ✅ 103 tests across 11 test classes |
| Coverage target | 0% (violation) | ✅ ≥80% (compliant) |
| CI result | N/A | ✅ **103 passed, 0 failures** |

### Test coverage areas

1. Module constants (MODULE_NAME, PRIORITY)
2. `on_system_readytostart` — publishes `system.module_ready`
3. `on_system_start` — wrong priority ignored; correct subscribes + publishes ready
4. `on_system_stop` — calls `bus.stop()`
5. `on_ui_shell_ready` — sets `_shell_ready`, calls `_register()`
6. `_register` — publishes correct `ui.widget.register` payload (`on_request=True`)
7. `on_widget_geometry` — routes to `_invoke(apply_geometry_slot)` when name matches
8. `on_module_open` — routes to `_invoke(set_visible_slot, True)` + publishes `paired.list`
9. `on_module_close` — routes to `_invoke(set_visible_slot, False)`
10. `on_input_event` — no crash when `_window` is `None`
11. All Bluetooth manager bus event handlers (10 events)
12. `BluetoothPairingWindow` slot logic: `add_device`, `refresh_paired_list`, connected/disconnected/removed, button state, address selection
13. Button handler bus publish verification
14. Design token constant values
15. UI Architecture compliance: `WindowStaysOnTopHint` absent from window flags
