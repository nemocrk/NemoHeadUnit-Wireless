"""
test_bluez_bluetooth.py — Comprehensive Unit tests for BluezBluetoothAdapter (shared.hardware.bluez_bluetooth)
"""

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from shared.hardware.bluez_bluetooth import BluezBluetoothAdapter, PROFILE_PATH, AA_UUID

pytestmark = pytest.mark.unit


def test_bluez_rssi_to_bars():
    assert BluezBluetoothAdapter._rssi_to_bars(-50) == 5
    assert BluezBluetoothAdapter._rssi_to_bars(-60) == 5
    assert BluezBluetoothAdapter._rssi_to_bars(-65) == 4
    assert BluezBluetoothAdapter._rssi_to_bars(-70) == 4
    assert BluezBluetoothAdapter._rssi_to_bars(-75) == 3
    assert BluezBluetoothAdapter._rssi_to_bars(-85) == 2
    assert BluezBluetoothAdapter._rssi_to_bars(-95) == 1


def test_bluez_callbacks_and_telemetry():
    adapter = BluezBluetoothAdapter()
    assert adapter.get_adapter_address() == ""

    pin_cb = MagicMock()
    conn_cb = MagicMock()
    bat_cb = MagicMock()

    adapter.set_on_pin_callback(pin_cb)
    adapter.set_on_connection_callback(conn_cb)
    adapter.set_on_battery_callback(bat_cb)

    assert adapter._on_pin_requested_cb == pin_cb
    assert adapter._on_connection_cb == conn_cb
    assert adapter._on_battery_cb == bat_cb


def test_bluez_properties_changed_device_connection():
    adapter = BluezBluetoothAdapter()
    conn_cb = MagicMock()
    bat_cb = MagicMock()
    adapter.set_on_connection_callback(conn_cb)
    adapter.set_on_battery_callback(bat_cb)

    # 1. Device connected event
    path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    adapter._on_dbus_properties_changed(
        interface="org.bluez.Device1",
        changed={"Connected": True},
        invalidated=[],
        path=path
    )
    conn_cb.assert_called_once_with("AA:BB:CC:DD:EE:FF", True)

    # 2. RSSI update
    adapter._on_dbus_properties_changed(
        interface="org.bluez.Device1",
        changed={"RSSI": -65},
        invalidated=[],
        path=path
    )
    bat_cb.assert_called_with("AA:BB:CC:DD:EE:FF", -1, 4, "", False)


def test_bluez_properties_changed_battery():
    adapter = BluezBluetoothAdapter()
    bat_cb = MagicMock()
    adapter.set_on_battery_callback(bat_cb)

    path = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    adapter._on_dbus_properties_changed(
        interface="org.bluez.Battery1",
        changed={"Percentage": 85},
        invalidated=[],
        path=path
    )
    bat_cb.assert_called_once_with("11:22:33:44:55:66", 85, -1, "", False)


def test_bluez_properties_changed_ofono():
    adapter = BluezBluetoothAdapter()
    bat_cb = MagicMock()
    adapter.set_on_battery_callback(bat_cb)

    path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    adapter._on_dbus_properties_changed(
        interface="org.ofono.NetworkRegistration",
        changed={"Name": "Vodafone", "Status": "roaming", "Strength": 80},
        invalidated=[],
        path=path
    )
    # Strength 80 / 20 = 4 bars
    bat_cb.assert_called_once_with("AA:BB:CC:DD:EE:FF", -1, 4, "Vodafone", True)


def test_bluez_interfaces_added_battery():
    adapter = BluezBluetoothAdapter()
    bat_cb = MagicMock()
    adapter.set_on_battery_callback(bat_cb)

    path = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    adapter._on_dbus_interfaces_added(
        path=path,
        interfaces_and_props={
            "org.bluez.Battery1": {"Percentage": 92}
        }
    )
    bat_cb.assert_called_with("11:22:33:44:55:66", 92, -1, "", False)


def test_bluez_get_device_name():
    adapter = BluezBluetoothAdapter()
    assert adapter.get_device_name("") == ""

    # Mock D-Bus bus and object manager
    mock_bus = MagicMock()
    adapter._bus = mock_bus
    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = {
        "/org/bluez/hci0/dev_11_22_33_44_55_66": {
            "org.bluez.Device1": {
                "Address": "11:22:33:44:55:66",
                "Alias": "Pixel 8 Pro",
                "Name": "Pixel 8"
            }
        }
    }
    with patch("dbus.Interface", return_value=mock_mgr):
        name = adapter.get_device_name("11:22:33:44:55:66")
        assert name == "Pixel 8 Pro"

        # Not found fallback to clean address
        assert adapter.get_device_name("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"


def test_bluez_check_device_telemetry():
    adapter = BluezBluetoothAdapter()
    bat_cb = MagicMock()
    adapter.set_on_battery_callback(bat_cb)
    adapter._bus = MagicMock()

    mock_props = MagicMock()
    mock_props.Get.side_effect = lambda iface, prop: 75 if prop == "Percentage" else (-72 if prop == "RSSI" else None)
    with patch("dbus.Interface", return_value=mock_props):
        adapter._check_device_telemetry("/org/bluez/hci0/dev_11_22_33_44_55_66", "11:22:33:44:55:66")
        bat_cb.assert_called_once_with("11:22:33:44:55:66", 75, 3, "", False)


@pytest.mark.asyncio
async def test_bluez_setup_success():
    adapter = BluezBluetoothAdapter()
    mock_bus = MagicMock()
    mock_bus.list_names.return_value = ["org.bluez"]

    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}}
    }
    mock_props = MagicMock()
    mock_props.Get.return_value = "00:11:22:33:44:55"

    def iface_side_effect(obj, iface_name):
        if iface_name == "org.freedesktop.DBus.ObjectManager":
            return mock_mgr
        elif iface_name == "org.freedesktop.DBus.Properties":
            return mock_props
        return MagicMock()

    with patch("dbus.SystemBus", return_value=mock_bus), \
         patch("dbus.Interface", side_effect=iface_side_effect), \
         patch("dbus.service.Object.__init__", return_value=None), \
         patch("subprocess.run"):
        await adapter.setup("NemoHU", True, 0)

        assert adapter._initialized is True
        assert adapter.get_adapter_address() == "00:11:22:33:44:55"


@pytest.mark.asyncio
async def test_bluez_setup_no_bluez():
    adapter = BluezBluetoothAdapter()
    mock_bus = MagicMock()
    mock_bus.list_names.return_value = []
    with patch("dbus.SystemBus", return_value=mock_bus):
        with pytest.raises(RuntimeError, match="not registered"):
            await adapter.setup("NemoHU", True, 0)


def test_bluez_agent_and_profile_methods():
    adapter = BluezBluetoothAdapter()
    mock_pin_cb = MagicMock()
    adapter.set_on_pin_callback(mock_pin_cb)
    adapter._bus = MagicMock()

    with patch("dbus.service.Object.__init__", return_value=None):
        adapter._register_pairing_agent()
        agent = adapter._agent

        # RequestPinCode
        assert agent.RequestPinCode("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF") == "0000"
        mock_pin_cb.assert_called_with("AA:BB:CC:DD:EE:FF", "0000")

        # RequestConfirmation
        reply_h = MagicMock()
        error_h = MagicMock()
        agent.RequestConfirmation("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, reply_h, error_h)
        mock_pin_cb.assert_called_with("AA:BB:CC:DD:EE:FF", "123456")
        assert adapter._dbus_reply_handlers["AA:BB:CC:DD:EE:FF"] == reply_h

        # Other agent hooks
        assert int(agent.RequestPasskey("/dev_11")) == 0
        agent.DisplayPinCode("/dev_11", "1234")
        agent.DisplayPasskey("/dev_11", 654321)
        agent.AuthorizeService("/dev_11", AA_UUID)
        agent.Release()
        agent.Cancel()


@pytest.mark.asyncio
async def test_bluez_pair_and_confirm():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._initialized = True

    # 1. Pair device not found
    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = {}
    with patch("dbus.Interface", return_value=mock_mgr):
        ok, err = await adapter.pair_device("11:22:33:44:55:66", None)
        assert ok is False
        assert "not found" in err

    # 2. Pair device found
    mock_mgr.GetManagedObjects.return_value = {
        "/org/bluez/hci0/dev_11_22_33_44_55_66": {
            "org.bluez.Device1": {"Address": "11:22:33:44:55:66"}
        }
    }
    mock_device = MagicMock()
    mock_props = MagicMock()
    def iface_side_effect(obj, iface_name):
        if iface_name == "org.bluez.Device1":
            return mock_device
        if iface_name == "org.freedesktop.DBus.Properties":
            return mock_props
        return mock_mgr

    with patch("dbus.Interface", side_effect=iface_side_effect):
        ok, err = await adapter.pair_device("11:22:33:44:55:66", None)
        assert ok is True
        mock_device.Pair.assert_called_once()

    # 3. Confirm pairing True / False
    reply_h = MagicMock()
    error_h = MagicMock()
    adapter._dbus_reply_handlers["11:22:33:44:55:66"] = reply_h
    adapter._dbus_error_handlers["11:22:33:44:55:66"] = error_h

    with patch("dbus.Interface", side_effect=iface_side_effect):
        assert await adapter.confirm_pairing("11:22:33:44:55:66", True) is True
        reply_h.assert_called_once()

        adapter._dbus_error_handlers["11:22:33:44:55:66"] = error_h
        assert await adapter.confirm_pairing("11:22:33:44:55:66", False) is True
        error_h.assert_called_once()


@pytest.mark.asyncio
async def test_bluez_connect_and_disconnect():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._initialized = True

    # Duplicate connection skip
    adapter._connecting_devices.add("11:22:33:44:55:66")
    ok, err = await adapter.connect_device("11:22:33:44:55:66")
    assert ok is False
    assert "already in progress" in err
    adapter._connecting_devices.clear()

    # Device already connected in BlueZ
    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = {
        "/org/bluez/hci0/dev_11_22_33_44_55_66": {
            "org.bluez.Device1": {"Address": "11:22:33:44:55:66"}
        }
    }
    mock_props = MagicMock()
    mock_props.Get.return_value = True  # Connected == True
    mock_device = MagicMock()

    def iface_side_effect(obj, iface_name):
        if iface_name == "org.bluez.Device1":
            return mock_device
        if iface_name == "org.freedesktop.DBus.Properties":
            return mock_props
        return mock_mgr

    with patch("dbus.Interface", side_effect=iface_side_effect):
        ok, err = await adapter.connect_device("11:22:33:44:55:66")
        assert ok is True
        mock_device.Connect.assert_not_called()

        # Connect when not already connected
        mock_props.Get.return_value = False
        ok, err = await adapter.connect_device("11:22:33:44:55:66")
        assert ok is True
        mock_device.Connect.assert_called_once()

        # Disconnect device
        assert await adapter.disconnect_device("11:22:33:44:55:66") is True
        mock_device.Disconnect.assert_called_once()

        # Remove paired device
        mock_adapter = MagicMock()
        adapter._adapter = mock_adapter
        assert await adapter.remove_paired_device("11:22:33:44:55:66") is True
        mock_adapter.RemoveDevice.assert_called_once_with("/org/bluez/hci0/dev_11_22_33_44_55_66")


@pytest.mark.asyncio
async def test_bluez_get_paired_devices():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._initialized = True

    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = {
        "/org/bluez/hci0/dev_11_22_33_44_55_66": {
            "org.bluez.Device1": {
                "Address": "11:22:33:44:55:66",
                "Alias": "Pixel 8",
                "Paired": True,
                "Connected": True,
                "Trusted": True,
            }
        },
        "/org/bluez/hci0/dev_99_88_77_66_55_44": {
            "org.bluez.Device1": {
                "Address": "99:88:77:66:55:44",
                "Paired": False,
            }
        }
    }
    with patch("dbus.Interface", return_value=mock_mgr):
        paired = await adapter.get_paired_devices()
        assert len(paired) == 1
        assert paired[0]["address"] == "11:22:33:44:55:66"
        assert paired[0]["connected"] is True


def test_bluez_discovery_and_rfcomm_and_teardown():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._adapter = MagicMock()
    adapter._profile_mgr = MagicMock()
    adapter._agent_mgr = MagicMock()
    adapter._initialized = True

    # RFCOMM server registration
    rfcomm_cb = MagicMock()
    with patch("dbus.service.Object.__init__", return_value=None):
        assert adapter.register_rfcomm_server(rfcomm_cb) is True
        adapter._profile_mgr.RegisterProfile.assert_called_once()

        # Test Profile NewConnection
        profile = adapter._profile
        mock_fd = MagicMock()
        mock_fd.take.return_value = 42
        mock_sock = MagicMock()
        with patch("socket.fromfd", return_value=mock_sock), \
             patch("os.close"):
            profile.NewConnection("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", mock_fd, {})
            rfcomm_cb.assert_called_once_with(mock_sock, "AA:BB:CC:DD:EE:FF")
            profile.Release()

    # Discovery
    adapter._discovery_running = True
    asyncio.run(adapter.stop_discovery())
    assert adapter._discovery_running is False

    # Teardown
    asyncio.run(adapter.teardown())
    assert adapter._initialized is False
    adapter._profile_mgr.UnregisterProfile.assert_called_once()
    adapter._agent_mgr.UnregisterAgent.assert_called_once()


def test_bluez_discovery_and_get_devices():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._adapter = MagicMock()
    adapter._initialized = True

    # 1. _get_devices
    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = {
        "/org/bluez/hci0/dev_11_22_33_44_55_66": {
            "org.bluez.Device1": {
                "Address": "11:22:33:44:55:66",
                "Alias": "Pixel 8",
                "RSSI": -55,
            }
        }
    }
    with patch("dbus.Interface", return_value=mock_mgr):
        devices = adapter._get_devices()
        assert len(devices) == 1
        assert devices[0]["address"] == "11:22:33:44:55:66"
        assert devices[0]["name"] == "Pixel 8"
        assert devices[0]["rssi"] == -55

    # 2. _run_discovery
    found_devices = []
    def on_found(d):
        found_devices.append(d)

    with patch.object(adapter, "_get_devices", return_value=[{"address": "11:22:33:44:55:66", "name": "Pixel 8", "rssi": -55}]), \
         patch("time.sleep"):
        adapter._discovery_running = True
        # Let loop run once then stop
        def fake_sleep(t):
            adapter._discovery_running = False
        with patch("time.sleep", side_effect=fake_sleep):
            adapter._run_discovery(duration_sec=5, on_device_found_cb=on_found)

        assert len(found_devices) == 1
        assert found_devices[0]["address"] == "11:22:33:44:55:66"
        adapter._adapter.StartDiscovery.assert_called_once()
        adapter._adapter.StopDiscovery.assert_called_once()


def test_bluez_discovery_start_discovery_in_progress_and_recovery():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._adapter = MagicMock()
    adapter._initialized = True

    # Test "InProgress" exception handled cleanly
    adapter._adapter.StartDiscovery.side_effect = Exception("org.bluez.Error.InProgress: Operation already in progress")
    adapter._discovery_running = True
    with patch.object(adapter, "_get_devices", return_value=[]), \
         patch("time.sleep", side_effect=lambda t: setattr(adapter, "_discovery_running", False)):
        adapter._run_discovery(duration_sec=1, on_device_found_cb=MagicMock())
        adapter._adapter.StopDiscovery.assert_called_once()

    # Test "NotReady" exception attempts power recovery
    adapter._adapter.StartDiscovery.side_effect = [Exception("org.bluez.Error.NotReady: Resource Not Ready"), None]
    adapter._discovery_running = True
    mock_props = MagicMock()
    with patch("dbus.Interface", return_value=mock_props), \
         patch.object(adapter, "_get_devices", return_value=[]), \
         patch("time.sleep", side_effect=lambda t: setattr(adapter, "_discovery_running", False)):
        adapter._run_discovery(duration_sec=1, on_device_found_cb=MagicMock())
        mock_props.Set.assert_called_once()


@pytest.mark.asyncio
async def test_bluez_start_and_stop_discovery_async():
    adapter = BluezBluetoothAdapter()
    adapter._discovery_running = False

    with patch.object(adapter, "_run_discovery") as m_run:
        cb = MagicMock()
        await adapter.start_discovery(2, cb)
        m_run.assert_called_once_with(2, cb)

        # Calling again when already running is no-op
        adapter._discovery_running = True
        m_run.reset_mock()
        await adapter.start_discovery(2, cb)
        m_run.assert_not_called()


@pytest.mark.asyncio
async def test_bluez_connect_device_error_handling():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._initialized = True
    adapter._find_device_path = MagicMock(return_value="/org/bluez/hci0/dev_11_22_33_44_55_66")

    mock_device = MagicMock()
    mock_props = MagicMock()
    mock_props.Get.return_value = False

    def fake_dbus_interface(obj, iface):
        if iface == "org.freedesktop.DBus.Properties":
            return mock_props
        return mock_device

    with patch("dbus.Interface", side_effect=fake_dbus_interface):
        success, err = await adapter.connect_device("11:22:33:44:55:66")
        assert success is True
        mock_device.Connect.assert_called_once()

        # Extract callbacks passed to Connect
        kwargs = mock_device.Connect.call_args[1]
        reply_handler = kwargs["reply_handler"]
        error_handler = kwargs["error_handler"]

        # Test success callback
        adapter._connecting_devices.add("11:22:33:44:55:66")
        reply_handler()
        assert "11:22:33:44:55:66" not in adapter._connecting_devices

        # Test busy link error callback -> Disconnect
        adapter._connecting_devices.add("11:22:33:44:55:66")
        error_handler(Exception("br-connection-busy"))
        assert "11:22:33:44:55:66" not in adapter._connecting_devices
        mock_device.Disconnect.assert_called_once()

        # Test failed error callback -> ConnectProfile fallback
        adapter._connecting_devices.add("11:22:33:44:55:66")
        error_handler(Exception("br-connection-unknown"))
        mock_device.ConnectProfile.assert_called_once()


@pytest.mark.asyncio
async def test_bluez_disconnect_device():
    adapter = BluezBluetoothAdapter()
    adapter._bus = MagicMock()
    adapter._initialized = True
    adapter._find_device_path = MagicMock(return_value="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

    mock_device = MagicMock()
    with patch("dbus.Interface", return_value=mock_device):
        res = await adapter.disconnect_device("AA:BB:CC:DD:EE:FF")
        assert res is True
        mock_device.Disconnect.assert_called_once()
        assert "AA:BB:CC:DD:EE:FF" in adapter._disconnected_override_addrs


