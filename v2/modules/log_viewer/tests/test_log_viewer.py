"""
Unit tests for:
  - shared.logger.BusLogHandler
  - shared.logger.attach_bus
  - v2/modules/log_viewer bus handlers and LogViewerWindow slots

No real ZMQ broker is required. All bus interactions are mocked.
All Qt widget tests use QApplication (offscreen) without showing any window.
"""

import importlib
import logging
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path setup  (mirrors the pattern used in every v2 module)
# ---------------------------------------------------------------------------

_HERE    = Path(__file__).parent          # v2/modules/log_viewer/tests/
_MODULE  = _HERE.parent                   # v2/modules/log_viewer/
_MODULES = _MODULE.parent                 # v2/modules/
_V2      = _MODULES.parent               # v2/

for _p in (str(_V2), str(_MODULES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Helpers to reset module-level singletons between tests
# ---------------------------------------------------------------------------

def _reset_logger_state():
    """Clear LoggerManager registry and global _bus_handler."""
    import shared.logger as logger_mod
    logger_mod._bus_handler = None
    logger_mod.LoggerManager._loggers.clear()
    # Also remove handlers from underlying logging.Logger instances
    for name in list(logging.Logger.manager.loggerDict.keys()):
        lgr = logging.getLogger(name)
        lgr.handlers = [h for h in lgr.handlers
                        if not isinstance(h, logger_mod.BusLogHandler)]


# ===========================================================================
# TestBusLogHandler
# ===========================================================================

class TestBusLogHandler(unittest.TestCase):
    """Tests for shared.logger.BusLogHandler."""

    def setUp(self):
        _reset_logger_state()
        from shared.logger import BusLogHandler
        self.BusLogHandler = BusLogHandler
        self.mock_bus = MagicMock()

    def _make_record(self, name="test_mod", level=logging.INFO, msg="hello"):
        record = logging.LogRecord(
            name=name, level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        return record

    def test_emit_publishes_log_entry(self):
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record(name="my_module", level=logging.INFO, msg="test msg")
        handler.emit(record)
        self.mock_bus.publish.assert_called_once()
        topic, payload = self.mock_bus.publish.call_args[0]
        self.assertEqual(topic, "log.entry")
        self.assertEqual(payload["module"],  "my_module")
        self.assertEqual(payload["level"],   "INFO")
        self.assertEqual(payload["message"], "test msg")
        self.assertIn("ts", payload)
        self.assertIsInstance(payload["ts"], float)

    def test_emit_level_warning(self):
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record(level=logging.WARNING, msg="watch out")
        handler.emit(record)
        _, payload = self.mock_bus.publish.call_args[0]
        self.assertEqual(payload["level"], "WARNING")

    def test_emit_level_error(self):
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record(level=logging.ERROR, msg="boom")
        handler.emit(record)
        _, payload = self.mock_bus.publish.call_args[0]
        self.assertEqual(payload["level"], "ERROR")

    def test_emit_level_debug(self):
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record(level=logging.DEBUG, msg="dbg")
        handler.emit(record)
        _, payload = self.mock_bus.publish.call_args[0]
        self.assertEqual(payload["level"], "DEBUG")

    def test_emit_ts_is_recent(self):
        before = time.time()
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record()
        handler.emit(record)
        after = time.time()
        _, payload = self.mock_bus.publish.call_args[0]
        self.assertGreaterEqual(payload["ts"], before - 1)
        self.assertLessEqual(payload["ts"], after + 1)

    def test_emit_bus_error_is_silenced(self):
        """A broken bus must never raise from emit()."""
        self.mock_bus.publish.side_effect = RuntimeError("bus dead")
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record()
        try:
            handler.emit(record)  # must not raise
        except Exception as exc:  # noqa: BLE001
            self.fail(f"emit() raised unexpectedly: {exc}")

    def test_emit_zmq_error_is_silenced(self):
        """ZMQ errors must also be silenced."""
        import zmq
        self.mock_bus.publish.side_effect = zmq.ZMQError("socket closed")
        handler = self.BusLogHandler(self.mock_bus)
        record  = self._make_record()
        try:
            handler.emit(record)
        except Exception as exc:
            self.fail(f"emit() raised on ZMQError: {exc}")


# ===========================================================================
# TestAttachBus
# ===========================================================================

class TestAttachBus(unittest.TestCase):
    """Tests for shared.logger.attach_bus()."""

    def setUp(self):
        _reset_logger_state()
        import shared.logger as logger_mod
        self.logger_mod = logger_mod
        self.mock_bus = MagicMock()

    def tearDown(self):
        _reset_logger_state()

    def test_attach_bus_sets_global_handler(self):
        self.assertIsNone(self.logger_mod._bus_handler)
        self.logger_mod.attach_bus(self.mock_bus)
        self.assertIsNotNone(self.logger_mod._bus_handler)
        self.assertIsInstance(self.logger_mod._bus_handler,
                              self.logger_mod.BusLogHandler)

    def test_attach_bus_reaches_existing_logger(self):
        log = self.logger_mod.get_logger("existing_mod")
        self.logger_mod.attach_bus(self.mock_bus)
        bus_handlers = [h for h in log.logger.handlers
                        if isinstance(h, self.logger_mod.BusLogHandler)]
        self.assertEqual(len(bus_handlers), 1)

    def test_attach_bus_reaches_future_logger(self):
        self.logger_mod.attach_bus(self.mock_bus)
        log = self.logger_mod.get_logger("future_mod")
        bus_handlers = [h for h in log.logger.handlers
                        if isinstance(h, self.logger_mod.BusLogHandler)]
        self.assertEqual(len(bus_handlers), 1)

    def test_attach_bus_idempotent_no_duplicate_handlers(self):
        """Calling attach_bus twice must not add two BusLogHandlers."""
        log = self.logger_mod.get_logger("idempotent_mod")
        self.logger_mod.attach_bus(self.mock_bus)
        self.logger_mod.attach_bus(MagicMock())  # second call with different bus
        bus_handlers = [h for h in log.logger.handlers
                        if isinstance(h, self.logger_mod.BusLogHandler)]
        self.assertEqual(len(bus_handlers), 1)

    def test_log_call_triggers_publish(self):
        self.logger_mod.attach_bus(self.mock_bus)
        log = self.logger_mod.get_logger("publish_test")
        log.info("should reach bus")
        self.mock_bus.publish.assert_called()
        topic, payload = self.mock_bus.publish.call_args[0]
        self.assertEqual(topic, "log.entry")
        self.assertIn("should reach bus", payload["message"])

    def test_multiple_loggers_share_same_handler_instance(self):
        self.logger_mod.attach_bus(self.mock_bus)
        log_a = self.logger_mod.get_logger("mod_a")
        log_b = self.logger_mod.get_logger("mod_b")
        handler_a = next(h for h in log_a.logger.handlers
                         if isinstance(h, self.logger_mod.BusLogHandler))
        handler_b = next(h for h in log_b.logger.handlers
                         if isinstance(h, self.logger_mod.BusLogHandler))
        self.assertIs(handler_a, handler_b)


# ===========================================================================
# TestLogViewerBusHandlers  (pure logic — no Qt)
# ===========================================================================

class TestLogViewerBusHandlers(unittest.TestCase):
    """
    Tests for the bus handler functions in log_viewer/main.py.
    Qt is not instantiated; _window is kept None so _invoke is a no-op.
    """

    def setUp(self):
        _reset_logger_state()
        # Provide a stub PyQt6 so the module can be imported without a display
        self._setup_pyqt6_stub()
        import log_viewer.main as lv
        importlib.reload(lv)  # ensure clean state
        self.lv = lv
        self.lv._window = None
        self.mock_bus = MagicMock()
        self.lv.bus = self.mock_bus

    def _setup_pyqt6_stub(self):
        """Inject a minimal PyQt6 stub if real PyQt6 is not available."""
        if "PyQt6" in sys.modules:
            return
        pyqt6 = types.ModuleType("PyQt6")
        for sub in ("QtCore", "QtWidgets", "QtGui"):
            mod = types.ModuleType(f"PyQt6.{sub}")
            for attr in ("Qt", "QMetaObject", "Q_ARG", "pyqtSlot",
                         "QApplication", "QMainWindow", "QWidget",
                         "QVBoxLayout", "QHBoxLayout", "QPushButton",
                         "QLabel", "QComboBox", "QTextEdit", "QStatusBar",
                         "QColor", "QTextCharFormat", "QTextCursor", "QFont"):
                setattr(mod, attr, MagicMock())
            sys.modules[f"PyQt6.{sub}"] = mod
        sys.modules["PyQt6"] = pyqt6

    def test_on_system_readytostart_publishes_module_ready(self):
        self.lv.on_system_readytostart()
        self.mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": "log_viewer", "priority": 2},
        )

    def test_on_system_start_wrong_priority_ignored(self):
        self.lv.on_system_start("system.start", {"priority": 0})
        self.mock_bus.publish.assert_not_called()

    def test_on_system_start_correct_priority_publishes_ready(self):
        self.lv.on_system_start("system.start", {"priority": 2})
        self.mock_bus.publish.assert_called_with(
            "system.ready",
            {"name": "log_viewer", "priority": 2},
        )

    def test_on_system_stop_calls_bus_stop(self):
        self.lv._app = None
        self.lv.on_system_stop("system.stop", {})
        self.mock_bus.stop.assert_called_once()

    def test_on_log_entry_with_window_none_does_not_raise(self):
        self.lv._window = None
        payload = {"module": "bt", "level": "INFO", "message": "ok", "ts": time.time()}
        try:
            self.lv.on_log_entry("log.entry", payload)
        except Exception as exc:
            self.fail(f"on_log_entry raised with _window=None: {exc}")

    def test_on_log_entry_missing_fields_does_not_raise(self):
        self.lv._window = None
        try:
            self.lv.on_log_entry("log.entry", {})
        except Exception as exc:
            self.fail(f"on_log_entry raised with empty payload: {exc}")

    def test_on_log_entry_level_uppercased(self):
        """level in payload may be lowercase; handler must uppercase it."""
        invoked = []
        self.lv._window = MagicMock()

        def fake_invoke(slot, *args):
            invoked.append((slot, args))

        self.lv._invoke = fake_invoke
        payload = {"module": "x", "level": "warning", "message": "w", "ts": time.time()}
        self.lv.on_log_entry("log.entry", payload)
        self.assertEqual(len(invoked), 1)
        _, args = invoked[0]
        self.assertEqual(args[2], "WARNING")


# ===========================================================================
# TestLogViewerWindow  (Qt offscreen)
# ===========================================================================

@unittest.skipUnless(
    importlib.util.find_spec("PyQt6") is not None,
    "PyQt6 not available — skipping Qt widget tests",
)
class TestLogViewerWindow(unittest.TestCase):
    """Tests for LogViewerWindow slots using a real (offscreen) QApplication."""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        _reset_logger_state()
        from log_viewer.main import LogViewerWindow
        self.window = LogViewerWindow()

    def tearDown(self):
        self.window.close()

    def test_initial_log_area_is_empty(self):
        self.assertEqual(self.window._log_area.toPlainText().strip(), "")

    def test_initial_line_count_is_zero(self):
        self.assertEqual(self.window._line_count, 0)

    def test_initial_filter_level_is_all(self):
        self.assertEqual(self.window._filter_level, "ALL")

    def test_append_log_line_increments_count(self):
        self.window.append_log_line("12:00:00.000", "mod", "INFO", "hello")
        self.assertEqual(self.window._line_count, 1)

    def test_append_log_line_text_appears(self):
        self.window.append_log_line("12:00:00.000", "mod", "INFO", "hello world")
        self.assertIn("hello world", self.window._log_area.toPlainText())

    def test_append_multiple_lines(self):
        for i in range(5):
            self.window.append_log_line("12:00:00.000", "mod", "INFO", f"line {i}")
        self.assertEqual(self.window._line_count, 5)

    def test_clear_resets_count(self):
        self.window.append_log_line("12:00:00.000", "mod", "INFO", "x")
        self.window._on_clear_clicked()
        self.assertEqual(self.window._line_count, 0)
        self.assertEqual(self.window._log_area.toPlainText().strip(), "")

    def test_filter_debug_blocks_lower_level(self):
        """With filter=INFO, DEBUG lines must be suppressed."""
        self.window._filter_level = "INFO"
        self.window.append_log_line("12:00:00.000", "mod", "DEBUG", "should be hidden")
        self.assertNotIn("should be hidden", self.window._log_area.toPlainText())
        self.assertEqual(self.window._line_count, 0)

    def test_filter_info_allows_warning(self):
        self.window._filter_level = "INFO"
        self.window.append_log_line("12:00:00.000", "mod", "WARNING", "visible")
        self.assertIn("visible", self.window._log_area.toPlainText())

    def test_filter_error_blocks_warning(self):
        self.window._filter_level = "ERROR"
        self.window.append_log_line("12:00:00.000", "mod", "WARNING", "hidden")
        self.assertNotIn("hidden", self.window._log_area.toPlainText())

    def test_filter_all_allows_debug(self):
        self.window._filter_level = "ALL"
        self.window.append_log_line("12:00:00.000", "mod", "DEBUG", "debug visible")
        self.assertIn("debug visible", self.window._log_area.toPlainText())

    def test_max_lines_enforced(self):
        """After exceeding max_lines the count should not grow beyond the cap."""
        import shared.logger as lm  # noqa: F401 — ensure path is valid
        import log_viewer.main as lv
        lv._config["max_lines"] = 5
        for i in range(10):
            self.window.append_log_line("12:00:00.000", "mod", "INFO", f"line {i}")
        self.assertLessEqual(self.window._line_count, 5)
        lv._config["max_lines"] = 500  # restore default

    def test_set_status_updates_status_bar(self):
        self.window.set_status("test status message")
        self.assertEqual(
            self.window._status.currentMessage(), "test status message"
        )

    def test_on_filter_changed_updates_filter(self):
        self.window._on_filter_changed("ERROR")
        self.assertEqual(self.window._filter_level, "ERROR")


if __name__ == "__main__":
    unittest.main()
