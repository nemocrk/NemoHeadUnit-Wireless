"""
test_ap_manager.py — Unit tests for hostapd_helper/ap_manager.py

All subprocess calls are mocked — no hostapd, dnsmasq, or root required.
"""

import subprocess
from unittest.mock import patch, MagicMock, mock_open
import pytest

from hostapd_helper.ap_manager import (
    APManager, APConfig, WPA2_SECURITY_MODE, AP_TYPE_DYNAMIC
)

# GATEWAY_IP is no longer a module constant; it is a field on APConfig
_DEFAULT_GATEWAY_IP = APConfig().gateway_ip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(ssid="TestSSID", key="testkey123", interface="wlan0") -> APManager:
    cfg = APConfig(ssid=ssid, key=key, interface=interface)
    return APManager(cfg)


def _mock_popen(returncode=None) -> MagicMock:
    proc = MagicMock()
    proc.poll.return_value = returncode
    proc.pid = 1234
    return proc


# ---------------------------------------------------------------------------
# APConfig defaults
# ---------------------------------------------------------------------------

class TestAPConfig:
    def test_defaults(self):
        cfg = APConfig()
        assert cfg.interface == "wlan0"
        assert cfg.ssid == "AndroidAutoAP"
        assert cfg.key == ""
        assert cfg.channel == 6


# ---------------------------------------------------------------------------
# get_params
# ---------------------------------------------------------------------------

class TestGetParams:
    def test_returns_all_required_keys(self):
        mgr = _make_manager()
        mgr._bssid = "AA:BB:CC:DD:EE:FF"
        params = mgr.get_params()
        for key in ["ssid", "key", "bssid", "interface", "gateway_ip",
                    "security_mode", "ap_type"]:
            assert key in params

    def test_security_mode_is_wpa2(self):
        mgr = _make_manager()
        assert mgr.get_params()["security_mode"] == WPA2_SECURITY_MODE

    def test_ap_type_is_dynamic(self):
        mgr = _make_manager()
        assert mgr.get_params()["ap_type"] == AP_TYPE_DYNAMIC

    def test_gateway_ip_matches_config(self):
        mgr = _make_manager()
        assert mgr.get_params()["gateway_ip"] == mgr._cfg.gateway_ip

    def test_default_gateway_ip_value(self):
        mgr = _make_manager()
        assert mgr.get_params()["gateway_ip"] == _DEFAULT_GATEWAY_IP


# ---------------------------------------------------------------------------
# is_running
# ---------------------------------------------------------------------------

class TestIsRunning:
    def test_both_running(self):
        mgr = _make_manager()
        mgr._hostapd_proc = _mock_popen(None)
        mgr._dnsmasq_proc = _mock_popen(None)
        assert mgr.is_running() is True

    def test_hostapd_dead(self):
        mgr = _make_manager()
        mgr._hostapd_proc = _mock_popen(1)
        mgr._dnsmasq_proc = _mock_popen(None)
        assert mgr.is_running() is False

    def test_dnsmasq_dead(self):
        mgr = _make_manager()
        mgr._hostapd_proc = _mock_popen(None)
        mgr._dnsmasq_proc = _mock_popen(1)
        assert mgr.is_running() is False

    def test_neither_started(self):
        mgr = _make_manager()
        assert mgr.is_running() is False


# ---------------------------------------------------------------------------
# _generate_key
# ---------------------------------------------------------------------------

class TestGenerateKey:
    def test_default_length(self):
        key = APManager._generate_key()
        assert len(key) == 12

    def test_custom_length(self):
        key = APManager._generate_key(24)
        assert len(key) == 24

    def test_alphanumeric_only(self):
        key = APManager._generate_key(100)
        assert key.isalnum()

    def test_keys_are_random(self):
        keys = {APManager._generate_key() for _ in range(20)}
        assert len(keys) > 1


# ---------------------------------------------------------------------------
# start / stop (mocked subprocess)
# ---------------------------------------------------------------------------

class TestStartStop:
    @patch("hostapd_helper.ap_manager.subprocess.Popen")
    @patch("hostapd_helper.ap_manager.subprocess.run")
    @patch("builtins.open", mock_open(read_data="aa:bb:cc:dd:ee:ff"))
    def test_start_returns_true_on_success(self, mock_run, mock_popen):
        mock_run.return_value = MagicMock(returncode=0)
        mock_popen.return_value = _mock_popen(None)

        mgr = _make_manager(key="existingkey")
        result = mgr.start()

        assert result is True
        assert mgr._hostapd_proc is not None
        assert mgr._dnsmasq_proc is not None
        mgr.stop()

    @patch("hostapd_helper.ap_manager.subprocess.Popen")
    @patch("hostapd_helper.ap_manager.subprocess.run")
    @patch("builtins.open", mock_open(read_data="aa:bb:cc:dd:ee:ff"))
    def test_key_auto_generated_when_empty(self, mock_run, mock_popen):
        mock_run.return_value = MagicMock(returncode=0)
        mock_popen.return_value = _mock_popen(None)

        mgr = _make_manager(key="")
        mgr.start()

        assert mgr._cfg.key != ""
        assert len(mgr._cfg.key) == 12
        mgr.stop()

    @patch("hostapd_helper.ap_manager.subprocess.Popen")
    @patch("hostapd_helper.ap_manager.subprocess.run")
    @patch("builtins.open", mock_open(read_data="aa:bb:cc:dd:ee:ff"))
    def test_stop_clears_procs(self, mock_run, mock_popen):
        proc = _mock_popen(None)
        mock_popen.return_value = proc
        mock_run.return_value = MagicMock(returncode=0)

        mgr = _make_manager(key="key")
        mgr.start()
        mgr.stop()

        assert mgr._hostapd_proc is None
        assert mgr._dnsmasq_proc is None

    @patch("hostapd_helper.ap_manager.subprocess.run",
           side_effect=subprocess.CalledProcessError(1, "ip"))
    @patch("builtins.open", mock_open(read_data="aa:bb:cc:dd:ee:ff"))
    def test_start_fails_on_interface_error(self, mock_run):
        mgr = _make_manager(key="key")
        result = mgr.start()
        assert result is False
