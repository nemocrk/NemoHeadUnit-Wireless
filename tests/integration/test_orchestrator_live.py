# tests/integration/test_orchestrator_live.py
import pytest
import subprocess
import sys
import time
import os
import signal
from pathlib import Path
from tests.integration.harness.environment import IntegrationEnvironment

pytestmark = pytest.mark.integration


def test_orchestrator_multiprocessing_boot_and_sigterm(tmp_path):
    """Verify backend orchestrator boots modules in multiprocessing mode and exits cleanly on SIGTERM."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"

    with IntegrationEnvironment(tmp_path) as env:
        # Pass current environment with isolated NEMO_CONFIG_DIR and NEMO_IPC_DIR
        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multiprocessing"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
        )

        try:
            boot_detected = False
            start_time = time.monotonic()
            collected_lines = []

            # Wait up to 6.0s for orchestrator to boot Priority wave 0
            while time.monotonic() - start_time < 6.0:
                if proc.poll() is not None:
                    break
                line = proc.stdout.readline()
                if line:
                    collected_lines.append(line.strip())
                    if "Starting Web Browser Head Unit Backend Orchestrator" in line or "bus_broker" in line:
                        boot_detected = True
                        break
                else:
                    time.sleep(0.1)

            assert proc.poll() is None, f"Orchestrator died prematurely: {' '.join(collected_lines)}"
            assert boot_detected, f"Orchestrator boot banner not detected in stdout: {collected_lines}"

            # Send SIGTERM for graceful exit
            proc.terminate()
            try:
                proc.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
                pytest.fail("Orchestrator did not terminate within 8.0s timeout")

            assert proc.returncode in (0, -signal.SIGTERM, 143), f"Unexpected returncode: {proc.returncode}"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_orchestrator_multithreading_boot_and_sigterm(tmp_path):
    """Verify backend orchestrator boots modules in multithreading mode and exits cleanly on SIGTERM."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"

    with IntegrationEnvironment(tmp_path) as env:
        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multithreading"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
        )

        try:
            boot_detected = False
            start_time = time.monotonic()
            collected_lines = []

            while time.monotonic() - start_time < 6.0:
                if proc.poll() is not None:
                    break
                line = proc.stdout.readline()
                if line:
                    collected_lines.append(line.strip())
                    if "Starting Web Browser Head Unit Backend Orchestrator" in line:
                        boot_detected = True
                        break
                else:
                    time.sleep(0.1)

            assert proc.poll() is None, f"Orchestrator died prematurely: {' '.join(collected_lines)}"
            assert boot_detected, f"Orchestrator boot banner not detected in stdout: {collected_lines}"

            # Graceful shutdown via SIGTERM
            proc.terminate()
            try:
                proc.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
                pytest.fail("Orchestrator did not terminate within 8.0s timeout")

            assert proc.returncode in (0, -signal.SIGTERM, 143), f"Unexpected returncode: {proc.returncode}"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
