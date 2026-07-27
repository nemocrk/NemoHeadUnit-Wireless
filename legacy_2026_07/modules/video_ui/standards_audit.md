# Standards Audit Report — modules/video_ui

## Coding Conventions
All standards met.

## UI Architecture & Design System
- **Violating File Path**: `modules/video_ui/main.py`
  - **Details of Violations**:
    - **Line 761 (Visual Surface)**: Class `_VideoWindow` is configured as a standard decorated desktop `QMainWindow` instead of a frameless tool window.
    - **Line 895 (Geometry Control)**: Explicitly manages geometry coordinates autonomously at startup.
    - **Layout Engine Bypass (Architectural)**: Fails to interface with the `ui_shell` central layout manager (`ui.widget.register`) and ignores compositor-driven size reflowing.
    - **Lines 495 & 503 (Typography)**: Instantiates non-standard fonts; the clock label uses `Monospace` and the status label uses `Sans`, violating the DM Sans typography guidelines.
    - **Line 488 (Visual Palette - Background)**: Employs background color `#0d0d0d`, which is darker than the soft slate color palette tokens.
    - **Line 163 (Visual Palette - Accents)**: Uses status color `#e05252` (bright red) for alerts/states, which is not defined in the design system palette.
  - **Concrete Recommendations/Fixes**:
    - Refactor `_VideoWindow` to include `FramelessWindowHint`, `WA_TranslucentBackground`, and `Tool` window configuration attributes.
    - Alter initialization to register the video player as a central docked widget (`ui.widget.register` with `dock="center"`) and subscribe to `ui.widget.geometry` notifications to control window bounds.
    - Replace the font definitions at Lines 495 & 503 with the DM Sans typography standard.
    - Replace background and accent color constants (such as `#0d0d0d` and `#e05252`) with values defined in the Design System color token dictionary.

## Test Suite & Coverage
- **Violating File Path**: `tests/unit/modules/video_ui/test_video_ui.py` (Targeting implementation: `modules/video_ui/main.py`)
  - **Details**: Missing unit test coverage on:
    - Lines 257–328: GStreamer pipeline instantiation and hardware decoder capabilities probing.
    - Lines 366–477: YUV shader compilation, custom OpenGL ES rendering loops, and fallback video layout widgets.
    - Lines 677–726: Raw media sample decoding loops and buffer callback handlers.
  - **Current Coverage**: 42% (Violates the 80% coverage mandate).
  - **Concrete Recommendations/Fixes**:
    - Decouple the GStreamer pipeline controller logic and OpenGL canvas from the Qt window wrapper class.
    - Mock GStreamer binding calls and OpenGL GL state manipulation routines inside tests to run window components headlessly.
    - Implement mock capabilities probes to verify that the video pipeline selects the correct hardware H.264/H.265 decoder block when available.
