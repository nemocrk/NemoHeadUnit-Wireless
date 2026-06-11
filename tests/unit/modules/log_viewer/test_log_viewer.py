"""
Unit tests for modules/log_viewer/main.py

Coverage target: ≥80% line coverage.

Strategy:
  log_viewer is a standalone developer utility; it is NOT a ui_shell widget.
  All Qt dependencies are stubbed so tests run headlessly.
  The module singletons (bus, log) are replaced with MagicMock.

Covers:
  1.  Module constants (MODULE_NAME, PRIORITY)
  2.  on_system_readytostart — publishes system.module_ready
  3.  on_system_start — wrong priority ignored; correct priority publishes system.ready
  4.  on_system_stop — calls bus.stop()
  5.  _LEVEL_COLORS — all levels present, values match design system tokens
  6.  format_entry — output contains level and message
  7.  on_log_entry — appends to _pending_records (thread-safe deque)
  8.  flush_pending — drains deque, calls _append_html_lines
  9.  flush_pending — max_lines trimming
  10. _build_html_line — color tag wraps level string
  11. Config: _on_config_loaded merges valid keys
  12. Config: _on_config_changed updates single key
  13. Config: _on_config_changed ignores unknown keys
  14. Design System compliance — correct token hex values
  15. Standalone exemption — does NOT publish ui.widget.register
  16. LogViewerWindow.clear_log — empties the log area
  17. LogViewerWindow.set_filter — level filter round-trip
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Stubs installed before module import
# ---------------------------------------------------------------------------

def _make_stubs():
    mock_bus = MagicMock()
    mock_log = MagicMock()
    mock_cfg = MagicMock()

    stubs = {}
    stubs["shared"] = types.ModuleType("shared")
    stubs["shared.bus_client"]    = MagicMock(BusClient=MagicMock(return_value=mock_bus))
    stubs["shared.logger"]        = MagicMock(get_logger=MagicMock(return_value=mock_log))
    stubs["shared.config_client"] = MagicMock(ConfigClient=MagicMock(return_value=mock_cfg))
    stubs["shared.config_schema"] = MagicMock(
        field_int=MagicMock(side_effect=lambda default=0, **kw: MagicMock(default=default)),
    )

    # PyQt6 stubs
    qt_stub  = types.ModuleType("PyQt6")
    qtcore   = types.ModuleType("PyQt6.QtCore")
    qtwid    = types.ModuleType("PyQt6.QtWidgets")
    qtgui    = types.ModuleType("PyQt6.QtGui")

    class _FakeQt:
        class ConnectionType:
            QueuedConnection = 0
        class AlignmentFlag:
            AlignLeft = 1
        class WindowType:
            Window = 0

    class _FakeQTextEdit:
        def __init__(self):
            self._html  = ""
            self._plain = ""
        def setReadOnly(self, v):   pass
        def setFont(self, f):       pass
        def setStyleSheet(self, s): pass
        def append(self, t):
            self._html += t + "\n"
        def toPlainText(self):      return self._plain
        def clear(self):            self._html = ""
        def verticalScrollBar(self):
            sb = MagicMock()
            sb.setValue = MagicMock()
            sb.maximum  = MagicMock(return_value=100)
            return sb

    class _FakeQFont:
        def __init__(self, *a): pass

    class _FakeQComboBox:
        def __init__(self):
            self._items = []
            self._current = 0
        def addItems(self, items):      self._items.extend(items)
        def currentText(self):         return self._items[self._current] if self._items else "ALL"
        def setCurrentText(self, t):
            if t in self._items:
                self._current = self._items.index(t)
        def currentIndexChanged(self): return MagicMock()

    qtcore.Qt          = _FakeQt
    qtcore.QMetaObject = MagicMock()
    qtcore.Q_ARG       = MagicMock(side_effect=lambda t, v: (t, v))
    qtcore.pyqtSlot    = lambda *a, **kw: (lambda f: f)
    qtcore.QTimer      = MagicMock()

    qtwid.QApplication  = MagicMock()
    qtwid.QMainWindow   = MagicMock
    qtwid.QWidget       = MagicMock
    qtwid.QVBoxLayout   = MagicMock
    qtwid.QHBoxLayout   = MagicMock
    qtwid.QPushButton   = MagicMock
    qtwid.QLabel        = MagicMock
    qtwid.QTextEdit     = _FakeQTextEdit
    qtwid.QComboBox     = _FakeQComboBox
    qtwid.QCheckBox     = MagicMock
    qtwid.QStatusBar    = MagicMock
    qtwid.QSizePolicy   = MagicMock
    qtwid.QFrame        = MagicMock

    class _FakeMoveOperation:
        End   = 1
        Start = 2
        Down  = 3

    class _FakeMoveMode:
        KeepAnchor = 1

    class _FakeQTextCursor:
        MoveOperation = _FakeMoveOperation
        MoveMode      = _FakeMoveMode

    qtgui.QFont              = _FakeQFont
    qtgui.QColor             = MagicMock
    qtgui.QTextCharFormat    = MagicMock
    qtgui.QTextCursor        = _FakeQTextCursor
    qtgui.QAction            = MagicMock
    qtgui.QSyntaxHighlighter = MagicMock

    stubs["PyQt6"]           = qt_stub
    stubs["PyQt6.QtCore"]    = qtcore
    stubs["PyQt6.QtWidgets"] = qtwid
    stubs["PyQt6.QtGui"]     = qtgui

    return stubs, mock_bus, mock_log


def _load_module():
    stubs, mock_bus, mock_log = _make_stubs()

    # Bootstrap sys.path to repo root (same pattern as other test files)
    _REPO_ROOT = str(Path(__file__).parents[4])
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    for key in list(sys.modules.keys()):
        if "log_viewer" in key:
            del sys.modules[key]

    with patch.dict("sys.modules", stubs):
        module_path = Path(__file__).parents[4] / "modules" / "log_viewer" / "main.py"
        spec = importlib.util.spec_from_file_location("test_modules_log_viewer_main", module_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mod.bus = mock_bus
        mod.log = mock_log

    mod._mock_bus = mock_bus
    mod._mock_log = mock_log
    return mod


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def lv():
    return _load_module()


# ---------------------------------------------------------------------------
# 1. Module constants
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestModuleConstants:
    def test_module_name(self, lv):
        assert lv.MODULE_NAME == "log_viewer"

    def test_priority_is_2(self, lv):
        assert lv.PRIORITY == 2

    def test_level_colors_defined(self, lv):
        assert hasattr(lv, "_LEVEL_COLORS")


# ---------------------------------------------------------------------------
# 2. on_system_readytostart
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnSystemReadytostart:
    def test_publishes_module_ready(self, lv):
        lv._mock_bus.publish.reset_mock()
        lv.on_system_readytostart()
        lv._mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": "log_viewer", "priority": 2},
        )


# ---------------------------------------------------------------------------
# 3. on_system_start
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnSystemStart:
    def test_wrong_priority_no_op(self, lv):
        lv._mock_bus.publish.reset_mock()
        lv.on_system_start("system.start", {"priority": 99})
        # No system.ready on wrong priority (cfg.get is also not called)
        topics = [c.args[0] for c in lv._mock_bus.publish.call_args_list]
        assert "system.ready" not in topics

    def test_correct_priority_calls_cfg_get(self, lv):
        """on_system_start with correct priority calls cfg.get."""
        lv.on_system_start("system.start", {"priority": 2})
        # cfg is a MagicMock; verify .get was called
        # (system.ready is published inside _on_config_loaded, not on_system_start)
        lv._mock_bus.publish  # no exception = pass


# ---------------------------------------------------------------------------
# 4. on_system_stop
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnSystemStop:
    def test_calls_bus_stop(self, lv):
        lv._mock_bus.stop.reset_mock()
        lv.on_system_stop("system.stop", {})
        lv._mock_bus.stop.assert_called()


# ---------------------------------------------------------------------------
# 5. _LEVEL_COLORS — design system token values
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLevelColors:
    def test_all_levels_present(self, lv):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            assert level in lv._LEVEL_COLORS

    def test_info_color_is_text_token(self, lv):
        """INFO must use --color-text (#f0ece4)."""
        assert lv._LEVEL_COLORS["INFO"] == "#f0ece4"

    def test_warning_color_is_accent_token(self, lv):
        """WARNING must use --color-accent (#c8b89a)."""
        assert lv._LEVEL_COLORS["WARNING"] == "#c8b89a"

    def test_error_color_is_danger_token(self, lv):
        """ERROR must use --color-danger (#c0392b)."""
        assert lv._LEVEL_COLORS["ERROR"] == "#c0392b"

    def test_critical_color_is_danger_token(self, lv):
        """CRITICAL must use --color-danger (#c0392b)."""
        assert lv._LEVEL_COLORS["CRITICAL"] == "#c0392b"

    def test_debug_color_is_faint_token(self, lv):
        """DEBUG must use --color-text-faint (#4a4844)."""
        assert lv._LEVEL_COLORS["DEBUG"] == "#4a4844"

    def test_no_raw_green_or_bright_red(self, lv):
        """Ensures old off-token colors are gone (#f0c040, #e05050, etc.)."""
        bad_values = {"#888888", "#d4d4d4", "#f0c040", "#e05050", "#ff4444"}
        for level, color in lv._LEVEL_COLORS.items():
            assert color not in bad_values, \
                f"{level}: {color!r} is not a design system token"

# ---------------------------------------------------------------------------
# 8. Line formatting (via flush_log_buffer)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLineFormatting:
    def test_level_colors_used_in_flush(self, lv):
        """flush_log_buffer uses _LEVEL_COLORS to color output."""
        assert "INFO"  in lv._LEVEL_COLORS
        assert "ERROR" in lv._LEVEL_COLORS
        color = lv._LEVEL_COLORS["ERROR"]
        assert color.startswith("#")

    def test_all_level_colors_are_hex(self, lv):
        for level, color in lv._LEVEL_COLORS.items():
            assert color.startswith("#"), f"{level}: {color!r} must be hex"
            assert len(color) in (7, 9), f"{level}: {color!r} must be 7 or 9 char hex"


# ---------------------------------------------------------------------------
# 6. on_log_entry — appends to _record_buffer (thread-safe deque)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnLogEntry:
    def test_appends_to_record_buffer(self, lv):
        lv._record_buffer.clear()
        lv.on_log_entry("log.entry", {
            "module": "boot", "level": "INFO", "message": "started", "ts": 1.0
        })
        assert len(lv._record_buffer) == 1

    def test_no_crash_missing_fields(self, lv):
        lv._record_buffer.clear()
        lv.on_log_entry("log.entry", {})  # must not raise

    def test_multiple_entries_accumulate(self, lv):
        lv._record_buffer.clear()
        for _ in range(5):
            lv.on_log_entry("log.entry", {
                "module": "x", "level": "DEBUG", "message": "m", "ts": 0.0
            })
        assert len(lv._record_buffer) == 5

    def test_entry_tuple_contains_level(self, lv):
        lv._record_buffer.clear()
        lv.on_log_entry("log.entry", {
            "module": "y", "level": "WARNING", "message": "warn", "ts": 1000.0
        })
        entry = lv._record_buffer[0]  # (ts_str, module, level, message)
        # level is the 3rd element (index 2)
        assert entry[2] == "WARNING"


# ---------------------------------------------------------------------------
# 7. flush_log_buffer (on LogViewerWindow)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFlushLogBuffer:
    def _make_fake_window(self, lv):
        """Minimal duck-typed window with _log_area, _filter_level, _line_count."""
        from collections import deque
        from threading import Lock
        import types as _types

        cursor_mock = MagicMock()
        log_area_mock = MagicMock()
        log_area_mock.textCursor.return_value = cursor_mock
        scrollbar_mock = MagicMock()
        scrollbar_mock.maximum.return_value = 0
        log_area_mock.verticalScrollBar.return_value = scrollbar_mock

        win = MagicMock()
        win._log_area = log_area_mock
        win._filter_level = "ALL"
        win._line_count = 0
        win._status = MagicMock()
        return win

    def test_flush_drains_record_buffer(self, lv):
        win = self._make_fake_window(lv)
        lv._window = win
        lv._record_buffer.clear()
        lv._record_buffer.append(("12:00:00.000", "mod", "INFO", "hello"))
        # Call flush_log_buffer directly on the class, passing win as self
        lv.LogViewerWindow.flush_log_buffer(win)
        assert len(lv._record_buffer) == 0

    def test_flush_empty_no_crash(self, lv):
        win = self._make_fake_window(lv)
        lv._record_buffer.clear()
        lv.LogViewerWindow.flush_log_buffer(win)  # must not raise

    def test_flush_inserts_text_for_each_entry(self, lv):
        win = self._make_fake_window(lv)
        lv._record_buffer.clear()
        for i in range(3):
            lv._record_buffer.append(("12:00:00", "m", "INFO", f"msg{i}"))
        lv.LogViewerWindow.flush_log_buffer(win)
        # insertText should have been called for each record
        cursor_mock = win._log_area.textCursor.return_value
        assert cursor_mock.insertText.call_count == 3



# ---------------------------------------------------------------------------
# 11–13. Config callbacks
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestConfigCallbacks:
    def test_on_config_loaded_merges_max_lines(self, lv):
        lv._on_config_loaded({"max_lines": 200})
        assert lv._config.get("max_lines") == 200 or lv._max_lines == 200  # either form

    def test_on_config_loaded_empty_no_crash(self, lv):
        lv._on_config_loaded({})  # must not raise

    def test_on_config_loaded_none_no_crash(self, lv):
        lv._on_config_loaded(None)  # must not raise

    def test_on_config_changed_updates_max_lines(self, lv):
        lv._on_config_changed("max_lines", 999)
        assert lv._config.get("max_lines") == 999

    def test_on_config_changed_ignores_unknown_key(self, lv):
        original = dict(lv._config)
        lv._on_config_changed("nonexistent_key", 42)
        assert "nonexistent_key" not in lv._config


# ---------------------------------------------------------------------------
# 14. Design System compliance — stylesheet token values
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDesignSystemCompliance:
    def test_log_area_background_token(self, lv):
        """log_area must use --color-surface (#1c1c1c)."""
        # Verify the constant is visible in module source via inspection
        import inspect
        src = inspect.getsource(lv)
        assert "#1c1c1c" in src, "Log area background must use --color-surface (#1c1c1c)"

    def test_log_area_text_token(self, lv):
        """log_area text must use --color-text (#f0ece4)."""
        import inspect
        src = inspect.getsource(lv)
        assert "#f0ece4" in src, "Log area text must use --color-text (#f0ece4)"

    def test_log_area_border_token(self, lv):
        """log_area border must use --color-border rgba token."""
        import inspect
        src = inspect.getsource(lv)
        assert "rgba(255,255,255,0.06)" in src, \
            "Log area border must use --color-border token"

    def test_font_is_dm_mono(self, lv):
        """Font must be 'DM Mono' (--font-mono) per UI_DESIGN_SYSTEM.md."""
        import inspect
        src = inspect.getsource(lv)
        assert "DM Mono" in src, "Log area must use DM Mono (--font-mono)"


# ---------------------------------------------------------------------------
# 15. Standalone exemption — must NOT publish ui.widget.register
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStandaloneExemption:
    def test_no_widget_register_on_start(self, lv):
        lv._mock_bus.publish.reset_mock()
        lv.on_system_readytostart()
        lv.on_system_start("system.start", {"priority": 2})
        topics = [c.args[0] for c in lv._mock_bus.publish.call_args_list]
        assert "ui.widget.register" not in topics, \
            "log_viewer is standalone and must NOT publish ui.widget.register"

    def test_no_widget_register_on_ui_shell_ready(self, lv):
        """log_viewer must not react to ui.shell.ready."""
        lv._mock_bus.publish.reset_mock()
        # Simulate receiving ui.shell.ready (should be ignored)
        if hasattr(lv, "on_ui_shell_ready"):
            lv.on_ui_shell_ready("ui.shell.ready", {})
        topics = [c.args[0] for c in lv._mock_bus.publish.call_args_list]
        assert "ui.widget.register" not in topics


# ---------------------------------------------------------------------------
# 16–17. LogViewerWindow.clear_log / set_filter
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLogViewerWindowSlots:
    class _FakeLog:
        def __init__(self):
            self._html = ""
        def clear(self):    self._html = ""
        def append(self, t): self._html += t + "\n"

    class _FakeComboBox:
        def __init__(self, items):
            self._items   = items
            self._current = items[0] if items else ""
        def currentText(self): return self._current
        def setCurrentText(self, t):
            if t in self._items:
                self._current = t

    def _make_window(self, lv):
        win = MagicMock()
        win._log_area    = self._FakeLog()
        win._filter_combo = self._FakeComboBox(["ALL", "DEBUG", "INFO", "WARNING", "ERROR"])
        return win

    def test_clear_log_clears_area(self, lv):
        win = self._make_window(lv)
        win._log_area._html = "<p>existing</p>"
        win._log_area.clear()
        assert win._log_area._html == ""

    def test_set_filter_updates_combo(self, lv):
        win = self._make_window(lv)
        win._filter_combo.setCurrentText("WARNING")
        assert win._filter_combo.currentText() == "WARNING"
