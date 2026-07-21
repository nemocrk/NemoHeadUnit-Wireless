"""
registry.py — resolve a channel descriptor dict → module_type string.

Routing logic
-------------
Each channel dict (as produced by service_discovery.channels_from_sdr_bytes)
has the shape::

    {"channel_id": <int>, "<descriptor_key>": { ... }}

For av_channel the dict also carries:
    "av_channel"    — AVStreamType int  (VIDEO=3, AUDIO=1)
    "audio_type" — AudioType int     (MEDIA=3, SPEECH=1, SYSTEM=2, ALARM=4)
                   only present when av_type == AUDIO

Routing is done on the *descriptor key* (the oneof field name) rather than
on channel_id, because channel_id values can vary across AA protocol versions
and phone manufacturers.

Module-type → subprocess mapping
---------------------------------
  module_type     script path
  ----------      ------------
  video           modules/channel_modules/channel_video/main.py
  audio           modules/channel_modules/channel_audio/main.py  (3 instances)
  input           modules/channel_modules/channel_input/main.py
  sensor          modules/channel_modules/channel_sensor/main.py

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

from oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType                     # noqa: E402
_AUDIO_CODEC_VALUES = frozenset({
    MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC_ADTS,
    MediaCodecType.MEDIA_CODEC_AUDIO_PCM,
    MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC,
})
_VIDEO_CODEC_VALUES = frozenset({
    MediaCodecType.MEDIA_CODEC_VIDEO_H264_BP,
    MediaCodecType.MEDIA_CODEC_VIDEO_VP9,
    MediaCodecType.MEDIA_CODEC_VIDEO_AV1,
    MediaCodecType.MEDIA_CODEC_VIDEO_H265,
})

# ---------------------------------------------------------------------------
# AudioType constants  (mirrors AudioTypeEnum proto)
# ---------------------------------------------------------------------------

from oaa.audio.AudioTypeEnum_pb2 import AudioType  # noqa: E402

AUDIO_TYPE_SPEECH  = AudioType.SPEECH
AUDIO_TYPE_SYSTEM  = AudioType.SYSTEM
AUDIO_TYPE_MEDIA   = AudioType.MEDIA
AUDIO_TYPE_ALARM   = AudioType.ALARM

# ---------------------------------------------------------------------------
# Descriptor keys that are known but not yet implemented
# ---------------------------------------------------------------------------

_SKIP_KEYS: frozenset[str] = frozenset({
    "navigation_channel",
    "media_info_channel",
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

        if av_type in _VIDEO_CODEC_VALUES:
            return "video"

        if av_type in _AUDIO_CODEC_VALUES:
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
    
    if "av_input_channel" in channel_descriptor:
        return "av_input"
    
    if "bluetooth_channel" in channel_descriptor:
        return "bluetooth"
    
    if "wifi_channel" in channel_descriptor:
        return "wifi"

    # --- Known but not-yet-implemented channels ---
    for skip_key in _SKIP_KEYS:
        if skip_key in channel_descriptor:
            raise SkipChannel(
                f"ch{channel_id}: {skip_key!r} has no module yet — skipping"
            )

    if list(channel_descriptor.keys()) == ["channel_id"]:
        raise SkipChannel(
            f"ch{channel_id}: no descriptor field set — skipping"
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
