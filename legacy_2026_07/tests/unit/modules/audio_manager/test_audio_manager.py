"""
Unit tests for audio_manager/main.py

Strategy:
  - Enumeration helpers (_enum_sinks_wpctl, _enum_sinks_pactl, etc.) are tested
    by patching subprocess.run to return controlled stdout strings.
  - _build_schema is a pure function — tested without mocks.
  - Module-level functions (_refresh_devices, _on_config_loaded, _on_config_changed,
    on_audio_volume_set, on_audio_channel_volume_set, boot handlers) are tested
    using the `am` fixture which reloads the module with bus/cfg/log/subprocess mocked.
  - subprocess.run is always patched to avoid hitting real system commands.

Covers:
  Section 1 — _enum_sinks_wpctl: parses Sinks section, deduplicates, stops at next section,
               returns ["default"] on exception
  Section 2 — _enum_sinks_pactl: parses tab-separated output, deduplicates, fallback
  Section 3 — _enum_sources_wpctl: parses Sources section, excludes .monitor
  Section 4 — _enum_sources_pactl: parses tab-separated, excludes .monitor, fallback
  Section 5 — enumerate_sinks / enumerate_sources: wpctl preferred, pactl fallback
  Section 6 — _build_schema: keys, defaults, channel volume keys
  Section 7 — _get_sink_input_index: found/not-found/exception
  Section 8 — _set_global_volume / _set_channel_volume
  Section 9 — _refresh_devices: publishes lists + selected, fallback on missing device
  Section 10 — _on_config_loaded: merge, defaults, publishes sink/source/volume
  Section 11 — _on_config_changed: each key branch (sink/source/volume/volume_ch*/poll)
  Section 12 — on_audio_volume_set: valid, invalid type, out of range
  Section 13 — on_audio_channel_volume_set: valid, invalid type, out of range
  Section 14 — Boot handlers: readytostart, system.start, system.stop
"""

import sys
import importlib
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Module path
# ---------------------------------------------------------------------------
_MOD = "modules.audio_manager.main"


# ---------------------------------------------------------------------------
# Subprocess stdout helpers
# ---------------------------------------------------------------------------

_WPCTL_SINKS_OUTPUT = """\
PipeWire 'pipewire-0' [v0.3.65]
 Audio
  Sinks:
    *  47. alsa_output.pci-0000_00_1f.3.analog-stereo [vol: 0.80]
       51. bluez_output.AA_BB_CC.1 [vol: 1.00]
  Sources:
    *  48. alsa_input.pci-0000_00_1f.3.analog-stereo [vol: 1.00]
"""

_WPCTL_SOURCES_OUTPUT = """\
PipeWire 'pipewire-0' [v0.3.65]
 Audio
  Sinks:
    *  47. alsa_output.pci-0000_00_1f.3.analog-stereo [vol: 0.80]
  Sources:
    *  48. alsa_input.pci-0000_00_1f.3.analog-stereo [vol: 1.00]
       52. alsa_output.pci-0000_00_1f.3.analog-stereo.monitor [vol: 1.00]
"""

_PACTL_SINKS_OUTPUT = (
    "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\ts32le\tRUNNING\n"
    "1\tbluez_output.AA_BB_CC.1\tmodule-bluez5-device.c\ts16le\tIDLE\n"
)

_PACTL_SOURCES_OUTPUT = (
    "0\talsa_input.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\ts32le\tRUNNING\n"
    "1\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tmodule-alsa-card.c\ts32le\tIDLE\n"
)

_PACTL_SINK_INPUTS_VERBOSE = """\
Sink Input #12
\tDriver: protocol-native.c
\tOwner Module: n/a
\tClient: 7
\tSink: 47
\tSample Specification: float32le 2ch 44100Hz
\tProperties:
\t\tstream.name = "ch4"
"""


def _make_run_result(stdout: str, returncode: int = 0):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def am():
    """
    Reload audio_manager/main.py with all I/O dependencies mocked.
    Returns (mod, mock_bus, mock_cfg, mock_subprocess_run).
    """
    mock_bus = MagicMock()
    mock_cfg_inst = MagicMock()
    mock_log = MagicMock()

    # subprocess.run returns empty by default — individual tests override as needed
    mock_run = MagicMock(return_value=_make_run_result(""))

    for key in list(sys.modules.keys()):
        if "audio_manager" in key:
            del sys.modules[key]

    with patch("shared.bus_client.BusClient", return_value=mock_bus), \
         patch("shared.config_client.ConfigClient", return_value=mock_cfg_inst), \
         patch("shared.logger.get_logger", return_value=mock_log), \
         patch("subprocess.run", mock_run):
        import audio_manager.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus
        mod.cfg = mock_cfg_inst
        mod.log = mock_log
        mod._sinks   = ["default"]
        mod._sources = ["default"]
        mod._config  = {"sink": "default", "source": "default", "volume": 80,
                        "poll_interval_s": 30,
                        "volume_ch4": 100, "volume_ch6": 100, "volume_ch10": 100}
        mod._poll_stop.clear()
        yield mod, mock_bus, mock_cfg_inst, mock_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _topics(mock_bus) -> list[str]:
    return [c.args[0] for c in mock_bus.publish.call_args_list]


def _payload(mock_bus, topic: str) -> dict:
    for c in mock_bus.publish.call_args_list:
        if c.args[0] == topic:
            return c.args[1]
    return {}


# ===========================================================================
# Section 1 — _enum_sinks_wpctl
# ===========================================================================

class TestEnumSinksWpctl:

    @pytest.mark.unit
    def test_parses_sinks_section(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_WPCTL_SINKS_OUTPUT)):
            result = mod._enum_sinks_wpctl()
        assert "alsa_output.pci-0000_00_1f.3.analog-stereo" in result
        assert "bluez_output.AA_BB_CC.1" in result

    @pytest.mark.unit
    def test_always_starts_with_default(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_WPCTL_SINKS_OUTPUT)):
            result = mod._enum_sinks_wpctl()
        assert result[0] == "default"

    @pytest.mark.unit
    def test_does_not_include_sources(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_WPCTL_SINKS_OUTPUT)):
            result = mod._enum_sinks_wpctl()
        assert not any(".monitor" in s or "alsa_input" in s for s in result)

    @pytest.mark.unit
    def test_returns_default_on_exception(self, am):
        mod, *_ = am
        with patch("subprocess.run", side_effect=Exception("timeout")):
            result = mod._enum_sinks_wpctl()
        assert result == ["default"]

    @pytest.mark.unit
    def test_deduplicates(self, am):
        mod, *_ = am
        duplicate_output = _WPCTL_SINKS_OUTPUT + \
            "       47. alsa_output.pci-0000_00_1f.3.analog-stereo [vol: 0.80]\n"
        with patch("subprocess.run", return_value=_make_run_result(duplicate_output)):
            result = mod._enum_sinks_wpctl()
        assert result.count("alsa_output.pci-0000_00_1f.3.analog-stereo") == 1


# ===========================================================================
# Section 2 — _enum_sinks_pactl
# ===========================================================================

class TestEnumSinksPactl:

    @pytest.mark.unit
    def test_parses_tab_separated(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_PACTL_SINKS_OUTPUT)):
            result = mod._enum_sinks_pactl()
        assert "alsa_output.pci-0000_00_1f.3.analog-stereo" in result
        assert "bluez_output.AA_BB_CC.1" in result

    @pytest.mark.unit
    def test_starts_with_default(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_PACTL_SINKS_OUTPUT)):
            result = mod._enum_sinks_pactl()
        assert result[0] == "default"

    @pytest.mark.unit
    def test_returns_default_on_exception(self, am):
        mod, *_ = am
        with patch("subprocess.run", side_effect=Exception("not found")):
            result = mod._enum_sinks_pactl()
        assert result == ["default"]


# ===========================================================================
# Section 3 — _enum_sources_wpctl
# ===========================================================================

class TestEnumSourcesWpctl:

    @pytest.mark.unit
    def test_parses_sources_section(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_WPCTL_SOURCES_OUTPUT)):
            result = mod._enum_sources_wpctl()
        assert "alsa_input.pci-0000_00_1f.3.analog-stereo" in result

    @pytest.mark.unit
    def test_excludes_monitor_sources(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_WPCTL_SOURCES_OUTPUT)):
            result = mod._enum_sources_wpctl()
        assert not any(".monitor" in s for s in result)

    @pytest.mark.unit
    def test_returns_default_on_exception(self, am):
        mod, *_ = am
        with patch("subprocess.run", side_effect=OSError()):
            result = mod._enum_sources_wpctl()
        assert result == ["default"]


# ===========================================================================
# Section 4 — _enum_sources_pactl
# ===========================================================================

class TestEnumSourcesPactl:

    @pytest.mark.unit
    def test_parses_sources(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_PACTL_SOURCES_OUTPUT)):
            result = mod._enum_sources_pactl()
        assert "alsa_input.pci-0000_00_1f.3.analog-stereo" in result

    @pytest.mark.unit
    def test_excludes_monitor(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_PACTL_SOURCES_OUTPUT)):
            result = mod._enum_sources_pactl()
        assert not any(".monitor" in s for s in result)

    @pytest.mark.unit
    def test_returns_default_on_exception(self, am):
        mod, *_ = am
        with patch("subprocess.run", side_effect=Exception()):
            result = mod._enum_sources_pactl()
        assert result == ["default"]


# ===========================================================================
# Section 5 — enumerate_sinks / enumerate_sources
# ===========================================================================

class TestEnumerateSinksAndSources:

    @pytest.mark.unit
    def test_enumerate_sinks_prefers_wpctl(self, am):
        mod, *_ = am
        with patch.object(mod, "_enum_sinks_wpctl", return_value=["default", "sink_a"]), \
             patch.object(mod, "_enum_sinks_pactl", return_value=["default", "sink_b"]):
            result = mod.enumerate_sinks()
        assert result == ["default", "sink_a"]

    @pytest.mark.unit
    def test_enumerate_sinks_falls_back_to_pactl_if_wpctl_only_default(self, am):
        mod, *_ = am
        with patch.object(mod, "_enum_sinks_wpctl", return_value=["default"]), \
             patch.object(mod, "_enum_sinks_pactl", return_value=["default", "sink_b"]):
            result = mod.enumerate_sinks()
        assert result == ["default", "sink_b"]

    @pytest.mark.unit
    def test_enumerate_sources_prefers_wpctl(self, am):
        mod, *_ = am
        with patch.object(mod, "_enum_sources_wpctl", return_value=["default", "src_a"]), \
             patch.object(mod, "_enum_sources_pactl", return_value=["default", "src_b"]):
            result = mod.enumerate_sources()
        assert result == ["default", "src_a"]

    @pytest.mark.unit
    def test_enumerate_sources_falls_back_to_pactl(self, am):
        mod, *_ = am
        with patch.object(mod, "_enum_sources_wpctl", return_value=["default"]), \
             patch.object(mod, "_enum_sources_pactl", return_value=["default", "src_b"]):
            result = mod.enumerate_sources()
        assert result == ["default", "src_b"]


# ===========================================================================
# Section 6 — _build_schema
# ===========================================================================

class TestBuildSchema:

    @pytest.mark.unit
    def test_contains_all_keys(self, am):
        mod, *_ = am
        schema = mod._build_schema(["default"], ["default"])
        for key in ("sink", "source", "volume", "poll_interval_s",
                    "volume_ch4", "volume_ch6", "volume_ch10"):
            assert key in schema

    @pytest.mark.unit
    def test_volume_default_is_80(self, am):
        mod, *_ = am
        schema = mod._build_schema(["default"], ["default"])
        assert schema["volume"].default == 80

    @pytest.mark.unit
    def test_channel_volumes_default_is_100(self, am):
        mod, *_ = am
        schema = mod._build_schema(["default"], ["default"])
        for ch_id in (4, 6, 10):
            assert schema[f"volume_ch{ch_id}"].default == 100

    @pytest.mark.unit
    def test_sink_enum_uses_provided_sinks(self, am):
        mod, *_ = am
        sinks = ["default", "alsa_output.pci"]
        schema = mod._build_schema(sinks, ["default"])
        assert schema["sink"].choices == sinks

    @pytest.mark.unit
    def test_empty_sinks_fallback_to_default(self, am):
        mod, *_ = am
        schema = mod._build_schema([], ["default"])
        assert schema["sink"].choices == ["default"]


# ===========================================================================
# Section 7 — _get_sink_input_index
# ===========================================================================

class TestGetSinkInputIndex:

    @pytest.mark.unit
    def test_finds_stream_by_name(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_PACTL_SINK_INPUTS_VERBOSE)):
            result = mod._get_sink_input_index("ch4")
        assert result == 12

    @pytest.mark.unit
    def test_returns_none_when_not_found(self, am):
        mod, *_ = am
        with patch("subprocess.run", return_value=_make_run_result(_PACTL_SINK_INPUTS_VERBOSE)):
            result = mod._get_sink_input_index("ch99")
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_exception(self, am):
        mod, *_ = am
        with patch("subprocess.run", side_effect=Exception("broken")):
            result = mod._get_sink_input_index("ch4")
        assert result is None


# ===========================================================================
# Section 8 — _set_global_volume / _set_channel_volume
# ===========================================================================

class TestVolumeControl:

    @pytest.mark.unit
    def test_set_global_volume_calls_wpctl(self, am):
        mod, _, _, mock_run = am
        mock_run.reset_mock()
        mock_run.return_value = _make_run_result("")
        mod._set_global_volume(75)
        cmd = mock_run.call_args.args[0]
        assert "wpctl" in cmd
        assert "75%" in cmd

    @pytest.mark.unit
    def test_set_global_volume_returns_true_on_success(self, am):
        mod, _, _, mock_run = am
        mock_run.return_value = _make_run_result("")
        assert mod._set_global_volume(50) is True

    @pytest.mark.unit
    def test_set_global_volume_returns_false_on_exception(self, am):
        mod, *_ = am
        with patch("subprocess.run", side_effect=Exception("wpctl missing")):
            assert mod._set_global_volume(50) is False

    @pytest.mark.unit
    def test_set_channel_volume_calls_pactl(self, am):
        mod, _, _, mock_run = am
        with patch.object(mod, "_get_sink_input_index", return_value=12):
            mock_run.reset_mock()
            mock_run.return_value = _make_run_result("")
            mod._set_channel_volume(4, 80)
        cmd = mock_run.call_args.args[0]
        assert "pactl" in cmd
        assert "set-sink-input-volume" in cmd

    @pytest.mark.unit
    def test_set_channel_volume_returns_false_when_no_index(self, am):
        mod, *_ = am
        with patch.object(mod, "_get_sink_input_index", return_value=None):
            assert mod._set_channel_volume(4, 80) is False


# ===========================================================================
# Section 9 — _refresh_devices
# ===========================================================================

class TestRefreshDevices:

    @pytest.mark.unit
    def test_publishes_sinks_and_sources_list(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "enumerate_sinks", return_value=["default", "sink_a"]), \
             patch.object(mod, "enumerate_sources", return_value=["default", "src_a"]):
            mock_bus.publish.reset_mock()
            mod._refresh_devices(publish=True)
        assert "audio.sinks.list" in _topics(mock_bus)
        assert "audio.sources.list" in _topics(mock_bus)

    @pytest.mark.unit
    def test_publishes_sink_selected(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "enumerate_sinks", return_value=["default"]), \
             patch.object(mod, "enumerate_sources", return_value=["default"]):
            mock_bus.publish.reset_mock()
            mod._refresh_devices(publish=True)
        assert "audio.sink.selected" in _topics(mock_bus)

    @pytest.mark.unit
    def test_fallback_to_default_when_configured_sink_disappears(self, am):
        mod, mock_bus, *_ = am
        mod._config["sink"] = "missing_sink"
        with patch.object(mod, "enumerate_sinks", return_value=["default"]), \
             patch.object(mod, "enumerate_sources", return_value=["default"]):
            mock_bus.publish.reset_mock()
            mod._refresh_devices(publish=True)
        payload = _payload(mock_bus, "audio.sink.selected")
        assert payload["sink"] == "default"

    @pytest.mark.unit
    def test_updates_module_sinks_list(self, am):
        mod, *_ = am
        with patch.object(mod, "enumerate_sinks", return_value=["default", "new_sink"]), \
             patch.object(mod, "enumerate_sources", return_value=["default"]):
            mod._refresh_devices(publish=True)
        assert "new_sink" in mod._sinks


# ===========================================================================
# Section 10 — _on_config_loaded
# ===========================================================================

class TestOnConfigLoaded:

    @pytest.mark.unit
    def test_empty_config_uses_defaults(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mod._on_config_loaded({})
        # Config unchanged from defaults injected by fixture
        assert mod._config["volume"] == 80

    @pytest.mark.unit
    def test_merges_persisted_values(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mod._on_config_loaded({"volume": 65, "sink": "alsa_output.pci"})
        assert mod._config["volume"] == 65
        assert mod._config["sink"] == "alsa_output.pci"

    @pytest.mark.unit
    def test_publishes_sink_selected(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mock_bus.publish.reset_mock()
            mod._on_config_loaded({"sink": "alsa_output.pci"})
        assert "audio.sink.selected" in _topics(mock_bus)

    @pytest.mark.unit
    def test_publishes_volume_changed_when_set_succeeds(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mock_bus.publish.reset_mock()
            mod._on_config_loaded({"volume": 70})
        assert "audio.volume.changed" in _topics(mock_bus)

    @pytest.mark.unit
    def test_rejects_structural_values(self, am):
        mod, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mod._on_config_loaded({"sink": {"nested": "bad"}})
        # Structural values are filtered out — sink stays at default
        assert mod._config["sink"] == "default"


# ===========================================================================
# Section 11 — _on_config_changed
# ===========================================================================

class TestOnConfigChanged:

    @pytest.mark.unit
    def test_sink_change_publishes_sink_selected(self, am):
        mod, mock_bus, *_ = am
        mock_bus.publish.reset_mock()
        mod._on_config_changed("sink", "alsa_output.pci")
        payload = _payload(mock_bus, "audio.sink.selected")
        assert payload["sink"] == "alsa_output.pci"

    @pytest.mark.unit
    def test_source_change_publishes_source_selected(self, am):
        mod, mock_bus, *_ = am
        mock_bus.publish.reset_mock()
        mod._on_config_changed("source", "alsa_input.pci")
        payload = _payload(mock_bus, "audio.source.selected")
        assert payload["source"] == "alsa_input.pci"

    @pytest.mark.unit
    def test_volume_change_calls_set_global_volume(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True) as mock_sv:
            mod._on_config_changed("volume", 55)
        mock_sv.assert_called_once_with(55)

    @pytest.mark.unit
    def test_volume_change_publishes_volume_changed(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mock_bus.publish.reset_mock()
            mod._on_config_changed("volume", 55)
        assert "audio.volume.changed" in _topics(mock_bus)

    @pytest.mark.unit
    def test_volume_ch_calls_set_channel_volume(self, am):
        mod, *_ = am
        with patch.object(mod, "_set_channel_volume", return_value=True) as mock_cv:
            mod._on_config_changed("volume_ch4", 90)
        mock_cv.assert_called_once_with(4, 90)

    @pytest.mark.unit
    def test_poll_interval_change_no_crash(self, am):
        mod, *_ = am
        mod._on_config_changed("poll_interval_s", 60)  # must not raise
        assert mod._config["poll_interval_s"] == 60

    @pytest.mark.unit
    def test_unknown_key_ignored(self, am):
        mod, *_ = am
        mod._on_config_changed("unknown_key", "value")  # must not raise
        assert "unknown_key" not in mod._config

    @pytest.mark.unit
    def test_structural_value_rejected(self, am):
        mod, *_ = am
        original = mod._config["sink"]
        mod._on_config_changed("sink", {"nested": "bad"})
        assert mod._config["sink"] == original


# ===========================================================================
# Section 12 — on_audio_volume_set
# ===========================================================================

class TestOnAudioVolumeSet:

    @pytest.mark.unit
    def test_valid_volume_publishes_changed(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mock_bus.publish.reset_mock()
            mod.on_audio_volume_set("audio.volume.set", {"volume": 60})
        payload = _payload(mock_bus, "audio.volume.changed")
        assert payload["volume"] == 60

    @pytest.mark.unit
    def test_valid_volume_updates_config(self, am):
        mod, _, _, mock_run = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mod.on_audio_volume_set("audio.volume.set", {"volume": 42})
        assert mod._config["volume"] == 42

    @pytest.mark.unit
    def test_invalid_type_no_publish(self, am):
        mod, mock_bus, *_ = am
        mock_bus.publish.reset_mock()
        mod.on_audio_volume_set("audio.volume.set", {"volume": "loud"})
        assert "audio.volume.changed" not in _topics(mock_bus)

    @pytest.mark.unit
    def test_out_of_range_no_publish(self, am):
        mod, mock_bus, *_ = am
        mock_bus.publish.reset_mock()
        mod.on_audio_volume_set("audio.volume.set", {"volume": 150})
        assert "audio.volume.changed" not in _topics(mock_bus)

    @pytest.mark.unit
    def test_boundary_0_is_valid(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mock_bus.publish.reset_mock()
            mod.on_audio_volume_set("audio.volume.set", {"volume": 0})
        assert "audio.volume.changed" in _topics(mock_bus)

    @pytest.mark.unit
    def test_boundary_100_is_valid(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_set_global_volume", return_value=True):
            mock_bus.publish.reset_mock()
            mod.on_audio_volume_set("audio.volume.set", {"volume": 100})
        assert "audio.volume.changed" in _topics(mock_bus)


# ===========================================================================
# Section 13 — on_audio_channel_volume_set
# ===========================================================================

class TestOnAudioChannelVolumeSet:

    @pytest.mark.unit
    def test_valid_payload_calls_set_channel_volume(self, am):
        mod, *_ = am
        with patch.object(mod, "_set_channel_volume") as mock_cv:
            mod.on_audio_channel_volume_set(
                "audio.channel_volume.set", {"channel_id": 4, "volume": 80}
            )
        mock_cv.assert_called_once_with(4, 80)

    @pytest.mark.unit
    def test_valid_payload_updates_config(self, am):
        mod, *_ = am
        with patch.object(mod, "_set_channel_volume"):
            mod.on_audio_channel_volume_set(
                "audio.channel_volume.set", {"channel_id": 4, "volume": 75}
            )
        assert mod._config["volume_ch4"] == 75

    @pytest.mark.unit
    def test_invalid_type_no_call(self, am):
        mod, *_ = am
        with patch.object(mod, "_set_channel_volume") as mock_cv:
            mod.on_audio_channel_volume_set(
                "t", {"channel_id": "four", "volume": 80}
            )
        mock_cv.assert_not_called()

    @pytest.mark.unit
    def test_out_of_range_no_call(self, am):
        mod, *_ = am
        with patch.object(mod, "_set_channel_volume") as mock_cv:
            mod.on_audio_channel_volume_set(
                "t", {"channel_id": 4, "volume": 200}
            )
        mock_cv.assert_not_called()


# ===========================================================================
# Section 14 — Boot handlers
# ===========================================================================

class TestBootHandlers:

    @pytest.mark.unit
    def test_readytostart_publishes_module_ready(self, am):
        mod, mock_bus, *_ = am
        mock_bus.publish.reset_mock()
        mod.on_system_readytostart()
        payload = _payload(mock_bus, "system.module_ready")
        assert payload == {"name": "audio_manager", "priority": 1}

    @pytest.mark.unit
    def test_system_start_wrong_priority_no_action(self, am):
        mod, mock_bus, *_ = am
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 5})
        assert "system.ready" not in _topics(mock_bus)

    @pytest.mark.unit
    def test_system_start_correct_priority_publishes_ready(self, am):
        mod, mock_bus, *_ = am
        with patch.object(mod, "_refresh_devices"), \
             patch.object(mod, "_start_poll_thread"), \
             patch.object(mod, "_start_udev_thread"):
            mock_bus.publish.reset_mock()
            mod.on_system_start("system.start", {"priority": 1})
            mod._on_config_loaded({"volume": 80})
        assert "system.ready" in _topics(mock_bus)

    @pytest.mark.unit
    def test_system_start_calls_refresh_devices(self, am):
        mod, *_ = am
        with patch.object(mod, "_refresh_devices") as mock_refresh, \
             patch.object(mod, "_start_poll_thread"), \
             patch.object(mod, "_start_udev_thread"):
            mod.on_system_start("system.start", {"priority": 1})
        mock_refresh.assert_called_once_with(publish=True)

    @pytest.mark.unit
    def test_system_stop_calls_bus_stop(self, am):
        mod, mock_bus, *_ = am
        mod.on_system_stop("system.stop", {})
        mock_bus.stop.assert_called()

    @pytest.mark.unit
    def test_system_stop_sets_poll_stop_event(self, am):
        mod, mock_bus, *_ = am
        mod.on_system_stop("system.stop", {})
        assert mod._poll_stop.is_set()
