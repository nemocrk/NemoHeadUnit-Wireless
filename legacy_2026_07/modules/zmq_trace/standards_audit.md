# Standards Audit — ZMQ Trace Module

This document outlines the audit findings for `modules/zmq_trace` against the project coding, UI, and testing standards.

## Coding Conventions
All standards met.
- Class names conform to PEP 8 PascalCase (e.g., `Metrics`).
- Functions, methods, and variables are named using `snake_case`.
- Event handlers adhere to the `on_<event_name>` naming convention.

## UI Architecture & Design System
Not applicable.
- This module is a headless monitoring daemon operating without a graphical user interface.

## Test Suite & Coverage
**VIOLATIONS FOUND**

### Violations
- **File Path**: `modules/zmq_trace/main.py` (Lines 164-215, 218-282, 283-330)
- **Description**: Unit tests defined in `tests/unit/modules/zmq_trace/test_zmq_trace.py` only achieve **59.49%** line coverage, which falls short of the >=80% target.
- **Root Cause**: The unit tests mock/patch the `threading.Thread` class when loading configuration. This prevents the background execution of loop functions `collector_loop` (lines 164-215) and `reporter_loop` (lines 218-282). Additionally, the console report formatting function `print_report` (lines 283-330) is patched out in tests and never executed on actual data. Since the module is not invoked by integration or E2E tests, this coverage gap is not addressed elsewhere.

### Recommendations & Fixes
- Refactor the unit tests in `tests/unit/modules/zmq_trace/test_zmq_trace.py` to directly execute `collector_loop`, `reporter_loop`, and `print_report` with appropriate mocked dependencies:
  - Test `collector_loop` by passing a mocked ZeroMQ `PULL` socket that yields a set of predefined test events, then raises `zmq.Again` to gracefully exit the loop.
  - Test `print_report` directly by supplying a metrics snapshot with mocked/predefined statistics and verifying formatting correctness, avoiding mock patches that entirely bypass the function.
