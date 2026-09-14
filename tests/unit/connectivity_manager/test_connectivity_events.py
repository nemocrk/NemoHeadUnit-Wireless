import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.connectivity_manager.main import ConnectivityManagerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_conn_events():
    with patch("shared.base_module.BusClient"), \
         patch("modules.connectivity_manager.main.get_bluetooth_adapter") as mock_bt_fac, \
         patch("modules.connectivity_manager.main.get_wifi_adapter") as mock_wifi_fac, \
         patch("modules.connectivity_manager.main.get_audio_adapter") as mock_audio_fac, \
         patch("modules.connectivity_manager.main.BlueZHFClient"), \
         patch("modules.connectivity_manager.main.BlueZPBAPClient"):

        mock_bt = MagicMock()
        mock_bt.get_paired_devices = AsyncMock()
        mock_bt.connect_device = AsyncMock(return_value=(True, "Connected"))
        mock_bt.get_device_name.return_value = "TestPhone"
        mock_bt_fac.return_value = mock_bt

        mock_wifi = MagicMock()
        mock_wifi.start_ap = AsyncMock(return_value=(True, {"ssid": "AP", "key": "pw", "bssid": "00:11:22:33:44:55", "gateway_ip": "192.168.50.1"}))
        mock_wifi_fac.return_value = mock_wifi

        mock_audio = MagicMock()
        mock_audio.ensure_hfp_loopback = AsyncMock()
        mock_audio_fac.return_value = mock_audio

        mod = ConnectivityManagerModule()
        mod._bt_adapter = mock_bt
        mod._wifi_adapter = mock_wifi
        mod._audio_adapter = mock_audio
        mod.publish = MagicMock()
        yield mod


@pytest.mark.asyncio
async def test_autoconnect_loop_priority_and_filtering(mock_conn_events):
    mock_conn_events._running = True
    mock_conn_events.config["known_aa_devices"] = ["AA:AA:AA:AA:AA:AA"]
    mock_conn_events.config["ignored_devices"] = ["CC:CC:CC:CC:CC:CC"]

    # 3 devices: 1 known AA, 1 regular, 1 ignored
    mock_conn_events._bt_adapter.get_paired_devices.return_value = [
        {"address": "BB:BB:BB:BB:BB:BB", "name": "RegularPhone", "connected": False},
        {"address": "CC:CC:CC:CC:CC:CC", "name": "IgnoredSpeaker", "connected": False},
        {"address": "AA:AA:AA:AA:AA:AA", "name": "AndroidAutoPhone", "connected": False},
    ]

    # Run autoconnect loop for one iteration then cancel
    task = asyncio.create_task(mock_conn_events._autoconnect_loop())
    mock_conn_events.on_try_autoconnect("bluetooth_manager.try_autoconnect", {})
    await asyncio.sleep(0.05)
    mock_conn_events._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # AA:AA should be prioritized and connected first
    mock_conn_events._bt_adapter.connect_device.assert_called_with("AA:AA:AA:AA:AA:AA")


@pytest.mark.asyncio
async def test_rfcomm_connection_triggers_wifi_and_handshake(mock_conn_events):
    mock_sock = MagicMock()
    mock_conn_events._running = True
    mock_conn_events._rfcomm_listening = True

    with patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        mock_conn_events._on_rfcomm_connection(mock_sock, "11:22:33:44:55:66")

        # Let the async _start_ap_and_handshake run
        await asyncio.sleep(0.05)

        assert mock_conn_events._rfcomm_connected is True
        assert mock_conn_events._active_device == "11:22:33:44:55:66"
        mock_conn_events.publish.assert_any_call(
            "rfcomm.handshake.started", {"device_address": "11:22:33:44:55:66"}
        )
        mock_conn_events._wifi_adapter.start_ap.assert_called_once()
        mock_thread.start.assert_called_once()


@pytest.mark.asyncio
async def test_hfp_state_changed_telephony_and_audio(mock_conn_events):
    mock_conn_events._active_device = "11:22:33:44:55:66"

    # 1. Inbound call state
    mock_conn_events._on_hfp_state_changed({
        "is_in_call": True,
        "call_state": "INCOMING",
        "caller_name": "Alice",
        "phone_number": "+1234567890",
        "battery_pct": 85,
        "signal_bars": 4,
        "carrier": "Vodafone",
    })

    mock_conn_events.publish.assert_called_with("phone.status", {
        "is_in_call": True,
        "call_state": "INCOMING",
        "caller_name": "Alice",
        "phone_number": "+1234567890",
        "battery_pct": 85,
        "signal_bars": 4,
        "carrier": "Vodafone",
        "device_address": "11:22:33:44:55:66",
        "is_connected": True,
        "device_name": "TestPhone",
        "battery_level": 85,
        "signal_strength": 4,
        "operator_name": "Vodafone",
    })


def test_bluetooth_telemetry_changed_event(mock_conn_events):
    mock_conn_events._on_bluetooth_telemetry_changed(
        address="11:22:33:44:55:66",
        battery_pct=90,
        signal_bars=5,
        operator_name="TIM",
        is_roaming=False,
    )

    mock_conn_events.publish.assert_called_with("phone.status", {
        "source": "bluetooth_hfp",
        "device_address": "11:22:33:44:55:66",
        "device_name": "TestPhone",
        "is_connected": True,
        "operator_name": "TIM",
        "carrier": "TIM",
        "battery_level": 90,
        "battery_pct": 90,
        "signal_strength": 5,
        "signal_bars": 5,
        "is_roaming": False,
    })


def test_pin_requested_and_device_connection_events(mock_conn_events):
    # PIN callback
    mock_conn_events._on_pin_requested("11:22:33:44:55:66", "123456")
    assert mock_conn_events._pairing_pin == "123456"
    assert mock_conn_events._pairing_device == "11:22:33:44:55:66"
    mock_conn_events.publish.assert_called_with("bluetooth_manager.pairing.pin", {
        "device_address": "11:22:33:44:55:66",
        "pin": "123456",
    })

    # Connection changed callback: connected
    mock_conn_events._on_device_connection_changed("11:22:33:44:55:66", True)
    assert mock_conn_events._active_device == "11:22:33:44:55:66"
    mock_conn_events.publish.assert_any_call("bluetooth_manager.paired.connected", {
        "device_address": "11:22:33:44:55:66",
        "device_name": "TestPhone",
    })

    # Connection changed callback: disconnected
    mock_conn_events._on_device_connection_changed("11:22:33:44:55:66", False)
    assert mock_conn_events._active_device is None
    mock_conn_events.publish.assert_any_call("bluetooth_manager.paired.disconnected", {
        "device_address": "11:22:33:44:55:66",
        "device_name": "TestPhone",
    })
