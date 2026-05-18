"""
NemoHeadUnit-Wireless v2 — Integration Tests: Channel Lifecycle
================================================================
Fase 2 — Integration Test §2

Scope: ChannelManagerSession + bus ZMQ in-process.
Strategy: la sessione viene testata iniettando direttamente le sue dipendenze
(Launcher mockato, bus reale in-process) anziché avviare sottoprocessi reali.
Questo permette di testare tutta la logica di stato (module_ready, shutdown,
crash, timeout) senza I/O di sistema.

Approach ibrido:
  Gruppo A — ChannelManagerSession unit-in-integration:
    Launcher patchato con MagicMock; bus ZMQ reale usato per pubblicare/ricevere
    i topic channel_manager.* e verificare i side-effect sul bus.

  Gruppo B — Bus event routing (handler functions):
    Le funzioni on_* di channel_manager/main.py vengono invocate su un bus
    ZMQ in-process reale, verificando i topic pubblicati.

Marker: @pytest.mark.integration
Dipendenze: conftest.in_process_broker, conftest._TestBusClient
Rif: docs/TEST_SUITE_ARCHITECTURE.md §3.2
"""
from __future__ import annotations

import threading
import time
import uuid
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait(lst: list, count: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(lst) >= count:
            return True
        time.sleep(0.01)
    return False


def _make_bus_client(in_process_broker, name: str = None):
    """BusClient connesso al broker in-process."""
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
    from shared.bus_client import BusClient
    c = BusClient(module_name=name or f"t_{uuid.uuid4().hex[:6]}")
    return c


def _make_session(in_process_broker) -> "ChannelManagerSession":
    """Crea una ChannelManagerSession con il bus patchato al broker in-process."""
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]

    # Importa il modulo con il bus già patchato
    import importlib
    import channel_manager.main as cm_main
    importlib.reload(cm_main)  # garantisce il bus fresco con i nuovi indirizzi
    cm_main.resolve_module_type = lambda channel_id, ch: ch.get("type", "audio")
    cm_main.module_name = lambda module_type, channel_id: f"{module_type}_{channel_id}"

    return cm_main.ChannelManagerSession()


def _minimal_channels(n: int = 1) -> list[dict]:
    """n descrittori di canale minimali per i test."""
    # channel_id 0 = control channel (sempre skippato)
    # channel_id 1+ = canali media
    return [{"channel_id": i + 1, "type": "audio"} for i in range(n)]


def _sdr_hex() -> str:
    return uuid.uuid4().hex


# ===========================================================================
# Gruppo 1 — ChannelManagerSession: start e tracking module_ready
# ===========================================================================

class TestSessionStart:
    """Verifica la logica di avvio sessione e tracking readiness."""

    @pytest.mark.integration
    def test_session_starts_without_error(self, in_process_broker):
        """ChannelManagerSession.start() con Launcher mockato non solleva."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher

        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        mock_launcher.start_all.assert_called_once()

    @pytest.mark.integration
    def test_control_channel_skipped(self, in_process_broker):
        """channel_id=0 (control channel) non viene inoltrato al Launcher."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = []
        session._launcher = mock_launcher

        session.start(_sdr_hex(), [{"channel_id": 0}])
        # start_all deve essere chiamato con lista vuota
        args = mock_launcher.start_all.call_args[0][0]
        assert all(ch["channel_id"] != 0 for ch in args)

    @pytest.mark.integration
    def test_expected_set_populated_after_start(self, in_process_broker):
        """Dopo start(), _expected contiene i nomi dei moduli lanciati."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "video_1"]
        session._launcher = mock_launcher

        session.start(_sdr_hex(), [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "video"},
        ])
        assert session._expected == {"audio_1", "video_1"}

    @pytest.mark.integration
    def test_on_module_ready_updates_ready_set(self, in_process_broker):
        """on_module_ready() aggiunge il nome a _ready."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        # Simula il messaggio in arrivo
        session.on_module_ready("audio_1")
        assert "audio_1" in session._ready

    @pytest.mark.integration
    def test_all_ready_event_set_when_last_module_ready(self, in_process_broker):
        """_all_ready event viene set() quando l'ultimo modulo pubblica module_ready."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "sensor_2"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "sensor"},
        ])

        session.on_module_ready("audio_1")
        assert not session._all_ready.is_set()
        session.on_module_ready("sensor_2")
        assert session._all_ready.is_set()

    @pytest.mark.integration
    def test_unknown_module_ready_ignored(self, in_process_broker):
        """on_module_ready() con nome non atteso non modifica _ready."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.on_module_ready("unknown_module_99")
        assert "unknown_module_99" not in session._ready
        assert not session._all_ready.is_set()

    @pytest.mark.integration
    def test_on_module_ready_to_start_sends_module_start(self, in_process_broker):
        """on_module_ready_to_start() pubblica channel_manager.module_start sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_start", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.on_module_ready_to_start("audio_1", priority=1)
        ok = _wait(received, 1)
        spy.stop()

        assert ok, "channel_manager.module_start non ricevuto"
        assert received[0].get("priority") == 1

    @pytest.mark.integration
    def test_on_module_ready_to_start_unknown_name_no_publish(self, in_process_broker):
        """on_module_ready_to_start() con nome sconosciuto non pubblica module_start."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_start", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.on_module_ready_to_start("NOT_IN_EXPECTED", priority=1)
        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0

    @pytest.mark.integration
    def test_multiple_modules_ready_all_tracked(self, in_process_broker):
        """5 moduli tutti ready: _ready contiene tutti i 5 nomi."""
        names = [f"audio_{i + 1}" for i in range(5)]
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = names
        session._launcher = mock_launcher
        channels = [{"channel_id": i + 1, "type": "audio"} for i in range(5)]
        session.start(_sdr_hex(), channels)

        for n in names:
            session.on_module_ready(n)

        assert session._ready == set(names)
        assert session._all_ready.is_set()


# ===========================================================================
# Gruppo 2 — wait_all_ready: channels_ready pubblicato sul bus
# ===========================================================================

class TestWaitAllReady:
    """Verifica che wait_all_ready() pubblichi i topic corretti sul bus reale."""

    @pytest.mark.integration
    def test_channels_ready_published_after_all_ready(self, in_process_broker):
        """Quando tutti i moduli sono ready, viene pubblicato channel_manager.channels_ready."""
        received = []
        sdr = _sdr_hex()

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(sdr, [{"channel_id": 1, "type": "audio"}])

        # Simula il modulo che diventa ready PRIMA del wait
        session.on_module_ready("audio_1")

        result = session.wait_all_ready(sdr)
        ok = _wait(received, 1)
        spy.stop()

        assert result is True
        assert ok, "channel_manager.channels_ready non ricevuto"
        assert received[0].get("sdr_bytes_hex") == sdr

    @pytest.mark.integration
    def test_channels_ready_contains_correct_sdr(self, in_process_broker):
        """Il payload di channels_ready contiene esattamente lo sdr_bytes_hex passato."""
        received = []
        sdr = "deadbeef1234"

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(sdr, [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")
        session.wait_all_ready(sdr)

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["sdr_bytes_hex"] == "deadbeef1234"

    @pytest.mark.integration
    def test_aa_channel_open_published_per_active_module(self, in_process_broker):
        """wait_all_ready() pubblica aa.channel.open per ogni modulo attivo."""
        received = []
        sdr = _sdr_hex()

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("aa.channel.open", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "video_2"]
        session._launcher = mock_launcher
        session.start(sdr, [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "video"},
        ])
        session.on_module_ready("audio_1")
        session.on_module_ready("video_2")
        session.wait_all_ready(sdr)

        ok = _wait(received, 2)
        spy.stop()

        assert ok, f"Ricevuti solo {len(received)} aa.channel.open"
        names = {r["module_name"] for r in received}
        assert "audio_1" in names
        assert "video_2" in names

    @pytest.mark.integration
    def test_wait_all_ready_returns_false_on_timeout(self, in_process_broker):
        """wait_all_ready() ritorna False se i moduli non diventano ready entro il timeout."""
        import channel_manager.main as cm_main
        original_timeout = cm_main.CHILDREN_READY_TIMEOUT
        cm_main.CHILDREN_READY_TIMEOUT = 0.3  # timeout brevissimo per il test

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        # NON chiamiamo on_module_ready — timeout deve scattare

        result = session.wait_all_ready(_sdr_hex())
        cm_main.CHILDREN_READY_TIMEOUT = original_timeout

        assert result is False

    @pytest.mark.integration
    def test_wait_all_ready_no_channels_returns_true(self, in_process_broker):
        """Con lista vuota, documenta il comportamento corrente di timeout."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = []  # nessun modulo
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [])  # lista vuota

        sdr = _sdr_hex()
        result = session.wait_all_ready(sdr)
        spy.stop()

        assert result is True
        assert received == []

    @pytest.mark.integration
    def test_wait_all_ready_in_background_thread(self, in_process_broker):
        """wait_all_ready() può essere eseguito in un thread separato (non blocca il bus)."""
        received = []
        sdr = _sdr_hex()

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(sdr, [{"channel_id": 1, "type": "audio"}])

        results: list[bool] = []
        t = threading.Thread(target=lambda: results.append(session.wait_all_ready(sdr)))
        t.start()

        # Simula modulo che diventa ready dopo 200ms
        time.sleep(0.2)
        session.on_module_ready("audio_1")

        t.join(timeout=3.0)
        ok = _wait(received, 1)
        spy.stop()

        assert results == [True]
        assert ok


# ===========================================================================
# Gruppo 3 — Shutdown
# ===========================================================================

class TestSessionShutdown:
    """Verifica la sequenza di shutdown del ChannelManagerSession."""

    @pytest.mark.integration
    def test_shutdown_publishes_module_stop(self, in_process_broker):
        """shutdown() pubblica channel_manager.module_stop sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_stop", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")

        session.shutdown()
        ok = _wait(received, 1)
        spy.stop()

        assert ok, "channel_manager.module_stop non ricevuto"

    @pytest.mark.integration
    def test_shutdown_publishes_channel_manager_stopped(self, in_process_broker):
        """shutdown() pubblica channel_manager.stopped sul bus."""
        received = []
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.stopped", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")

        session.shutdown()
        ok = _wait(received, 1)
        spy.stop()

        assert ok, "channel_manager.stopped non ricevuto"

    @pytest.mark.integration
    def test_shutdown_publishes_aa_channel_close_per_active_module(self, in_process_broker):
        """shutdown() pubblica aa.channel.close per ogni modulo attivo."""
        received = []
        sdr = _sdr_hex()
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("aa.channel.close", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "sensor_2"]
        session._launcher = mock_launcher
        session.start(sdr, [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "sensor"},
        ])
        session.on_module_ready("audio_1")
        session.on_module_ready("sensor_2")
        session.wait_all_ready(sdr)  # popola _all_active_channels
        time.sleep(0.1)

        session.shutdown()
        ok = _wait(received, 2)
        spy.stop()

        assert ok, f"Ricevuti solo {len(received)} aa.channel.close"

    @pytest.mark.integration
    def test_shutdown_calls_launcher_stop_all(self, in_process_broker):
        """shutdown() chiama Launcher.stop_all()."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")

        session.shutdown()
        mock_launcher.stop_all.assert_called_once()

    @pytest.mark.integration
    def test_shutdown_sets_is_active_false(self, in_process_broker):
        """Dopo shutdown(), _is_active è False."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.shutdown()
        assert session._is_active is False

    @pytest.mark.integration
    def test_shutdown_clears_active_channels(self, in_process_broker):
        """Dopo shutdown(), _all_active_channels è vuota."""
        sdr = _sdr_hex()
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(sdr, [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")
        session.wait_all_ready(sdr)

        session.shutdown()
        assert session._all_active_channels == []

    @pytest.mark.integration
    def test_double_shutdown_does_not_crash(self, in_process_broker):
        """Chiamare shutdown() due volte non solleva eccezioni."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.shutdown()
        try:
            session.shutdown()
        except Exception as e:
            pytest.fail(f"Secondo shutdown() ha sollevato: {e}")


# ===========================================================================
# Gruppo 4 — module_stopped ACK tracking
# ===========================================================================

class TestModuleStoppedTracking:
    """Verifica il tracking degli ACK channel_manager.module_stopped."""

    @pytest.mark.integration
    def test_on_module_stopped_updates_stopped_set(self, in_process_broker):
        """on_module_stopped() aggiunge il nome a _stopped."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.on_module_stopped("audio_1")
        assert "audio_1" in session._stopped

    @pytest.mark.integration
    def test_all_stopped_event_set_when_all_ack(self, in_process_broker):
        """_all_stopped event viene set() quando tutti i moduli ACKano module_stop."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "sensor_2"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "sensor"},
        ])

        session.on_module_stopped("audio_1")
        assert not session._all_stopped.is_set()
        session.on_module_stopped("sensor_2")
        assert session._all_stopped.is_set()

    @pytest.mark.integration
    def test_unknown_module_stopped_ignored(self, in_process_broker):
        """on_module_stopped() con nome sconosciuto non modifica _stopped."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        session.on_module_stopped("ghost_module")
        assert "ghost_module" not in session._stopped

    @pytest.mark.integration
    def test_partial_stop_ack_not_all_stopped(self, in_process_broker):
        """Solo alcuni ACK ricevuti: _all_stopped non viene settato."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "video_2", "sensor_3"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "video"},
            {"channel_id": 3, "type": "sensor"},
        ])

        session.on_module_stopped("audio_1")
        session.on_module_stopped("video_2")
        # sensor_3 non ACKa
        assert not session._all_stopped.is_set()


# ===========================================================================
# Gruppo 5 — Bus event handlers (on_* functions)
# ===========================================================================

class TestBusEventHandlers:
    """Verifica che gli handler on_* di channel_manager/main.py reagiscano
    correttamente ai messaggi ricevuti sul bus ZMQ in-process reale."""

    @pytest.mark.integration
    def test_system_start_publishes_system_ready(self, in_process_broker):
        """on_system_start() con priority=2 pubblica system.ready."""
        received = []

        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        cm.on_system_start("system.start", {"priority": 2})
        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.ready non pubblicato"
        assert received[0].get("name") == "channel_manager"
        assert received[0].get("priority") == 2

    @pytest.mark.integration
    def test_system_start_wrong_priority_no_publish(self, in_process_broker):
        """on_system_start() con priority≠2 non pubblica system.ready."""
        received = []

        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        cm.on_system_start("system.start", {"priority": 99})
        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0

    @pytest.mark.integration
    def test_on_system_readytostart_publishes_module_ready(self, in_process_broker):
        """on_system_readytostart() pubblica system.module_ready con name e priority."""
        received = []

        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        cm.on_system_readytostart()
        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["name"] == "channel_manager"
        assert received[0]["priority"] == 2

    @pytest.mark.integration
    def test_on_oaa_open_channels_ignores_empty_payload(self, in_process_broker):
        """open_channels con payload vuoto non crea sessione né pubblica topic."""
        received = []

        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)
        cm._session = None

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        cm.on_oaa_control_channel_open_channels("oaa_control_channel.open_channels", {})
        time.sleep(0.2)
        spy.stop()

        assert cm._session is None
        assert len(received) == 0

    @pytest.mark.integration
    def test_on_aa_session_shutdown_calls_session_shutdown(self, in_process_broker):
        """on_aa_session_shutdown() chiama session.shutdown() e azzera _session."""
        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        mock_session = MagicMock()
        cm._session = mock_session

        cm.on_aa_session_shutdown("aa.session.shutdown", {})

        mock_session.shutdown.assert_called_once()
        assert cm._session is None

    @pytest.mark.integration
    def test_on_aa_session_shutdown_no_session_no_crash(self, in_process_broker):
        """on_aa_session_shutdown() senza sessione attiva non solleva."""
        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)
        cm._session = None

        try:
            cm.on_aa_session_shutdown("aa.session.shutdown", {})
        except Exception as e:
            pytest.fail(f"Ha sollevato con _session=None: {e}")

    @pytest.mark.integration
    def test_on_aa_session_restart_calls_session_shutdown(self, in_process_broker):
        """on_aa_session_restart() chiama session.shutdown() e azzera _session."""
        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        mock_session = MagicMock()
        cm._session = mock_session

        cm.on_aa_session_restart("aa.session.restart", {})

        mock_session.shutdown.assert_called_once()
        assert cm._session is None

    @pytest.mark.integration
    def test_on_module_ready_to_start_delegates_to_session(self, in_process_broker):
        """on_channel_manager_module_ready_to_start() delega alla sessione attiva."""
        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        mock_session = MagicMock()
        cm._session = mock_session

        cm.on_channel_manager_module_ready_to_start(
            "channel_manager.module_ready_to_start",
            {"name": "audio_1", "priority": 1}
        )

        mock_session.on_module_ready_to_start.assert_called_once_with("audio_1", 1)

    @pytest.mark.integration
    def test_on_module_ready_delegates_to_session(self, in_process_broker):
        """on_channel_manager_module_ready() delega alla sessione attiva."""
        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        mock_session = MagicMock()
        cm._session = mock_session

        cm.on_channel_manager_module_ready(
            "channel_manager.module_ready",
            {"name": "sensor_3", "priority": 1}
        )

        mock_session.on_module_ready.assert_called_once_with("sensor_3")

    @pytest.mark.integration
    def test_on_module_stopped_delegates_to_session(self, in_process_broker):
        """on_channel_manager_module_stopped() delega alla sessione attiva."""
        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)

        mock_session = MagicMock()
        cm._session = mock_session

        cm.on_channel_manager_module_stopped(
            "channel_manager.module_stopped",
            {"name": "video_2"}
        )

        mock_session.on_module_stopped.assert_called_once_with("video_2")


# ===========================================================================
# Gruppo 6 — Full lifecycle (end-to-end sul bus reale)
# ===========================================================================

class TestFullLifecycle:
    """Scenari completi publish→subscribe→response tramite bus ZMQ reale."""

    @pytest.mark.integration
    def test_full_open_then_shutdown_sequence_on_bus(self, in_process_broker):
        """
        Scenario:
          1. Spy si iscrive a channels_ready e module_stop
          2. Sessione parte con Launcher mockato
          3. Tutti i moduli diventano ready
          4. channels_ready viene ricevuto
          5. Sessione viene fermata
          6. module_stop e stopped vengono ricevuti
        """
        sdr = _sdr_hex()
        ready_received = []
        stop_received = []
        stopped_received = []

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: ready_received.append(p))
        spy.subscribe("channel_manager.module_stop", lambda t, p: stop_received.append(p))
        spy.subscribe("channel_manager.stopped", lambda t, p: stopped_received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1", "sensor_2"]
        session._launcher = mock_launcher
        session.start(sdr, [
            {"channel_id": 1, "type": "audio"},
            {"channel_id": 2, "type": "sensor"},
        ])

        session.on_module_ready("audio_1")
        session.on_module_ready("sensor_2")
        session.wait_all_ready(sdr)

        ok_ready = _wait(ready_received, 1)
        assert ok_ready, "channels_ready non ricevuto"

        session.shutdown()

        ok_stop = _wait(stop_received, 1)
        ok_stopped = _wait(stopped_received, 1)
        spy.stop()

        assert ok_stop, "module_stop non ricevuto"
        assert ok_stopped, "channel_manager.stopped non ricevuto"

    @pytest.mark.integration
    def test_two_sessions_sequential(self, in_process_broker):
        """Due sessioni sequenziali: la seconda parte dopo lo shutdown della prima."""
        sdr1 = _sdr_hex()
        sdr2 = _sdr_hex()
        ready_received = []

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.channels_ready", lambda t, p: ready_received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        # Sessione 1
        s1 = _make_session(in_process_broker)
        m1 = MagicMock()
        m1.start_all.return_value = ["audio_1"]
        s1._launcher = m1
        s1.start(sdr1, [{"channel_id": 1, "type": "audio"}])
        s1.on_module_ready("audio_1")
        s1.wait_all_ready(sdr1)
        s1.shutdown()

        ok1 = _wait(ready_received, 1)
        assert ok1
        assert ready_received[0]["sdr_bytes_hex"] == sdr1

        # Sessione 2
        s2 = _make_session(in_process_broker)
        m2 = MagicMock()
        m2.start_all.return_value = ["audio_1"]
        s2._launcher = m2
        s2.start(sdr2, [{"channel_id": 1, "type": "audio"}])
        s2.on_module_ready("audio_1")
        s2.wait_all_ready(sdr2)
        s2.shutdown()

        ok2 = _wait(ready_received, 2)
        spy.stop()

        assert ok2
        assert ready_received[1]["sdr_bytes_hex"] == sdr2

    @pytest.mark.integration
    def test_channels_ready_followed_by_aa_session_shutdown_on_bus(self, in_process_broker):
        """
        Pattern reale: channels_ready pubblicato → successivo aa.session.shutdown
        provoca correttamente module_stop sul bus.
        """
        sdr = _sdr_hex()
        stop_received = []

        import shared.bus_client as _bc
        _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
        _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
        import importlib
        import channel_manager.main as cm
        importlib.reload(cm)
        cm._session = None

        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_stop", lambda t, p: stop_received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        # Monta sessione manualmente nel modulo
        mock_session = MagicMock()
        cm._session = mock_session

        cm.on_aa_session_shutdown("aa.session.shutdown", {})
        time.sleep(0.1)
        spy.stop()

        mock_session.shutdown.assert_called_once()
        assert cm._session is None

    @pytest.mark.integration
    def test_module_ready_then_stopped_full_round_trip(self, in_process_broker):
        """
        Round trip completo:
          module_ready_to_start → module_start → module_ready →
          module_stop → module_stopped
        Tutto mediato dal bus ZMQ in-process reale.
        """
        sdr = _sdr_hex()
        module_start_received = []
        channels_ready_received = []
        module_stop_received = []

        # Spy su tutti i topic critici
        spy = _make_bus_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_start", lambda t, p: module_start_received.append(p))
        spy.subscribe("channel_manager.channels_ready", lambda t, p: channels_ready_received.append(p))
        spy.subscribe("channel_manager.module_stop", lambda t, p: module_stop_received.append(p))
        spy.start(blocking=False)
        time.sleep(0.1)

        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(sdr, [{"channel_id": 1, "type": "audio"}])

        # Step 1: module_ready_to_start
        session.on_module_ready_to_start("audio_1", priority=1)
        ok_start = _wait(module_start_received, 1)
        assert ok_start, "module_start non ricevuto"

        # Step 2: module_ready
        session.on_module_ready("audio_1")
        session.wait_all_ready(sdr)
        ok_ready = _wait(channels_ready_received, 1)
        assert ok_ready, "channels_ready non ricevuto"

        # Step 3: shutdown
        session.shutdown()
        ok_stop = _wait(module_stop_received, 1)
        spy.stop()

        assert ok_stop, "module_stop non ricevuto"

    @pytest.mark.integration
    def test_crash_check_false_when_not_active(self, in_process_broker):
        """check_crashes() ritorna False quando _is_active=False (nessuna sessione attiva)."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.check_crashes.return_value = []
        session._launcher = mock_launcher
        session._is_active = False

        result = session.check_crashes()
        assert result is False
        mock_launcher.check_crashes.assert_not_called()

    @pytest.mark.integration
    def test_crash_check_true_when_launcher_reports_crash(self, in_process_broker):
        """check_crashes() ritorna True quando Launcher segnala un crash."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.check_crashes.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session._is_active = True

        result = session.check_crashes()
        assert result is True

    @pytest.mark.integration
    def test_crash_check_false_when_no_crash(self, in_process_broker):
        """check_crashes() ritorna False quando nessun figlio è crashato."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.check_crashes.return_value = []
        session._launcher = mock_launcher
        session._is_active = True

        result = session.check_crashes()
        assert result is False


# ===========================================================================
# Gruppo 7 — Isolamento: messaggi non interferiscono tra sessioni
# ===========================================================================

class TestSessionIsolation:
    """Verifica che i messaggi di una sessione non contaminino la sessione successiva."""

    @pytest.mark.integration
    def test_module_ready_from_previous_session_ignored(self, in_process_broker):
        """module_ready per un modulo non in _expected viene ignorato."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])

        # Messaggio da sessione precedente (stale)
        session.on_module_ready("stale_module_from_old_session")
        assert "stale_module_from_old_session" not in session._ready
        assert not session._all_ready.is_set()

    @pytest.mark.integration
    def test_new_session_clears_ready_set(self, in_process_broker):
        """Ogni nuova sessione parte con _ready vuoto."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")

        # Ricominciamo
        mock_launcher.start_all.return_value = ["sensor_2"]
        session.start(_sdr_hex(), [{"channel_id": 2, "type": "sensor"}])
        assert "audio_1" not in session._ready
        assert session._ready == set()

    @pytest.mark.integration
    def test_new_session_clears_stopped_set(self, in_process_broker):
        """Ogni nuova sessione parte con _stopped vuoto."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        session.on_module_stopped("audio_1")

        mock_launcher.start_all.return_value = ["sensor_2"]
        session.start(_sdr_hex(), [{"channel_id": 2, "type": "sensor"}])
        assert "audio_1" not in session._stopped
        assert session._stopped == set()

    @pytest.mark.integration
    def test_new_session_clears_all_ready_event(self, in_process_broker):
        """Ogni nuova sessione resetta _all_ready event."""
        session = _make_session(in_process_broker)
        mock_launcher = MagicMock()
        mock_launcher.start_all.return_value = ["audio_1"]
        session._launcher = mock_launcher
        session.start(_sdr_hex(), [{"channel_id": 1, "type": "audio"}])
        session.on_module_ready("audio_1")
        assert session._all_ready.is_set()

        mock_launcher.start_all.return_value = ["sensor_2"]
        session.start(_sdr_hex(), [{"channel_id": 2, "type": "sensor"}])
        assert not session._all_ready.is_set()
