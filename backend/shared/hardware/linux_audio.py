"""
linux_audio.py — Linux PulseAudio/ALSA hardware audio adapter.
"""

import shutil
import asyncio
from typing import Dict, Any
from shared.logger import get_logger
from .base_audio import BaseAudioAdapter

log = get_logger("hardware.linux_audio")


class LinuxPulseAudioAdapter(BaseAudioAdapter):
    """Linux implementation using pactl or amixer CLI/DBus."""

    def __init__(self):
        self._muted = False
        self._volume = 80
        self._pactl_cmd = shutil.which("pactl")
        self._amixer_cmd = shutil.which("amixer")
        if self._pactl_cmd:
            log.info(f"LinuxPulseAudioAdapter initialized using 'pactl' ({self._pactl_cmd})")
        elif self._amixer_cmd:
            log.info(f"LinuxPulseAudioAdapter initialized using 'amixer' ({self._amixer_cmd})")
        else:
            log.warning("LinuxPulseAudioAdapter initialized but neither 'pactl' nor 'amixer' binaries were found on PATH")

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
            except Exception as e:
                log.debug(f"pactl get-sink-volume notice: {e}")

            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "get-sink-mute", "@DEFAULT_SINK@",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                out = stdout.decode().lower()
                self._muted = "yes" in out
            except Exception as e:
                log.debug(f"pactl get-sink-mute notice: {e}")

        return {"volume": self._volume, "muted": self._muted}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        volume = max(0, min(100, volume))
        self._volume = volume
        if self._pactl_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"
                )
            except Exception as e:
                log.warning(f"pactl set-sink-volume failed: {e}")
        elif self._amixer_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._amixer_cmd, "set", "Master", f"{volume}%"
                )
            except Exception as e:
                log.warning(f"amixer set Master volume failed: {e}")
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
            except Exception as e:
                log.warning(f"pactl set-sink-mute failed: {e}")
        elif self._amixer_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._amixer_cmd, "set", "Master", "toggle"
                )
            except Exception as e:
                log.warning(f"amixer set Master toggle failed: {e}")
        return {"volume": self._volume, "muted": self._muted}
