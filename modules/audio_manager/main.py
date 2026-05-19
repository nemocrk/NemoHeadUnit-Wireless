"""
NemoHeadUnit-Wireless v2 — audio_manager

Centralised audio device and volume manager.

Responsibilities
----------------
* Enumerate PulseAudio/PipeWire sinks (output devices) and sources (input
  devices) without using `pactl list …` (too slow).  Primary method:
  `wpctl status` (~5 ms); fallback: `pactl list sinks/sources short`.
* Expose sinks / sources in the module config schema so config_manager
  and config_ui can render device-selection widgets.
* Publish the selected sink / source on the bus so channel_modules
  (audio, av_input) can open pacat on the correct device.
* Manage the global system volume via `wpctl set-volume @DEFAULT_SINK@`.
* Manage per-channel pacat sink volume via `pactl set-sink-input-volume`
  (requires the pacat sink-input index, obtained with `pactl list
  sink-inputs short`).
* Re-enumerate on hotplug events (udev SOUND subsystem) and via a
  periodic polling fallback every `poll_interval_s` seconds.

Bus contract
------------
  Name        : audio_manager
  Priority    : 1
  Subscribes  : system.readytostart
                system.start        {priority: int}
                system.stop         {}
                config.response     (auto via ConfigClient)
                config.changed      (auto via ConfigClient)
                audio.volume.set         {volume: int}   0-100  global
                audio.channel_volume.set {channel_id: int, volume: int}  0-100
  Publishes   : system.module_ready → {name, priority}
                system.ready       → {name, priority}
                audio.sinks.list   → {sinks: [str, ...]}
                audio.sources.list → {sources: [str, ...]}
                audio.sink.selected   → {sink: str}
                audio.source.selected → {source: str}
                audio.volume.changed  → {volume: int}
  Config keys :
    sink             enum  "default"  PulseAudio sink name
    source           enum  "default"  PulseAudio source name
    volume           int   80         Global volume (0-100)
    volume_ch4       int   100        Per-channel volume ch4 MEDIA (0-100)
    volume_ch6       int   100        Per-channel volume ch6 SPEECH (0-100)
    volume_ch10      int   100        Per-channel volume ch10 SYSTEM (0-100)
    poll_interval_s  int   30         Device re-enumeration interval (seconds)
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE    = Path(__file__).parent        # v2/modules/audio_manager/
_MODULES = _HERE.parent                 # v2/modules/
_V2      = _MODULES.parent              # v2/

for _p in (_V2, _MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_schema import (             # noqa: E402
    field_int, field_enum,
)

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------
MODULE_NAME = "audio_manager"
PRIORITY: int = 1

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Device enumeration helpers
# ---------------------------------------------------------------------------

def _enum_sinks_wpctl() -> list[str]:
    """Enumerate PulseAudio/PipeWire sinks via `wpctl status` (~5 ms)."""
    try:
        result = subprocess.run(
            ["wpctl", "status"],
            capture_output=True, text=True, timeout=2,
        )
        devices: list[str] = ["default"]
        in_sinks = False
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if "Sinks:" in stripped:
                in_sinks = True
                continue
            if not in_sinks:
                continue
            if not stripped or (
                stripped.endswith(":")
                and "│" not in line
                and "├" not in line
                and "└" not in line
            ):
                break
            dot_idx = line.find(".")
            if dot_idx == -1:
                continue
            name = line[dot_idx + 1:].strip().split()[0].rstrip(",")
            if name and name not in devices:
                devices.append(name)
        return devices
    except Exception:
        return ["default"]


def _enum_sinks_pactl() -> list[str]:
    """Fallback: enumerate sinks via `pactl list sinks short`."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True, text=True, timeout=2,
        )
        devices = ["default"]
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[1].strip()
                if name and name not in devices:
                    devices.append(name)
        return devices
    except Exception:
        return ["default"]


def _enum_sources_wpctl() -> list[str]:
    """Enumerate PulseAudio/PipeWire sources via `wpctl status` (~5 ms).

    Monitor sources (.monitor) are excluded — they capture sink output,
    not physical microphone input.
    """
    try:
        result = subprocess.run(
            ["wpctl", "status"],
            capture_output=True, text=True, timeout=2,
        )
        devices: list[str] = ["default"]
        in_sources = False
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if "Sources:" in stripped:
                in_sources = True
                continue
            if not in_sources:
                continue
            if not stripped or (
                stripped.endswith(":")
                and "│" not in line
                and "├" not in line
                and "└" not in line
            ):
                break
            dot_idx = line.find(".")
            if dot_idx == -1:
                continue
            name = line[dot_idx + 1:].strip().split()[0].rstrip(",")
            if name and ".monitor" not in name and name not in devices:
                devices.append(name)
        return devices
    except Exception:
        return ["default"]


def _enum_sources_pactl() -> list[str]:
    """Fallback: enumerate sources via `pactl list sources short`."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=2,
        )
        devices = ["default"]
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[1].strip()
                if name and ".monitor" not in name and name not in devices:
                    devices.append(name)
        return devices
    except Exception:
        return ["default"]


def enumerate_sinks() -> list[str]:
    """Return sink list: wpctl primary, pactl fallback."""
    result = _enum_sinks_wpctl()
    if len(result) > 1:
        return result
    return _enum_sinks_pactl()


def enumerate_sources() -> list[str]:
    """Return source list: wpctl primary, pactl fallback."""
    result = _enum_sources_wpctl()
    if len(result) > 1:
        return result
    return _enum_sources_pactl()


# ---------------------------------------------------------------------------
# Sink-input index helpers (for per-channel pactl volume)
# ---------------------------------------------------------------------------

def _get_sink_input_index(stream_name: str) -> int | None:
    """Return the pactl sink-input index for a pacat stream by name.

    pacat is launched with `--stream-name=ch<channel_id>`, so stream_name
    should be e.g. "ch4", "ch6".

    Uses `pactl list sink-inputs short` which is fast (~10 ms) because it
    only retrieves a one-liner per active sink-input.
    """
    try:
        result = subprocess.run(
            ["pactl", "list", "sink-inputs", "short"],
            capture_output=True, text=True, timeout=2,
        )
        for line in result.stdout.splitlines():
            # Format: <index>\t<sink-id>\t<client-id>\t<format>\t<state>
            # The stream name is NOT in the short listing; we need to
            # cross-reference with `pactl list sink-inputs` (verbose).
            pass
        # Short listing does not include stream-name.  Use verbose parse.
        result_verbose = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True, text=True, timeout=2,
        )
        current_index: int | None = None
        for line in result_verbose.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Sink Input #"):
                current_index = int(stripped.split("#")[1])
            elif stripped.startswith("media.name") or "stream.name" in stripped:
                # e.g.  media.name = "ch4"
                #        stream.name = "ch4"
                if f'"{stream_name}"' in stripped or f"= {stream_name}" in stripped:
                    return current_index
        return None
    except Exception as exc:
        log.warning("_get_sink_input_index: error for %r — %s", stream_name, exc)
        return None


# ---------------------------------------------------------------------------
# Volume control
# ---------------------------------------------------------------------------

def _set_global_volume(volume: int) -> bool:
    """Set global system volume via `wpctl set-volume @DEFAULT_SINK@`.

    volume: 0-100 (percent).
    Returns True on success.
    """
    try:
        subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_SINK@", f"{volume}%"],
            check=True, timeout=2,
        )
        log.info("Global volume → %d%%", volume)
        return True
    except Exception as exc:
        log.warning("_set_global_volume: wpctl failed — %s", exc)
        return False


def _set_channel_volume(channel_id: int, volume: int) -> bool:
    """Set per-channel pacat sink-input volume via `pactl set-sink-input-volume`.

    Looks up the sink-input index by stream name `ch<channel_id>`.
    volume: 0-100 (percent).
    Returns True on success.
    """
    stream_name = f"ch{channel_id}"
    index = _get_sink_input_index(stream_name)
    if index is None:
        log.warning(
            "_set_channel_volume: sink-input for stream %r not found — "
            "pacat may not be running yet",
            stream_name,
        )
        return False
    try:
        subprocess.run(
            ["pactl", "set-sink-input-volume", str(index), f"{volume}%"],
            check=True, timeout=2,
        )
        log.info("Channel volume ch=%d (sink-input #%d) → %d%%", channel_id, index, volume)
        return True
    except Exception as exc:
        log.warning(
            "_set_channel_volume: pactl failed ch=%d index=%d — %s",
            channel_id, index, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Schema builder (dynamic — depends on enumerated devices)
# ---------------------------------------------------------------------------

_CHANNEL_IDS = (4, 6, 10)   # MEDIA, SPEECH, SYSTEM


def _build_schema(sinks: list[str], sources: list[str]) -> dict:
    schema: dict = {
        "sink": field_enum(
            default="default",
            choices=sinks if sinks else ["default"],
        ),
        "source": field_enum(
            default="default",
            choices=sources if sources else ["default"],
        ),
        "volume": field_int(default=80, min=0, max=100),
        "poll_interval_s": field_int(default=30, min=5, max=300),
    }
    for ch_id in _CHANNEL_IDS:
        schema[f"volume_ch{ch_id}"] = field_int(default=100, min=0, max=100)
    return schema


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_sinks:   list[str] = ["default"]
_sources: list[str] = ["default"]
_schema:  dict = _build_schema(["default"], ["default"])
_config:  dict = {k: v.default for k, v in _schema.items()}

_poll_thread: threading.Thread | None = None
_poll_stop:   threading.Event         = threading.Event()
_udev_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------
# Device refresh
# ---------------------------------------------------------------------------

def _refresh_devices(publish: bool = True) -> None:
    """Re-enumerate sinks and sources, update schema choices, publish on bus."""
    global _sinks, _sources, _schema

    new_sinks   = enumerate_sinks()
    new_sources = enumerate_sources()

    changed = (new_sinks != _sinks) or (new_sources != _sources)
    _sinks   = new_sinks
    _sources = new_sources

    # Rebuild schema so config_manager can update its widget choices.
    _schema = _build_schema(_sinks, _sources)

    if publish or changed:
        bus.publish("audio.sinks.list",   {"sinks":   _sinks})
        bus.publish("audio.sources.list", {"sources": _sources})
        log.info(
            "Devices refreshed — sinks=%s sources=%s",
            _sinks, _sources,
        )

    # Validate current sink/source selections against new lists and re-publish.
    sink   = _config.get("sink",   "default")
    source = _config.get("source", "default")

    if sink not in _sinks:
        log.warning("Configured sink %r no longer available — falling back to default", sink)
        sink = "default"
        _config["sink"] = sink

    if source not in _sources:
        log.warning("Configured source %r no longer available — falling back to default", source)
        source = "default"
        _config["source"] = source

    bus.publish("audio.sink.selected",   {"sink":   sink})
    bus.publish("audio.source.selected", {"source": source})


# ---------------------------------------------------------------------------
# Polling thread
# ---------------------------------------------------------------------------

def _poll_loop() -> None:
    while not _poll_stop.wait(timeout=_config.get("poll_interval_s", 30)):
        log.debug("poll_loop: re-enumerating devices")
        _refresh_devices(publish=False)


def _start_poll_thread() -> None:
    global _poll_thread
    _poll_stop.clear()
    _poll_thread = threading.Thread(
        target=_poll_loop,
        name="audio-manager-poll",
        daemon=True,
    )
    _poll_thread.start()


# ---------------------------------------------------------------------------
# udev hotplug thread
# ---------------------------------------------------------------------------

def _udev_loop() -> None:
    """Listen for udev SOUND subsystem events and trigger device refresh.

    Requires `pyudev`.  Gracefully skips if not installed.
    """
    try:
        import pyudev  # type: ignore
    except ImportError:
        log.info("pyudev not available — hotplug detection disabled (polling only)")
        return

    try:
        context  = pyudev.Context()
        monitor  = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="sound")
        monitor.start()
        log.info("udev monitor started (subsystem=sound)")
        for device in monitor:
            if _poll_stop.is_set():
                break
            action = device.action
            if action in ("add", "remove", "change"):
                log.info("udev event: action=%s device=%s — refreshing devices", action, device)
                _refresh_devices(publish=True)
    except Exception as exc:
        log.warning("_udev_loop: error — %s", exc)


def _start_udev_thread() -> None:
    global _udev_thread
    _udev_thread = threading.Thread(
        target=_udev_loop,
        name="audio-manager-udev",
        daemon=True,
    )
    _udev_thread.start()


# ---------------------------------------------------------------------------
# Config callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        log.info("No persisted config — using schema defaults")
        return
    merged = {k: v.default for k, v in _schema.items()}
    merged.update({k: v for k, v in config.items() if k in _schema and not isinstance(v, (dict, list))})
    _config = merged
    log.info("Config loaded: %r", _config)

    # Apply initial selections and volume immediately.
    bus.publish("audio.sink.selected",   {"sink":   _config.get("sink",   "default")})
    bus.publish("audio.source.selected", {"source": _config.get("source", "default")})
    vol = _config.get("volume", 80)
    if _set_global_volume(vol):
        bus.publish("audio.volume.changed", {"volume": vol})


def _on_config_changed(key: str, value: Any) -> None:
    if key not in _schema:
        log.warning("config.changed: unknown key %r — ignoring", key)
        return
    if isinstance(value, (dict, list)):
        log.warning("config.changed: structural value for %r rejected", key)
        return
    _config[key] = value
    log.info("Config changed: %s = %r", key, value)

    if key == "sink":
        bus.publish("audio.sink.selected", {"sink": value})

    elif key == "source":
        bus.publish("audio.source.selected", {"source": value})

    elif key == "volume":
        vol = int(value)
        if _set_global_volume(vol):
            bus.publish("audio.volume.changed", {"volume": vol})

    elif key.startswith("volume_ch"):
        try:
            ch_id = int(key.split("volume_ch")[1])
        except (IndexError, ValueError):
            log.warning("config.changed: cannot parse channel id from key %r", key)
            return
        _set_channel_volume(ch_id, int(value))

    elif key == "poll_interval_s":
        log.info("poll_interval_s changed to %d — next poll will use new interval", value)


# ---------------------------------------------------------------------------
# Bus topic handlers
# ---------------------------------------------------------------------------

def on_audio_volume_set(topic: str, payload: dict) -> None:
    """Handle audio.volume.set {volume: int} — global volume via wpctl."""
    volume = payload.get("volume")
    if not isinstance(volume, int) or not (0 <= volume <= 100):
        log.warning("on_audio_volume_set: invalid payload %r", payload)
        return
    _config["volume"] = volume
    if _set_global_volume(volume):
        bus.publish("audio.volume.changed", {"volume": volume})


def on_audio_channel_volume_set(topic: str, payload: dict) -> None:
    """Handle audio.channel_volume.set {channel_id: int, volume: int}."""
    channel_id = payload.get("channel_id")
    volume     = payload.get("volume")
    if not isinstance(channel_id, int) or not isinstance(volume, int):
        log.warning("on_audio_channel_volume_set: invalid payload %r", payload)
        return
    if not (0 <= volume <= 100):
        log.warning("on_audio_channel_volume_set: volume %r out of range", volume)
        return
    cfg_key = f"volume_ch{channel_id}"
    _config[cfg_key] = volume
    _set_channel_volume(channel_id, volume)


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info("system.readytostart — announcing priority %d", PRIORITY)
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return

    log.info("system.start priority=%d — initialising...", PRIORITY)

    # Enumerate devices before requesting config so schema choices are
    # populated when config_manager echoes them back.
    _refresh_devices(publish=True)

    # Register dynamic schema (includes discovered device choices).
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.get(schema=_schema)

    # Start background threads.
    _start_poll_thread()
    _start_udev_thread()

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published (priority=%d)", PRIORITY)


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down audio_manager")
    _poll_stop.set()
    bus.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    cfg.register()

    bus.subscribe("system.readytostart",         on_system_readytostart)
    bus.subscribe("system.start",                on_system_start)
    bus.subscribe("system.stop",                 on_system_stop)
    bus.subscribe("audio.volume.set",            on_audio_volume_set)
    bus.subscribe("audio.channel_volume.set",    on_audio_channel_volume_set)

    log.info("audio_manager started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
