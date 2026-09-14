"""
base_audio.py — Abstract Base Interface & Audio Adapter Factory.
"""

import abc
import sys
from typing import Dict, Any
from shared.logger import get_logger

log = get_logger("hardware.base_audio")


class BaseAudioAdapter(abc.ABC):
    """
    Abstract Hardware Adapter Interface for Master Audio Volume Controls.
    Cross-platform compliance: Linux (PulseAudio/ALSA DBus/pactl), Windows (pycaw/ctypes), Mock fallback.
    """

    @abc.abstractmethod
    async def get_volume(self) -> Dict[str, Any]:
        """Return dict with 'volume' (0-100) and 'muted' (bool)."""
        pass

    @abc.abstractmethod
    async def set_volume(self, volume: int) -> Dict[str, Any]:
        """Set volume (0-100) and return new volume state."""
        pass

    @abc.abstractmethod
    async def volume_up(self, step: int = 5) -> Dict[str, Any]:
        """Increase volume by step percent."""
        pass

    @abc.abstractmethod
    async def volume_down(self, step: int = 5) -> Dict[str, Any]:
        """Decrease volume by step percent."""
        pass

    @abc.abstractmethod
    async def toggle_mute(self) -> Dict[str, Any]:
        """Toggle mute state and return new state."""
        pass

    @abc.abstractmethod
    async def get_available_sinks(self) -> list[Dict[str, Any]]:
        """Return list of available audio output sinks [{'id': ..., 'name': ..., 'description': ...}]."""
        pass

    @abc.abstractmethod
    async def get_available_sources(self) -> list[Dict[str, Any]]:
        """Return list of available audio input sources [{'id': ..., 'name': ..., 'description': ...}]."""
        pass

    @abc.abstractmethod
    async def set_active_sink(self, sink_id: str) -> bool:
        """Set active output sink ID or 'default'."""
        pass

    @abc.abstractmethod
    async def set_active_source(self, source_id: str) -> bool:
        """Set active input source ID or 'default'."""
        pass

    @abc.abstractmethod
    async def ensure_hfp_loopback(self, active: bool, bluez_source: str = "", bluez_sink: str = "") -> Dict[str, Any]:
        """Enable or disable bidirectional loopback routing between BlueZ HFP and system mic/speaker."""
        pass


def get_audio_adapter() -> BaseAudioAdapter:
    """Factory creating and logging the appropriate audio adapter for the active OS platform."""
    if sys.platform == "win32":
        try:
            from .windows_audio import WindowsCoreAudioAdapter
            adapter = WindowsCoreAudioAdapter()
            log.info("🔊 Audio Hardware Adapter: Selected 'WindowsCoreAudioAdapter' (Platform: Windows/win32)")
            return adapter
        except Exception as exc:
            log.warning(f"⚠️ Audio Hardware Adapter: Failed to load WindowsCoreAudioAdapter ({exc}) — falling back to MockAudioAdapter")
            from .mock_audio import MockAudioAdapter
            return MockAudioAdapter(reason=f"Windows WASAPI load error: {exc}")

    elif sys.platform.startswith("linux"):
        try:
            from .linux_audio import LinuxPulseAudioAdapter
            adapter = LinuxPulseAudioAdapter()
            log.info("🔊 Audio Hardware Adapter: Selected 'LinuxPulseAudioAdapter' (Platform: Linux/PulseAudio/ALSA)")
            return adapter
        except Exception as exc:
            log.warning(f"⚠️ Audio Hardware Adapter: Failed to load LinuxPulseAudioAdapter ({exc}) — falling back to MockAudioAdapter")
            from .mock_audio import MockAudioAdapter
            return MockAudioAdapter(reason=f"Linux PulseAudio load error: {exc}")

    log.info(f"🔊 Audio Hardware Adapter: Selected 'MockAudioAdapter' (Platform: {sys.platform})")
    from .mock_audio import MockAudioAdapter
    return MockAudioAdapter(reason=f"Unsupported OS platform: {sys.platform}")
