"""
registry.py — resolve a channel descriptor dict → module_type string.

Routing logic
-------------
Each channel dict (as produced by service_discovery.channels_from_sdr_bytes)
has the shape::

    {"channel_id": <int>, "<descriptor_key>": { ... }}

For av_channel the dict also carries:
    "av_type"    — AVStreamType int  (VIDEO=1, AUDIO=2)
    "audio_type" — AudioType int     (MEDIA=1, SPEECH=4, SYSTEM=3)
                   only present when av_type == AUDIO

Routing is done on the *descriptor key* (the oneof field name) rather than
on channel_id, because channel_id values can vary across AA protocol versions
and phone manufacturers.

Module-type → subprocess mapping
---------------------------------
  module_type     script path
  ----------      ------------
  video           v2/modules/channel_modules/channel_video/main.py
  audio           v2/modules/channel_modules/channel_audio/main.py  (3 instances)
  input           v2/modules/channel_modules/channel_input/main.py
  sensor          v2/modules/channel_modules/channel_sensor/main.py

All other descriptor keys (av_input_channel, bluetooth_channel,
navigation_channel, media_info_channel, wifi_channel, phone_status_channel)
are not yet implemented — resolve_module_type() raises SkipChannel so
channel_manager can skip them with a warning instead of aborting.

Public API
----------
  resolve_module_type(channel_id, channel_descriptor) → str
      Raises KeyError   — unknown / unresolvable av_type combination
      Raises SkipChannel — known but not-yet-implemented channel type

  module_name(module_type, channel_id) → str
      Returns the canonical subprocess name, e.g. channel_video_3.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Sentinel exception — "known but not yet implemented"
# ---------------------------------------------------------------------------

class SkipChannel(Exception):
    """Raised when a channel is recognised but has no module yet.

    channel_manager catches this and logs a warning instead of aborting.
    """


# ---------------------------------------------------------------------------
# AVStreamType constants  (mirrors AVStreamTypeEnum proto)
# ---------------------------------------------------------------------------

AV_STREAM_VIDEO = 1   # AVStreamType.VIDEO
AV_STREAM_AUDIO = 2   # AVStreamType.AUDIO

# ---------------------------------------------------------------------------
# AudioType constants  (mirrors AudioTypeEnum proto)
# ---------------------------------------------------------------------------

AUDIO_TYPE_MEDIA   = 1   # AudioType.MEDIA
AUDIO_TYPE_SYSTEM  = 3   # AudioType.SYSTEM
AUDIO_TYPE_SPEECH  = 4   # AudioType.SPEECH

# ---------------------------------------------------------------------------
# Descriptor keys that are known but not yet implemented
# ---------------------------------------------------------------------------

_SKIP_KEYS: frozenset[str] = frozenset({
    "av_input_channel",
    "bluetooth_channel",
    "navigation_channel",
    "media_info_channel",
    "wifi_channel",
    "phone_status_channel",
})

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_module_type(channel_id: int, channel_descriptor: dict) -> str:
    """
    Return the module_type string for a given channel.

    Args:
        channel_id:          numeric channel id from the SDR channels list.
        channel_descriptor:  the channel dict as produced by
                             service_discovery.channels_from_sdr_bytes(), e.g.
                             {"av_channel": {"av_type": 1}} or
                             {"av_channel": {"av_type": 2, "audio_type": 1}} or
                             {"input_channel": {}}.

    Returns:
        module_type string: one of "video", "audio", "input", "sensor".

    Raises:
        SkipChannel: channel type is known but has no module yet.
        KeyError:    channel type is unrecognised or av_type/audio_type is
                     outside the expected range.
    """
    # --- AV channels ---
    if "av_channel" in channel_descriptor:
        av = channel_descriptor["av_channel"]
        av_type = av.get("av_type")

        if av_type == AV_STREAM_VIDEO:
            return "video"

        if av_type == AV_STREAM_AUDIO:
            audio_type = av.get("audio_type")
            if audio_type == AUDIO_TYPE_MEDIA:
                return "audio"
            if audio_type == AUDIO_TYPE_SPEECH:
                return "audio"
            if audio_type == AUDIO_TYPE_SYSTEM:
                return "audio"
            raise KeyError(
                f"ch{channel_id}: unknown audio_type={audio_type!r} "
                f"in av_channel descriptor"
            )

        raise KeyError(
            f"ch{channel_id}: unknown av_type={av_type!r} in av_channel descriptor"
        )

    # --- Direct descriptor-key mappings ---
    if "input_channel" in channel_descriptor:
        return "input"

    if "sensor_channel" in channel_descriptor:
        return "sensor"

    # --- Known but not-yet-implemented channels ---
    for skip_key in _SKIP_KEYS:
        if skip_key in channel_descriptor:
            raise SkipChannel(
                f"ch{channel_id}: {skip_key!r} has no module yet — skipping"
            )

    raise KeyError(
        f"ch{channel_id}: no module_type mapping found for descriptor keys "
        f"{list(channel_descriptor.keys())!r}"
    )


def module_name(module_type: str, channel_id: int) -> str:
    """Return the canonical MODULE_NAME for a channel subprocess.

    Pattern: channel_{module_type}_{channel_id}
    Examples: channel_video_3, channel_audio_4, channel_audio_5,
              channel_audio_6, channel_input_1, channel_sensor_2.
    """
    return f"channel_{module_type}_{channel_id}"
