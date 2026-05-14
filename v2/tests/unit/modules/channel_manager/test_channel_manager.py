"""
Unit tests for channel_manager/main.py and channel_manager/registry.py

Strategy:
  - registry.py is pure Python — imported directly, scope="module".
  - main.py has module-level singletons (bus, log, _session) — reloaded via
    _patch_cm fixture (autouse=False so registry tests are unaffected).
  - Launcher and BusClient are fully mocked.
  - ChannelManagerSession is tested in isolation by injecting a mock Launcher
    directly onto the instance.

Covers:
  Section 1 — registry.resolve_module_type:  av_channel VIDEO/AUDIO×3, input,
               sensor, av_input, bluetooth, wifi, SkipChannel, KeyError
  Section 2 — registry.module_name: naming convention
  Section 3 — ChannelManagerSession.start: channel list processing, skip, error
  Section 4 — ChannelManagerSession readiness tracking: on_module_ready,
               on_module_ready_to_start, on_module_stopped, wait_all_ready
  Section 5 — ChannelManagerSession.shutdown: bus publishes, launcher stop_all
  Section 6 — ChannelManagerSession.check_crashes: active/inactive state
  Section 7 — module-level handlers: system.readytostart, system.start,
               system.stop, open_channels, module_ready, module_stopped,
               aa.session.shutdown, aa.session.restart
"""

import sys
import importlib
import threading
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REGISTRY_MOD = "modules.channel_manager.registry"
_MAIN_MOD     = "modules.channel_manager.main"


@pytest.fixture(scope="module")
def reg():
    """Pure-Python registry module — stateless, import once per session."""
    if _REGISTRY_MOD in sys.modules:
        del sys.modules[_REGISTRY_MOD]
    import channel_manager.registry as mod
    importlib.reload(mod)
    return mod


@pytest.fixture()
def cm(tmp_path):
    """
    Reload channel_manager/main.py with BusClient and Launcher fully mocked.
    Returns (mod, mock_bus, mock_launcher_cls).
    """
    mock_bus_instance = MagicMock()
    mock_bus_instance.start.return_value = MagicMock()   # fake thread
    mock_launcher_cls = MagicMock()
    mock_launcher_instance = MagicMock()
    mock_launcher_cls.return_value = mock_launcher_instance
    mock_launcher_instance.start_all.return_value = set()
    mock_launcher_instance.check_crashes.return_value = []

    patches = {
        "shared.bus_client.BusClient":           MagicMock(return_value=mock_bus_instance),
        "shared.logger.get_logger":              MagicMock(return_value=MagicMock()),
        "modules.channel_manager.launcher.Launcher": mock_launcher_cls,
    }

    with patch.dict("sys.modules", {}):
        for mod_name in list(sys.modules.keys()):
            if _MAIN_MOD in mod_name:
                del sys.modules[mod_name]

    for key in [_MAIN_MOD]:
        if key in sys.modules:
            del sys.modules[key]

    with patch("shared.bus_client.BusClient", return_value=mock_bus_instance), \
         patch("shared.logger.get_logger", return_value=MagicMock()), \
         patch("modules.channel_manager.launcher.Launcher", mock_launcher_cls):
        import channel_manager.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus_instance
        mod._session = None
        yield mod, mock_bus_instance, mock_launcher_cls, mock_launcher_instance


def _make_session(mod, mock_launcher_instance):
    """Build a ChannelManagerSession with injected mock Launcher."""
    session = mod.ChannelManagerSession()
    session._launcher = mock_launcher_instance
    return session


# ===========================================================================
# Section 1 — registry.resolve_module_type
# ===========================================================================

class TestResolveModuleType:

    @pytest.mark.unit
    def test_av_video_returns_video(self, reg):
        ch = {"channel_id": 3, "av_channel": {"av_type": reg.AV_STREAM_VIDEO}}
        assert reg.resolve_module_type(3, ch) == "video"

    @pytest.mark.unit
    def test_av_audio_media_returns_audio(self, reg):
        ch = {"channel_id": 4, "av_channel": {"av_type": reg.AV_STREAM_AUDIO, "audio_type": reg.AUDIO_TYPE_MEDIA}}
        assert reg.resolve_module_type(4, ch) == "audio"

    @pytest.mark.unit
    def test_av_audio_speech_returns_audio(self, reg):
        ch = {"channel_id": 5, "av_channel": {"av_type": reg.AV_STREAM_AUDIO, "audio_type": reg.AUDIO_TYPE_SPEECH}}
        assert reg.resolve_module_type(5, ch) == "audio"

    @pytest.mark.unit
    def test_av_audio_system_returns_audio(self, reg):
        ch = {"channel_id": 6, "av_channel": {"av_type": reg.AV_STREAM_AUDIO, "audio_type": reg.AUDIO_TYPE_SYSTEM}}
        assert reg.resolve_module_type(6, ch) == "audio"

    @pytest.mark.unit
    def test_input_channel_returns_input(self, reg):
        ch = {"channel_id": 1, "input_channel": {}}
        assert reg.resolve_module_type(1, ch) == "input"

    @pytest.mark.unit
    def test_sensor_channel_returns_sensor(self, reg):
        ch = {"channel_id": 2, "sensor_channel": {}}
        assert reg.resolve_module_type(2, ch) == "sensor"

    @pytest.mark.unit
    def test_av_input_channel_returns_av_input(self, reg):
        ch = {"channel_id": 7, "av_input_channel": {}}
        assert reg.resolve_module_type(7, ch) == "av_input"

    @pytest.mark.unit
    def test_bluetooth_channel_returns_bluetooth(self, reg):
        ch = {"channel_id": 8, "bluetooth_channel": {}}
        assert reg.resolve_module_type(8, ch) == "bluetooth"

    @pytest.mark.unit
    def test_wifi_channel_returns_wifi(self, reg):
        ch = {"channel_id": 14, "wifi_channel": {}}
        assert reg.resolve_module_type(14, ch) == "wifi"

    @pytest.mark.unit
    def test_navigation_raises_skip_channel(self, reg):
        ch = {"channel_id": 9, "navigation_channel": {}}
        with pytest.raises(reg.SkipChannel):
            reg.resolve_module_type(9, ch)

    @pytest.mark.unit
    def test_media_info_raises_skip_channel(self, reg):
        ch = {"channel_id": 10, "media_info_channel": {}}
        with pytest.raises(reg.SkipChannel):
            reg.resolve_module_type(10, ch)

    @pytest.mark.unit
    def test_phone_status_raises_skip_channel(self, reg):
        ch = {"channel_id": 11, "phone_status_channel": {}}
        with pytest.raises(reg.SkipChannel):
            reg.resolve_module_type(11, ch)

    @pytest.mark.unit
    def test_only_channel_id_raises_skip_channel(self, reg):
        ch = {"channel_id": 99}
        with pytest.raises(reg.SkipChannel):
            reg.resolve_module_type(99, ch)

    @pytest.mark.unit
    def test_unknown_descriptor_key_raises_key_error(self, reg):
        ch = {"channel_id": 99, "totally_unknown_channel": {}}
        with pytest.raises(KeyError):
            reg.resolve_module_type(99, ch)

    @pytest.mark.unit
    def test_av_audio_unknown_audio_type_raises_key_error(self, reg):
        ch = {"channel_id": 4, "av_channel": {"av_type": reg.AV_STREAM_AUDIO, "audio_type": 99}}
        with pytest.raises(KeyError):
            reg.resolve_module_type(4, ch)

    @pytest.mark.unit
    def test_av_unknown_av_type_raises_key_error(self, reg):
        ch = {"channel_id": 3, "av_channel": {"av_type": 99}}
        with pytest.raises(KeyError):
            reg.resolve_module_type(3, ch)


# ===========================================================================
# Section 2 — registry.module_name
# ===========================================================================

class TestModuleName:

    @pytest.mark.unit
    def test_video_naming(self, reg):
        assert reg.module_name("video", 3) == "channel_video_3"

    @pytest.mark.unit
    def test_audio_naming(self, reg):
        assert reg.module_name("audio", 4) == "channel_audio_4"

    @pytest.mark.unit
    def test_input_naming(self, reg):
        assert reg.module_name("input", 1) == "channel_input_1"

    @pytest.mark.unit
    def test_sensor_naming(self, reg):
        assert reg.module_name("sensor", 2) == "channel_sensor_2"

    @pytest.mark.unit
    def test_pattern_is_channel_type_id(self, reg):
        name = reg.module_name("foo", 99)
        assert name == "channel_foo_99"


# ===========================================================================
# Section 3 — ChannelManagerSession.start
# ===========================================================================

class TestSessionStart:

    @pytest.mark.unit
    def test_start_calls_launcher_start_all(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = {"channel_video_3"}
        channels = [{"channel_id": 3, "av_channel": {"av_type": 3}}]
        session.start("aabbcc", channels)
        mock_launcher.start_all.assert_called_once()

    @pytest.mark.unit
    def test_start_skips_control_channel_0(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = set()
        channels = [{"channel_id": 0}]
        session.start("aabbcc", channels)
        args = mock_launcher.start_all.call_args[0][0]
        assert args == []

    @pytest.mark.unit
    def test_start_skips_skip_channel(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = set()
        channels = [{"channel_id": 9, "navigation_channel": {}}]
        session.start("aabbcc", channels)
        args = mock_launcher.start_all.call_args[0][0]
        assert args == []

    @pytest.mark.unit
    def test_start_raises_on_unknown_channel(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        channels = [{"channel_id": 99, "totally_unknown_channel": {}}]
        with pytest.raises(KeyError):
            session.start("aabbcc", channels)

    @pytest.mark.unit
    def test_start_sets_is_active(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = set()
        session.start("aabbcc", [])
        assert session._is_active is True

    @pytest.mark.unit
    def test_start_populates_expected_set(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = {"channel_video_3", "channel_audio_4"}
        channels = [
            {"channel_id": 3, "av_channel": {"av_type": 3}},
            {"channel_id": 4, "av_channel": {"av_type": 1, "audio_type": 3}},
        ]
        session.start("aabbcc", channels)
        assert session._expected == {"channel_video_3", "channel_audio_4"}

    @pytest.mark.unit
    def test_start_launch_list_contains_sdr_bytes_hex(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = {"channel_input_1"}
        channels = [{"channel_id": 1, "input_channel": {}}]
        session.start("deadbeef", channels)
        launch_list = mock_launcher.start_all.call_args[0][0]
        assert launch_list[0]["sdr_bytes_hex"] == "deadbeef"

    @pytest.mark.unit
    def test_start_multiple_audio_channels(self, cm):
        mod, mock_bus, _, mock_launcher = cm
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = {
            "channel_audio_4", "channel_audio_5", "channel_audio_6"
        }
        channels = [
            {"channel_id": 4, "av_channel": {"av_type": 1, "audio_type": 3}},
            {"channel_id": 5, "av_channel": {"av_type": 1, "audio_type": 1}},
            {"channel_id": 6, "av_channel": {"av_type": 1, "audio_type": 2}},
        ]
        session.start("ff", channels)
        assert session._expected == {"channel_audio_4", "channel_audio_5", "channel_audio_6"}


# ===========================================================================
# Section 4 — ChannelManagerSession readiness tracking
# ===========================================================================

class TestSessionReadiness:

    def _started_session(self, mod, mock_launcher, names):
        session = _make_session(mod, mock_launcher)
        mock_launcher.start_all.return_value = set(names)
        channels = []
        for i, name in enumerate(names, start=1):
            if "_sensor_" in name:
                channels.append({"channel_id": i, "sensor_channel": {}})
            elif "_video_" in name:
                channels.append({"channel_id": i, "av_channel": {"av_type": 3}})
            elif "_audio_" in name:
                channels.append({"channel_id": i, "av_channel": {"av_type": 1, "audio_type": 3}})
            else:
                channels.append({"channel_id": i, "input_channel": {}})
        session.start("aa", channels)
        return session

    @pytest.mark.unit
    def test_on_module_ready_adds_to_ready(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_input_1"])
        session.on_module_ready("channel_input_1")
        assert "channel_input_1" in session._ready

    @pytest.mark.unit
    def test_on_module_ready_unexpected_name_ignored(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_input_1"])
        session.on_module_ready("channel_ghost_99")
        assert "channel_ghost_99" not in session._ready

    @pytest.mark.unit
    def test_all_ready_event_set_when_all_modules_ready(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_input_1", "channel_sensor_2"])
        session.on_module_ready("channel_input_1")
        assert not session._all_ready.is_set()
        session.on_module_ready("channel_sensor_2")
        assert session._all_ready.is_set()

    @pytest.mark.unit
    def test_on_module_ready_to_start_publishes_module_start(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_video_3"])
        session.on_module_ready_to_start("channel_video_3", priority=5)
        mock_bus.publish.assert_called_with("channel_manager.module_start", {"priority": 5})

    @pytest.mark.unit
    def test_on_module_ready_to_start_unknown_name_ignored(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_video_3"])
        mock_bus.publish.reset_mock()
        session.on_module_ready_to_start("channel_ghost_99", priority=1)
        mock_bus.publish.assert_not_called()

    @pytest.mark.unit
    def test_on_module_stopped_adds_to_stopped(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_video_3"])
        session.on_module_stopped("channel_video_3")
        assert "channel_video_3" in session._stopped

    @pytest.mark.unit
    def test_on_module_stopped_unexpected_ignored(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_video_3"])
        session.on_module_stopped("channel_ghost")
        assert "channel_ghost" not in session._stopped

    @pytest.mark.unit
    def test_all_stopped_event_set_when_all_stopped(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_video_3", "channel_audio_4"])
        session.on_module_stopped("channel_video_3")
        assert not session._all_stopped.is_set()
        session.on_module_stopped("channel_audio_4")
        assert session._all_stopped.is_set()

    @pytest.mark.unit
    def test_wait_all_ready_publishes_channels_ready(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_input_1"])
        session.on_module_ready("channel_input_1")
        mock_bus.publish.reset_mock()
        result = session.wait_all_ready("hex123")
        assert result is True
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "channel_manager.channels_ready" in topics

    @pytest.mark.unit
    def test_wait_all_ready_channels_ready_payload(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_input_1"])
        session.on_module_ready("channel_input_1")
        mock_bus.publish.reset_mock()
        session.wait_all_ready("myhex")
        payload_calls = {c.args[0]: c.args[1] for c in mock_bus.publish.call_args_list}
        assert payload_calls["channel_manager.channels_ready"]["sdr_bytes_hex"] == "myhex"

    @pytest.mark.unit
    def test_wait_all_ready_timeout_returns_false(self, cm):
        mod, mock_bus, _, ml = cm
        session = self._started_session(mod, ml, ["channel_video_3"])
        # Don't fire on_module_ready — let it timeout
        mod.CHILDREN_READY_TIMEOUT = 0.05
        result = session.wait_all_ready("hex")
        mod.CHILDREN_READY_TIMEOUT = 10.0  # restore
        assert result is False


# ===========================================================================
# Section 5 — ChannelManagerSession.shutdown
# ===========================================================================

class TestSessionShutdown:

    @pytest.mark.unit
    def test_shutdown_publishes_module_stop(self, cm):
        mod, mock_bus, _, ml = cm
        session = _make_session(mod, ml)
        session.shutdown()
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "channel_manager.module_stop" in topics

    @pytest.mark.unit
    def test_shutdown_publishes_stopped(self, cm):
        mod, mock_bus, _, ml = cm
        session = _make_session(mod, ml)
        session.shutdown()
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "channel_manager.stopped" in topics

    @pytest.mark.unit
    def test_shutdown_calls_launcher_stop_all(self, cm):
        mod, mock_bus, _, ml = cm
        session = _make_session(mod, ml)
        session.shutdown()
        ml.stop_all.assert_called_once()

    @pytest.mark.unit
    def test_shutdown_sets_is_active_false(self, cm):
        mod, mock_bus, _, ml = cm
        session = _make_session(mod, ml)
        session._is_active = True
        session.shutdown()
        assert session._is_active is False

    @pytest.mark.unit
    def test_shutdown_publishes_aa_channel_close_for_active_channels(self, cm):
        mod, mock_bus, _, ml = cm
        session = _make_session(mod, ml)
        session._all_active_channels = [{"channel_id": 3, "module_name": "channel_video_3", "module_type": "video"}]
        mock_bus.publish.reset_mock()
        session.shutdown()
        topics = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "aa.channel.close" in topics

    @pytest.mark.unit
    def test_shutdown_clears_active_channels(self, cm):
        mod, mock_bus, _, ml = cm
        session = _make_session(mod, ml)
        session._all_active_channels = [{"channel_id": 3, "module_name": "x", "module_type": "y"}]
        session.shutdown()
        assert session._all_active_channels == []


# ===========================================================================
# Section 6 — ChannelManagerSession.check_crashes
# ===========================================================================

class TestSessionCheckCrashes:

    @pytest.mark.unit
    def test_check_crashes_returns_false_when_no_crashes(self, cm):
        mod, mock_bus, _, ml = cm
        ml.check_crashes.return_value = []
        session = _make_session(mod, ml)
        session._is_active = True
        result = session.check_crashes()
        assert result is False

    @pytest.mark.unit
    def test_check_crashes_returns_true_when_crash(self, cm):
        mod, mock_bus, _, ml = cm
        ml.check_crashes.return_value = ["channel_video_3"]
        session = _make_session(mod, ml)
        session._is_active = True
        result = session.check_crashes()
        assert result is True

    @pytest.mark.unit
    def test_check_crashes_skips_when_inactive(self, cm):
        mod, mock_bus, _, ml = cm
        ml.check_crashes.return_value = ["channel_video_3"]
        session = _make_session(mod, ml)
        session._is_active = False
        result = session.check_crashes()
        assert result is False
        ml.check_crashes.assert_not_called()


# ===========================================================================
# Section 7 — Module-level handlers
# ===========================================================================

class TestModuleLevelHandlers:

    @pytest.mark.unit
    def test_on_system_readytostart_publishes_module_ready(self, cm):
        mod, mock_bus, _, _ = cm
        mock_bus.publish.reset_mock()
        mod.on_system_readytostart()
        mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": mod.MODULE_NAME, "priority": mod.PRIORITY}
        )

    @pytest.mark.unit
    def test_on_system_start_wrong_priority_no_publish(self, cm):
        mod, mock_bus, _, _ = cm
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 99})
        mock_bus.publish.assert_not_called()

    @pytest.mark.unit
    def test_on_system_start_correct_priority_publishes_ready(self, cm):
        mod, mock_bus, _, _ = cm
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": mod.PRIORITY})
        mock_bus.publish.assert_called_once_with(
            "system.ready",
            {"name": mod.MODULE_NAME, "priority": mod.PRIORITY}
        )

    @pytest.mark.unit
    def test_on_system_stop_calls_bus_stop(self, cm):
        mod, mock_bus, _, _ = cm
        mod._session = None
        mod.on_system_stop("system.stop", {})
        mock_bus.stop.assert_called()

    @pytest.mark.unit
    def test_on_system_stop_shuts_down_active_session(self, cm):
        mod, mock_bus, _, ml = cm
        mock_session = MagicMock()
        mod._session = mock_session
        mod.on_system_stop("system.stop", {})
        mock_session.shutdown.assert_called_once()
        assert mod._session is None

    @pytest.mark.unit
    def test_open_channels_missing_sdr_does_not_start_session(self, cm):
        mod, mock_bus, _, _ = cm
        mod._session = None
        mod.on_oaa_control_channel_open_channels("t", {"sdr_bytes_hex": "", "channels": []})
        assert mod._session is None

    @pytest.mark.unit
    def test_open_channels_missing_channels_does_not_start_session(self, cm):
        mod, mock_bus, _, _ = cm
        mod._session = None
        mod.on_oaa_control_channel_open_channels("t", {"sdr_bytes_hex": "abc", "channels": []})
        assert mod._session is None

    @pytest.mark.unit
    def test_open_channels_kills_stale_session(self, cm):
        mod, mock_bus, _, ml = cm
        old_session = MagicMock()
        mod._session = old_session
        # Patch ChannelManagerSession to avoid real subprocess logic
        with patch.object(mod, "ChannelManagerSession") as mock_cls:
            fake_session = MagicMock()
            fake_session.start.side_effect = KeyError("forced")
            mock_cls.return_value = fake_session
            mod.on_oaa_control_channel_open_channels(
                "t", {"sdr_bytes_hex": "abc", "channels": [{"channel_id": 1}]}
            )
        old_session.shutdown.assert_called_once()

    @pytest.mark.unit
    def test_on_module_ready_delegated_to_session(self, cm):
        mod, mock_bus, _, _ = cm
        mock_session = MagicMock()
        mod._session = mock_session
        mod.on_channel_manager_module_ready("t", {"name": "channel_video_3"})
        mock_session.on_module_ready.assert_called_once_with("channel_video_3")

    @pytest.mark.unit
    def test_on_module_ready_no_session_no_crash(self, cm):
        mod, mock_bus, _, _ = cm
        mod._session = None
        mod.on_channel_manager_module_ready("t", {"name": "x"})  # must not raise

    @pytest.mark.unit
    def test_on_module_stopped_delegated_to_session(self, cm):
        mod, mock_bus, _, _ = cm
        mock_session = MagicMock()
        mod._session = mock_session
        mod.on_channel_manager_module_stopped("t", {"name": "channel_audio_4"})
        mock_session.on_module_stopped.assert_called_once_with("channel_audio_4")

    @pytest.mark.unit
    def test_on_aa_session_shutdown_clears_session(self, cm):
        mod, mock_bus, _, _ = cm
        mock_session = MagicMock()
        mod._session = mock_session
        mod.on_aa_session_shutdown("t", {})
        mock_session.shutdown.assert_called_once()
        assert mod._session is None

    @pytest.mark.unit
    def test_on_aa_session_restart_clears_session(self, cm):
        mod, mock_bus, _, _ = cm
        mock_session = MagicMock()
        mod._session = mock_session
        mod.on_aa_session_restart("t", {})
        mock_session.shutdown.assert_called_once()
        assert mod._session is None

    @pytest.mark.unit
    def test_on_aa_session_shutdown_no_session_no_crash(self, cm):
        mod, mock_bus, _, _ = cm
        mod._session = None
        mod.on_aa_session_shutdown("t", {})  # must not raise

    @pytest.mark.unit
    def test_on_aa_session_restart_no_session_no_crash(self, cm):
        mod, mock_bus, _, _ = cm
        mod._session = None
        mod.on_aa_session_restart("t", {})  # must not raise
