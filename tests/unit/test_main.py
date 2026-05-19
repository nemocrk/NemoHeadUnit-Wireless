"""
test_main.py — Unit tests for main.py pure functions.

All ZMQ sockets, subprocesses and filesystem access are mocked.
Tests exercise only the pure/stateless helpers that do not require
a live broker or real subprocesses.

Coverage sections:
  1.  discover_modules()                  — path filtering
  2.  _publish()                          — send_multipart + NOBLOCK
  3.  _publish()                          — zmq.Again is swallowed
  4.  _module_status()                    — active vs exited
  5.  _terminate_all()                    — already-exited processes skipped
  6.  _terminate_all()                    — running processes get terminate()
  7.  _terminate_all()                    — timeout triggers kill()
  8.  _terminate_broker()                 — already-exited broker skipped
  9.  _terminate_broker()                 — running broker gets terminate()
  10. _terminate_broker()                 — timeout triggers kill()
  11. _collect_module_ready()             — maps priority correctly
  12. _collect_module_ready()             — unreplied modules get priority 1
  13. _wait_for_level_ready()             — missing modules logged as warnings
  14. _wait_channel_manager_stopped()     — returns True on ACK
  15. _wait_channel_manager_stopped()     — returns False on timeout
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# ZMQ stub factory
# ---------------------------------------------------------------------------

def _make_zmq_stub():
    zmq_mod = types.ModuleType("zmq")
    zmq_mod.PUB      = 1
    zmq_mod.SUB      = 2
    zmq_mod.XPUB     = 3
    zmq_mod.XSUB     = 4
    zmq_mod.SNDHWM   = 5
    zmq_mod.RCVHWM   = 6
    zmq_mod.LINGER   = 7
    zmq_mod.NOBLOCK  = 8
    zmq_mod.SUBSCRIBE = 9
    zmq_mod.Again    = type("Again",    (Exception,), {})
    zmq_mod.ZMQError = type("ZMQError", (Exception,), {})

    fake_socket  = MagicMock()
    fake_context = MagicMock()
    fake_context.socket.return_value = fake_socket
    zmq_mod.Context = MagicMock(return_value=fake_context)
    return zmq_mod, fake_context, fake_socket


# ---------------------------------------------------------------------------
# Fixture: load main.py with mocked deps
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_module():
    import importlib
    zmq_stub, fake_ctx, fake_socket = _make_zmq_stub()
    mock_log = MagicMock()

    shared_logger_stub = types.SimpleNamespace(
        get_logger=MagicMock(return_value=mock_log),
        attach_bus=MagicMock(),
    )
    shared_bus_stub = types.SimpleNamespace(
        BusClient=MagicMock(return_value=MagicMock()),
    )

    with patch.dict("sys.modules", {
        "zmq":               zmq_stub,
        "shared.logger":     shared_logger_stub,
        "shared.bus_client": shared_bus_stub,
    }):
        if "main" in sys.modules:
            del sys.modules["main"]
        import main as mod
        importlib.reload(mod)
        mod.zmq  = zmq_stub
        mod.log  = mock_log
        yield mod, zmq_stub, fake_ctx, fake_socket, mock_log


# ============================================================================
# 1. discover_modules()
# ============================================================================

@pytest.mark.unit
class TestDiscoverModules:

    def test_returns_list(self, main_module, tmp_path):
        mod, *_ = main_module
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "main.py").write_text("")
        with patch.object(mod, "MODULES_DIR", tmp_path):
            result = mod.discover_modules()
        assert isinstance(result, list)

    def test_finds_main_py(self, main_module, tmp_path):
        mod, *_ = main_module
        (tmp_path / "mymod").mkdir()
        (tmp_path / "mymod" / "main.py").write_text("")
        with patch.object(mod, "MODULES_DIR", tmp_path):
            result = mod.discover_modules()
        assert any(p.parent.name == "mymod" for p in result)

    def test_excludes_underscore_dirs(self, main_module, tmp_path):
        mod, *_ = main_module
        (tmp_path / "_template").mkdir()
        (tmp_path / "_template" / "main.py").write_text("")
        with patch.object(mod, "MODULES_DIR", tmp_path):
            result = mod.discover_modules()
        assert not any(p.parent.name.startswith("_") for p in result)

    def test_excludes_disabled_modules(self, main_module, tmp_path):
        """main.py.disabled must not be discovered."""
        mod, *_ = main_module
        (tmp_path / "disabled_mod").mkdir()
        (tmp_path / "disabled_mod" / "main.py.disabled").write_text("")
        with patch.object(mod, "MODULES_DIR", tmp_path):
            result = mod.discover_modules()
        assert not any(p.parent.name == "disabled_mod" for p in result)

    def test_empty_modules_dir(self, main_module, tmp_path):
        mod, *_ = main_module
        with patch.object(mod, "MODULES_DIR", tmp_path):
            result = mod.discover_modules()
        assert result == []

    def test_multiple_modules_sorted(self, main_module, tmp_path):
        mod, *_ = main_module
        for name in ["zzz", "aaa", "mmm"]:
            (tmp_path / name).mkdir()
            (tmp_path / name / "main.py").write_text("")
        with patch.object(mod, "MODULES_DIR", tmp_path):
            result = mod.discover_modules()
        names = [p.parent.name for p in result]
        assert names == sorted(names)


# ============================================================================
# 2. _publish() — happy path
# ============================================================================

@pytest.mark.unit
class TestPublish:

    def test_send_multipart_called(self, main_module):
        mod, zmq_stub, _, fake_socket, _ = main_module
        fake_socket.reset_mock()
        mod._publish(fake_socket, "some.topic", {"key": "val"})
        fake_socket.send_multipart.assert_called_once()

    def test_topic_encoded_as_first_frame(self, main_module):
        mod, zmq_stub, _, fake_socket, _ = main_module
        fake_socket.reset_mock()
        mod._publish(fake_socket, "hello.world", {})
        frames = fake_socket.send_multipart.call_args[0][0]
        assert frames[0] == b"hello.world"

    def test_payload_json_encoded_as_second_frame(self, main_module):
        mod, zmq_stub, _, fake_socket, _ = main_module
        fake_socket.reset_mock()
        payload = {"x": 42}
        mod._publish(fake_socket, "t", payload)
        frames = fake_socket.send_multipart.call_args[0][0]
        assert json.loads(frames[1]) == payload

    def test_noblock_flag_used(self, main_module):
        mod, zmq_stub, _, fake_socket, _ = main_module
        fake_socket.reset_mock()
        mod._publish(fake_socket, "t", {})
        kwargs = fake_socket.send_multipart.call_args[1]
        assert kwargs.get("flags") == zmq_stub.NOBLOCK

    def test_again_is_swallowed(self, main_module):
        mod, zmq_stub, _, fake_socket, _ = main_module
        fake_socket.send_multipart.side_effect = zmq_stub.Again()
        mod._publish(fake_socket, "t", {})
        fake_socket.send_multipart.side_effect = None


# ============================================================================
# 4. _module_status()
# ============================================================================

@pytest.mark.unit
class TestModuleStatus:

    def test_running_process_is_active(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = None
        assert mod._module_status(proc) == "active"

    def test_exited_zero_shows_code(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = 0
        assert "0" in mod._module_status(proc)

    def test_exited_nonzero_shows_code(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = 1
        assert "1" in mod._module_status(proc)


# ============================================================================
# 5-7. _terminate_all()
# ============================================================================

@pytest.mark.unit
class TestTerminateAll:

    def test_already_exited_processes_skipped(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        mod._terminate_all([("mod_a", proc)])
        proc.terminate.assert_not_called()

    def test_running_process_gets_terminate(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = None
        proc.returncode = None
        # First wait() raises TimeoutExpired; subsequent calls return normally
        proc.wait.side_effect = [subprocess.TimeoutExpired("x", 1), None]
        mod._terminate_all([("mod_b", proc)])
        proc.terminate.assert_called()

    def test_timeout_triggers_kill(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = None
        proc.returncode = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("x", 1), None]
        mod._terminate_all([("mod_c", proc)])
        proc.kill.assert_called()


# ============================================================================
# 8-10. _terminate_broker()
# ============================================================================

@pytest.mark.unit
class TestTerminateBroker:

    def test_already_exited_broker_skipped(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = 0
        mod._terminate_broker(proc)
        proc.terminate.assert_not_called()

    def test_running_broker_gets_terminate(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        mod._terminate_broker(proc)
        proc.terminate.assert_called_once()

    def test_timeout_triggers_kill(self, main_module):
        mod, *_ = main_module
        proc = MagicMock()
        proc.poll.return_value = None
        # First wait(timeout=GRACE_PERIOD) raises; second wait() after kill() succeeds
        proc.wait.side_effect = [subprocess.TimeoutExpired("x", 1), None]
        mod._terminate_broker(proc)
        proc.kill.assert_called_once()


# ============================================================================
# 11-12. _collect_module_ready()
# ============================================================================

@pytest.mark.unit
class TestCollectModuleReady:

    def _make_sub_socket(self, zmq_stub, messages):
        """Build a fake SUB socket that yields `messages` then stops polling."""
        sub = MagicMock()
        poll_returns = [True] * len(messages) + [False] * 10
        sub.poll.side_effect = poll_returns
        encoded = [
            [b"system.module_ready", json.dumps(m).encode()]
            for m in messages
        ]
        sub.recv_multipart.side_effect = encoded
        return sub

    def test_priority_map_correct(self, main_module):
        mod, zmq_stub, fake_ctx, _, _ = main_module
        messages = [
            {"name": "alpha", "priority": 1},
            {"name": "beta",  "priority": 2},
        ]
        sub = self._make_sub_socket(zmq_stub, messages)
        fake_ctx.socket.return_value = sub

        result = mod._collect_module_ready(
            MagicMock(),
            ["alpha", "beta"],
            window=0.05,
        )
        assert "alpha" in result.get(1, [])
        assert "beta"  in result.get(2, [])

    def test_unreplied_modules_get_priority_1(self, main_module):
        mod, zmq_stub, fake_ctx, _, _ = main_module
        sub = MagicMock()
        sub.poll.return_value = False
        fake_ctx.socket.return_value = sub

        result = mod._collect_module_ready(
            MagicMock(),
            ["silent_mod"],
            window=0.05,
        )
        assert "silent_mod" in result.get(1, [])


# ============================================================================
# 13. _wait_for_level_ready() — missing modules logged
# ============================================================================

@pytest.mark.unit
class TestWaitForLevelReady:

    def test_missing_modules_logged_as_warning(self, main_module):
        mod, zmq_stub, fake_ctx, _, mock_log = main_module
        sub = MagicMock()
        sub.poll.return_value = False
        fake_ctx.socket.return_value = sub
        mock_log.reset_mock()

        mod._wait_for_level_ready(
            MagicMock(),
            priority=1,
            expected=["ghost_mod"],
            timeout_per_module=0.05,
        )
        assert mock_log.warning.called

    def test_empty_expected_returns_immediately(self, main_module):
        mod, zmq_stub, fake_ctx, _, _ = main_module
        fake_ctx.socket.reset_mock()
        mod._wait_for_level_ready(MagicMock(), priority=1, expected=[], timeout_per_module=0.05)


# ============================================================================
# 14-15. _wait_channel_manager_stopped()
# ============================================================================

@pytest.mark.unit
class TestWaitChannelManagerStopped:

    def test_returns_true_on_ack(self, main_module):
        mod, zmq_stub, fake_ctx, _, _ = main_module
        sub = MagicMock()
        sub.poll.side_effect = [True, False]
        sub.recv_multipart.return_value = [b"channel_manager.stopped"]
        fake_ctx.socket.return_value = sub

        result = mod._wait_channel_manager_stopped(timeout=0.1)
        assert result is True

    def test_returns_false_on_timeout(self, main_module):
        mod, zmq_stub, fake_ctx, _, _ = main_module
        sub = MagicMock()
        sub.poll.return_value = False
        fake_ctx.socket.return_value = sub

        result = mod._wait_channel_manager_stopped(timeout=0.05)
        assert result is False
