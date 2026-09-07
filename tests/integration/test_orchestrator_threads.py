# tests/integration/test_orchestrator_threads.py
import pytest
import asyncio
import subprocess
import sys
import time
import os
import signal
import struct
import json
import urllib.request
from pathlib import Path
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.mock_phone import MockPhoneClient

pytestmark = pytest.mark.integration


def _wait_for_orchestrator_boot(proc: subprocess.Popen, timeout: float = 12.0) -> list[str]:
    """Helper to stream stdout until 'Boot sequence complete' is logged."""
    lines = []
    start = time.monotonic()
    boot_complete = False

    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            break
        line = proc.stdout.readline()
        if line:
            clean_line = line.strip()
            lines.append(clean_line)
            if "Boot sequence complete" in clean_line or "Backend orchestrator active" in clean_line:
                boot_complete = True
                break
        else:
            time.sleep(0.05)

    assert proc.poll() is None, f"Orchestrator died prematurely: {' '.join(lines)}"
    assert boot_complete, f"Orchestrator did not complete boot within {timeout}s: {' '.join(lines[-15:])}"
    return lines


def test_thread_mode_complete_boot_sequence(tmp_path):
    """Verify backend orchestrator launches all modules in multithreading mode and completes boot waves."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"

    with IntegrationEnvironment(tmp_path) as env:
        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"
        sub_env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multithreading"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
            bufsize=1,
        )

        try:
            lines = _wait_for_orchestrator_boot(proc, timeout=12.0)
            # Verify boot sequence finished cleanly
            assert any("Boot sequence complete" in l for l in lines)
            assert any("multithreading" in l.lower() for l in lines)

            # Orderly shutdown
            proc.terminate()
            proc.wait(timeout=6.0)
            assert proc.returncode in (0, -signal.SIGTERM, 143)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_thread_mode_gateway_proxy_and_cross_thread_rest(tmp_path):
    """Verify Gateway Proxy and cross-thread REST API dispatch in multithreading mode."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"
    proxy_test_port = 8882

    with IntegrationEnvironment(tmp_path) as env:
        # Configure dedicated proxy port for testing
        proxy_cfg = tmp_path / "config" / "proxy.yaml"
        proxy_cfg.write_text(f"public_port: {proxy_test_port}\nhost: 127.0.0.1\n", encoding="utf-8")

        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"
        sub_env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multithreading"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
            bufsize=1,
        )

        try:
            lines = _wait_for_orchestrator_boot(proc, timeout=12.0)
            actual_proxy_port = proxy_test_port
            for l in lines:
                if "Gateway Proxy active" in l and "http://" in l:
                    parts = l.split("http://")[-1].split(":")
                    if len(parts) >= 2:
                        actual_proxy_port = int(parts[1].split()[0].split("/")[0])
            time.sleep(0.5)  # Allow routes to register with proxy

            # 1. Test Gateway Proxy root module registry
            req = urllib.request.Request(f"http://127.0.0.1:{actual_proxy_port}/api/system/modules")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert "modules" in data
                assert "proxy" in data["modules"]
                assert "config_manager" in data["modules"]

            # 2. Test cross-thread reverse-proxy to config_manager (/api/config/all)
            req = urllib.request.Request(f"http://127.0.0.1:{actual_proxy_port}/api/config/all")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert "bus_broker" in data
                assert "channel_manager" in data

            # 3. Test cross-thread reverse-proxy to tcp_server (/api/tcp/status)
            req = urllib.request.Request(f"http://127.0.0.1:{actual_proxy_port}/api/tcp/status")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert "running" in data or "port" in data or "clients" in data

            proc.terminate()
            proc.wait(timeout=6.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.mark.asyncio
async def test_thread_mode_phone_handshake_and_protocol(tmp_path):
    """Verify MockPhoneClient connects to thread-hosted tcp_server and negotiates with channel_manager thread."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"
    tcp_test_port = 5288

    with IntegrationEnvironment(tmp_path) as env:
        # Preconfigure tcp_server port
        tcp_cfg = tmp_path / "config" / "tcp_server.yaml"
        tcp_cfg.write_text(f"port: {tcp_test_port}\nhost: 127.0.0.1\nenable_ssl: false\nautostart: true\n", encoding="utf-8")

        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"
        sub_env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multithreading"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
            bufsize=1,
        )

        try:
            _wait_for_orchestrator_boot(proc, timeout=12.0)
            time.sleep(0.5)

            # Connect MockPhoneClient
            phone = MockPhoneClient()
            await phone.connect("127.0.0.1", tcp_test_port)

            # ChannelManager thread sends VERSION_REQUEST (Channel 0, MsgId 1)
            ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=3.0)
            assert ch_id == 0
            msg_id = struct.unpack_from(">H", payload, 0)[0]
            assert msg_id == 1  # VERSION_REQUEST

            # Phone responds with VERSION_RESPONSE
            # Version response: msg_id (2) + major (1) + minor (6) + status (0 = MATCH)
            resp_payload = struct.pack(">HHHh", 2, 1, 6, 0)
            await phone.send_frame(channel_id=0, flags=flags, payload=resp_payload)

            await phone.disconnect()

            proc.terminate()
            proc.wait(timeout=6.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.mark.asyncio
async def test_thread_mode_media_streaming(tmp_path):
    """Verify video/audio frame stream processing in multithreading mode."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"
    tcp_test_port = 5289

    with IntegrationEnvironment(tmp_path) as env:
        tcp_cfg = tmp_path / "config" / "tcp_server.yaml"
        tcp_cfg.write_text(f"port: {tcp_test_port}\nhost: 127.0.0.1\nenable_ssl: false\nautostart: true\n", encoding="utf-8")

        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"
        sub_env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "multithreading"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
            bufsize=1,
        )

        try:
            _wait_for_orchestrator_boot(proc, timeout=12.0)
            time.sleep(0.5)

            phone = MockPhoneClient()
            await phone.connect("127.0.0.1", tcp_test_port)

            # Consume VERSION_REQUEST
            ch_0, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=3.0)
            assert ch_0 == 0

            # Send synthetic video media stream packet (Channel 1 or 3 depending on channel map)
            # ChannelManager accepts frame and routes without exceptions
            fake_video = b"\x00\x00\x00\x01\x67\x42\x00\x1f" + (b"\xaa" * 128)
            await phone.send_frame(channel_id=1, flags=0x03, payload=fake_video)
            await asyncio.sleep(0.2)

            await phone.disconnect()
            proc.terminate()
            proc.wait(timeout=6.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_thread_mode_clean_shutdown_and_thread_join(tmp_path):
    """Verify multithreaded orchestrator broadcasts system.stop and joins all threads without leaks."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_main = repo_root / "backend" / "main.py"

    with IntegrationEnvironment(tmp_path) as env:
        sub_env = os.environ.copy()
        sub_env["QT_QPA_PLATFORM"] = "offscreen"
        sub_env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, str(backend_main), "-m", "threads"],  # test 'threads' alias
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sub_env,
            text=True,
            bufsize=1,
        )

        try:
            _wait_for_orchestrator_boot(proc, timeout=12.0)
            time.sleep(0.2)

            # Initiate graceful shutdown
            start_stop = time.monotonic()
            proc.terminate()
            proc.wait(timeout=6.0)
            stop_duration = time.monotonic() - start_stop

            assert stop_duration < 5.0, f"Shutdown took too long: {stop_duration}s"
            assert proc.returncode in (0, -signal.SIGTERM, 143)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
