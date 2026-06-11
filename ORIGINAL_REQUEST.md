# Original User Request

## Initial Request — 2026-06-10T16:12:25Z

Perform a comprehensive codebase-wide audit of all backend and frontend components on the `feature/merge-main-to-root` branch, checking for compliance with the project's coding conventions, architectural rules, UI design system guidelines, and test coverage standards. Report all violations without making automatic modifications to the code. Generate individual violation reports per component next to the components themselves in the repository.

Working directory: /home/nemo/NemoHeadUnit-Wireless
Integrity mode: development

## Requirements

### R1. Coding Convention Audit
Audit all Python source files in the repository to verify they strictly adhere to Python coding conventions, including `snake_case` for classes, functions, and variables, and the `on_<event_name>` convention for event handlers.

### R2. UI Architecture and Design System Audit
Audit all UI components (specifically under `modules/*_ui/`) to verify compliance with `docs/UI_DESIGN_SYSTEM.md` (matte dark colors, warm sand accents, DM Sans font sizes/weights, spacing, lack of dropshadows, single arc menu, ellipse backgrounds) and `docs/UI_ARCHITECTURE.md` (multi-process isolation, coordinate computation via ui_shell, input trap flow, z-order constraints).

### R3. Test Suite and Coverage Audit
Audit all tests in `tests/` to verify compliance with `docs/TEST_SUITE_ARCHITECTURE.md`, specifically ensuring that unit tests do not import sibling modules, hardware tests are correctly parameterized/mocked with skip triggers, and test coverage is at least 80% for every component.

### R4. Component Violation Reporting
Generate a markdown file named `standards_audit.md` inside each audited module or component directory (e.g. `modules/ui_shell/standards_audit.md`, `modules/floating_menu_ui/standards_audit.md`, etc.) listing all detected violations, their locations, and recommendations for bringing them to standard. If a component is fully compliant, the report should state "All standards met."

## Acceptance Criteria

### Audit Completion and Placement
- [ ] A `standards_audit.md` file exists in each module directory under `modules/` (including ui_shell, floating_menu_ui, navbar_ui, bluetooth_ui, config_ui, video_ui, audio_manager, bluetooth_manager, config_manager, etc.) and in `shared/` and `tests/`.

### Report Completeness and Format
- [ ] Each `standards_audit.md` is formatted in Markdown with clear sections for: "Coding Conventions", "UI Architecture & Design System" (if applicable), and "Test Suite & Coverage".
- [ ] Each section in the report either lists specific violating file paths with line numbers and descriptions, or states "All standards met."
- [ ] The report includes concrete recommendations/fixes for any listed violation.

### Verification Accuracy (Agent-as-Judge)
- [ ] An independent auditing agent verifies that the reports do not contain false positives or false negatives (e.g., confirming that color codes listed as violations actually violate `docs/UI_DESIGN_SYSTEM.md`, and that no violations were missed).
