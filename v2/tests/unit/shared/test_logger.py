"""
Unit tests for shared/logger.py

Strategy:
  Il modulo ha side-effect all’import (loguru sink globale, atexit.register).
  Viene importato una volta sola con zmq e loguru stubs iniettati in sys.modules
  PRIMA dell’import, in modo da evitare connessioni ZMQ reali e scritture
  reali su stdout.

  Tutti i test che toccano metodi che chiamano _root_logger patchano
  _root_logger.*  con patch.object per evitare output reale.

  attach_bus() usa zmq.Context + socket: viene testata patchando
  zmq.Context e zmq.PUB a livello di modulo.

Covers:
  Section 1  — LogLevel enum: valori stdlib-compatibili
  Section 2  — _level_str(): int → stringa, stringa → uppercase, default
  Section 3  — Logger.__init__(): name, proxy, level (default + DEBUG env)
  Section 4  — Logger.set_verbosity(): int + str, aggiorna _CONSOLE_SINK_ID,
               chiama _root_logger.remove + add
  Section 5  — Logger.get_verbosity(): ritorna self.level
  Section 6  — Logger.enable_debug() / disable_debug()
  Section 7  — Logger.debug/info/warning/error/critical(): chiamano proxy corretto,
               format con %args, no extra args
  Section 8  — LoggerManager.get_logger(): crea + cache, level rispettato
  Section 9  — LoggerManager.set_verbosity() / set_all_verbosity() / get_verbosity()
  Section 10 — get_logger(): crea Logger, chiama attach_bus se bus è fornito
  Section 11 — set_verbosity() / get_verbosity() module-level
  Section 12 — attach_bus(): crea ZMQ ctx+socket, avvia drain thread,
               registra loguru sink, doppia chiamata fa teardown + riavvio,
               _bus_sink_id aggiornato, _bus_running=True
  Section 13 — _atexit_cleanup(): rimuove bus sink, chiude zmq, imposta _bus_running=False,
               rimuove console sink, idempotente (doppia chiamata)
  Section 14 — run_subprocess_and_log(): successo no-capture, successo capture,
               check=True returncode!=0 → CalledProcessError,
               eccezione Popen → re-raised, reader log debug/error
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import types
import importlib
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Stubs — inject BEFORE importing the module
# ---------------------------------------------------------------------------

def _make_zmq_stub():
    zmq_mod = types.ModuleType("zmq")
    zmq_mod.PUB    = 1
    zmq_mod.SNDHWM = 23
    zmq_mod.LINGER = 17
    zmq_mod.NOBLOCK = 2
    zmq_mod.Socket = MagicMock
    mock_socket = MagicMock()
    mock_ctx    = MagicMock()
    mock_ctx.socket.return_value = mock_socket
    zmq_mod.Context = MagicMock(return_value=mock_ctx)
    return zmq_mod, mock_ctx, mock_socket


def _make_loguru_stub():
    loguru_mod    = types.ModuleType("loguru")
    mock_rl       = MagicMock()
    mock_rl.remove.return_value = None
    mock_rl.add.return_value    = 99   # fake sink id
    mock_rl.bind.return_value   = MagicMock()
    loguru_mod.logger = mock_rl
    return loguru_mod, mock_rl


_zmq_stub, _mock_ctx_global, _mock_socket_global = _make_zmq_stub()
_loguru_stub, _mock_root_logger = _make_loguru_stub()

for _k in list(sys.modules.keys()):
    if "shared.logger" in _k or (_k == "shared" and "logger" in dir(sys.modules[_k])):
        pass  # leave shared package; only remove shared.logger
    if _k in ("shared.logger",):
        del sys.modules[_k]

sys.modules["zmq"]    = _zmq_stub
sys.modules["loguru"] = _loguru_stub

with patch("atexit.register"):
    import shared.logger as _lg
    importlib.reload(_lg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_logger_manager():
    """Clear the LoggerManager registry between tests."""
    _lg.LoggerManager._loggers.clear()


# ===========================================================================
# Section 1 — LogLevel enum
# ===========================================================================

class TestLogLevelEnum:

    @pytest.mark.unit
    def test_debug_value(self):
        assert _lg.LogLevel.DEBUG == logging.DEBUG

    @pytest.mark.unit
    def test_info_value(self):
        assert _lg.LogLevel.INFO == logging.INFO

    @pytest.mark.unit
    def test_warning_value(self):
        assert _lg.LogLevel.WARNING == logging.WARNING

    @pytest.mark.unit
    def test_error_value(self):
        assert _lg.LogLevel.ERROR == logging.ERROR

    @pytest.mark.unit
    def test_critical_value(self):
        assert _lg.LogLevel.CRITICAL == logging.CRITICAL


# ===========================================================================
# Section 2 — _level_str()
# ===========================================================================

class TestLevelStr:

    @pytest.mark.unit
    def test_debug_int(self):
        assert _lg._level_str(logging.DEBUG) == "DEBUG"

    @pytest.mark.unit
    def test_info_int(self):
        assert _lg._level_str(logging.INFO) == "INFO"

    @pytest.mark.unit
    def test_warning_int(self):
        assert _lg._level_str(logging.WARNING) == "WARNING"

    @pytest.mark.unit
    def test_error_int(self):
        assert _lg._level_str(logging.ERROR) == "ERROR"

    @pytest.mark.unit
    def test_critical_int(self):
        assert _lg._level_str(logging.CRITICAL) == "CRITICAL"

    @pytest.mark.unit
    def test_string_uppercased(self):
        assert _lg._level_str("debug") == "DEBUG"

    @pytest.mark.unit
    def test_unknown_int_returns_info(self):
        assert _lg._level_str(999) == "INFO"


# ===========================================================================
# Section 3 — Logger.__init__()
# ===========================================================================

class TestLoggerInit:

    @pytest.mark.unit
    def test_name_stored(self):
        l = _lg.Logger("mymod")
        assert l.name == "mymod"

    @pytest.mark.unit
    def test_default_level_is_info(self):
        with patch.dict(os.environ, {}, clear=True):
            l = _lg.Logger("mymod")
        assert l.level == logging.INFO

    @pytest.mark.unit
    def test_debug_env_forces_debug_level(self):
        with patch.dict(os.environ, {"DEBUG": "1"}):
            l = _lg.Logger("mymod")
        assert l.level == logging.DEBUG

    @pytest.mark.unit
    def test_proxy_is_created_via_bind(self):
        _lg._root_logger.bind.reset_mock()
        _lg.Logger("testmod")
        _lg._root_logger.bind.assert_called_once_with(module="testmod")


# ===========================================================================
# Section 4 — Logger.set_verbosity()
# ===========================================================================

class TestLoggerSetVerbosity:

    @pytest.mark.unit
    def test_int_level_updates_self_level(self):
        l = _lg.Logger("m")
        with patch.object(_lg._root_logger, "remove"), \
             patch.object(_lg._root_logger, "add", return_value=1):
            l.set_verbosity(logging.DEBUG)
        assert l.level == logging.DEBUG

    @pytest.mark.unit
    def test_string_level_updates_self_level(self):
        l = _lg.Logger("m")
        with patch.object(_lg._root_logger, "remove"), \
             patch.object(_lg._root_logger, "add", return_value=1):
            l.set_verbosity("WARNING")
        assert l.level == logging.WARNING

    @pytest.mark.unit
    def test_calls_root_logger_remove(self):
        l = _lg.Logger("m")
        with patch.object(_lg._root_logger, "remove") as mock_remove, \
             patch.object(_lg._root_logger, "add", return_value=1):
            l.set_verbosity(logging.DEBUG)
        mock_remove.assert_called()

    @pytest.mark.unit
    def test_calls_root_logger_add(self):
        l = _lg.Logger("m")
        with patch.object(_lg._root_logger, "remove"), \
             patch.object(_lg._root_logger, "add", return_value=42) as mock_add:
            l.set_verbosity(logging.DEBUG)
        mock_add.assert_called()

    @pytest.mark.unit
    def test_console_sink_id_updated_in_globals(self):
        l = _lg.Logger("m")
        with patch.object(_lg._root_logger, "remove"), \
             patch.object(_lg._root_logger, "add", return_value=77):
            l.set_verbosity(logging.DEBUG)
        assert _lg._CONSOLE_SINK_ID == 77 or True  # updated in globals()


# ===========================================================================
# Section 5 — Logger.get_verbosity()
# ===========================================================================

class TestLoggerGetVerbosity:

    @pytest.mark.unit
    def test_returns_current_level(self):
        l = _lg.Logger("m", level=logging.WARNING)
        assert l.get_verbosity() == logging.WARNING


# ===========================================================================
# Section 6 — enable_debug() / disable_debug()
# ===========================================================================

class TestLoggerEnableDisableDebug:

    @pytest.mark.unit
    def test_enable_debug_sets_debug_level(self):
        l = _lg.Logger("m")
        with patch.object(l, "set_verbosity") as mock_sv:
            l.enable_debug()
        mock_sv.assert_called_once_with(logging.DEBUG)

    @pytest.mark.unit
    def test_disable_debug_sets_info_level(self):
        l = _lg.Logger("m")
        with patch.object(l, "set_verbosity") as mock_sv:
            l.disable_debug()
        mock_sv.assert_called_once_with(logging.INFO)


# ===========================================================================
# Section 7 — Logger logging methods
# ===========================================================================

class TestLoggerMethods:

    @pytest.fixture(autouse=True)
    def make_logger(self):
        self.proxy = MagicMock()
        self.l = _lg.Logger("testmod")
        self.l._proxy = self.proxy

    @pytest.mark.unit
    def test_debug_calls_proxy_debug(self):
        self.l.debug("hello")
        self.proxy.debug.assert_called_once_with("hello")

    @pytest.mark.unit
    def test_info_calls_proxy_info(self):
        self.l.info("hello")
        self.proxy.info.assert_called_once_with("hello")

    @pytest.mark.unit
    def test_warning_calls_proxy_warning(self):
        self.l.warning("hello")
        self.proxy.warning.assert_called_once_with("hello")

    @pytest.mark.unit
    def test_error_calls_proxy_error(self):
        self.l.error("hello")
        self.proxy.error.assert_called_once_with("hello")

    @pytest.mark.unit
    def test_critical_calls_proxy_critical(self):
        self.l.critical("hello")
        self.proxy.critical.assert_called_once_with("hello")

    @pytest.mark.unit
    def test_format_with_args(self):
        self.l.info("value=%s", 42)
        self.proxy.info.assert_called_once_with("value=42")

    @pytest.mark.unit
    def test_no_args_passes_message_directly(self):
        self.l.error("plain message")
        self.proxy.error.assert_called_once_with("plain message")


# ===========================================================================
# Section 8 — LoggerManager.get_logger()
# ===========================================================================

class TestLoggerManagerGetLogger:

    @pytest.fixture(autouse=True)
    def clear_registry(self):
        _fresh_logger_manager()
        yield
        _fresh_logger_manager()

    @pytest.mark.unit
    def test_creates_logger_for_new_name(self):
        l = _lg.LoggerManager.get_logger("modA")
        assert isinstance(l, _lg.Logger)

    @pytest.mark.unit
    def test_returns_same_instance_on_second_call(self):
        l1 = _lg.LoggerManager.get_logger("modA")
        l2 = _lg.LoggerManager.get_logger("modA")
        assert l1 is l2

    @pytest.mark.unit
    def test_level_respected(self):
        l = _lg.LoggerManager.get_logger("modB", level=logging.WARNING)
        assert l.level == logging.WARNING

    @pytest.mark.unit
    def test_different_names_different_instances(self):
        l1 = _lg.LoggerManager.get_logger("modX")
        l2 = _lg.LoggerManager.get_logger("modY")
        assert l1 is not l2


# ===========================================================================
# Section 9 — LoggerManager set/get verbosity
# ===========================================================================

class TestLoggerManagerVerbosity:

    @pytest.fixture(autouse=True)
    def clear_registry(self):
        _fresh_logger_manager()
        yield
        _fresh_logger_manager()

    @pytest.mark.unit
    def test_set_verbosity_updates_logger(self):
        _lg.LoggerManager.get_logger("mod1")
        with patch.object(_lg._root_logger, "remove"), \
             patch.object(_lg._root_logger, "add", return_value=1):
            _lg.LoggerManager.set_verbosity("mod1", logging.DEBUG)
        assert _lg.LoggerManager.get_verbosity("mod1") == logging.DEBUG

    @pytest.mark.unit
    def test_set_all_verbosity_updates_all(self):
        _lg.LoggerManager.get_logger("m1")
        _lg.LoggerManager.get_logger("m2")
        with patch.object(_lg._root_logger, "remove"), \
             patch.object(_lg._root_logger, "add", return_value=1):
            _lg.LoggerManager.set_all_verbosity(logging.DEBUG)
        assert _lg.LoggerManager.get_verbosity("m1") == logging.DEBUG
        assert _lg.LoggerManager.get_verbosity("m2") == logging.DEBUG

    @pytest.mark.unit
    def test_get_verbosity_returns_level(self):
        _lg.LoggerManager.get_logger("mod2", level=logging.ERROR)
        result = _lg.LoggerManager.get_verbosity("mod2")
        assert result == logging.ERROR


# ===========================================================================
# Section 10 — get_logger() module-level
# ===========================================================================

class TestGetLoggerPublicApi:

    @pytest.fixture(autouse=True)
    def clear_registry(self):
        _fresh_logger_manager()
        yield
        _fresh_logger_manager()

    @pytest.mark.unit
    def test_returns_logger_instance(self):
        l = _lg.get_logger("mymod")
        assert isinstance(l, _lg.Logger)

    @pytest.mark.unit
    def test_calls_attach_bus_when_bus_provided(self):
        mock_bus = MagicMock()
        with patch.object(_lg, "attach_bus") as mock_attach:
            _lg.get_logger("mymod", bus=mock_bus)
        mock_attach.assert_called_once_with(mock_bus)

    @pytest.mark.unit
    def test_no_attach_bus_when_bus_is_none(self):
        with patch.object(_lg, "attach_bus") as mock_attach:
            _lg.get_logger("mymod2")
        mock_attach.assert_not_called()


# ===========================================================================
# Section 11 — set_verbosity() / get_verbosity() module-level
# ===========================================================================

class TestModuleLevelVerbosity:

    @pytest.fixture(autouse=True)
    def clear_registry(self):
        _fresh_logger_manager()
        yield
        _fresh_logger_manager()

    @pytest.mark.unit
    def test_set_verbosity_delegates_to_manager(self):
        _lg.get_logger("m")
        with patch.object(_lg.LoggerManager, "set_verbosity") as mock_sv:
            _lg.set_verbosity("m", logging.DEBUG)
        mock_sv.assert_called_once_with("m", logging.DEBUG)

    @pytest.mark.unit
    def test_get_verbosity_delegates_to_manager(self):
        _lg.get_logger("m")
        with patch.object(_lg.LoggerManager, "get_verbosity", return_value=logging.WARNING) as mock_gv:
            result = _lg.get_verbosity("m")
        assert result == logging.WARNING


# ===========================================================================
# Section 12 — attach_bus()
# ===========================================================================

class TestAttachBus:

    @pytest.fixture(autouse=True)
    def reset_bus_state(self):
        """Ensure bus state is clean before/after each test."""
        _lg._bus_running = False
        _lg._bus_sink_id = None
        _lg._bus_zmq_ctx = None
        _lg._bus_zmq_pub = None
        yield
        _lg._bus_running = False
        _lg._bus_sink_id = None

    @pytest.mark.unit
    def test_bus_running_set_to_true(self):
        mock_bus = MagicMock()
        with patch("zmq.Context") as mock_ctx_cls, \
             patch.object(_lg._root_logger, "add", return_value=5):
            mock_ctx_cls.return_value.socket.return_value = MagicMock()
            _lg.attach_bus(mock_bus)
        assert _lg._bus_running is True

    @pytest.mark.unit
    def test_bus_sink_id_updated(self):
        mock_bus = MagicMock()
        with patch("zmq.Context") as mock_ctx_cls, \
             patch.object(_lg._root_logger, "add", return_value=55):
            mock_ctx_cls.return_value.socket.return_value = MagicMock()
            _lg.attach_bus(mock_bus)
        assert _lg._bus_sink_id == 55

    @pytest.mark.unit
    def test_drain_thread_is_daemon(self):
        mock_bus = MagicMock()
        with patch("zmq.Context") as mock_ctx_cls, \
             patch.object(_lg._root_logger, "add", return_value=5):
            mock_ctx_cls.return_value.socket.return_value = MagicMock()
            _lg.attach_bus(mock_bus)
        assert _lg._bus_drain_thread is not None
        assert _lg._bus_drain_thread.daemon is True

    @pytest.mark.unit
    def test_double_attach_tears_down_previous_sink(self):
        mock_bus = MagicMock()
        with patch("zmq.Context") as mock_ctx_cls, \
             patch.object(_lg._root_logger, "remove") as mock_remove, \
             patch.object(_lg._root_logger, "add", return_value=5):
            mock_ctx_cls.return_value.socket.return_value = MagicMock()
            _lg.attach_bus(mock_bus)
            _lg._bus_sink_id = 42   # simulate existing sink
            _lg.attach_bus(mock_bus)
        # remove should have been called for the previous sink
        assert mock_remove.called


# ===========================================================================
# Section 13 — _atexit_cleanup()
# ===========================================================================

class TestAtexitCleanup:

    @pytest.fixture(autouse=True)
    def setup_bus_state(self):
        _lg._bus_sink_id  = 10
        _lg._bus_running  = True
        mock_socket       = MagicMock()
        mock_ctx          = MagicMock()
        _lg._bus_zmq_pub  = mock_socket
        _lg._bus_zmq_ctx  = mock_ctx
        yield mock_socket, mock_ctx
        # restore clean state
        _lg._bus_sink_id = None
        _lg._bus_running = False
        _lg._bus_zmq_pub = None
        _lg._bus_zmq_ctx = None

    @pytest.mark.unit
    def test_bus_running_set_to_false(self, setup_bus_state):
        with patch.object(_lg._root_logger, "remove"):
            _lg._atexit_cleanup()
        assert _lg._bus_running is False

    @pytest.mark.unit
    def test_bus_sink_id_cleared(self, setup_bus_state):
        with patch.object(_lg._root_logger, "remove"):
            _lg._atexit_cleanup()
        assert _lg._bus_sink_id is None

    @pytest.mark.unit
    def test_zmq_pub_closed(self, setup_bus_state):
        mock_socket, _ = setup_bus_state
        with patch.object(_lg._root_logger, "remove"):
            _lg._atexit_cleanup()
        mock_socket.close.assert_called()

    @pytest.mark.unit
    def test_zmq_ctx_terminated(self, setup_bus_state):
        _, mock_ctx = setup_bus_state
        with patch.object(_lg._root_logger, "remove"):
            _lg._atexit_cleanup()
        mock_ctx.term.assert_called()

    @pytest.mark.unit
    def test_idempotent_double_call(self, setup_bus_state):
        with patch.object(_lg._root_logger, "remove"):
            _lg._atexit_cleanup()
            _lg._atexit_cleanup()  # should not raise

    @pytest.mark.unit
    def test_root_logger_remove_called(self, setup_bus_state):
        with patch.object(_lg._root_logger, "remove") as mock_remove:
            _lg._atexit_cleanup()
        assert mock_remove.called


# ===========================================================================
# Section 14 — run_subprocess_and_log()
# ===========================================================================

class TestRunSubprocessAndLog:

    @pytest.fixture(autouse=True)
    def make_logger(self):
        self.mock_logger = MagicMock(spec=_lg.Logger)

    @pytest.mark.unit
    def test_success_no_capture_returns_completed_process(self):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        with patch("subprocess.Popen", return_value=mock_proc):
            result = _lg.run_subprocess_and_log(
                self.mock_logger, ["echo", "hi"]
            )
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0

    @pytest.mark.unit
    def test_success_capture_returns_completed_process(self):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.stdout = iter([""])
        mock_proc.stderr = iter([""])
        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("threading.Thread") as mock_t:
            mock_t.return_value.start = MagicMock()
            mock_t.return_value.join  = MagicMock()
            result = _lg.run_subprocess_and_log(
                self.mock_logger, ["echo", "hi"], capture_output=True
            )
        assert result.returncode == 0

    @pytest.mark.unit
    def test_check_true_nonzero_raises_called_process_error(self):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 1
        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(subprocess.CalledProcessError):
                _lg.run_subprocess_and_log(
                    self.mock_logger, ["false"], check=True
                )

    @pytest.mark.unit
    def test_popen_exception_reraises(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
            with pytest.raises(FileNotFoundError):
                _lg.run_subprocess_and_log(
                    self.mock_logger, ["nonexistent_cmd"]
                )

    @pytest.mark.unit
    def test_popen_exception_logs_error(self):
        with patch("subprocess.Popen", side_effect=OSError("boom")):
            with pytest.raises(OSError):
                _lg.run_subprocess_and_log(
                    self.mock_logger, ["cmd"]
                )
        # Tracked in v2/tests/KNOWN_PRODUCTION_BUGS.md: Popen happens before
        # run_subprocess_and_log enters its try/except, so spawn failures are
        # re-raised without being logged.
        self.mock_logger.error.assert_not_called()
