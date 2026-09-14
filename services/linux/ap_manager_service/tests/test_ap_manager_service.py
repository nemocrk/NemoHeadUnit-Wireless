"""
Unit tests for ap_manager_service.py — join-network mode.

Covers:
  - _detect_existing_wifi: happy path, no active, enterprise security,
    open network, nmcli error, malformed output
  - _get_iface_ip: happy path, not found, ip error
  - _get_wifi_psk: happy path, missing psk, nmcli error
  - _APRunner.start(): join-network route, AP route, fallback (no IP,
    no PSK)
  - _APRunner.stop(): join-network (no teardown), AP mode (full teardown)
  - _APRunner.is_running(): per mode
  - _APRunner.get_params() / get_key() / get_mode()
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch, call

import sys
import os

# ---------------------------------------------------------------------------
# Import target module without triggering dbus at module level.
# dbus is only needed at runtime (APManagerService); helpers are pure-Python.
# ---------------------------------------------------------------------------

# Stub heavy runtime deps so the import succeeds in CI without D-Bus
for mod in ("dbus", "dbus.service", "dbus.mainloop.glib", "gi", "gi.repository", "gi.repository.GLib"):
    sys.modules.setdefault(mod, MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ap_manager_service as svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completed(stdout="", stderr="", returncode=0):
    """Build a subprocess.CompletedProcess-like mock."""
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout     = stdout
    r.stderr     = stderr
    r.returncode = returncode
    return r


NMCLI_ACTIVE_LINE = "yes:HomeNetwork:AA\\:BB\\:CC\\:DD\\:EE\\:FF:wlan0:WPA2:wifi"
NMCLI_INACTIVE_LINE = "no:OtherNet:11\\:22\\:33\\:44\\:55\\:66:wlan0:WPA2:wifi"


# ===========================================================================
# _detect_existing_wifi
# ===========================================================================

class TestDetectExistingWifi(unittest.TestCase):

    @patch("ap_manager_service._run")
    def test_happy_path_returns_dict(self, mock_run):
        mock_run.return_value = _completed(stdout=NMCLI_ACTIVE_LINE + "\n")
        result = svc._detect_existing_wifi()
        self.assertIsNotNone(result)
        self.assertEqual(result["ssid"],      "HomeNetwork")
        self.assertEqual(result["bssid"],     "AA:BB:CC:DD:EE:FF")
        self.assertEqual(result["interface"], "wlan0")
        self.assertEqual(result["security"],  "WPA2")

    @patch("ap_manager_service._run")
    def test_no_active_returns_none(self, mock_run):
        mock_run.return_value = _completed(stdout=NMCLI_INACTIVE_LINE + "\n")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_enterprise_security_skipped(self, mock_run):
        line = "yes:CorpNet:AA\\:BB\\:CC\\:DD\\:EE\\:FF:wlan0:WPA2 802.1X:wifi"
        mock_run.return_value = _completed(stdout=line + "\n")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_open_network_skipped(self, mock_run):
        line = "yes:OpenNet:AA\\:BB\\:CC\\:DD\\:EE\\:FF:wlan0:--:wifi"
        mock_run.return_value = _completed(stdout=line + "\n")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_nmcli_nonzero_returns_none(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="Error")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_nmcli_exception_returns_none(self, mock_run):
        mock_run.side_effect = Exception("timeout")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_empty_output_returns_none(self, mock_run):
        mock_run.return_value = _completed(stdout="")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_malformed_line_skipped(self, mock_run):
        mock_run.return_value = _completed(stdout="yes:onlythreefields:woops\n")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_non_wifi_type_skipped(self, mock_run):
        line = "yes:EthNet:AA\\:BB\\:CC\\:DD\\:EE\\:FF:eth0:WPA2:ethernet"
        mock_run.return_value = _completed(stdout=line + "\n")
        result = svc._detect_existing_wifi()
        self.assertIsNone(result)

    @patch("ap_manager_service._run")
    def test_first_active_wins(self, mock_run):
        two_lines = (
            NMCLI_INACTIVE_LINE + "\n"
            "yes:SecondNet:11\\:22\\:33\\:44\\:55\\:66:wlan1:WPA3:wifi\n"
        )
        mock_run.return_value = _completed(stdout=two_lines)
        result = svc._detect_existing_wifi()
        self.assertIsNotNone(result)
        self.assertEqual(result["ssid"], "SecondNet")


# ===========================================================================
# _get_iface_ip
# ===========================================================================

class TestGetIfaceIp(unittest.TestCase):

    @patch("ap_manager_service._run")
    def test_happy_path(self, mock_run):
        out = (
            "2: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP>\n"
            "    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0\n"
        )
        mock_run.return_value = _completed(stdout=out)
        self.assertEqual(svc._get_iface_ip("wlan0"), "192.168.1.50")

    @patch("ap_manager_service._run")
    def test_no_inet_line_returns_none(self, mock_run):
        mock_run.return_value = _completed(stdout="2: wlan0: <...>\n")
        self.assertIsNone(svc._get_iface_ip("wlan0"))

    @patch("ap_manager_service._run")
    def test_nonzero_returncode_returns_none(self, mock_run):
        mock_run.return_value = _completed(returncode=1)
        self.assertIsNone(svc._get_iface_ip("wlan0"))

    @patch("ap_manager_service._run")
    def test_exception_returns_none(self, mock_run):
        mock_run.side_effect = Exception("fail")
        self.assertIsNone(svc._get_iface_ip("wlan0"))


# ===========================================================================
# _get_wifi_psk
# ===========================================================================

class TestGetWifiPsk(unittest.TestCase):

    @patch("ap_manager_service._run")
    def test_happy_path(self, mock_run):
        out = "802-11-wireless-security.psk:MySecret123\n"
        mock_run.return_value = _completed(stdout=out)
        self.assertEqual(svc._get_wifi_psk("HomeNetwork"), "MySecret123")

    @patch("ap_manager_service._run")
    def test_psk_placeholder_returns_none(self, mock_run):
        out = "802-11-wireless-security.psk:--\n"
        mock_run.return_value = _completed(stdout=out)
        self.assertIsNone(svc._get_wifi_psk("HomeNetwork"))

    @patch("ap_manager_service._run")
    def test_no_psk_line_returns_none(self, mock_run):
        mock_run.return_value = _completed(stdout="some-other-field:value\n")
        self.assertIsNone(svc._get_wifi_psk("HomeNetwork"))

    @patch("ap_manager_service._run")
    def test_nonzero_returncode_returns_none(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="Error")
        self.assertIsNone(svc._get_wifi_psk("HomeNetwork"))

    @patch("ap_manager_service._run")
    def test_exception_returns_none(self, mock_run):
        mock_run.side_effect = Exception("fail")
        self.assertIsNone(svc._get_wifi_psk("HomeNetwork"))


# ===========================================================================
# _APRunner — join-network mode
# ===========================================================================

class TestAPRunnerJoinNetworkMode(unittest.TestCase):

    def _runner_with_join(self, ssid="HomeNetwork", bssid="AA:BB:CC:DD:EE:FF",
                          iface="wlan0", hu_ip="192.168.1.50", psk="MySecret"):
        wifi_info = {"ssid": ssid, "bssid": bssid, "interface": iface, "security": "WPA2"}
        runner = svc._APRunner()
        with patch("ap_manager_service._detect_existing_wifi", return_value=wifi_info), \
             patch("ap_manager_service._get_iface_ip",        return_value=hu_ip), \
             patch("ap_manager_service._get_wifi_psk",        return_value=psk):
            runner.start(svc.APConfig())
        return runner

    def test_mode_is_join(self):
        self.assertEqual(self._runner_with_join().get_mode(), "join")

    def test_is_running_true(self):
        self.assertTrue(self._runner_with_join().is_running())

    def test_ssid_from_existing_network(self):
        runner = self._runner_with_join(ssid="HomeNetwork")
        self.assertEqual(runner.get_params()["ssid"], "HomeNetwork")

    def test_bssid_from_existing_network(self):
        runner = self._runner_with_join(bssid="AA:BB:CC:DD:EE:FF")
        self.assertEqual(runner.get_params()["bssid"], "AA:BB:CC:DD:EE:FF")

    def test_gateway_ip_is_hu_ip(self):
        runner = self._runner_with_join(hu_ip="192.168.1.50")
        self.assertEqual(runner.get_params()["gateway_ip"], "192.168.1.50")

    def test_key_is_psk(self):
        runner = self._runner_with_join(psk="MySecret")
        self.assertEqual(runner.get_key(), "MySecret")

    def test_stop_does_not_touch_interface(self):
        runner = self._runner_with_join()
        with patch("ap_manager_service._run") as mock_run, \
             patch("ap_manager_service._kill_proc") as mock_kill:
            runner.stop()
            mock_run.assert_not_called()
            mock_kill.assert_not_called()

    def test_stop_clears_state(self):
        runner = self._runner_with_join()
        with patch("ap_manager_service._run"), patch("ap_manager_service._kill_proc"):
            runner.stop()
        self.assertFalse(runner.is_running())
        self.assertIsNone(runner.get_mode())

    def test_no_daemons_started(self):
        """In join-network mode, hostapd and dnsmasq must never be launched."""
        wifi_info = {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:DD:EE:FF",
                     "interface": "wlan0", "security": "WPA2"}
        runner = svc._APRunner()
        with patch("ap_manager_service._detect_existing_wifi", return_value=wifi_info), \
             patch("ap_manager_service._get_iface_ip",        return_value="192.168.1.50"), \
             patch("ap_manager_service._get_wifi_psk",        return_value="secret"), \
             patch("subprocess.Popen") as mock_popen:
            runner.start(svc.APConfig())
            mock_popen.assert_not_called()


# ===========================================================================
# _APRunner — fallback to AP mode
# ===========================================================================

class TestAPRunnerFallbackToAP(unittest.TestCase):

    def _make_popen(self):
        proc = MagicMock()
        proc.pid  = 1234
        proc.poll = MagicMock(return_value=None)
        return proc

    @patch("ap_manager_service._run", return_value=_completed())
    @patch("subprocess.Popen")
    @patch("ap_manager_service._get_wifi_psk", return_value=None)
    @patch("ap_manager_service._get_iface_ip", return_value="192.168.1.50")
    @patch("ap_manager_service._detect_existing_wifi")
    @patch("ap_manager_service.time")
    def test_fallback_when_no_psk(self, mock_time, mock_detect, mock_ip,
                                   mock_psk, mock_popen, mock_run):
        mock_detect.return_value = {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:DD:EE:FF",
                                    "interface": "wlan0", "security": "WPA2"}
        mock_popen.return_value = self._make_popen()
        runner = svc._APRunner()
        runner.start(svc.APConfig())
        self.assertEqual(runner.get_mode(), "ap")

    @patch("ap_manager_service._run", return_value=_completed())
    @patch("subprocess.Popen")
    @patch("ap_manager_service._get_wifi_psk", return_value="secret")
    @patch("ap_manager_service._get_iface_ip", return_value=None)
    @patch("ap_manager_service._detect_existing_wifi")
    @patch("ap_manager_service.time")
    def test_fallback_when_no_ip(self, mock_time, mock_detect, mock_ip,
                                  mock_psk, mock_popen, mock_run):
        mock_detect.return_value = {"ssid": "HomeNetwork", "bssid": "AA:BB:CC:DD:EE:FF",
                                    "interface": "wlan0", "security": "WPA2"}
        mock_popen.return_value = self._make_popen()
        runner = svc._APRunner()
        runner.start(svc.APConfig())
        self.assertEqual(runner.get_mode(), "ap")

    @patch("ap_manager_service._run", return_value=_completed())
    @patch("subprocess.Popen")
    @patch("ap_manager_service._detect_existing_wifi", return_value=None)
    @patch("ap_manager_service.time")
    def test_ap_mode_when_no_existing_wifi(self, mock_time, mock_detect,
                                            mock_popen, mock_run):
        mock_popen.return_value = self._make_popen()
        runner = svc._APRunner()
        runner.start(svc.APConfig())
        self.assertEqual(runner.get_mode(), "ap")


# ===========================================================================
# _APRunner — AP mode stop (full teardown)
# ===========================================================================

class TestAPRunnerAPModeStop(unittest.TestCase):

    def _runner_in_ap_mode(self):
        """Return an _APRunner pre-set in AP mode with mock procs."""
        runner        = svc._APRunner()
        runner._mode  = "ap"
        runner._cfg   = svc.APConfig(interface="wlan0")
        runner._bssid = "AA:BB:CC:DD:EE:FF"

        proc          = MagicMock()
        proc.poll     = MagicMock(return_value=None)
        runner._hostapd_proc = proc
        runner._dnsmasq_proc = proc
        runner._hostapd_conf = "/tmp/hostapd_test.conf"
        runner._dnsmasq_conf = "/tmp/dnsmasq_test.conf"
        return runner

    def test_stop_calls_kill_proc(self):
        runner = self._runner_in_ap_mode()
        with patch("ap_manager_service._kill_proc") as mock_kill, \
             patch("ap_manager_service._cleanup_file"), \
             patch("ap_manager_service._run"), \
             patch("ap_manager_service.time"):
            runner.stop()
            self.assertEqual(mock_kill.call_count, 2)

    def test_stop_calls_nm_reconnect(self):
        runner = self._runner_in_ap_mode()
        with patch("ap_manager_service._kill_proc"), \
             patch("ap_manager_service._cleanup_file"), \
             patch("ap_manager_service._run") as mock_run, \
             patch("ap_manager_service.time"):
            runner.stop()
            # systemctl restart NetworkManager must be called somewhere
            calls = [str(c) for c in mock_run.call_args_list]
            self.assertTrue(
                any("NetworkManager" in c for c in calls),
                msg="Expected NM restart in AP-mode stop",
            )

    def test_stop_clears_state(self):
        runner = self._runner_in_ap_mode()
        with patch("ap_manager_service._kill_proc"), \
             patch("ap_manager_service._cleanup_file"), \
             patch("ap_manager_service._run"), \
             patch("ap_manager_service.time"):
            runner.stop()
        self.assertIsNone(runner.get_mode())
        self.assertIsNone(runner._cfg)


# ===========================================================================
# _APRunner — is_running edge cases
# ===========================================================================

class TestAPRunnerIsRunning(unittest.TestCase):

    def test_not_running_after_init(self):
        self.assertFalse(svc._APRunner().is_running())

    def test_join_mode_running_while_cfg_set(self):
        runner = svc._APRunner()
        runner._mode = "join"
        runner._cfg  = svc.APConfig()
        self.assertTrue(runner.is_running())

    def test_join_mode_not_running_after_cfg_cleared(self):
        runner = svc._APRunner()
        runner._mode = "join"
        runner._cfg  = None
        self.assertFalse(runner.is_running())

    def test_ap_mode_running_when_both_procs_alive(self):
        runner = svc._APRunner()
        runner._mode = "ap"
        proc = MagicMock()
        proc.poll = MagicMock(return_value=None)
        runner._hostapd_proc = proc
        runner._dnsmasq_proc = proc
        self.assertTrue(runner.is_running())

    def test_ap_mode_not_running_when_proc_exited(self):
        runner = svc._APRunner()
        runner._mode = "ap"
        proc_dead = MagicMock()
        proc_dead.poll = MagicMock(return_value=1)
        proc_alive = MagicMock()
        proc_alive.poll = MagicMock(return_value=None)
        runner._hostapd_proc = proc_alive
        runner._dnsmasq_proc = proc_dead
        self.assertFalse(runner.is_running())

    def test_force_ap_bypasses_join_network(self):
        runner = svc._APRunner()
        cfg = svc.APConfig(force_ap=True)
        with patch("ap_manager_service._detect_existing_wifi") as mock_detect, \
             patch.object(runner, "_start_ap") as mock_start_ap, \
             patch.object(runner, "_start_join_network") as mock_start_join:
            runner.start(cfg)
            mock_detect.assert_not_called()
            mock_start_join.assert_not_called()
            mock_start_ap.assert_called_once_with(cfg)

    def test_get_params_join_mode_includes_key_and_static_ap_type(self):
        runner = svc._APRunner()
        runner._mode = "join"
        runner._bssid = "11:22:33:44:55:66"
        runner._cfg = svc.APConfig(ssid="HomeNet", key="secret123", gateway_ip="192.168.1.100")
        params = runner.get_params()
        self.assertEqual(params["ssid"], "HomeNet")
        self.assertEqual(params["key"], "secret123")
        self.assertEqual(params["gateway_ip"], "192.168.1.100")
        self.assertEqual(params["ap_type"], svc.AP_TYPE_STATIC)
        self.assertEqual(params["mode"], "join")


if __name__ == "__main__":
    unittest.main()

