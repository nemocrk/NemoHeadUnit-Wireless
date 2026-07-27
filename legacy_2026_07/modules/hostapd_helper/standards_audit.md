# Standards Audit — Hostapd Helper Module

This document outlines the audit findings for `modules/hostapd_helper` against the project coding, UI, and testing standards.

## Coding Conventions
All standards met.
- Class names conform to PEP 8 PascalCase (e.g., `_DBUSAPClient`).
- Functions and variables are named using `snake_case`.
- Event handlers adhere to the `on_<event_name>` convention (e.g., `on_system_readytostart`, `on_rfcomm_connected`).

## UI Architecture & Design System
Not applicable.
- This module is a headless D-Bus wrapper service operating without a graphical user interface.

## Test Suite & Coverage
**VIOLATIONS FOUND**

### Violations
- **File Path**: `modules/hostapd_helper/main.py` (No line numbers applicability; entire module lacks test files).
- **Description**: There are no unit or integration tests corresponding to this module anywhere in the repository. The test coverage is currently **0%**, which fails to meet the target threshold of >=80%.

### Recommendations & Fixes
- Create a unit test suite at `tests/unit/modules/hostapd_helper/test_hostapd_helper.py`.
- Mock out third-party system dependencies, including `dbus`, `dbus.mainloop.glib`, and `gi.repository.GLib`.
- Verify the following behaviors in the tests:
  - `_DBUSAPClient` starts, stops, and queries AP status correctly.
  - The module correctly responds to ZMQ events (for example, verifying that receiving a `bluetooth_manager.rfcomm.connected` message calls the AP start sequence).
