"""
NemoHeadUnit-Wireless v2 — Integration Tests: AudioManager
===========================================================
Fase 2 — Integration Test §3

Scope: modules/audio_manager/main.py — handler functions e pubblicazioni bus.

Strategy: approccio ibrido identico a test_channel_lifecycle.py:
  - Bus ZMQ reale in-process (fixture in_process_broker)
  - subprocess.run patchato con MagicMock/patch per evitare I/O reale
    (wpctl / pactl non disponibili in CI)
  - Le funzioni on_* del modulo vengono invocate direttamente su un bus
    in-process reale, verificando i topic pubblicati.

Gruppi:
  1. Boot protocol (system.readytostart / system.start / system.stop)
  2. Volume handlers (audio.volume.set / audio.channel_volume.set)
  3. Config callbacks (_on_config_loaded / _on_config_changed)
  4. Device refresh (_refresh_devices)
  5. Schema builder (_build_schema)
  6. Robustezza e casi limite

Marker: @pytest.mark.integration
Dipendenze: conftest.in_process_broker
Rif: docs/TEST_SUITE_ARCHITECTURE.md §3.2
"""
from __future__ import annotations

import importlib
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait(lst: list, count: int, timeout: float = 3.0) -> bool:
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if len(lst) >= count:
            return True
        _t.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def cleanup_audio_manager_threads():
    yield
    try:
        import audio_manager.main as am
        if hasattr(am, "_poll_stop"):
            am._poll_stop.set()
        if hasattr(am, "_poll_thread") and am._poll_thread and am._poll_thread.is_alive():
            am._poll_thread.join(timeout=1.0)
    except Exception:
        pass


def _make_bus_client(in_process_broker, name: str | None = None):
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
    from shared.bus_client import BusClient
    return BusClient(module_name=name or f"t_{uuid.uuid4().hex[:6]}")


def _load_module(in_process_broker):
    """Import (or reload) audio_manager.main with the in-process broker."""
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]

    import audio_manager.main as am
    importlib.reload(am)
    return am


# ---------------------------------------------------------------------------
# Fake subprocess.run that simulates wpctl/pactl output
# ---------------------------------------------------------------------------

def _fake_subprocess_run(cmd, **kwargs):
    """Minimal subprocess.run stub that returns device-like output."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    if "wpctl" in cmd and "status" in cmd:
        result.stdout = (
            "Audio\n"
            " ├─ Sinks:\n"
            " │     51. alsa_output.pci-0000_00_1f.3.analog-stereo\n"
            " └─ Sources:\n"
            "       53. alsa_input.pci-0000_00_1f.3.analog-stereo\n"
        )
    elif "pactl" in cmd and "sinks" in cmd:
        result.stdout = "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le\tSUSPENDED\n"
    elif "pactl" in cmd and "sources" in cmd:
        result.stdout = "1\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le\tSUSPENDED\n"
    elif "wpctl" in cmd and "set-volume" in cmd:
        pass  # volume set — no output needed
    return result


# ===========================================================================
# Gruppo 1 — Boot protocol
# ===========================================================================

class TestBootProtocol:
    """Verifica on_system_readytostart, on_system_start, on_system_stop."""

    @pytest.mark.integration
    def test_readytostart_publishes_module_ready(self, in_process_broker):
        """on_system_readytostart() pubblica system.module_ready sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_system_readytostart()

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.module_ready non ricevuto"
        assert received[0]["name"] == "audio_manager"
        assert received[0]["priority"] == am.PRIORITY

    @pytest.mark.integration
    def test_readytostart_correct_priority(self, in_process_broker):
        """Il payload di system.module_ready contiene priority == PRIORITY del modulo."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_system_readytostart()

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["priority"] == 1

    @pytest.mark.integration
    def test_system_start_publishes_system_ready(self, in_process_broker):
        """on_system_start() con priority corretta pubblica system.ready sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            # cfg.get richiede config_manager — patch il ConfigClient
            am.cfg = MagicMock()
            am.on_system_start("system.start", {"priority": am.PRIORITY})
            # Trigger config loaded callback to announce system.ready
            am._on_config_loaded({"volume": 80})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.ready non ricevuto"
        assert received[0]["name"] == "audio_manager"

    @pytest.mark.integration
    def test_system_start_wrong_priority_ignored(self, in_process_broker):
        """on_system_start() con priority diversa NON pubblica system.ready."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.cfg = MagicMock()
            am.on_system_start("system.start", {"priority": am.PRIORITY + 99})

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0, "system.ready NON doveva essere pubblicato"

    @pytest.mark.integration
    def test_system_start_triggers_device_refresh(self, in_process_broker):
        """on_system_start() pubblica audio.sinks.list e audio.sources.list."""
        sinks_received = []
        sources_received = []

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sinks.list",   lambda t, p: sinks_received.append(p))
        spy.subscribe("audio.sources.list", lambda t, p: sources_received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.cfg = MagicMock()
            am.on_system_start("system.start", {"priority": am.PRIORITY})

        ok_sinks   = _wait(sinks_received, 1)
        ok_sources = _wait(sources_received, 1)
        spy.stop()

        assert ok_sinks,   "audio.sinks.list non ricevuto"
        assert ok_sources, "audio.sources.list non ricevuto"

    @pytest.mark.integration
    def test_system_stop_stops_bus(self, in_process_broker):
        """on_system_stop() chiama bus.stop() senza sollevare eccezioni."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            # Non deve sollevare
            try:
                am.on_system_stop("system.stop", {})
            except Exception as exc:
                pytest.fail(f"on_system_stop ha sollevato: {exc}")


# ===========================================================================
# Gruppo 2 — Volume handlers
# ===========================================================================

class TestVolumeHandlers:
    """Verifica on_audio_volume_set e on_audio_channel_volume_set."""

    @pytest.mark.integration
    def test_volume_set_publishes_volume_changed(self, in_process_broker):
        """audio.volume.set valido pubblica audio.volume.changed sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": 75})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.volume.changed non ricevuto"
        assert received[0]["volume"] == 75

    @pytest.mark.integration
    def test_volume_set_updates_config(self, in_process_broker):
        """on_audio_volume_set() aggiorna _config['volume']."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": 42})
            assert am._config.get("volume") == 42

    @pytest.mark.integration
    def test_volume_set_zero_is_valid(self, in_process_broker):
        """Volume 0 è valido e pubblica audio.volume.changed."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": 0})

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["volume"] == 0

    @pytest.mark.integration
    def test_volume_set_100_is_valid(self, in_process_broker):
        """Volume 100 è valido e pubblica audio.volume.changed."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": 100})

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["volume"] == 100

    @pytest.mark.integration
    def test_volume_set_out_of_range_ignored(self, in_process_broker):
        """Volume 101 è fuori range: audio.volume.changed NON viene pubblicato."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": 101})

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0, "audio.volume.changed non doveva essere pubblicato"

    @pytest.mark.integration
    def test_volume_set_negative_ignored(self, in_process_broker):
        """Volume -1 è fuori range: audio.volume.changed NON viene pubblicato."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": -1})

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0

    @pytest.mark.integration
    def test_volume_set_missing_key_ignored(self, in_process_broker):
        """Payload senza 'volume' non causa crash e non pubblica nulla."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            try:
                am.on_audio_volume_set("audio.volume.set", {})
            except Exception as exc:
                pytest.fail(f"on_audio_volume_set ha sollevato: {exc}")

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0

    @pytest.mark.integration
    def test_volume_set_string_value_ignored(self, in_process_broker):
        """Payload con volume string (non int) viene ignorato."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": "loud"})

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0

    @pytest.mark.integration
    def test_channel_volume_set_updates_config(self, in_process_broker):
        """on_audio_channel_volume_set() aggiorna _config['volume_ch4']."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            # Simula _get_sink_input_index non trovato (pacat non attivo)
            am._config["volume_ch4"] = 100
            am.on_audio_channel_volume_set(
                "audio.channel_volume.set",
                {"channel_id": 4, "volume": 60},
            )
            assert am._config.get("volume_ch4") == 60

    @pytest.mark.integration
    def test_channel_volume_set_missing_channel_id_ignored(self, in_process_broker):
        """Payload senza channel_id non causa crash."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            try:
                am.on_audio_channel_volume_set(
                    "audio.channel_volume.set",
                    {"volume": 50},
                )
            except Exception as exc:
                pytest.fail(f"on_audio_channel_volume_set ha sollevato: {exc}")

    @pytest.mark.integration
    def test_channel_volume_out_of_range_ignored(self, in_process_broker):
        """Volume canale 200 viene ignorato (fuori range 0-100)."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            original = am._config.get("volume_ch6", 100)
            am.on_audio_channel_volume_set(
                "audio.channel_volume.set",
                {"channel_id": 6, "volume": 200},
            )
            # _config non deve essere aggiornato
            assert am._config.get("volume_ch6", original) == original


# ===========================================================================
# Gruppo 3 — Config callbacks
# ===========================================================================

class TestConfigCallbacks:
    """Verifica _on_config_loaded e _on_config_changed."""

    @pytest.mark.integration
    def test_on_config_loaded_merges_persisted_values(self, in_process_broker):
        """_on_config_loaded() applica i valori persistiti su _config."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._on_config_loaded({"volume": 55, "poll_interval_s": 60})
            assert am._config["volume"] == 55
            assert am._config["poll_interval_s"] == 60

    @pytest.mark.integration
    def test_on_config_loaded_empty_uses_defaults(self, in_process_broker):
        """_on_config_loaded({}) lascia i default inalterati."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._on_config_loaded({})
            # Default volume = 80
            assert am._config.get("volume") == 80

    @pytest.mark.integration
    def test_on_config_loaded_publishes_sink_selected(self, in_process_broker):
        """_on_config_loaded() pubblica audio.sink.selected."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sink.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._sinks = ["default", "my_sink"]
            am._on_config_loaded({"sink": "my_sink", "volume": 50})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.sink.selected non ricevuto"
        assert received[0]["sink"] == "my_sink"

    @pytest.mark.integration
    def test_on_config_loaded_publishes_source_selected(self, in_process_broker):
        """_on_config_loaded() pubblica audio.source.selected."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.source.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._sources = ["default", "my_mic"]
            am._on_config_loaded({"source": "my_mic", "volume": 50})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.source.selected non ricevuto"
        assert received[0]["source"] == "my_mic"

    @pytest.mark.integration
    def test_on_config_changed_sink_publishes_sink_selected(self, in_process_broker):
        """_on_config_changed('sink', ...) pubblica audio.sink.selected."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sink.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._on_config_changed("sink", "alsa_output.pci.analog-stereo")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.sink.selected non ricevuto"
        assert received[0]["sink"] == "alsa_output.pci.analog-stereo"

    @pytest.mark.integration
    def test_on_config_changed_source_publishes_source_selected(self, in_process_broker):
        """_on_config_changed('source', ...) pubblica audio.source.selected."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.source.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._on_config_changed("source", "alsa_input.pci.analog-stereo")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.source.selected non ricevuto"
        assert received[0]["source"] == "alsa_input.pci.analog-stereo"

    @pytest.mark.integration
    def test_on_config_changed_volume_publishes_volume_changed(self, in_process_broker):
        """_on_config_changed('volume', 70) pubblica audio.volume.changed."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._on_config_changed("volume", 70)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.volume.changed non ricevuto"
        assert received[0]["volume"] == 70

    @pytest.mark.integration
    def test_on_config_changed_unknown_key_ignored(self, in_process_broker):
        """_on_config_changed con chiave sconosciuta non causa crash."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            try:
                am._on_config_changed("nonexistent_key_xyz", 42)
            except Exception as exc:
                pytest.fail(f"_on_config_changed ha sollevato con chiave sconosciuta: {exc}")

    @pytest.mark.integration
    def test_on_config_changed_structural_value_rejected(self, in_process_broker):
        """_on_config_changed con valore dict viene ignorato silenziosamente."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            original = am._config.get("volume", 80)
            am._on_config_changed("volume", {"nested": "object"})
            assert am._config.get("volume", 80) == original

    @pytest.mark.integration
    def test_on_config_changed_volume_ch_updates_config(self, in_process_broker):
        """_on_config_changed('volume_ch4', 80) aggiorna _config['volume_ch4']."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._on_config_changed("volume_ch4", 80)
            assert am._config.get("volume_ch4") == 80


# ===========================================================================
# Gruppo 4 — Device refresh
# ===========================================================================

class TestDeviceRefresh:
    """Verifica _refresh_devices e i topic pubblicati."""

    @pytest.mark.integration
    def test_refresh_publishes_sinks_list(self, in_process_broker):
        """_refresh_devices() pubblica audio.sinks.list sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sinks.list", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._refresh_devices(publish=True)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.sinks.list non ricevuto"
        assert "sinks" in received[0]
        assert isinstance(received[0]["sinks"], list)

    @pytest.mark.integration
    def test_refresh_publishes_sources_list(self, in_process_broker):
        """_refresh_devices() pubblica audio.sources.list sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sources.list", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._refresh_devices(publish=True)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.sources.list non ricevuto"
        assert "sources" in received[0]
        assert isinstance(received[0]["sources"], list)

    @pytest.mark.integration
    def test_refresh_publishes_sink_selected(self, in_process_broker):
        """_refresh_devices() pubblica audio.sink.selected sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sink.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._refresh_devices(publish=True)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.sink.selected non ricevuto"
        assert "sink" in received[0]

    @pytest.mark.integration
    def test_refresh_publishes_source_selected(self, in_process_broker):
        """_refresh_devices() pubblica audio.source.selected sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.source.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._refresh_devices(publish=True)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "audio.source.selected non ricevuto"
        assert "source" in received[0]

    @pytest.mark.integration
    def test_refresh_sinks_contains_default(self, in_process_broker):
        """La lista sinks pubblicata contiene sempre 'default'."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sinks.list", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._refresh_devices(publish=True)

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert "default" in received[0]["sinks"]

    @pytest.mark.integration
    def test_refresh_unavailable_sink_falls_back_to_default(self, in_process_broker):
        """Se il sink configurato non è più disponibile, viene usato 'default'."""
        received_sink = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sink.selected", lambda t, p: received_sink.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            # Sink configurato non presente nella lista enumerata
            am._config["sink"] = "ghost_sink_xyz"
            am._refresh_devices(publish=True)

        ok = _wait(received_sink, 1)
        spy.stop()

        assert ok
        assert received_sink[-1]["sink"] == "default"

    @pytest.mark.integration
    def test_refresh_unavailable_source_falls_back_to_default(self, in_process_broker):
        """Se il source configurato non è più disponibile, viene usato 'default'."""
        received_source = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.source.selected", lambda t, p: received_source.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._config["source"] = "ghost_source_xyz"
            am._refresh_devices(publish=True)

        ok = _wait(received_source, 1)
        spy.stop()

        assert ok
        assert received_source[-1]["source"] == "default"

    @pytest.mark.integration
    def test_refresh_publish_false_still_publishes_selected(self, in_process_broker):
        """_refresh_devices(publish=False) pubblica sink.selected / source.selected anche se la lista non cambia."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.sink.selected", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            # Prima chiamata: popola la lista interna
            am._refresh_devices(publish=True)
            received.clear()
            # Seconda chiamata: lista invariata, publish=False
            am._refresh_devices(publish=False)

        ok = _wait(received, 1, timeout=1.5)
        spy.stop()

        # selected viene sempre pubblicato, anche se la lista non cambia
        assert ok, "audio.sink.selected non pubblicato con publish=False"


# ===========================================================================
# Gruppo 5 — Schema builder
# ===========================================================================

class TestSchemaBuilder:
    """Verifica la funzione _build_schema."""

    @pytest.mark.integration
    def test_build_schema_contains_sink_key(self, in_process_broker):
        """_build_schema restituisce uno schema con chiave 'sink'."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
        schema = am._build_schema(["default", "sink_a"], ["default"])
        assert "sink" in schema

    @pytest.mark.integration
    def test_build_schema_contains_source_key(self, in_process_broker):
        """_build_schema restituisce uno schema con chiave 'source'."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
        schema = am._build_schema(["default"], ["default", "mic_a"])
        assert "source" in schema

    @pytest.mark.integration
    def test_build_schema_contains_volume_key(self, in_process_broker):
        """_build_schema restituisce uno schema con chiave 'volume'."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
        schema = am._build_schema(["default"], ["default"])
        assert "volume" in schema

    @pytest.mark.integration
    def test_build_schema_contains_per_channel_volumes(self, in_process_broker):
        """_build_schema include volume_ch4, volume_ch6, volume_ch10."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
        schema = am._build_schema(["default"], ["default"])
        for ch_id in (4, 6, 10):
            assert f"volume_ch{ch_id}" in schema, f"volume_ch{ch_id} mancante"

    @pytest.mark.integration
    def test_build_schema_empty_sinks_defaults_to_default(self, in_process_broker):
        """_build_schema con sinks=[] usa ['default'] come choices."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
        schema = am._build_schema([], ["default"])
        # Il campo sink deve esistere e avere default='default'
        assert schema["sink"].default == "default"

    @pytest.mark.integration
    def test_build_schema_poll_interval_has_correct_default(self, in_process_broker):
        """poll_interval_s ha default=30."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
        schema = am._build_schema(["default"], ["default"])
        assert schema["poll_interval_s"].default == 30


# ===========================================================================
# Gruppo 6 — Robustezza e casi limite
# ===========================================================================

class TestRobustness:
    """Comportamento del modulo in condizioni anomale."""

    @pytest.mark.integration
    def test_volume_set_wpctl_failure_no_publish(self, in_process_broker):
        """Se wpctl fallisce, audio.volume.changed NON viene pubblicato."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        def _failing_run(cmd, **kwargs):
            raise OSError("wpctl not found")

        with patch("subprocess.run", side_effect=_failing_run):
            am = _load_module(in_process_broker)
            am.on_audio_volume_set("audio.volume.set", {"volume": 50})

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0, "audio.volume.changed non doveva essere pubblicato"

    @pytest.mark.integration
    def test_readytostart_does_not_crash_without_bus_start(self, in_process_broker):
        """on_system_readytostart() non crasha se il bus non è stato avviato in blocking."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            try:
                am.on_system_readytostart()
            except Exception as exc:
                pytest.fail(f"on_system_readytostart ha sollevato: {exc}")

    @pytest.mark.integration
    def test_multiple_volume_set_sequence(self, in_process_broker):
        """5 volume.set consecutivi producono 5 audio.volume.changed."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("audio.volume.changed", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            for vol in (10, 20, 30, 40, 50):
                am.on_audio_volume_set("audio.volume.set", {"volume": vol})

        ok = _wait(received, 5)
        spy.stop()

        assert ok, f"Ricevuti solo {len(received)} su 5 audio.volume.changed"
        assert [r["volume"] for r in received] == [10, 20, 30, 40, 50]

    @pytest.mark.integration
    def test_config_changed_list_value_rejected(self, in_process_broker):
        """_on_config_changed con valore list viene ignorato silenziosamente."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            original = am._config.get("volume", 80)
            try:
                am._on_config_changed("volume", [1, 2, 3])
            except Exception as exc:
                pytest.fail(f"_on_config_changed ha sollevato con list value: {exc}")
            assert am._config.get("volume", 80) == original

    @pytest.mark.integration
    def test_system_stop_sets_poll_stop_event(self, in_process_broker):
        """on_system_stop() imposta _poll_stop so che il thread di polling si fermi."""
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            am = _load_module(in_process_broker)
            am._poll_stop.clear()
            am.on_system_stop("system.stop", {})
            assert am._poll_stop.is_set()
