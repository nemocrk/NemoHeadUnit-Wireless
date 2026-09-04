import sys
import pytest
from shared.hardware.base_bluetooth import get_bluetooth_adapter, BaseBluetoothAdapter
from shared.hardware.base_wifi_ap import get_wifi_adapter, BaseWifiApAdapter
from shared.platform.windows import setup_windows_dll_directories


def test_get_bluetooth_adapter():
    adapter = get_bluetooth_adapter()
    assert isinstance(adapter, BaseBluetoothAdapter)


def test_get_wifi_adapter():
    adapter = get_wifi_adapter()
    assert isinstance(adapter, BaseWifiApAdapter)


def test_setup_windows_dll_directories():
    result = setup_windows_dll_directories()
    assert isinstance(result, list)
    if sys.platform != "win32":
        assert result == []
