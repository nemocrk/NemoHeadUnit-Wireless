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

    async def get_available_sinks(self) -> list[Dict[str, Any]]:
        sinks = [{"id": "default", "name": "System Default Output", "description": "Default System Output"}]
        if sys.platform == "win32":
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    from pycaw.pycaw import AudioUtilities
                    devices = AudioUtilities.GetAllDevices()
                    for dev in devices:
                        try:
                            if hasattr(dev, "FriendlyName") and dev.FriendlyName:
                                flow = getattr(dev, "dataFlow", 0)
                                state = getattr(dev, "state", 1)
                                if flow in (0, "eRender") and state in (1, "DEVICE_STATE_ACTIVE", "ACTIVE"):
                                    sinks.append({"id": dev.FriendlyName, "name": dev.FriendlyName, "description": dev.FriendlyName})
                        except Exception:
                            continue
                except Exception as e:
                    log.debug(f"Windows sink enumeration notice: {e}")
        log.info(f"🔊 [Windows Audio] Discovered {len(sinks)} output sink(s): {[s['name'] for s in sinks]}")
        return sinks

    async def get_available_sources(self) -> list[Dict[str, Any]]:
        sources = [{"id": "default", "name": "System Default Input", "description": "Default System Microphone"}]
        if sys.platform == "win32":
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    from pycaw.pycaw import AudioUtilities
                    devices = AudioUtilities.GetAllDevices()
                    for dev in devices:
                        try:
                            if hasattr(dev, "FriendlyName") and dev.FriendlyName:
                                flow = getattr(dev, "dataFlow", None)
                                state = getattr(dev, "state", 1)
                                if flow in (1, "eCapture") and state in (1, "DEVICE_STATE_ACTIVE", "ACTIVE"):
                                    sources.append({"id": dev.FriendlyName, "name": dev.FriendlyName, "description": dev.FriendlyName})
                        except Exception:
                            continue
                    if len(sources) == 1:
                        mic = AudioUtilities.GetMicrophone()
                        if mic and hasattr(mic, "FriendlyName") and mic.FriendlyName:
                            sources.append({"id": mic.FriendlyName, "name": mic.FriendlyName, "description": mic.FriendlyName})
                except Exception as e:
                    log.debug(f"Windows source enumeration notice: {e}")
        log.info(f"🎤 [Windows Audio] Discovered {len(sources)} input source(s): {[s['name'] for s in sources]}")
        return sources

    async def set_active_sink(self, sink_id: str) -> bool:
        self._active_sink = sink_id
        log.info(f"WindowsCoreAudioAdapter active sink set to '{sink_id}'")
        return True

    async def set_active_source(self, source_id: str) -> bool:
        self._active_source = source_id
        log.info(f"WindowsCoreAudioAdapter active source set to '{source_id}'")
        return True
