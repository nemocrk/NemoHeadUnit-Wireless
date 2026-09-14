"""
test_linux_audio.py — Unit tests for LinuxPulseAudioAdapter (shared.hardware.linux_audio)
"""

import json
from unittest.mock import AsyncMock, patch
import pytest

from shared.hardware.linux_audio import LinuxPulseAudioAdapter

pytestmark = pytest.mark.unit


@pytest.fixture
def pulse_adapter():
    adapter = LinuxPulseAudioAdapter()
    adapter._pactl_cmd = "/usr/bin/pactl"
    adapter._amixer_cmd = None
    return adapter


@pytest.fixture
def amixer_adapter():
    adapter = LinuxPulseAudioAdapter()
    adapter._pactl_cmd = None
    adapter._amixer_cmd = "/usr/bin/amixer"
    return adapter


@pytest.mark.asyncio
async def test_pulse_get_volume(pulse_adapter):
    proc_vol = AsyncMock()
    proc_vol.returncode = 0
    proc_vol.communicate = AsyncMock(return_value=(b"Volume: front-left: 65536 / 75% / -0.00 dB\n", b""))

    proc_mute = AsyncMock()
    proc_mute.returncode = 0
    proc_mute.communicate = AsyncMock(return_value=(b"Mute: no\n", b""))

    with patch("asyncio.create_subprocess_exec", side_effect=[proc_vol, proc_mute]):
        res = await pulse_adapter.get_volume()
        assert res["volume"] == 75
        assert res["muted"] is False
        assert res["sink"] == "@DEFAULT_SINK@"


@pytest.mark.asyncio
async def test_pulse_set_volume(pulse_adapter):
    proc = AsyncMock()
    proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        res = await pulse_adapter.set_volume(85)
        assert res["volume"] == 85
        mock_exec.assert_called_once_with(
            "/usr/bin/pactl", "set-sink-volume", "@DEFAULT_SINK@", "85%"
        )


@pytest.mark.asyncio
async def test_pulse_volume_up_and_down(pulse_adapter):
    proc = AsyncMock()
    proc.returncode = 0
    pulse_adapter._volume = 50

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        res_up = await pulse_adapter.volume_up(10)
        assert res_up["volume"] == 60

        res_down = await pulse_adapter.volume_down(15)
        assert res_down["volume"] == 45


@pytest.mark.asyncio
async def test_pulse_toggle_mute(pulse_adapter):
    proc = AsyncMock()
    proc.returncode = 0
    pulse_adapter._muted = False

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        res = await pulse_adapter.toggle_mute()
        assert res["muted"] is True
        mock_exec.assert_called_once_with(
            "/usr/bin/pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"
        )


@pytest.mark.asyncio
async def test_pulse_get_available_sinks_json(pulse_adapter):
    mock_data = [
        {
            "name": "alsa_output.pci-0000_00_1b.0.analog-stereo",
            "description": "Built-in Audio Analog Stereo",
        },
        {
            "name": "bluez_sink.AA_BB_CC",
            "properties": {"device.description": "Bluetooth Headset"},
        }
    ]
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(json.dumps(mock_data).encode("utf-8"), b""))

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        sinks = await pulse_adapter.get_available_sinks()
        assert len(sinks) == 3
        assert sinks[0]["id"] == "default"
        assert sinks[1]["id"] == "alsa_output.pci-0000_00_1b.0.analog-stereo"
        assert sinks[1]["name"] == "Built-in Audio Analog Stereo"
        assert sinks[2]["id"] == "bluez_sink.AA_BB_CC"


@pytest.mark.asyncio
async def test_pulse_get_available_sources_json(pulse_adapter):
    mock_data = [
        {
            "name": "alsa_input.pci-0000_00_1b.0.analog-stereo",
            "description": "Built-in Microphone",
        }
    ]
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(json.dumps(mock_data).encode("utf-8"), b""))

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        sources = await pulse_adapter.get_available_sources()
        assert len(sources) == 2
        assert sources[0]["id"] == "default"
        assert sources[1]["id"] == "alsa_input.pci-0000_00_1b.0.analog-stereo"


@pytest.mark.asyncio
async def test_pulse_set_active_sink_and_source(pulse_adapter):
    proc = AsyncMock()
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        ok_sink = await pulse_adapter.set_active_sink("custom_sink")
        assert ok_sink is True
        assert pulse_adapter._target_sink() == "custom_sink"

        ok_src = await pulse_adapter.set_active_source("custom_source")
        assert ok_src is True
        assert pulse_adapter._active_source == "custom_source"


@pytest.mark.asyncio
async def test_amixer_fallback_volume_and_mute(amixer_adapter):
    proc = AsyncMock()
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        res_vol = await amixer_adapter.set_volume(70)
        assert res_vol["volume"] == 70
        mock_exec.assert_called_with(
            "/usr/bin/amixer", "set", "Master", "70%"
        )

        res_mute = await amixer_adapter.toggle_mute()
        assert res_mute["muted"] is True
        mock_exec.assert_called_with(
            "/usr/bin/amixer", "set", "Master", "toggle"
        )


@pytest.mark.asyncio
async def test_pulse_ensure_hfp_loopback(pulse_adapter):
    proc_rx = AsyncMock()
    proc_rx.returncode = 0
    proc_rx.communicate = AsyncMock(return_value=(b"101\n", b""))

    proc_tx = AsyncMock()
    proc_tx.returncode = 0
    proc_tx.communicate = AsyncMock(return_value=(b"102\n", b""))

    # 1. Activate loopbacks
    with patch("asyncio.create_subprocess_exec", side_effect=[proc_rx, proc_tx]):
        res = await pulse_adapter.ensure_hfp_loopback(
            active=True,
            bluez_source="bluez_source.phone",
            bluez_sink="bluez_sink.phone"
        )
        assert res["active"] is True
        assert res["rx_loopback_id"] == "101"
        assert res["tx_loopback_id"] == "102"

    # 2. Deactivate loopbacks
    proc_unload = AsyncMock()
    proc_unload.returncode = 0
    with patch("asyncio.create_subprocess_exec", return_value=proc_unload) as mock_exec:
        res_down = await pulse_adapter.ensure_hfp_loopback(active=False)
        assert res_down["active"] is False
        assert pulse_adapter._rx_loopback_id == ""
        assert pulse_adapter._tx_loopback_id == ""
        assert mock_exec.call_count == 2


@pytest.mark.asyncio
async def test_pulse_sinks_and_sources_short_fallback(pulse_adapter):
    # Simulate JSON failure, falling back to 'pactl list short sinks'
    proc_json_fail = AsyncMock()
    proc_json_fail.returncode = 1
    proc_json_fail.communicate = AsyncMock(return_value=(b"", b"error"))

    proc_short = AsyncMock()
    proc_short.returncode = 0
    proc_short.communicate = AsyncMock(return_value=(b"0\talsa_output.fallback\tmodule-alsa-sink.c\tfloat32le 2ch 48000Hz\tSUSPENDED\n", b""))

    with patch("asyncio.create_subprocess_exec", side_effect=[proc_json_fail, proc_short]):
        sinks = await pulse_adapter.get_available_sinks()
        assert any(s["id"] == "alsa_output.fallback" for s in sinks)

