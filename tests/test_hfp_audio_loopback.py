"""
test_hfp_audio_loopback.py — Tests for bidirectional HFP audio loopback management.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from backend.shared.hardware.mock_audio import MockAudioAdapter
from backend.shared.hardware.linux_audio import LinuxPulseAudioAdapter


def test_mock_audio_adapter_hfp_loopback():
    async def _run():
        adapter = MockAudioAdapter()
        res = await adapter.ensure_hfp_loopback(active=True, bluez_source="bluez_source.00_11_22", bluez_sink="bluez_sink.00_11_22")
        assert res["active"] is True
        assert res["rx_loopback_id"] != ""
        assert res["tx_loopback_id"] != ""

        res_off = await adapter.ensure_hfp_loopback(active=False)
        assert res_off["active"] is False
        assert res_off["rx_loopback_id"] == ""
        assert res_off["tx_loopback_id"] == ""

    asyncio.run(_run())


def test_linux_pulse_audio_hfp_loopback_activation_and_teardown():
    async def _run():
        adapter = LinuxPulseAudioAdapter()
        adapter._pactl_cmd = "/usr/bin/pactl"

        load_mock = AsyncMock()
        load_mock.returncode = 0
        load_mock.communicate = AsyncMock(return_value=(b"128\n", b""))

        unload_mock = AsyncMock()
        unload_mock.returncode = 0
        unload_mock.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", side_effect=[load_mock, load_mock, unload_mock, unload_mock]):
            res_on = await adapter.ensure_hfp_loopback(
                active=True,
                bluez_source="bluez_input.C8_2A_DD_8C_40_44.0",
                bluez_sink="bluez_output.C8_2A_DD_8C_40_44.0"
            )
            assert res_on["active"] is True
            assert res_on["rx_loopback_id"] == "128"
            assert res_on["tx_loopback_id"] == "128"

            res_off = await adapter.ensure_hfp_loopback(active=False)
            assert res_off["active"] is False
            assert res_off["rx_loopback_id"] == ""
            assert res_off["tx_loopback_id"] == ""

    asyncio.run(_run())


def test_linux_pulse_audio_hfp_loopback_auto_discovery():
    async def _run():
        adapter = LinuxPulseAudioAdapter()
        adapter._pactl_cmd = "/usr/bin/pactl"

        async def mock_sources():
            return [
                {"id": "default", "name": "Internal Mic"},
                {"id": "bluez_input.AA_BB_CC", "name": "Phone Hands-Free"}
            ]

        async def mock_sinks():
            return [
                {"id": "default", "name": "Speakers"},
                {"id": "bluez_output.AA_BB_CC", "name": "Phone SCO"}
            ]

        adapter.get_available_sources = mock_sources
        adapter.get_available_sinks = mock_sinks

        load_mock = AsyncMock()
        load_mock.returncode = 0
        load_mock.communicate = AsyncMock(return_value=(b"42\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=load_mock):
            res = await adapter.ensure_hfp_loopback(active=True)
            assert res["active"] is True
            assert res["bluez_source"] == "bluez_input.AA_BB_CC"
            assert res["bluez_sink"] == "bluez_output.AA_BB_CC"

    asyncio.run(_run())
