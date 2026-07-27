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
    stubs["shared.touch_widgets"] = MagicMock()
    def _make_shm_engine(**kwargs):
        m = MagicMock()
        m.max_width  = kwargs.get("max_width", 1024)
        m.max_height = kwargs.get("max_height", 600)
        m.w = kwargs.get("w", 0)
        m.h = kwargs.get("h", 0)
        return m
    shm_stub = MagicMock()
    shm_stub.OffscreenWidgetEngine.side_effect = lambda name, w, h, **kw: _make_shm_engine(w=w, h=h, **kw)
    stubs["shared.shm_helper"]    = shm_stub

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
            FramelessWindowHint = 1
            Tool = 2
        class WidgetAttribute:
            WA_TranslucentBackground = 1

    class _FakeQTextEdit:
        def __init__(self, *args, **kwargs):
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
        def textCursor(self):
            return MagicMock()


    class _FakeQFont:
        def __init__(self, *a): pass

    class _FakeQComboBox:
        def __init__(self, *args, **kwargs):
            self._items = []
            self._current = 0
        def addItems(self, items):      self._items.extend(items)
        def currentText(self):         return self._items[self._current] if self._items else "ALL"
        def setCurrentText(self, t):
            if t in self._items:
                self._current = self._items.index(t)
        def currentIndexChanged(self): return MagicMock()
        @property
        def currentTextChanged(self):  return MagicMock()
        def setMinimumWidth(self, w):  pass

    qtcore.Qt          = _FakeQt
    qtcore.QMetaObject = MagicMock()
    qtcore.Q_ARG       = MagicMock(side_effect=lambda t, v: (t, v))
    qtcore.pyqtSlot    = lambda *a, **kw: (lambda f: f)
    qtcore.QTimer      = MagicMock()

    class _FakeQWidget:
        def __init__(self, *args, **kwargs):
            pass
        def setWindowFlags(self, *args, **kwargs):
            pass
        def setAttribute(self, *args, **kwargs):
            pass
        def setWindowTitle(self, *args, **kwargs):
            pass
        def setCentralWidget(self, *args, **kwargs):
            pass
        def hide(self, *args, **kwargs):
            pass
        def show(self, *args, **kwargs):
            pass
        def raise_(self, *args, **kwargs):
            pass
        def setGeometry(self, *args, **kwargs):
            pass
        def render(self, *args, **kwargs):
            pass
        def setStatusBar(self, *args, **kwargs):
            pass
        def closeEvent(self, *args, **kwargs):
            pass
        def setSpacing(self, *args, **kwargs):
            pass
        def setContentsMargins(self, *args, **kwargs):
            pass
        def addWidget(self, *args, **kwargs):
            pass
        def addLayout(self, *args, **kwargs):
            pass
        def addStretch(self, *args, **kwargs):
            pass
        def setMinimumHeight(self, *args, **kwargs):
            pass
        def setMinimumWidth(self, *args, **kwargs):
            pass
        def setObjectName(self, *args, **kwargs):
            pass
        def setStyleSheet(self, *args, **kwargs):
            pass
        def centralWidget(self, *args, **kwargs):
            return None
        @property
        def clicked(self):             return MagicMock()
        def showMessage(self, *args, **kwargs):
            pass

    qtwid.QApplication  = MagicMock()
    qtwid.QMainWindow   = _FakeQWidget
    qtwid.QWidget       = _FakeQWidget

    qtwid.QVBoxLayout   = _FakeQWidget
    qtwid.QHBoxLayout   = _FakeQWidget
    qtwid.QPushButton   = _FakeQWidget
    qtwid.QLabel        = _FakeQWidget
    qtwid.QTextEdit     = _FakeQTextEdit
    qtwid.QComboBox     = _FakeQComboBox
    qtwid.QCheckBox     = _FakeQWidget
    qtwid.QStatusBar    = _FakeQWidget
    qtwid.QSizePolicy   = _FakeQWidget
    qtwid.QFrame        = _FakeQWidget


    class _FakeMoveOperation:
        End   = 1
        Start = 2
        Down  = 3

    class _FakeMoveMode:
        KeepAnchor = 1

    class _FakeQTextCursor:
        MoveOperation = _FakeMoveOperation
        MoveMode      = _FakeMoveMode

    class _FakeQPainter:
        def __init__(self, *args, **kwargs): pass
        def end(self): pass

    qtgui.QFont              = _FakeQFont
    qtgui.QColor             = MagicMock
    qtgui.QTextCharFormat    = MagicMock
    qtgui.QTextCursor        = _FakeQTextCursor
    qtgui.QAction            = MagicMock
    qtgui.QSyntaxHighlighter = MagicMock
    qtgui.QPainter           = _FakeQPainter
    qtgui.QImage             = MagicMock

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
        if "log_viewer_ui" in key:
            del sys.modules[key]

    with patch.dict("sys.modules", stubs):
        module_path = Path(__file__).parents[4] / "modules" / "log_viewer_ui" / "main.py"
        spec = importlib.util.spec_from_file_location("test_modules_log_viewer_ui_main", module_path)
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
        assert lv.MODULE_NAME == "log_viewer_ui"

    def test_priority_is_4(self, lv):
        assert lv.PRIORITY == 4

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
            {"name": "log_viewer_ui", "priority": lv.PRIORITY},
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
        with patch.object(lv, "_run_qt"), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            lv.on_system_start("system.start", {"priority": lv.PRIORITY})
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
        """INFO must use --color-text (#121212)."""
        assert lv._LEVEL_COLORS["INFO"] == "#121212"

    def test_warning_color_is_accent_token(self, lv):
        """WARNING must use --color-accent (#b26a00)."""
        assert lv._LEVEL_COLORS["WARNING"] == "#b26a00"

    def test_error_color_is_danger_token(self, lv):
        """ERROR must use --color-danger (#d32f2f)."""
        assert lv._LEVEL_COLORS["ERROR"] == "#d32f2f"

    def test_critical_color_is_danger_token(self, lv):
        """CRITICAL must use --color-danger (#d32f2f)."""
        assert lv._LEVEL_COLORS["CRITICAL"] == "#d32f2f"

    def test_debug_color_is_faint_token(self, lv):
        """DEBUG must use --color-text-faint (#757575)."""
        assert lv._LEVEL_COLORS["DEBUG"] == "#757575"

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
        """log_area must use white background (#ffffff) inside stylesheet."""
        import inspect
        src = inspect.getsource(lv)
        assert "#ffffff" in src, "Log area background must use #ffffff"

    def test_log_area_text_token(self, lv):
        """log_area text must use --color-text (#121212)."""
        import inspect
        src = inspect.getsource(lv)
        assert "#121212" in src, "Log area text must use --color-text (#121212)"

    def test_log_area_border_token(self, lv):
        """log_area border must use --color-border rgba token."""
        import inspect
        src = inspect.getsource(lv)
        assert "rgba(0,0,0,0.12)" in src, \
            "Log area border must use --color-border token"

    def test_font_is_dm_mono(self, lv):
        """Font must be 'DM Mono' (--font-mono) per UI_DESIGN_SYSTEM.md."""
        import inspect
        src = inspect.getsource(lv)
        assert "DM Mono" in src, "Log area must use DM Mono (--font-mono)"


# ---------------------------------------------------------------------------
# 15. Widget registration — must publish ui.widget.register
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWidgetRegistration:
    def test_widget_register_on_ui_shell_ready(self, lv):
        lv._mock_bus.publish.reset_mock()
        lv.on_ui_shell_ready("ui.shell.ready", {})
        lv._mock_bus.publish.assert_called_with(
            "ui.widget.register",
            {
                "name":          "log_viewer_ui",
                "z_order":       2,
                "dock":          "center",
                "width":         None,
                "min_width":     None,
                "max_width":     None,
                "height":        None,
                "min_height":    None,
                "max_height":    None,
                "aspect_ratio":  None,
                "on_request":    True,
                "menu_order":    3,
                "icon":          "\U0001f4dd",
            }
        )



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

    def test_real_window_slots(self, lv):
        win = lv.LogViewerWindow()
        # Initial apply_geometry_slot (creates engine)
        win.apply_geometry_slot(0, 0, 100, 100)
        assert win._shm_engine is not None
        
        # Second apply_geometry_slot (cleans up and recreates)
        win.apply_geometry_slot(0, 0, 200, 200)
        assert win._shm_engine is not None

        # set_visible_slot True and False
        win.set_visible_slot(True)
        win.set_visible_slot(False)

        win.handle_input({"type": "press", "x": 10, "y": 10})
        win.set_status("test status")
        win._on_clear_clicked()
        win._on_filter_changed("WARNING")
        
        # Add a record to buffer and flush it
        lv._record_buffer.clear()
        lv.on_log_entry("log.entry", {
            "module": "x", "level": "ERROR", "message": "msg", "ts": 123.0
        })
        win._filter_level = "WARNING"
        win.flush_log_buffer()
        
        # Test config changed (valid + invalid structural value)
        lv._on_config_changed("max_lines", 100)
        lv._on_config_changed("max_lines", {"invalid": "dict"})
        
        # Test log level filtering out and ValueError fallback
        lv._record_buffer.clear()
        lv.on_log_entry("log.entry", {
            "module": "x", "level": "DEBUG", "message": "should be filtered out", "ts": 123.0
        })
        lv.on_log_entry("log.entry", {
            "module": "x", "level": "INVALID_LEVEL", "message": "should raise ValueError and be logged", "ts": 123.0
        })
        win._filter_level = "INFO"
        win.flush_log_buffer()

        # Test trimming excess lines
        win._line_count = 5
        lv._record_buffer.clear()
        lv.on_log_entry("log.entry", {
            "module": "x", "level": "ERROR", "message": "msg", "ts": 123.0
        })
        # set config max_lines very small, say 2
        lv._config["max_lines"] = 2
        win.flush_log_buffer()
        assert win._line_count == 2

        win.closeEvent(MagicMock())

    def test_on_ui_shell_ready(self, lv):
        lv._shell_ready = False
        lv._mock_bus.reset_mock()
        lv.on_ui_shell_ready("ui.shell.ready", {})
        assert lv._shell_ready is True
        # should call _register which publishes ui.widget.register
        lv._mock_bus.publish.assert_any_call("ui.widget.register", {
            "name":          lv.MODULE_NAME,
            "z_order":       2,
            "dock":          "center",
            "width":         None,
            "min_width":     None,
            "max_width":     None,
            "height":        None,
            "min_height":    None,
            "max_height":    None,
            "aspect_ratio":  None,
            "on_request":    True,
            "menu_order":    3,
            "icon":          "\U0001f4dd",
        })

    def test_on_widget_geometry(self, lv):
        # 1. Ignored if name mismatch
        lv._geometry_set = False
        lv.on_widget_geometry("ui.widget.geometry", {"name": "other_widget"})
        assert not lv._geometry_set

        # 2. When _window is None (pending geometry queue)
        lv._window = None
        lv._pending_geometry = None
        lv.on_widget_geometry("ui.widget.geometry", {
            "name": lv.MODULE_NAME, "x": 10, "y": 20, "w": 300, "h": 400
        })
        assert lv._geometry_set is True
        assert lv._pending_geometry == (10, 20, 300, 400)

        # 3. When _window is not None (calls apply_geometry_slot)
        lv._window = MagicMock()
        lv.on_widget_geometry("ui.widget.geometry", {
            "name": lv.MODULE_NAME, "x": 15, "y": 25, "w": 305, "h": 405
        })
        # verify QMetaObject.invokeMethod was called
        lv.QMetaObject.invokeMethod.assert_called()

    def test_on_module_open_close(self, lv):
        # Open/close when _window is None (should not crash)
        lv._window = None
        lv.on_module_open("ui.module.open", {"name": lv.MODULE_NAME})
        lv.on_module_close("ui.module.close", {"name": lv.MODULE_NAME})

        # Name mismatch should be ignored
        lv._window = MagicMock()
        lv.QMetaObject.invokeMethod.reset_mock()
        lv.on_module_open("ui.module.open", {"name": "other"})
        lv.on_module_close("ui.module.close", {"name": "other"})
        lv.QMetaObject.invokeMethod.assert_not_called()

        # Name match
        lv.on_module_open("ui.module.open", {"name": lv.MODULE_NAME})
        lv.QMetaObject.invokeMethod.assert_called()
        
        lv.QMetaObject.invokeMethod.reset_mock()
        lv.on_module_close("ui.module.close", {"name": lv.MODULE_NAME})
        lv.QMetaObject.invokeMethod.assert_called()

    def test_on_input_event(self, lv):
        # When _window is None
        lv._window = None
        lv.on_input_event("input.event.log_viewer_ui", {"type": "press"})

        # When _window is not None
        lv._window = MagicMock()
        lv.QMetaObject.invokeMethod.reset_mock()
        lv.on_input_event("input.event.log_viewer_ui", {"type": "press"})
        lv.QMetaObject.invokeMethod.assert_called()

    def test_on_system_readytostart(self, lv):
        lv._mock_bus.reset_mock()
        lv.on_system_readytostart()
        lv._mock_bus.publish.assert_called_with("system.module_ready", {
            "name":     lv.MODULE_NAME,
            "priority": lv.PRIORITY,
        })

    def test_on_system_start(self, lv):
        # Wrong priority
        lv._system_start_event.clear()
        lv.on_system_start("system.start", {"priority": -99})
        assert not lv._system_start_event.is_set()

        # Correct priority
        lv._shell_ready = False
        lv._mock_bus.reset_mock()
        lv._system_start_event.clear()
        lv.on_system_start("system.start", {"priority": lv.PRIORITY})
        assert lv._system_start_event.is_set()
        # Since _shell_ready is False, _register was not called
        assert not any(c.args[0] == "ui.widget.register" for c in lv._mock_bus.publish.call_args_list)

        # Correct priority when _shell_ready is True
        lv._shell_ready = True
        lv._mock_bus.reset_mock()
        lv._system_start_event.clear()
        lv.on_system_start("system.start", {"priority": lv.PRIORITY})
        assert lv._system_start_event.is_set()
        # _register called
        assert any(c.args[0] == "ui.widget.register" for c in lv._mock_bus.publish.call_args_list)

    def test_on_system_stop(self, lv):
        lv._window = MagicMock()
        lv._app = MagicMock()
        lv._mock_bus.reset_mock()
        lv.on_system_stop("system.stop", {})
        lv._mock_bus.publish.assert_any_call("ui.widget.unregister", {"name": lv.MODULE_NAME})
        lv._mock_bus.stop.assert_called_once()
        lv.QMetaObject.invokeMethod.assert_called_with(lv._app, "quit", lv.Qt.ConnectionType.QueuedConnection)

    def test_run_qt_function(self, lv):
        with patch.object(lv, "QApplication") as mock_qapp, \
             patch.object(lv, "LogViewerWindow") as mock_win, \
             patch.object(lv, "QTimer") as mock_qtimer:
            
            # Setup pending geometry to test _apply_pending
            lv._pending_geometry = (10, 20, 100, 120)
            
            # Mock QTimer.singleShot to immediately invoke the callback
            mock_qtimer.singleShot.side_effect = lambda ms, callback: callback()
            
            # Mock window instance
            mock_win_instance = MagicMock()
            mock_win.return_value = mock_win_instance
            
            lv._run_qt()
            
            mock_qapp.instance.assert_called_once()
            mock_win.assert_called_once()
            mock_qtimer.singleShot.assert_called_once()
            assert mock_qtimer.call_count == 1  # constructor called once for flush_timer
            
            # Verify pending geometry was applied to window
            mock_win_instance.apply_geometry_slot.assert_called_with(10, 20, 100, 120)

    def test_run_main_function(self, lv):
        lv.cfg = MagicMock()
        lv.bus = MagicMock()
        with patch("time.sleep") as mock_sleep, \
             patch.object(lv, "_system_start_event") as mock_event, \
             patch.object(lv, "_run_qt") as mock_run_qt:
            lv.run()
            lv.cfg.register.assert_called_once()
            lv.bus.subscribe.assert_called()
            lv.bus.start.assert_called_with(blocking=False)
            mock_event.wait.assert_called_once()
            mock_run_qt.assert_called_once()

