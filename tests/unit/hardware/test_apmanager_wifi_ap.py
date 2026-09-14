"""
test_apmanager_wifi_ap.py — Unit tests for APManagerWifiApAdapter (shared.hardware.apmanager_wifi_ap)
"""

import asyncio
import sys
from unittest.mock import MagicMock, patch
import pytest
pytest.importorskip("dbus")

from shared.hardware.apmanager_wifi_ap import APManagerWifiApAdapter

pytestmark = [pytest.mark.unit, pytest.mark.dbus, pytest.mark.hardware]


@pytest.mark.asyncio
async def test_apmanager_on_ap_started():
    adapter = APManagerWifiApAdapter()
    adapter._loop = asyncio.get_running_loop()

    config_dict = {
        "ssid": "TestHotspot",
        "key": "secret123",
        "bssid": "00:11:22:33:44:55",
        "interface": "wlan0",
        "gateway_ip": "192.168.43.1",
        "security_mode": 8,
        "ap_type": 1,
        "mode": "ap",
    }

    adapter._on_ap_started(config_dict)
    await asyncio.sleep(0)
    assert adapter._active is True
    assert adapter._started_credentials["ssid"] == "TestHotspot"
    assert adapter._started_credentials["key"] == "secret123"
    assert adapter._ready_event.is_set()


@pytest.mark.asyncio
async def test_apmanager_on_ap_failed():
    adapter = APManagerWifiApAdapter()
    adapter._loop = asyncio.get_running_loop()
    adapter._active = True

    adapter._on_ap_failed("Interface busy")
    await asyncio.sleep(0)
    assert adapter._active is False
    assert adapter._started_credentials is None
    assert adapter._ready_event.is_set()


def test_apmanager_fetch_running_status():
    adapter = APManagerWifiApAdapter()
    mock_proxy = MagicMock()
    mock_proxy.Status.return_value = (
        1, "MySSID", "AA:BB:CC:DD:EE:FF", "10.0.0.1", "gen_key123", []
    )
    adapter._proxy = mock_proxy

    success, creds = adapter._fetch_running_status({"interface": "wlan0"})
    assert success is True
    assert adapter._active is True
    assert creds["ssid"] == "MySSID"
    assert creds["gateway_ip"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_apmanager_start_and_stop_ap():
    adapter = APManagerWifiApAdapter()
    adapter._loop = asyncio.get_running_loop()
    mock_proxy = MagicMock()
    mock_proxy.Start.return_value = (True, "OK")
    mock_proxy.Stop.return_value = (True, "OK")
    adapter._proxy = mock_proxy

    # Simulate APStarted signal triggering during start_ap wait
    async def _emit_started():
        await asyncio.sleep(0.02)
        adapter._on_ap_started({
            "ssid": "AutoAP",
            "key": "pass123",
            "bssid": "11:22:33:44:55:66",
            "gateway_ip": "10.0.0.1"
        })

    asyncio.create_task(_emit_started())
    success, creds = await adapter.start_ap({"ssid": "AutoAP"})
    assert success is True
    assert creds["ssid"] == "AutoAP"

    # Stop AP
    stopped = await adapter.stop_ap()
    assert stopped is True
    assert adapter._active is False
    mock_proxy.Stop.assert_called_once()


@pytest.mark.asyncio
async def test_apmanager_setup_lifecycle():
    adapter = APManagerWifiApAdapter()
    mock_bus = MagicMock()
    mock_proxy = MagicMock()
    mock_proxy.Status.return_value = (0, "", "", "", "", [])

    def iface_mock(obj, iface_name):
        return mock_proxy

    mock_glib = MagicMock()
    with patch("dbus.SystemBus", return_value=mock_bus), \
         patch("dbus.Interface", side_effect=iface_mock), \
         patch("dbus.mainloop.glib.DBusGMainLoop"), \
         patch.dict(sys.modules, {"gi": MagicMock(), "gi.repository": MagicMock(), "gi.repository.GLib": mock_glib}):
        await adapter.setup()
        assert adapter._bus == mock_bus
        assert adapter._proxy == mock_proxy
        mock_bus.add_signal_receiver.assert_called()


def test_apmanager_get_station_rssi():
    adapter = APManagerWifiApAdapter()
    # Not active -> None
    assert adapter.get_station_rssi() is None

    adapter._active = True
    adapter._started_credentials = {"interface": "wlan0"}

    # Mock subprocess output for various RSSI values
    with patch("subprocess.check_output") as mock_sub:
        mock_sub.return_value = "Station 11:22:33:44:55:66 (on wlan0)\n  signal: -55 dBm\n"
        assert adapter.get_station_rssi() == 5

        mock_sub.return_value = "Station 11:22:33:44:55:66 (on wlan0)\n  signal: -68 dBm\n"
        assert adapter.get_station_rssi() == 4

        mock_sub.return_value = "Station 11:22:33:44:55:66 (on wlan0)\n  signal: -75 dBm\n"
        assert adapter.get_station_rssi() == 3

        mock_sub.return_value = "Station 11:22:33:44:55:66 (on wlan0)\n  signal: -85 dBm\n"
        assert adapter.get_station_rssi() == 2

        mock_sub.return_value = "Station 11:22:33:44:55:66 (on wlan0)\n  signal: -95 dBm\n"
        assert adapter.get_station_rssi() == 1

        mock_sub.side_effect = Exception("iw error")
        assert adapter.get_station_rssi() is None


@pytest.mark.asyncio
async def test_apmanager_teardown():
    adapter = APManagerWifiApAdapter()
    adapter.stop_ap = MagicMock(return_value=asyncio.sleep(0, result=True))
    mock_glib = MagicMock()
    mock_glib.is_running.return_value = True
    adapter._glib_loop = mock_glib

    await adapter.teardown()
    mock_glib.quit.assert_called_once()

