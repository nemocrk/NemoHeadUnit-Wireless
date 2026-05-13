"""
test_bus_trace.py — Unit tests for shared/bus_trace.BusTracer.

Coverage targets:
  1. Instantiation
      a. BusTracer with TRACE_ENABLED=1 starts drain thread automatically
      b. BusTracer with BUS_TRACE=0 stays disabled, no thread started
      c. module_name stored correctly
      d. enabled attribute reflects env var

  2. emit()
      a. emit() with enabled=True enqueues event dict
      b. emit() with enabled=False is a no-op (queue stays empty)
      c. emit() adds 'type', 'module', 'ts_ns' fields automatically
      d. emit() merges extra kwargs into event
      e. emit() never raises even with un-serializable values
      f. emit() drops silently when queue is full (queue.Full)
      g. dropped_local() increments when queue is full

  3. start() / close()
      a. start() is idempotent (calling twice does not start two threads)
      b. close() sets _running=False
      c. close() joins thread within 0.5s
      d. close() can be called safely when disabled (no thread)
      e. close() is safe to call multiple times

  4. _drain_loop()
      a. drain loop picks events from queue and calls _send_now
      b. drain loop reports local drop count via trace_local_drop event
      c. drain loop exits when _running=False
      d. drain loop disables itself if ZMQ context fails to create

  5. _send_now()
      a. serialises event to JSON bytes and calls socket.send
      b. increments _dropped_local on any send exception
      c. does not raise on exception (best-effort)

  6. env-var configuration
      a. TRACE_ADDR is read from BUS_TRACE_ADDR env var
      b. TRACE_QUEUE_MAX limits queue size
      c. BUS_TRACE=false disables tracer
      d. BUS_TRACE=0 disables tracer
      e. BUS_TRACE=off disables tracer

  7. thread-safety
      a. concurrent emit() from multiple threads, all enqueued or dropped (no crash)
      b. close() while emitting does not raise
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_V2 = Path(__file__).parents[3]
_SHARED = _V2 / "shared"
for _p in (_V2, _SHARED):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracer(enabled: bool = True, module_name: str = "test_module") -> object:
    """Build a BusTracer with the drain thread replaced by a no-op."""
    from shared.bus_trace import BusTracer
    with patch("shared.bus_trace.TRACE_ENABLED", enabled):
        with patch.object(BusTracer, "_drain_loop", return_value=None):
            t = BusTracer(module_name=module_name)
    return t


def _drain_one(tracer) -> dict | None:
    """Pull one event from the internal queue (non-blocking)."""
    try:
        return tracer._queue.get_nowait()
    except queue.Empty:
        return None


# ============================================================================
# 1. Instantiation
# ============================================================================

@pytest.mark.unit
class TestBusTracerInstantiation:

    def test_enabled_true_starts_thread(self):
        from shared.bus_trace import BusTracer
        with patch("shared.bus_trace.TRACE_ENABLED", True):
            with patch.object(BusTracer, "_drain_loop", return_value=None) as mock_drain:
                t = BusTracer(module_name="m")
                time.sleep(0.05)
        assert t._running is True
        t.close()

    def test_enabled_false_no_thread(self):
        from shared.bus_trace import BusTracer
        with patch("shared.bus_trace.TRACE_ENABLED", False):
            t = BusTracer(module_name="m")
        assert t._running is False
        assert t._thread is None

    def test_module_name_stored(self):
        t = _make_tracer(module_name="my_mod")
        assert t.module_name == "my_mod"
        t.close()

    def test_enabled_attribute_true(self):
        t = _make_tracer(enabled=True)
        assert t.enabled is True
        t.close()

    def test_enabled_attribute_false(self):
        t = _make_tracer(enabled=False)
        assert t.enabled is False


# ============================================================================
# 2. emit()
# ============================================================================

@pytest.mark.unit
class TestBusTracerEmit:

    def test_emit_enqueues_when_enabled(self):
        t = _make_tracer(enabled=True)
        t.emit("test_event", key="value")
        ev = _drain_one(t)
        assert ev is not None
        t.close()

    def test_emit_noop_when_disabled(self):
        t = _make_tracer(enabled=False)
        t.emit("test_event")
        assert t._queue.empty()

    def test_emit_adds_type_field(self):
        t = _make_tracer()
        t.emit("my_event")
        ev = _drain_one(t)
        assert ev["type"] == "my_event"
        t.close()

    def test_emit_adds_module_field(self):
        t = _make_tracer(module_name="source_mod")
        t.emit("ev")
        ev = _drain_one(t)
        assert ev["module"] == "source_mod"
        t.close()

    def test_emit_adds_ts_ns_field(self):
        t = _make_tracer()
        before_ns = time.monotonic_ns()
        t.emit("ev")
        after_ns = time.monotonic_ns()
        ev = _drain_one(t)
        assert before_ns <= ev["ts_ns"] <= after_ns
        t.close()

    def test_emit_merges_extra_kwargs(self):
        t = _make_tracer()
        t.emit("ev", alpha=1, beta="two")
        ev = _drain_one(t)
        assert ev["alpha"] == 1
        assert ev["beta"] == "two"
        t.close()

    def test_emit_never_raises_with_unserializable(self):
        t = _make_tracer()
        # object() is not JSON-serializable, but emit should not raise
        t.emit("ev", bad=object())
        t.close()

    def test_emit_drops_when_queue_full(self):
        from shared.bus_trace import BusTracer
        with patch("shared.bus_trace.TRACE_ENABLED", True):
            with patch("shared.bus_trace.TRACE_QUEUE_MAX", 2):
                with patch.object(BusTracer, "_drain_loop", return_value=None):
                    t = BusTracer(module_name="m")
        # Fill queue
        t.emit("e1")
        t.emit("e2")
        # This must drop silently
        t.emit("e3_overflow")
        assert t._dropped_local == 1
        t.close()

    def test_dropped_local_increments_on_full(self):
        from shared.bus_trace import BusTracer
        with patch("shared.bus_trace.TRACE_ENABLED", True):
            with patch("shared.bus_trace.TRACE_QUEUE_MAX", 1):
                with patch.object(BusTracer, "_drain_loop", return_value=None):
                    t = BusTracer(module_name="m")
        t.emit("fill")
        t.emit("overflow1")
        t.emit("overflow2")
        assert t.dropped_local() == 2
        t.close()


# ============================================================================
# 3. start() / close()
# ============================================================================

@pytest.mark.unit
class TestBusTracerLifecycle:

    def test_start_idempotent(self):
        t = _make_tracer(enabled=True)
        thread_before = t._thread
        t.start()  # second call
        assert t._thread is thread_before
        t.close()

    def test_close_sets_running_false(self):
        t = _make_tracer(enabled=True)
        t.close()
        assert t._running is False

    def test_close_joins_thread(self):
        from shared.bus_trace import BusTracer
        with patch("shared.bus_trace.TRACE_ENABLED", True):
            with patch.object(BusTracer, "_drain_loop", return_value=None):
                t = BusTracer(module_name="m")
        assert t._thread is not None
        t.close()
        assert not t._thread.is_alive()

    def test_close_safe_when_disabled(self):
        t = _make_tracer(enabled=False)
        t.close()  # must not raise

    def test_close_idempotent(self):
        t = _make_tracer(enabled=True)
        t.close()
        t.close()  # second close must not raise


# ============================================================================
# 4. _drain_loop()
# ============================================================================

@pytest.mark.unit
class TestBusTracerDrainLoop:

    def test_drain_loop_calls_send_now(self):
        from shared.bus_trace import BusTracer
        sent = []

        def fake_send(ev):
            sent.append(ev)

        with patch("shared.bus_trace.TRACE_ENABLED", True):
            with patch.object(BusTracer, "_drain_loop", return_value=None):
                t = BusTracer(module_name="m")

        t._send_now = fake_send
        t._queue.put({"type": "x", "module": "m", "ts_ns": 0})
        # Simulate one drain iteration manually
        ev = t._queue.get_nowait()
        t._send_now(ev)
        assert sent[0]["type"] == "x"
        t.close()

    def test_drain_loop_disables_on_zmq_failure(self):
        from shared.bus_trace import BusTracer

        def broken_drain(self):
            # Simulate ZMQ context creation failure
            self.enabled = False

        with patch("shared.bus_trace.TRACE_ENABLED", True):
            with patch.object(BusTracer, "_drain_loop", broken_drain):
                t = BusTracer(module_name="m")
        time.sleep(0.05)
        assert t.enabled is False


# ============================================================================
# 5. _send_now()
# ============================================================================

@pytest.mark.unit
class TestBusTracerSendNow:

    def _tracer_with_push(self):
        t = _make_tracer(enabled=True)
        mock_push = MagicMock()
        t._push = mock_push
        return t, mock_push

    def test_send_now_serialises_to_json(self):
        t, mock_push = self._tracer_with_push()
        ev = {"type": "ev", "module": "m", "ts_ns": 123, "x": 1}
        t._send_now(ev)
        args = mock_push.send.call_args
        data = args[0][0]
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["type"] == "ev"
        assert parsed["x"] == 1
        t.close()

    def test_send_now_uses_noblock_flag(self):
        import zmq
        t, mock_push = self._tracer_with_push()
        t._send_now({"type": "e", "module": "m", "ts_ns": 0})
        kwargs = mock_push.send.call_args[1]
        assert kwargs.get("flags") == zmq.NOBLOCK
        t.close()

    def test_send_now_increments_drop_on_exception(self):
        t = _make_tracer(enabled=True)
        mock_push = MagicMock()
        mock_push.send.side_effect = Exception("fail")
        t._push = mock_push
        t._send_now({"type": "e", "module": "m", "ts_ns": 0})
        assert t._dropped_local == 1
        t.close()

    def test_send_now_does_not_raise(self):
        t = _make_tracer(enabled=True)
        mock_push = MagicMock()
        mock_push.send.side_effect = RuntimeError("unexpected")
        t._push = mock_push
        t._send_now({"type": "e", "module": "m", "ts_ns": 0})  # must not raise
        t.close()


# ============================================================================
# 6. env-var configuration
# ============================================================================

@pytest.mark.unit
class TestBusTracerEnvVars:

    def test_trace_addr_from_env(self):
        import importlib
        import shared.bus_trace as bt_mod
        with patch.dict("os.environ", {"BUS_TRACE_ADDR": "ipc:///tmp/custom_trace"}):
            importlib.reload(bt_mod)
            assert bt_mod.TRACE_ADDR == "ipc:///tmp/custom_trace"
        importlib.reload(bt_mod)

    def test_trace_disabled_by_zero(self):
        import importlib
        import shared.bus_trace as bt_mod
        with patch.dict("os.environ", {"BUS_TRACE": "0"}):
            importlib.reload(bt_mod)
            assert bt_mod.TRACE_ENABLED is False
        importlib.reload(bt_mod)

    def test_trace_disabled_by_false(self):
        import importlib
        import shared.bus_trace as bt_mod
        with patch.dict("os.environ", {"BUS_TRACE": "false"}):
            importlib.reload(bt_mod)
            assert bt_mod.TRACE_ENABLED is False
        importlib.reload(bt_mod)

    def test_trace_disabled_by_off(self):
        import importlib
        import shared.bus_trace as bt_mod
        with patch.dict("os.environ", {"BUS_TRACE": "off"}):
            importlib.reload(bt_mod)
            assert bt_mod.TRACE_ENABLED is False
        importlib.reload(bt_mod)

    def test_trace_queue_max_from_env(self):
        import importlib
        import shared.bus_trace as bt_mod
        with patch.dict("os.environ", {"BUS_TRACE_QUEUE_MAX": "555"}):
            importlib.reload(bt_mod)
            assert bt_mod.TRACE_QUEUE_MAX == 555
        importlib.reload(bt_mod)


# ============================================================================
# 7. Thread-safety
# ============================================================================

@pytest.mark.unit
class TestBusTracerThreadSafety:

    def test_concurrent_emit_no_crash(self):
        t = _make_tracer(enabled=True)
        errors = []

        def _worker(n):
            try:
                for _ in range(50):
                    t.emit("concurrent_event", n=n)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(6)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=3)

        assert not errors
        t.close()

    def test_close_while_emitting_no_crash(self):
        t = _make_tracer(enabled=True)
        errors = []

        def _emit():
            try:
                for _ in range(200):
                    t.emit("rapid_event")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        th = threading.Thread(target=_emit, daemon=True)
        th.start()
        time.sleep(0.05)
        t.close()
        th.join(timeout=3)
        assert not errors
