"""
Unit tests for v2/modules/channel_manager/registry.py
"""

import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).parent
_ROOT = _TESTS.parent.parent
_V2 = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in (str(_ROOT), str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

import v2.modules.channel_manager.registry as registry  # noqa: E402


def test_resolve_av_video_channel_type():
    module_type = registry.resolve_module_type(3, {"av_channel": {"av_type": 3}})
    assert module_type == "video"


def test_resolve_av_audio_channel_type():
    assert registry.resolve_module_type(4, {"av_channel": {"av_type": 1, "audio_type": 3}}) == "audio"
    assert registry.resolve_module_type(5, {"av_channel": {"av_type": 1, "audio_type": 1}}) == "audio"
    assert registry.resolve_module_type(6, {"av_channel": {"av_type": 1, "audio_type": 2}}) == "audio"


def test_audio_type_constants_match_proto():
    assert registry.AUDIO_TYPE_MEDIA == 3
    assert registry.AUDIO_TYPE_SPEECH == 1
    assert registry.AUDIO_TYPE_SYSTEM == 2
    assert registry.AUDIO_TYPE_ALARM == 4


def test_skip_channel_with_no_descriptor():
    with pytest.raises(registry.SkipChannel, match=r"ch10: no descriptor field set"):
        registry.resolve_module_type(10, {"channel_id": 10})
