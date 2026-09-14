import sys
import pytest
from unittest.mock import patch, MagicMock
from shared.hardware.base_audio import BaseAudioAdapter, get_audio_adapter
from shared.hardware.mock_audio import MockAudioAdapter
from shared.hardware.base_bluetooth import BaseBluetoothAdapter, get_bluetooth_adapter
from shared.hardware.base_wifi_ap import BaseWifiApAdapter, get_wifi_adapter

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_mock_audio_adapter_volume_operations():
    adapter = MockAudioAdapter()

    # Initial state
    vol_state = await adapter.get_volume()
    assert vol_state["volume"] == 80
    assert vol_state["muted"] is False

    # Clamping tests
    await adapter.set_volume(150)
    assert (await adapter.get_volume())["volume"] == 100

    await adapter.set_volume(-50)
    assert (await adapter.get_volume())["volume"] == 0

    # Volume step operations
    await adapter.set_volume(50)
    await adapter.volume_up(10)
    assert (await adapter.get_volume())["volume"] == 60

    await adapter.volume_down(20)
    assert (await adapter.get_volume())["volume"] == 40

    # Toggle mute
    muted_state = await adapter.toggle_mute()
    assert muted_state["muted"] is True
    unmuted_state = await adapter.toggle_mute()
    assert unmuted_state["muted"] is False


@pytest.mark.asyncio
async def test_mock_audio_adapter_sinks_sources_and_loopback():
    adapter = MockAudioAdapter()

    sinks = await adapter.get_available_sinks()
    assert len(sinks) > 0
    assert sinks[0]["id"] == "default"

    sources = await adapter.get_available_sources()
    assert len(sources) > 0
    assert sources[0]["id"] == "default"

    assert await adapter.set_active_sink("default") is True
    assert await adapter.set_active_source("default") is True

    # HFP loopback
    lb_active = await adapter.ensure_hfp_loopback(True, bluez_source="src", bluez_sink="sink")
    assert lb_active["active"] is True
    assert lb_active["rx_loopback_id"] != ""

    lb_inactive = await adapter.ensure_hfp_loopback(False)
    assert lb_inactive["active"] is False
    assert lb_inactive["rx_loopback_id"] == ""


def test_get_audio_adapter_factory():
    # Unsupported platform returns MockAudioAdapter
    with patch("sys.platform", "unknown_os"):
        adapter = get_audio_adapter()
        assert isinstance(adapter, MockAudioAdapter)

    # Linux fallback on exception
    with patch("sys.platform", "linux"), \
         patch("shared.hardware.linux_audio.LinuxPulseAudioAdapter", side_effect=Exception("No PA")):
        adapter = get_audio_adapter()
        assert isinstance(adapter, MockAudioAdapter)


def test_get_bluetooth_adapter_factory():
    # Linux fallback to Windows/Mock adapter on exception
    with patch("sys.platform", "linux"), \
         patch("shared.hardware.bluez_bluetooth.BluezBluetoothAdapter", side_effect=Exception("No DBus")):
        adapter = get_bluetooth_adapter()
        assert isinstance(adapter, BaseBluetoothAdapter)


def test_get_wifi_adapter_factory():
    # Linux fallback to Windows/Mock adapter on exception
    with patch("sys.platform", "linux"), \
         patch("shared.hardware.apmanager_wifi_ap.APManagerWifiApAdapter", side_effect=Exception("No APManager")):
        adapter = get_wifi_adapter()
        assert isinstance(adapter, BaseWifiApAdapter)
