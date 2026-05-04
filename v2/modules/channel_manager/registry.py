"""
registry.py — static mapping: channel descriptor → module_type.

Each entry maps a channel_id (as declared in the ServiceDiscoveryResponse)
to the module_type string used to derive the subprocess name and path:

  module_name  = channel_{module_type}_{channel_id}
  script_path  = v2/modules/channel_modules/channel_{module_type}/main.py

For av_channel the mapping is driven by av_channel.av_type:
  AV_TYPE_VIDEO  → "video"
  AV_TYPE_AUDIO  → "audio"

For all other channels it is a direct 1-to-1 mapping by channel_id.

WIP note: not all module_types have a corresponding channel_module yet.
If a script_path does not exist on disk channel_manager will raise and
abort session startup rather than silently ignoring the channel.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# AV sub-type constants (mirrors AVChannelMessage.AVType proto enum)
# ---------------------------------------------------------------------------

AV_TYPE_VIDEO  = 1
AV_TYPE_AUDIO  = 2

# ---------------------------------------------------------------------------
# Audio stream-type constants (mirrors AudioStreamType proto enum)
# ---------------------------------------------------------------------------

AUDIO_TYPE_MEDIA   = 1
AUDIO_TYPE_GUIDANCE = 2  # nav guidance
AUDIO_TYPE_SYSTEM  = 3
AUDIO_TYPE_SPEECH  = 4   # voice recognition

# ---------------------------------------------------------------------------
# Direct channel_id → module_type table (non-AV channels)
#
# channel_id values below mirror the SEMANTIC_DEFAULTS in service_discovery.py
# ---------------------------------------------------------------------------

# Channels whose module_type is determined purely by channel_id:
_DIRECT: dict[int, str] = {
    # ch 0  — control channel, handled by oaa_control_channel itself
    # ch 1  — input
    1:  "input",
    # ch 2  — sensor
    2:  "sensor",
    # ch 7  — media playback status
    7:  "media_status",
    # ch 8  — Bluetooth channel (different from the bluetooth *pairing* module)
    8:  "bluetooth",
    # ch 9  — navigation
    9:  "navigation",
    # ch 10 — media playback control
    10: "media_playback",
    # ch 11 — phone
    11: "phone",
    # ch 12 — notification
    12: "notification",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_module_type(channel_id: int, channel_descriptor: dict) -> str:
    """
    Return the module_type string for a given channel.

    Args:
        channel_id:           numeric channel id from the SDR channels list.
        channel_descriptor:   the channel dict as it appears in the SDR
                              (e.g. {"av_channel": {"av_type": 1, ...}}).

    Returns:
        module_type string (e.g. "video", "audio", "navigation").

    Raises:
        KeyError: if the channel_id cannot be resolved to a module_type.
    """
    # AV channels are identified by the presence of "av_channel" key
    if "av_channel" in channel_descriptor:
        av_type = channel_descriptor["av_channel"].get("av_type")
        if av_type == AV_TYPE_VIDEO:
            return "video"
        if av_type == AV_TYPE_AUDIO:
            return "audio"
        raise KeyError(
            f"ch{channel_id}: unknown av_type={av_type!r} in av_channel descriptor"
        )

    if channel_id in _DIRECT:
        return _DIRECT[channel_id]

    raise KeyError(
        f"ch{channel_id}: no module_type mapping found for descriptor "
        f"{list(channel_descriptor.keys())!r}"
    )


def module_name(module_type: str, channel_id: int) -> str:
    """Return the canonical MODULE_NAME for a channel subprocess.

    Pattern: channel_{module_type}_{channel_id}
    Example: channel_video_3, channel_audio_4, channel_navigation_9
    """
    return f"channel_{module_type}_{channel_id}"
