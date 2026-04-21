"""
Unit tests for v2/modules/rfcomm_handshake/main.py

Tested behaviours:
  - on_bluetooth_rfcomm_connected: stores device_address, calls _try_start_handshake
  - on_hostapd_ready: stores credentials, calls _try_start_handshake
  - _try_start_handshake: no-op when only address known
  - _try_start_handshake: no-op when only credentials known
  - _try_start_handshake: starts background thread when both conditions met
  - _run_handshake: publishes rfcomm.handshake.started
  - _run_handshake: publishes rfcomm.handshake.failed when RFCOMM connect fails
  - _run_handshake: publishes rfcomm.handshake.completed on success
  - _run_handshake: publishes rfcomm.handshake.failed when handshake fails
  - on_system_stop: closes pending socket and stops bus
  - on_system_stop: safe when no pending socket
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_TESTS   = Path(__file__).parent
_ROOT    = _TESTS.parent.parent
_V2      = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in (str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Stub shared dependencies
# ---------------------------------------------------------------------------

_bus_instance = MagicMock()
_bus_class    = MagicMock(return_value=_bus_instance)

_shared_pkg     = types.ModuleType("shared")
_bus_client_mod = types.ModuleType("shared.bus_client")
_logger_mod     = types.ModuleType("shared.logger")

_bus_client_mod.BusClient = _bus_class
_logger_mod.get_logger    = MagicMock(return_value=MagicMock())

sys.modules.setdefault("shared",             _shared_pkg)
sys.modules["shared.bus_client"] = _bus_client_mod
sys.modules["shared.logger"]     = _logger_mod

# Stub rfcomm_handshake sub-helpers
_rh_pkg        = types.ModuleType("rfcomm_handshake")
_handshake_mod = types.ModuleType("rfcomm_handshake.handshake")

_MockRfcommHandshake = MagicMock()
_handshake_mod.RfcommHandshake = _MockRfcommHandshake

sys.modules.setdefault("rfcomm_handshake",            _rh_pkg)
sys.modules["rfcomm_handshake.handshake"] = _handshake_mod

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------

import v2.modules.rfcomm_handshake.main as rh  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_module_state():
    rh._credentials    = None
    rh._device_address = None
    rh._pending_sock   = None
    rh.bus.publish.reset_mock()
    rh.bus.stop.reset_mock()
    _MockRfcommHandshake.reset_mock()


def _published(topic):
    return [
        args[1]
        for args, _ in rh.bus.publish.call_args_list
        if args[0] == topic
    ]


def _make_result(success=True, phone_ip="192.168.50.100", error=""):
    r = MagicMock()
    r.success  = success
    r.phone_ip = phone_ip
    r.error    = error
    return r


# ---------------------------------------------------------------------------
# Tests — on_bluetooth_rfcomm_connected
# ---------------------------------------------------------------------------

class TestRfcommConnected:
    def test_stores_device_address(self):
        rh.on_bluetooth_rfcomm_connected(
            "bluetooth.rfcomm.connected", {"device_address": "AA:BB:CC:DD:EE:FF"}
        )
        assert rh._device_address == "AA:BB:CC:DD:EE:FF"

    def test_calls_try_start_handshake(self):
        with patch.object(rh, "_try_start_handshake") as mock_try:
            rh.on_bluetooth_rfcomm_connected(
                "bluetooth.rfcomm.connected", {"device_address": "AA:BB:CC:DD:EE:FF"}
            )
            mock_try.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — on_hostapd_ready
# ---------------------------------------------------------------------------

class TestHostapdReady:
    def test_stores_credentials(self):
        creds = {"ssid": "AP", "key": "secret", "bssid": "11:22:33:44:55:66",
                 "interface": "wlan0", "gateway_ip": "192.168.50.1",
                 "security_mode": 8, "ap_type": 1}
        rh.on_hostapd_ready("hostapd.ready", creds)
        assert rh._credentials == creds

    def test_calls_try_start_handshake(self):
        with patch.object(rh, "_try_start_handshake") as mock_try:
            rh.on_hostapd_ready("hostapd.ready", {"ssid": "AP"})
            mock_try.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — _try_start_handshake
# ---------------------------------------------------------------------------

class TestTryStartHandshake:
    def test_noop_when_only_address_known(self):
        rh._device_address = "AA:BB:CC:DD:EE:FF"
        with patch("threading.Thread") as mock_thread:
            rh._try_start_handshake()
            mock_thread.assert_not_called()

    def test_noop_when_only_credentials_known(self):
        rh._credentials = {"ssid": "AP"}
        with patch("threading.Thread") as mock_thread:
            rh._try_start_handshake()
            mock_thread.assert_not_called()

    def test_starts_thread_when_both_conditions_met(self):
        rh._device_address = "AA:BB:CC:DD:EE:FF"
        rh._credentials    = {"ssid": "AP"}
        mock_thread = MagicMock()
        with patch("threading.Thread", return_value=mock_thread):
            rh._try_start_handshake()
            mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — _run_handshake
# ---------------------------------------------------------------------------

class TestRunHandshake:
    def _setup(self, address="AA:BB:CC:DD:EE:FF"):
        rh._device_address = address
        rh._credentials    = {"ssid": "AndroidAutoAP", "key": "abc"}

    def test_publishes_started(self):
        self._setup()
        with patch.object(rh, "_connect_rfcomm", return_value=None):
            rh._run_handshake()
        started = _published("rfcomm.handshake.started")
        assert len(started) == 1
        assert started[0]["device_address"] == "AA:BB:CC:DD:EE:FF"

    def test_connect_failure_publishes_failed(self):
        self._setup()
        with patch.object(rh, "_connect_rfcomm", return_value=None):
            rh._run_handshake()
        failed = _published("rfcomm.handshake.failed")
        assert len(failed) == 1
        assert failed[0]["device_address"] == "AA:BB:CC:DD:EE:FF"

    def test_successful_handshake_publishes_completed(self):
        self._setup()
        sock = MagicMock()
        hs = MagicMock()
        hs.run.return_value = _make_result(success=True, phone_ip="192.168.50.100")
        _MockRfcommHandshake.return_value = hs

        with patch.object(rh, "_connect_rfcomm", return_value=sock):
            rh._run_handshake()

        completed = _published("rfcomm.handshake.completed")
        assert len(completed) == 1
        assert completed[0]["phone_ip"] == "192.168.50.100"
        assert completed[0]["device_address"] == "AA:BB:CC:DD:EE:FF"

    def test_failed_handshake_publishes_failed_with_error(self):
        self._setup()
        sock = MagicMock()
        hs = MagicMock()
        hs.run.return_value = _make_result(success=False, error="timeout")
        _MockRfcommHandshake.return_value = hs

        with patch.object(rh, "_connect_rfcomm", return_value=sock):
            rh._run_handshake()

        failed = _published("rfcomm.handshake.failed")
        assert len(failed) == 1
        assert failed[0]["error"] == "timeout"

    def test_pending_sock_closed_after_run(self):
        self._setup()
        sock = MagicMock()
        hs = MagicMock()
        hs.run.return_value = _make_result()
        _MockRfcommHandshake.return_value = hs

        with patch.object(rh, "_connect_rfcomm", return_value=sock):
            rh._run_handshake()

        sock.close.assert_called_once()
        assert rh._pending_sock is None


# ---------------------------------------------------------------------------
# Tests — on_system_stop
# ---------------------------------------------------------------------------

class TestSystemStop:
    def test_closes_pending_socket(self):
        sock = MagicMock()
        rh._pending_sock = sock
        rh.on_system_stop("system.stop", {})
        sock.close.assert_called_once()
        assert rh._pending_sock is None

    def test_stops_bus(self):
        rh.on_system_stop("system.stop", {})
        rh.bus.stop.assert_called_once()

    def test_safe_when_no_pending_socket(self):
        rh._pending_sock = None
        rh.on_system_stop("system.stop", {})
        rh.bus.stop.assert_called_once()
