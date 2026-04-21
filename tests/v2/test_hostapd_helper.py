"""
Unit tests for v2/modules/hostapd_helper/main.py

Tested behaviours:
  - on_rfcomm_connected: builds APConfig from _config, starts APManager
  - on_rfcomm_connected: publishes hostapd.starting before start()
  - on_rfcomm_connected: publishes hostapd.failed when APManager.start() fails
  - on_rfcomm_connected: starts APMonitor with configured timeout
  - on_system_stop: stops monitor and manager, publishes hostapd.stopped
  - _on_ap_ready: publishes hostapd.ready with AP params
  - _on_ap_failed: tears down and publishes hostapd.failed
  - _on_config_loaded: merges persisted config, ignores unknown keys
  - _on_config_changed: updates single key, ignores unknown keys
  - _build_ap_config: maps every _config key to correct APConfig field
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

_cfg_instance = MagicMock()
_cfg_class    = MagicMock(return_value=_cfg_instance)

_shared_pkg        = types.ModuleType("shared")
_bus_client_mod    = types.ModuleType("shared.bus_client")
_config_client_mod = types.ModuleType("shared.config_client")
_logger_mod        = types.ModuleType("shared.logger")

_bus_client_mod.BusClient       = _bus_class
_config_client_mod.ConfigClient = _cfg_class
_logger_mod.get_logger          = MagicMock(return_value=MagicMock())

sys.modules.setdefault("shared",               _shared_pkg)
sys.modules["shared.bus_client"]    = _bus_client_mod
sys.modules["shared.config_client"] = _config_client_mod
sys.modules["shared.logger"]        = _logger_mod

# Stub hostapd_helper sub-helpers
_hh_pkg         = types.ModuleType("hostapd_helper")
_ap_manager_mod = types.ModuleType("hostapd_helper.ap_manager")
_ap_monitor_mod = types.ModuleType("hostapd_helper.ap_monitor")


class _FakeAPConfig:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_MockAPManager = MagicMock()
_MockAPMonitor = MagicMock()

_ap_manager_mod.APManager = _MockAPManager
_ap_manager_mod.APConfig  = _FakeAPConfig
_ap_monitor_mod.APMonitor = _MockAPMonitor

sys.modules.setdefault("hostapd_helper",              _hh_pkg)
sys.modules["hostapd_helper.ap_manager"] = _ap_manager_mod
sys.modules["hostapd_helper.ap_monitor"] = _ap_monitor_mod

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------

import v2.modules.hostapd_helper.main as hh  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_module_state():
    hh._ap_manager = None
    hh._ap_monitor = None
    hh._config     = dict(hh._DEFAULTS)
    hh.bus.publish.reset_mock()
    hh.bus.stop.reset_mock()
    _MockAPManager.reset_mock()
    _MockAPMonitor.reset_mock()


def _make_manager(start_ok=True):
    m = MagicMock()
    m.start.return_value = start_ok
    m._cfg = _FakeAPConfig(
        ssid="AndroidAutoAP",
        interface="wlan0",
        key="",
        channel=6,
        subnet="192.168.50",
        gateway_ip="192.168.50.1",
        dhcp_range_start="192.168.50.10",
        dhcp_range_end="192.168.50.50",
    )
    return m


def _published(topic):
    return [
        args[1]
        for args, _ in hh.bus.publish.call_args_list
        if args[0] == topic
    ]


# ---------------------------------------------------------------------------
# Tests — on_rfcomm_connected
# ---------------------------------------------------------------------------

class TestRfcommConnected:
    def test_publishes_hostapd_starting_before_start(self):
        manager = _make_manager()
        _MockAPManager.return_value = manager
        _MockAPMonitor.return_value = MagicMock()

        hh.on_rfcomm_connected("bluetooth.rfcomm.connected",
                                {"device_address": "AA:BB:CC:DD:EE:FF"})

        starting = _published("hostapd.starting")
        assert len(starting) == 1
        assert starting[0]["ssid"] == hh._DEFAULTS["ssid"]

    def test_happy_path_starts_manager_and_monitor(self):
        manager = _make_manager()
        monitor = MagicMock()
        _MockAPManager.return_value = manager
        _MockAPMonitor.return_value = monitor

        hh.on_rfcomm_connected("bluetooth.rfcomm.connected",
                                {"device_address": "AA:BB:CC:DD:EE:FF"})

        manager.start.assert_called_once()
        monitor.start.assert_called_once()

    def test_manager_start_failure_publishes_failed(self):
        _MockAPManager.return_value = _make_manager(start_ok=False)

        hh.on_rfcomm_connected("bluetooth.rfcomm.connected",
                                {"device_address": "AA:BB:CC:DD:EE:FF"})

        assert len(_published("hostapd.failed")) == 1
        # monitor must NOT be started
        _MockAPMonitor.return_value.start.assert_not_called()

    def test_monitor_created_with_configured_timeout(self):
        manager = _make_manager()
        _MockAPManager.return_value = manager
        _MockAPMonitor.return_value = MagicMock()
        hh._config["monitor_timeout"] = 45

        hh.on_rfcomm_connected("bluetooth.rfcomm.connected",
                                {"device_address": "AA:BB:CC:DD:EE:FF"})

        _, kwargs = _MockAPMonitor.call_args
        assert kwargs["timeout"] == 45.0


# ---------------------------------------------------------------------------
# Tests — system.stop
# ---------------------------------------------------------------------------

class TestSystemStop:
    def test_teardown_stops_monitor_and_manager(self):
        hh._ap_manager = MagicMock()
        hh._ap_monitor = MagicMock()

        hh.on_system_stop("system.stop", {})

        hh._ap_monitor.stop.assert_called_once()
        hh._ap_manager.stop.assert_called_once()
        assert len(_published("hostapd.stopped")) == 1
        hh.bus.stop.assert_called_once()

    def test_teardown_safe_when_no_manager_or_monitor(self):
        # Must not raise when both are None
        hh.on_system_stop("system.stop", {})
        assert len(_published("hostapd.stopped")) == 1


# ---------------------------------------------------------------------------
# Tests — APMonitor callbacks
# ---------------------------------------------------------------------------

class TestMonitorCallbacks:
    def test_on_ap_ready_publishes_hostapd_ready(self):
        params = {"ssid": "AndroidAutoAP", "key": "abc", "bssid": "11:22:33:44:55:66",
                  "interface": "wlan0", "gateway_ip": "192.168.50.1",
                  "security_mode": 8, "ap_type": 1}
        hh._on_ap_ready(params)
        ready = _published("hostapd.ready")
        assert len(ready) == 1
        assert ready[0] == params

    def test_on_ap_failed_publishes_hostapd_failed_and_tears_down(self):
        hh._ap_manager = MagicMock()
        hh._ap_monitor = MagicMock()

        hh._on_ap_failed("AP did not become active within 30s")

        failed = _published("hostapd.failed")
        assert len(failed) == 1
        assert "error" in failed[0]
        hh._ap_manager.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_on_config_loaded_merges_with_defaults(self):
        hh._on_config_loaded({"ssid": "MyAP", "channel": 11})
        assert hh._config["ssid"] == "MyAP"
        assert hh._config["channel"] == 11
        assert hh._config["interface"] == hh._DEFAULTS["interface"]

    def test_on_config_loaded_ignores_unknown_keys(self):
        hh._on_config_loaded({"nonexistent": True})
        assert "nonexistent" not in hh._config

    def test_on_config_changed_updates_key(self):
        hh._on_config_changed("gateway_ip", "10.0.0.1")
        assert hh._config["gateway_ip"] == "10.0.0.1"

    def test_on_config_changed_ignores_unknown_key(self):
        original = dict(hh._config)
        hh._on_config_changed("unknown", "x")
        assert hh._config == original


# ---------------------------------------------------------------------------
# Tests — _build_ap_config
# ---------------------------------------------------------------------------

class TestBuildAPConfig:
    def test_maps_all_config_keys_to_apconfig(self):
        hh._config.update({
            "interface":        "wlan1",
            "ssid":             "TestNet",
            "ap_password":      "s3cr3t",
            "channel":          11,
            "subnet":           "10.0.0",
            "gateway_ip":       "10.0.0.1",
            "dhcp_range_start": "10.0.0.10",
            "dhcp_range_end":   "10.0.0.50",
        })
        cfg = hh._build_ap_config()
        assert cfg.interface        == "wlan1"
        assert cfg.ssid             == "TestNet"
        assert cfg.key              == "s3cr3t"
        assert cfg.channel          == 11
        assert cfg.subnet           == "10.0.0"
        assert cfg.gateway_ip       == "10.0.0.1"
        assert cfg.dhcp_range_start == "10.0.0.10"
        assert cfg.dhcp_range_end   == "10.0.0.50"

    def test_empty_ap_password_maps_to_empty_key(self):
        hh._config["ap_password"] = ""
        cfg = hh._build_ap_config()
        assert cfg.key == ""
