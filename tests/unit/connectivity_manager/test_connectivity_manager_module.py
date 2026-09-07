import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.connectivity_manager.main import ConnectivityManagerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_conn_module():
    with patch("shared.base_module.BusClient"),          patch("modules.connectivity_manager.main.get_bluetooth_adapter") as mock_bt_fac,          patch("modules.connectivity_manager.main.get_wifi_adapter") as mock_wifi_fac,          patch("modules.connectivity_manager.main.get_audio_adapter") as mock_audio_fac,          patch("modules.connectivity_manager.main.BlueZHFClient"),          patch("modules.connectivity_manager.main.BlueZPBAPClient"):

        mock_bt = MagicMock()
        mock_bt.setup = AsyncMock()
        mock_bt.get_adapter_address.return_value = "00:11:22:33:44:55"
        mock_bt.get_paired_devices = AsyncMock(return_value=[{"address": "AA:BB:CC:11:22:33", "name": "Phone"}])
        mock_bt.start_discovery = AsyncMock()
        mock_bt.stop_discovery = AsyncMock()
        mock_bt.pair_device = AsyncMock(return_value=(True, "Pairing initiated"))
        mock_bt.confirm_pairing = AsyncMock(return_value=True)
        mock_bt.connect_device = AsyncMock(return_value=(True, "Connected"))
        mock_bt.disconnect_device = AsyncMock(return_value=True)
        mock_bt.remove_paired_device = AsyncMock(return_value=True)
        mock_bt_fac.return_value = mock_bt

        mock_wifi = MagicMock()
        mock_wifi.setup = AsyncMock()
        mock_wifi.start_ap = AsyncMock(return_value=(True, {"ssid": "AndroidAutoAP", "key": "12345678", "bssid": "00:11:22:33:44:55", "gateway_ip": "192.168.50.1"}))
        mock_wifi.stop_ap = AsyncMock(return_value=True)
        mock_wifi.get_status.return_value = {"active": True, "ssid": "AndroidAutoAP"}
        mock_wifi_fac.return_value = mock_wifi

        mod = ConnectivityManagerModule()
        mod._bt_adapter = mock_bt
        mod._wifi_adapter = mock_wifi
        yield mod


def test_connectivity_config_and_schema(mock_conn_module):
    defaults = mock_conn_module.get_default_config()
    assert defaults["adapter_name"] == "NemoHeadUnit"
    assert defaults["wifi_ssid"] == "AndroidAutoAP"
    assert defaults["autoconnect_enabled"] is True

    schema = mock_conn_module.get_schema()
    assert "wifi_channel" in schema
    assert "autoconnect_backoff_cap_s" in schema


@pytest.mark.asyncio
async def test_handle_get_status_and_paired(mock_conn_module):
    # GET /status
    req = MagicMock()
    resp = await mock_conn_module.handle_get_status(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert data["adapter_name"] == "NemoHeadUnit"
    assert data["bt_address"] == "00:11:22:33:44:55"
    assert data["wifi_active"] is True

    # GET /paired
    resp_paired = await mock_conn_module.handle_get_paired(req)
    assert resp_paired.status == 200
    data_paired = json.loads(resp_paired.text)
    assert len(data_paired["devices"]) == 1
    assert data_paired["devices"][0]["name"] == "Phone"


@pytest.mark.asyncio
async def test_handle_bluetooth_discovery_endpoints(mock_conn_module):
    # POST /discover
    req = MagicMock()
    req.json = AsyncMock(return_value={"duration_sec": 5})
    resp = await mock_conn_module.handle_post_discover(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "discovery_started"
    mock_conn_module._bt_adapter.start_discovery.assert_called_once()

    # GET /discovered
    mock_conn_module._discovered_devices = [{"address": "99:88:77:66:55:44", "name": "DiscoveredPhone"}]
    resp_disc = await mock_conn_module.handle_get_discovered(req)
    assert resp_disc.status == 200
    data_disc = json.loads(resp_disc.text)
    assert len(data_disc["devices"]) == 1


@pytest.mark.asyncio
async def test_handle_pairing_and_connect_endpoints(mock_conn_module):
    # POST /pair
    req_pair = MagicMock()
    req_pair.json = AsyncMock(return_value={"address": "AA:BB:CC:11:22:33"})
    resp_pair = await mock_conn_module.handle_post_pair(req_pair)
    assert resp_pair.status == 200
    mock_conn_module._bt_adapter.pair_device.assert_called_once()

    # POST /pair/confirm
    req_conf = MagicMock()
    req_conf.json = AsyncMock(return_value={"address": "AA:BB:CC:11:22:33", "confirm": True})
    resp_conf = await mock_conn_module.handle_post_pair_confirm(req_conf)
    assert resp_conf.status == 200
    mock_conn_module._bt_adapter.confirm_pairing.assert_called_once_with("AA:BB:CC:11:22:33", True)

    # POST /connect
    req_conn = MagicMock()
    req_conn.json = AsyncMock(return_value={"address": "AA:BB:CC:11:22:33"})
    resp_conn = await mock_conn_module.handle_post_connect(req_conn)
    assert resp_conn.status == 200
    mock_conn_module._bt_adapter.connect_device.assert_called_once_with("AA:BB:CC:11:22:33")

    # POST /disconnect
    resp_disc = await mock_conn_module.handle_post_disconnect(req_conn)
    assert resp_disc.status == 200
    mock_conn_module._bt_adapter.disconnect_device.assert_called_once_with("AA:BB:CC:11:22:33")


@pytest.mark.asyncio
async def test_handle_wifi_manual_controls_and_device_filter(mock_conn_module):
    # POST /wifi/start
    req = MagicMock()
    resp_wstart = await mock_conn_module.handle_wifi_start(req)
    assert resp_wstart.status == 200
    mock_conn_module._wifi_adapter.start_ap.assert_called_once()

    # POST /wifi/stop
    resp_wstop = await mock_conn_module.handle_wifi_stop(req)
    assert resp_wstop.status == 200
    mock_conn_module._wifi_adapter.stop_ap.assert_called_once()

    # POST /devices/ignore and unignore
    req_ignore = MagicMock()
    req_ignore.json = AsyncMock(return_value={"address": "XX:YY:ZZ:11:22:33"})
    resp_ign = await mock_conn_module.handle_post_ignore_device(req_ignore)
    assert resp_ign.status == 200
    assert "XX:YY:ZZ:11:22:33" in mock_conn_module.config.get("ignored_devices", [])

    resp_unign = await mock_conn_module.handle_post_unignore_device(req_ignore)
    assert resp_unign.status == 200
    assert "XX:YY:ZZ:11:22:33" not in mock_conn_module.config.get("ignored_devices", [])
