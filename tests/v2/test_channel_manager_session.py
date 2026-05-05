"""
Unit tests for v2/modules/channel_manager/main.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

_TESTS = Path(__file__).parent
_ROOT = _TESTS.parent.parent
_V2 = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in (str(_ROOT), str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

import v2.modules.channel_manager.main as cm  # noqa: E402


def test_three_audio_modules_launched(monkeypatch):
    session = cm.ChannelManagerSession()
    launch_calls = []

    def fake_start_all(channels):
        launch_calls.append(channels)
        return [ch["module_name"] for ch in channels]

    monkeypatch.setattr(session._launcher, "start_all", fake_start_all)

    channels = [
        {"channel_id": 3, "av_channel": {"av_type": 3}},
        {"channel_id": 4, "av_channel": {"av_type": 1, "audio_type": 3}},
        {"channel_id": 5, "av_channel": {"av_type": 1, "audio_type": 1}},
        {"channel_id": 6, "av_channel": {"av_type": 1, "audio_type": 2}},
        {"channel_id": 1, "input_channel": {}},
        {"channel_id": 2, "sensor_channel": {}},
    ]

    session.start("deadbeef", channels)

    assert len(launch_calls) == 1
    started = launch_calls[0]
    assert sum(1 for ch in started if ch["module_type"] == "audio") == 3
    assert {ch["module_name"] for ch in started if ch["module_type"] == "audio"} == {
        "channel_audio_4",
        "channel_audio_5",
        "channel_audio_6",
    }
    assert sum(1 for ch in started if ch["module_type"] == "video") == 1
    assert sum(1 for ch in started if ch["module_type"] == "input") == 1
    assert sum(1 for ch in started if ch["module_type"] == "sensor") == 1
