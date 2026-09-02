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
        self._active_sink: str = ""
        self._active_source: str = ""
        self._pactl_cmd = shutil.which("pactl")
        self._amixer_cmd = shutil.which("amixer")
        if self._pactl_cmd:
            log.info(f"LinuxPulseAudioAdapter initialized using 'pactl' ({self._pactl_cmd})")
        elif self._amixer_cmd:
            log.info(f"LinuxPulseAudioAdapter initialized using 'amixer' ({self._amixer_cmd})")
        else:
            log.warning("LinuxPulseAudioAdapter initialized but neither 'pactl' nor 'amixer' binaries were found on PATH")

    def _target_sink(self) -> str:
        return self._active_sink if self._active_sink and self._active_sink != "default" else "@DEFAULT_SINK@"

    async def get_volume(self) -> Dict[str, Any]:
        if self._pactl_cmd:
            sink = self._target_sink()
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "get-sink-volume", sink,
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
                    self._pactl_cmd, "get-sink-mute", sink,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                out = stdout.decode().lower()
                self._muted = "yes" in out
            except Exception as e:
                log.debug(f"pactl get-sink-mute notice: {e}")

        return {"volume": self._volume, "muted": self._muted, "sink": self._target_sink()}

    async def set_volume(self, volume: int) -> Dict[str, Any]:
        volume = max(0, min(100, volume))
        self._volume = volume
        if self._pactl_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-sink-volume", self._target_sink(), f"{volume}%"
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
        return {"volume": self._volume, "muted": self._muted, "sink": self._target_sink()}

    async def volume_up(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume + step)

    async def volume_down(self, step: int = 5) -> Dict[str, Any]:
        return await self.set_volume(self._volume - step)

    async def toggle_mute(self) -> Dict[str, Any]:
        self._muted = not self._muted
        if self._pactl_cmd:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-sink-mute", self._target_sink(), "toggle"
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
        return {"volume": self._volume, "muted": self._muted, "sink": self._target_sink()}

    async def get_available_sinks(self) -> list[Dict[str, Any]]:
        sinks = [{"id": "default", "name": "System Default Output", "description": "Default System Sink"}]
        if self._pactl_cmd:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "-f", "json", "list", "sinks",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    import json
                    data = json.loads(stdout.decode())
                    for item in data:
                        sink_name = item.get("name", "")
                        desc = item.get("description") or item.get("properties", {}).get("device.description", sink_name)
                        if sink_name:
                            sinks.append({"id": sink_name, "name": desc, "description": sink_name})
                    log.info(f"🔊 [Linux Audio] Discovered {len(sinks)} output sink(s): {[s['name'] for s in sinks]}")
                    return sinks
            except Exception:
                pass

            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "list", "short", "sinks",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                for line in stdout.decode().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        sink_name = parts[1]
                        sinks.append({"id": sink_name, "name": sink_name, "description": sink_name})
            except Exception as e:
                log.debug(f"pactl list short sinks failed: {e}")

        log.info(f"🔊 [Linux Audio] Discovered {len(sinks)} output sink(s): {[s['name'] for s in sinks]}")
        return sinks

    async def get_available_sources(self) -> list[Dict[str, Any]]:
        sources = [{"id": "default", "name": "System Default Input", "description": "Default System Source"}]
        if self._pactl_cmd:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "-f", "json", "list", "sources",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    import json
                    data = json.loads(stdout.decode())
                    for item in data:
                        src_name = item.get("name", "")
                        desc = item.get("description") or item.get("properties", {}).get("device.description", src_name)
                        if src_name:
                            sources.append({"id": src_name, "name": desc, "description": src_name})
                    log.info(f"🎤 [Linux Audio] Discovered {len(sources)} input source(s): {[s['name'] for s in sources]}")
                    return sources
            except Exception:
                pass

            try:
                proc = await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "list", "short", "sources",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                for line in stdout.decode().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        src_name = parts[1]
                        sources.append({"id": src_name, "name": src_name, "description": src_name})
            except Exception as e:
                log.debug(f"pactl list short sources failed: {e}")

        log.info(f"🎤 [Linux Audio] Discovered {len(sources)} input source(s): {[s['name'] for s in sources]}")
        return sources

    async def set_active_sink(self, sink_id: str) -> bool:
        self._active_sink = sink_id if sink_id and sink_id != "default" else ""
        log.info(f"🔊 [Linux Audio] Selected active output sink: '{self._target_sink()}'")
        if self._pactl_cmd and self._active_sink:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-default-sink", self._active_sink
                )
                log.info(f"LinuxPulseAudioAdapter set default sink to '{self._active_sink}'")
                return True
            except Exception as e:
                log.warning(f"pactl set-default-sink failed: {e}")
                return False
        return True

    async def set_active_source(self, source_id: str) -> bool:
        self._active_source = source_id if source_id and source_id != "default" else ""
        log.info(f"🎤 [Linux Audio] Selected active input source: '{self._active_source or 'default'}'")
        if self._pactl_cmd and self._active_source:
            try:
                await asyncio.create_subprocess_exec(
                    self._pactl_cmd, "set-default-source", self._active_source
                )
                log.info(f"LinuxPulseAudioAdapter set default source to '{self._active_source}'")
                return True
            except Exception as e:
                log.warning(f"pactl set-default-source failed: {e}")
                return False
        return True
