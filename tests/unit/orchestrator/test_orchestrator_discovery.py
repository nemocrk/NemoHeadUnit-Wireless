import os
import sys
import threading
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

import backend.main as main_mod
from backend.main import (
    get_execution_mode,
    discover_modules,
    _start_module,
    ModuleHandle,
)

pytestmark = pytest.mark.unit


def test_get_execution_mode_cli_and_env():
    # 1. Default fallback
    with patch.dict(os.environ, {}, clear=True):
        assert get_execution_mode([]) == "multiprocessing"

    # 2. CLI -m flag
    assert get_execution_mode(["-m", "multithreading"]) == "multithreading"
    assert get_execution_mode(["--mode", "threads"]) == "multithreading"
    assert get_execution_mode(["--mode", "thread"]) == "multithreading"
    assert get_execution_mode(["-m", "multiprocessing"]) == "multiprocessing"

    # 3. Environment variable fallback
    with patch.dict(os.environ, {"NEMO_EXECUTION_MODE": "multithreading"}):
        assert get_execution_mode([]) == "multithreading"

    with patch.dict(os.environ, {"NEMO_MODE": "threading"}):
        assert get_execution_mode([]) == "multithreading"


def test_discover_modules(tmp_path):
    # Create synthetic modules directory structure
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    # Valid modules
    (modules_dir / "bus_broker").mkdir()
    (modules_dir / "bus_broker" / "main.py").write_text("# broker")
    (modules_dir / "proxy").mkdir()
    (modules_dir / "proxy" / "main.py").write_text("# proxy")

    # Template and hidden directories (should be ignored)
    (modules_dir / "_template").mkdir()
    (modules_dir / "_template" / "main.py").write_text("# template")
    (modules_dir / "_hidden").mkdir()
    (modules_dir / "_hidden" / "main.py").write_text("# hidden")

    with patch.object(main_mod, "MODULES_DIR", modules_dir):
        discovered = discover_modules()
        discovered_names = [m.parent.name for m in discovered]
        assert discovered_names == ["bus_broker", "proxy"]
        assert "_template" not in discovered_names
        assert "_hidden" not in discovered_names


def test_start_module_thread_mode():
    mock_thread = MagicMock()
    with patch("threading.Thread", return_value=mock_thread):
        handle = _start_module(Path("/dummy/path/main.py"), "dummy_label", "multithreading")
        assert handle.label == "dummy_label"
        assert handle.mode == "multithreading"
        assert handle.thread == mock_thread
        mock_thread.start.assert_called_once()


def test_start_module_process_mode():
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        handle = _start_module(Path("/dummy/path/main.py"), "dummy_proc", "multiprocessing")
        assert handle.label == "dummy_proc"
        assert handle.mode == "multiprocessing"
        assert handle.proc == mock_proc
        mock_popen.assert_called_once()
        env_passed = mock_popen.call_args[1]["env"]
        assert "PYTHONPATH" in env_passed
        assert str(main_mod.BASE_DIR) in env_passed["PYTHONPATH"]


def test_start_module_stdout_pump_ready_event():
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    # Simulate stdout lines with readiness confirmation
    mock_proc.stdout.readline.side_effect = [
        "Initializing bus_broker...\n",
        "ZMQ Proxy thread active (XPUB/XSUB ready)\n",
        "",
    ]

    ready_event = threading.Event()
    with patch("subprocess.Popen", return_value=mock_proc):
        handle = _start_module(
            Path("/dummy/bus_broker/main.py"),
            "bus_broker",
            "multiprocessing",
            ready_event=ready_event,
        )
        assert handle.label == "bus_broker"
        # Wait briefly for pump thread to process lines
        assert ready_event.wait(timeout=1.0) is True
