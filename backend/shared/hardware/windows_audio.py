"""
windows_audio.py — Windows CoreAudio (WASAPI / pycaw) hardware audio adapter.
"""

import sys
from typing import Dict, Any
from shared.logger import get_logger
from .base_audio import BaseAudioAdapter

log = get_logger("hardware.windows_audio")


class WindowsCoreAudioAdapter(BaseAudioAdapter):
    """Windows implementation using pycaw / comtypes Core Audio API."""

    def __init__(self):
        self._volume = 80
        self._muted = False
        self._volume_interface = None
        self._init_pycaw()

    def _init_pycaw(self):
        if sys.platform != "win32":
            log.warning("WindowsCoreAudioAdapter initialized on non-Win32 platform — disabled WASAPI interface")
            return
        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            device = AudioUtilities.GetSpeakers()
            if hasattr(device, "EndpointVolume"):
                self._volume_interface = device.EndpointVolume
            elif hasattr(device, "Activate"):
                interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._volume_interface = interface.QueryInterface(IAudioEndpointVolume)
            else:
                # Fallback to direct CLSCTX_ALL AudioEndpointVolume query
                self._volume_interface = AudioUtilities.GetSpeakers().EndpointVolume
            log.info("WindowsCoreAudioAdapter initialized successfully via Windows Core Audio (pycaw/WASAPI)")
        except Exception as exc:
            self._volume_interface = None
            log.warning(f"WindowsCoreAudioAdapter failed to initialize WASAPI volume interface via pycaw: {exc}")

    async def get_volume(self) -> Dict[str, Any]:
        if self._volume_interface:
            try:
                vol_scalar = self._volume_interface.GetMasterVolumeLevelScalar()
                self._volume = int(round(vol_scalar * 100))
                self._muted = bool(self._volume_interface.GetMute())
            except Exception as exc:
                log.debug(f"WASAPI get_volume notice: {exc}")
        return {"volume": self._volume, "muted": self._muted}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        volume = max(0, min(100, volume))
        self._volume = volume
        if self._volume_interface:
            try:
                self._volume_interface.SetMasterVolumeLevelScalar(volume / 100.0, None)
                log.info(f"Windows master speaker volume set to {volume}% via WASAPI")
            except Exception as exc:
                log.warning(f"WASAPI SetMasterVolumeLevelScalar failed: {exc}")
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
                log.info(f"Windows master speaker mute toggled to {self._muted} via WASAPI")
            except Exception as exc:
                log.warning(f"WASAPI SetMute failed: {exc}")
        return {"volume": self._volume, "muted": self._muted}
