"""
test_bus_broker.py — Unit tests for bus_broker.run().

Coverage sections:
  1. Module-level constants
  2. run() — socket creation and binding
  3. run() — proxy thread is started as daemon
  4. run() — SIGINT/SIGTERM handlers registered
  5. run() — clean shutdown: sockets closed, context terminated, thread joined
  6. run() — _shutdown sets stop_event
"""

from __future__ import annotations

import signal
import sys
import threading
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Stubs so bus_broker.py imports without real zmq / shared packages
# ---------------------------------------------------------------------------

def _make_zmq_stub():
    zmq_mod = types.ModuleType("zmq")
    zmq_mod.XSUB    = 10
    zmq_mod.XPUB    = 11
    zmq_mod.RCVHWM  = 1
    zmq_mod.SNDHWM  = 2
    zmq_mod.LINGER  = 3
    zmq_mod.ZMQError = type("ZMQError", (Exception,), {})

    fake_socket = MagicMock()
    fake_context = MagicMock()
    fake_context.socket.return_value = fake_socket
    zmq_mod.Context = MagicMock(return_value=fake_context)
    zmq_mod.proxy   = MagicMock()

    return zmq_mod, fake_context, fake_socket


@pytest.fixture()
def broker_module():
    """Import bus_broker with all external deps mocked."""
    import importlib

    zmq_stub, fake_ctx, fake_socket = _make_zmq_stub()
    mock_log = MagicMock()

    with patch.dict("sys.modules", {
        "zmq": zmq_stub,
        "shared.logger": types.SimpleNamespace(get_logger=MagicMock(return_value=mock_log)),
    }):
        if "bus_broker" in sys.modules:
            del sys.modules["bus_broker"]
        import bus_broker as mod
        importlib.reload(mod)
        # Re-point zmq in the reloaded module
        mod.zmq = zmq_stub
        yield mod, fake_ctx, fake_socket, mock_log


def _run_with_immediate_stop(mod, fake_ctx, fake_socket, signal_to_fire=signal.SIGINT):
    """
    Call mod.run() but make stop_event fire immediately after sockets are
    set up, by patching threading.Event to auto-set.
    """
    original_event = threading.Event

    class _AutoEvent(original_event):
        """Returns True on the first wait() call (simulates stop signal)."""
        def wait(self, timeout=None):
            return True  # immediate

    with patch("threading.Event", _AutoEvent), \
         patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        mod.run()

    return mock_thread


# ============================================================================
# 1. Module-level constants
# ============================================================================

@pytest.mark.unit
class TestConstants:

    def test_broker_pub_addr(self, broker_module):
        mod, *_ = broker_module
        assert mod.BROKER_PUB_ADDR == "ipc:///tmp/nemobus_v2.pub"

    def test_broker_sub_addr(self, broker_module):
        mod, *_ = broker_module
        assert mod.BROKER_SUB_ADDR == "ipc:///tmp/nemobus_v2.sub"

    def test_hwm_is_positive(self, broker_module):
        mod, *_ = broker_module
        assert mod.HWM > 0

    def test_hwm_at_least_5000(self, broker_module):
        """HWM must be >= BUS_HWM in shared/bus_client.py."""
        mod, *_ = broker_module
        assert mod.HWM >= 5000


# ============================================================================
# 2. run() — socket creation and binding
# ============================================================================

@pytest.mark.unit
class TestSocketSetup:

    def test_context_created(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            mod.zmq.Context.reset_mock()
            mod.run()
        mod.zmq.Context.assert_called_once()

    def test_two_sockets_created(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            fake_ctx.socket.reset_mock()
            mod.run()
        assert fake_ctx.socket.call_count == 2

    def test_xsub_bound_to_pub_addr(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            fake_socket.reset_mock()
            mod.run()
        bind_calls = [str(c) for c in fake_socket.bind.call_args_list]
        assert any(mod.BROKER_PUB_ADDR in c for c in bind_calls)

    def test_xpub_bound_to_sub_addr(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            fake_socket.reset_mock()
            mod.run()
        bind_calls = [str(c) for c in fake_socket.bind.call_args_list]
        assert any(mod.BROKER_SUB_ADDR in c for c in bind_calls)


# ============================================================================
# 3. run() — proxy thread started as daemon
# ============================================================================

@pytest.mark.unit
class TestProxyThread:

    def test_thread_started(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            mock_thread = MagicMock()
            thr_cls.return_value = mock_thread
            mod.run()
        mock_thread.start.assert_called_once()

    def test_thread_is_daemon(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            mod.run()
        _, kwargs = thr_cls.call_args
        assert kwargs.get("daemon") is True


# ============================================================================
# 4. run() — signal handlers registered
# ============================================================================

@pytest.mark.unit
class TestSignalHandlers:

    def _run_and_capture_signals(self, mod):
        registered = {}
        original_signal = signal.signal

        def _capture(sig, handler):
            registered[sig] = handler

        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls, \
             patch("signal.signal", side_effect=_capture):
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            mod.run()
        return registered

    def test_sigint_registered(self, broker_module):
        mod, *_ = broker_module
        registered = self._run_and_capture_signals(mod)
        assert signal.SIGINT in registered

    def test_sigterm_registered(self, broker_module):
        mod, *_ = broker_module
        registered = self._run_and_capture_signals(mod)
        assert signal.SIGTERM in registered

    def test_sigint_and_sigterm_same_handler(self, broker_module):
        mod, *_ = broker_module
        registered = self._run_and_capture_signals(mod)
        assert registered[signal.SIGINT] is registered[signal.SIGTERM]


# ============================================================================
# 5. run() — clean shutdown: sockets closed, context terminated
# ============================================================================

@pytest.mark.unit
class TestCleanShutdown:

    def test_xsub_closed_on_stop(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            mock_thread = MagicMock()
            thr_cls.return_value = mock_thread
            mod.run()
        assert fake_socket.close.called

    def test_context_terminated_on_stop(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            mod.run()
        fake_ctx.term.assert_called_once()

    def test_thread_joined_on_stop(self, broker_module):
        mod, fake_ctx, fake_socket, _ = broker_module
        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls:
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            mock_thread = MagicMock()
            thr_cls.return_value = mock_thread
            mod.run()
        mock_thread.join.assert_called_once()


# ============================================================================
# 6. _shutdown() sets stop_event
# ============================================================================

@pytest.mark.unit
class TestShutdownHandler:

    def test_shutdown_sets_stop_event(self, broker_module):
        """_shutdown handler must call stop_event.set()."""
        mod, fake_ctx, fake_socket, _ = broker_module
        captured_handlers = {}

        with patch("threading.Event") as ev_cls, \
             patch("threading.Thread") as thr_cls, \
             patch("signal.signal", side_effect=lambda s, h: captured_handlers.update({s: h})):
            ev = MagicMock()
            ev.wait.return_value = True
            ev_cls.return_value = ev
            thr_cls.return_value = MagicMock()
            mod.run()

        handler = captured_handlers.get(signal.SIGINT)
        assert handler is not None
        handler(signal.SIGINT, None)  # simulate signal delivery
        ev.set.assert_called()
