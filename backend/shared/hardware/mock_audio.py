"""
mock_audio.py — Mock fallback hardware audio adapter for headless or test environments.
"""

from typing import Dict, Any
from shared.logger import get_logger
from .base_audio import BaseAudioAdapter

log = get_logger("hardware.mock_audio")


class MockAudioAdapter(BaseAudioAdapter):
    """Mock audio adapter for headless or test environments."""

    def __init__(self, reason: str = "Operating system audio controls unavailable"):
        self._volume = 80
        self._muted = False
        self._reason = reason
        log.info(f"MockAudioAdapter initialized (Reason: {reason})")

    async def get_volume(self) -> Dict[str, Any]:
        return {"volume": self._volume, "muted": self._muted}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        self._volume = max(0, min(100, volume))
        log.info(f"MockAudioAdapter volume set to {self._volume}%")
        return {"volume": self._volume, "muted": self._muted}

    async def volume_up(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume + step)

    async def volume_down(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume - step)

    async def toggle_mute(self) -> Dict[str, Any]:
        self._muted = not self._muted
        log.info(f"MockAudioAdapter mute toggled to {self._muted}")
        return {"volume": self._volume, "muted": self._muted}

    async def get_available_sinks(self) -> list[Dict[str, Any]]:
        return [{"id": "default", "name": "Mock Default Audio Output", "description": "Mock Output Sink"}]

    async def get_available_sources(self) -> list[Dict[str, Any]]:
        return [{"id": "default", "name": "Mock Default Audio Input", "description": "Mock Input Source"}]

    async def set_active_sink(self, sink_id: str) -> bool:
        log.info(f"MockAudioAdapter sink set to '{sink_id}'")
        return True

    async def set_active_source(self, source_id: str) -> bool:
        log.info(f"MockAudioAdapter source set to '{source_id}'")
        return True

    async def ensure_hfp_loopback(self, active: bool, bluez_source: str = "", bluez_sink: str = "") -> Dict[str, Any]:
        log.info(f"MockAudioAdapter ensure_hfp_loopback(active={active}, source={bluez_source}, sink={bluez_sink})")
        return {
            "active": active,
            "rx_loopback_id": "mock_rx_123" if active else "",
            "tx_loopback_id": "mock_tx_124" if active else "",
            "status": "ok"
        }
