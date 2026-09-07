import json
import subprocess
import time
from unittest.mock import MagicMock, patch
import pytest

from backend.main import (
    _collect_module_ready,
    _wait_for_level_ready,
    _terminate_all,
    ModuleHandle,
)

pytestmark = pytest.mark.unit


def test_collect_module_ready_all_reply():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    sub_sock.poll.return_value = True
    sub_sock.recv_multipart.side_effect = [
        [b"system.module_ready", json.dumps({"name": "config_manager", "priority": 1}).encode("utf-8")],
        [b"system.module_ready", json.dumps({"name": "proxy", "priority": 2}).encode("utf-8")],
    ]

    module_names = ["config_manager", "proxy"]
    priority_map = _collect_module_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        module_names=module_names,
        external_handled_module="bus_broker",
        window=1.0,
    )

    # Verify system.readytostart was sent
    pub_sock.send_multipart.assert_called_once()
    assert pub_sock.send_multipart.call_args[0][0][0] == b"system.readytostart"

    # Verify priority mapping
    assert priority_map[1] == ["config_manager"]
    assert priority_map[2] == ["proxy"]


def test_collect_module_ready_fallback_priority():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    # No modules reply
    sub_sock.poll.return_value = False

    module_names = ["unresponsive_mod"]
    priority_map = _collect_module_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        module_names=module_names,
        external_handled_module="bus_broker",
        window=0.01,
    )

    # Unresponsive module defaults to priority 1
    assert 1 in priority_map
    assert "unresponsive_mod" in priority_map[1]


def test_wait_for_level_ready_success():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    sub_sock.poll.return_value = True
    sub_sock.recv_multipart.side_effect = [
        [b"system.ready", json.dumps({"name": "tcp_server", "priority": 3}).encode("utf-8")],
        [b"system.ready", json.dumps({"name": "channel_manager", "priority": 3}).encode("utf-8")],
    ]

    expected = ["tcp_server", "channel_manager"]
    _wait_for_level_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        priority=3,
        expected=expected,
        timeout_per_module=0.5,
    )

    # Verify system.start was sent for priority 3
    pub_sock.send_multipart.assert_called_once()
    sent_frames = pub_sock.send_multipart.call_args[0][0]
    assert sent_frames[0] == b"system.start"
    payload = json.loads(sent_frames[1].decode("utf-8"))
    assert payload["priority"] == 3


def test_wait_for_level_ready_timeout():
    pub_sock = MagicMock()
    sub_sock = MagicMock()

    sub_sock.poll.return_value = False

    expected = ["slow_module"]
    # Should not raise exception on timeout
    _wait_for_level_ready(
        pub_sock=pub_sock,
        sub_sock=sub_sock,
        priority=4,
        expected=expected,
        timeout_per_module=0.01,
    )

    pub_sock.send_multipart.assert_called_once()


def test_terminate_all_processes_and_threads():
    # 1. Process already exited
    proc_exited = MagicMock()
    proc_exited.poll.return_value = 0

    # 2. Process responding gracefully to terminate()
    proc_graceful = MagicMock()
    proc_graceful.poll.side_effect = [None, None, 0, 0, 0]

    # 3. Process that hangs and requires kill()
    proc_stuck = MagicMock()
    proc_stuck.poll.side_effect = [None, None, None, None, None]
    proc_stuck.wait.side_effect = [None, subprocess.TimeoutExpired(cmd="stuck", timeout=3.0)]

    # 4. Thread already exited
    thread_exited = MagicMock()
    thread_exited.is_alive.return_value = False

    # 5. Active thread joined gracefully
    thread_active = MagicMock()
    thread_active.is_alive.side_effect = [True, False]

    handles = [
        ModuleHandle("exited_proc", "multiprocessing", proc=proc_exited),
        ModuleHandle("graceful_proc", "multiprocessing", proc=proc_graceful),
        ModuleHandle("stuck_proc", "multiprocessing", proc=proc_stuck),
        ModuleHandle("exited_thread", "multithreading", thread=thread_exited),
        ModuleHandle("active_thread", "multithreading", thread=thread_active),
    ]

    cur_time = [100.0]

    def mock_time():
        cur_time[0] += 0.5
        return cur_time[0]

    with patch("time.monotonic", side_effect=mock_time):
        _terminate_all(handles)

    # Verify graceful process received terminate
    proc_graceful.terminate.assert_called_once()

    # Verify stuck process was terminated then killed
    proc_stuck.terminate.assert_called_once()
    proc_stuck.kill.assert_called_once()

    # Verify active thread was joined
    thread_active.join.assert_called_once()
