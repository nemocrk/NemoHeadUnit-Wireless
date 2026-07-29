import abc
import os
import sys
import shutil
import asyncio
from typing import Dict, Any

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


class LinuxPulseAudioAdapter(BaseAudioAdapter):
    """Linux implementation using pactl or amixer CLI/DBus."""

    def __init__(self):
        self._muted = False
        self._volume = 80
        self._pactl_cmd = shutil.which("pactl")
        self._amixer_cmd = shutil.which("amixer")

    async def get_volume(self) -> Dict[str, Any]:
        if self._pactl_cmd:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "get-sink-volume", "@DEFAULT_SINK@",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                out = stdout.decode()
                if "%" in out:
                    parts = out.split("%")[0].split("/")
                    if len(parts) > 1:
                        self._volume = int(parts[-1].strip())
            except Exception:
                pass

            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "get-sink-mute", "@DEFAULT_SINK@",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                out = stdout.decode().lower()
                self._muted = "yes" in out
            except Exception:
                pass

        return {"volume": self._volume, "muted": self._muted}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        volume = max(0, min(100, volume))
        self._volume = volume
        if self._pactl_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"
                )
            except Exception:
                pass
        elif self._amixer_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._amixer_cmd, "set", "Master", f"{volume}%"
                )
            except Exception:
                pass
        return {"volume": self._volume, "muted": self._muted}

    async def volume_up(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume + step)

    async def volume_down(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume - step)

    async def toggle_mute(self) -> Dict[str, Any]:
        self._muted = not self._muted
        if self._pactl_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-sink-mute", "@DEFAULT_SINK@", "toggle"
                )
            except Exception:
                pass
        elif self._amixer_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._amixer_cmd, "set", "Master", "toggle"
                )
            except Exception:
                pass
        return {"volume": self._volume, "muted": self._muted}


class WindowsCoreAudioAdapter(BaseAudioAdapter):
    """Windows implementation using pycaw / ctypes Core Audio API."""

    def __init__(self):
        self._volume = 80
        self._muted = False
        self._volume_interface = None
        self._init_pycaw()

    def _init_pycaw(self):
        if sys.platform != "win32":
            return
        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self._volume_interface = interface.QueryInterface(IAudioEndpointVolume)
        except Exception:
            self._volume_interface = None

    async def get_volume(self) -> Dict[str, Any]:
        if self._volume_interface:
            try:
                vol_scalar = self._volume_interface.GetMasterVolumeLevelScalar()
                self._volume = int(round(vol_scalar * 100))
                self._muted = bool(self._volume_interface.GetMute())
            except Exception:
                pass
        return {"volume": self._volume, "muted": self._muted}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        volume = max(0, min(100, volume))
        self._volume = volume
        if self._volume_interface:
            try:
                self._volume_interface.SetMasterVolumeLevelScalar(volume / 100.0, None)
            except Exception:
                pass
        return {"volume": self._volume, "muted": self._muted}

    async def volume_up(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume + step)

    async def volume_down(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume - step)

    async def toggle_mute(self) -> Dict[str, Any]:
        self._muted = not self._muted
        if self._volume_interface:
            try:
                self._volume_interface.SetMute(1 if self._muted else 0, None)
            except Exception:
                pass
        return {"volume": self._volume, "muted": self._muted}


class MockAudioAdapter(BaseAudioAdapter):
    """Mock audio adapter for headless or test environments."""

    def __init__(self):
        self._volume = 80
        self._muted = False

    async def get_volume(self) -> Dict[str, Any]:
        return {"volume": self._volume, "muted": self._muted}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        self._volume = max(0, min(100, volume))
        return {"volume": self._volume, "muted": self._muted}

    async def volume_up(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume + step)

    async def volume_down(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume - step)

    async def toggle_mute(self) -> Dict[str, Any]:
        self._muted = not self._muted
        return {"volume": self._volume, "muted": self._muted}


def get_audio_adapter() -> BaseAudioAdapter:
    """Factory creating the appropriate audio adapter for the OS."""
    if sys.platform == "win32":
        try:
            return WindowsCoreAudioAdapter()
        except Exception:
            return MockAudioAdapter()
    elif sys.platform.startswith("linux"):
        return LinuxPulseAudioAdapter()
    return MockAudioAdapter()
