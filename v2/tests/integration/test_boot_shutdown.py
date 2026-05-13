"""
integration/test_boot_shutdown.py — Fase 2 §7

Testa la sequenza di boot e shutdown del sistema usando il bus ZMQ
in-process reale. Non esiste un system_controller separato: il boot
è coordinato dai singoli moduli tramite i topic:

  system.readytostart  →  ogni modulo pubblica system.module_ready {name, priority}
  system.start {priority}  →  modulo con priority matching risponde system.ready {name, priority}
  system.stop  →  ogni modulo fa cleanup e chiama bus.stop()

Approccio: usiamo BusClient in-process con spy per simulare N moduli
fittizi che seguono il boot protocol, verificando l'orchestrazione
come la farebbe un launcher reale.

Pattern:
  _make_client(broker, name)  — crea BusClient con BusTracer mockato
  _start_client(client)       — avvia receive loop
  _wait(lst, count, timeout)  — polling con deadline
  FakeModule                  — mini-classe che implementa il boot protocol
"""
from __future__ import annotations

import importlib
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(broker, name: str):
    """Crea BusClient con indirizzi in-process e BusTracer mockato."""
    pub_addr, sub_addr = broker
    with patch("shared.bus_client.BusTracer", return_value=MagicMock()):
        import shared.bus_client as _bc
        orig_pub = _bc.BROKER_PUB_ADDR
        orig_sub = _bc.BROKER_SUB_ADDR
        _bc.BROKER_PUB_ADDR = sub_addr  # client pubblica al SUB del broker
        _bc.BROKER_SUB_ADDR = pub_addr  # client riceve dal PUB del broker
        from shared.bus_client import BusClient
        client = BusClient(module_name=name)
        _bc.BROKER_PUB_ADDR = orig_pub
        _bc.BROKER_SUB_ADDR = orig_sub
    return client


def _start_client(client):
    client.start(blocking=False)
    time.sleep(0.05)
    return client


def _wait(lst: list, count: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(lst) >= count:
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# FakeModule: implementa il boot protocol per test
# ---------------------------------------------------------------------------

class FakeModule:
    """
    Simula un modulo NemoHeadUnit che segue il boot protocol.
    Si registra sul bus e risponde a system.readytostart / system.start / system.stop.
    """

    def __init__(self, broker, name: str, priority: int):
        self.name = name
        self.priority = priority
        self.client = _make_client(broker, name)
        self.module_ready_published: list[dict] = []
        self.system_ready_published: list[dict] = []
        self.stopped = False
        self._ready_events: list[dict] = []
        self._start_events: list[dict] = []
        self._stop_events: list[int] = []

        self.client.subscribe("system.readytostart", self._on_readytostart)
        self.client.subscribe("system.start", self._on_system_start)
        self.client.subscribe("system.stop", self._on_system_stop)

    def start(self):
        _start_client(self.client)

    def stop(self):
        self.client.stop()

    def _on_readytostart(self, topic=None, payload=None):
        self.client.publish("system.module_ready", {
            "name": self.name,
            "priority": self.priority,
        })
        self.module_ready_published.append({"name": self.name, "priority": self.priority})
        self._ready_events.append({"name": self.name, "priority": self.priority})

    def _on_system_start(self, topic=None, payload=None):
        if isinstance(payload, dict) and payload.get("priority") == self.priority:
            self.client.publish("system.ready", {
                "name": self.name,
                "priority": self.priority,
            })
            self.system_ready_published.append({"name": self.name, "priority": self.priority})
            self._start_events.append(payload)

    def _on_system_stop(self, topic=None, payload=None):
        self.stopped = True
        self._stop_events.append(1)


# ---------------------------------------------------------------------------
# Orchestrator: colleziona module_ready e invia system.start in ordine
# ---------------------------------------------------------------------------

class BootOrchestrator:
    """
    Simula il launcher/orchestratore che:
    1. Pubblica system.readytostart
    2. Raccoglie system.module_ready da tutti i moduli
    3. Invia system.start per ogni priority level in ordine crescente,
       aspettando system.ready da tutti i moduli di quella priority
    4. Pubblica system.stop
    """

    def __init__(self, broker):
        self.client = _make_client(broker, "orchestrator")
        self.module_ready_received: list[dict] = []
        self.system_ready_received: list[dict] = []

        self.client.subscribe("system.module_ready", self._on_module_ready)
        self.client.subscribe("system.ready", self._on_system_ready)

    def start(self):
        _start_client(self.client)

    def stop(self):
        self.client.stop()

    def _on_module_ready(self, topic=None, payload=None):
        if isinstance(payload, dict):
            self.module_ready_received.append(payload)

    def _on_system_ready(self, topic=None, payload=None):
        if isinstance(payload, dict):
            self.system_ready_received.append(payload)

    def publish_readytostart(self):
        self.client.publish("system.readytostart", {})

    def publish_start(self, priority: int):
        self.client.publish("system.start", {"priority": priority})

    def publish_stop(self):
        self.client.publish("system.stop", {})


# ---------------------------------------------------------------------------
# Gruppo 1 — single module boot protocol
# ---------------------------------------------------------------------------

class TestSingleModuleBoot:

    def test_module_responds_to_readytostart(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_a", priority=1)
        orch.start()
        mod.start()
        time.sleep(0.15)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 1)
        assert orch.module_ready_received[0]["name"] == "mod_a"
        assert orch.module_ready_received[0]["priority"] == 1

        mod.stop()
        orch.stop()

    def test_module_responds_to_system_start_correct_priority(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_b", priority=2)
        orch.start()
        mod.start()
        time.sleep(0.15)

        orch.publish_start(priority=2)
        assert _wait(orch.system_ready_received, 1)
        assert orch.system_ready_received[0]["name"] == "mod_b"
        assert orch.system_ready_received[0]["priority"] == 2

        mod.stop()
        orch.stop()

    def test_module_ignores_wrong_priority(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_c", priority=3)
        orch.start()
        mod.start()
        time.sleep(0.15)

        orch.publish_start(priority=1)  # wrong priority
        time.sleep(0.3)
        assert len(orch.system_ready_received) == 0
        assert len(mod.system_ready_published) == 0

        mod.stop()
        orch.stop()

    def test_module_responds_to_system_stop(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_d", priority=1)
        orch.start()
        mod.start()
        time.sleep(0.15)

        orch.publish_stop()
        assert _wait(mod._stop_events, 1)
        assert mod.stopped is True

        mod.stop()
        orch.stop()

    def test_module_ready_only_on_readytostart(self, in_process_broker):
        """system.module_ready non pubblicato prima di readytostart."""
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_e", priority=1)
        orch.start()
        mod.start()
        time.sleep(0.2)

        # Nessun readytostart pubblicato
        assert len(mod.module_ready_published) == 0

        mod.stop()
        orch.stop()


# ---------------------------------------------------------------------------
# Gruppo 2 — multi-module boot, sequential priorities
# ---------------------------------------------------------------------------

class TestMultiModuleBoot:

    def test_all_modules_respond_to_readytostart(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mods = [
            FakeModule(in_process_broker, "config_manager", priority=1),
            FakeModule(in_process_broker, "channel_manager", priority=2),
            FakeModule(in_process_broker, "video_ui", priority=3),
        ]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.2)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 3)
        names = {r["name"] for r in orch.module_ready_received}
        assert names == {"config_manager", "channel_manager", "video_ui"}

        for m in mods: m.stop()
        orch.stop()

    def test_sequential_start_by_priority(self, in_process_broker):
        """Ogni modulo risponde solo al proprio priority level."""
        orch = BootOrchestrator(in_process_broker)
        mod1 = FakeModule(in_process_broker, "config_manager", priority=1)
        mod2 = FakeModule(in_process_broker, "channel_manager", priority=2)
        orch.start()
        mod1.start()
        mod2.start()
        time.sleep(0.15)

        orch.publish_start(priority=1)
        assert _wait(orch.system_ready_received, 1, timeout=1.0)
        assert orch.system_ready_received[0]["name"] == "config_manager"
        # mod2 non ha ancora risposto
        assert len(mod2.system_ready_published) == 0

        orch.publish_start(priority=2)
        assert _wait(orch.system_ready_received, 2, timeout=1.0)
        assert orch.system_ready_received[1]["name"] == "channel_manager"

        mod1.stop()
        mod2.stop()
        orch.stop()

    def test_all_modules_receive_stop(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mods = [
            FakeModule(in_process_broker, "mod_1", priority=1),
            FakeModule(in_process_broker, "mod_2", priority=2),
            FakeModule(in_process_broker, "mod_3", priority=3),
        ]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.15)

        orch.publish_stop()
        for m in mods:
            assert _wait(m._stop_events, 1)
            assert m.stopped is True

        for m in mods: m.stop()
        orch.stop()

    def test_five_modules_all_priorities(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mods = [FakeModule(in_process_broker, f"mod_{i}", priority=i) for i in range(1, 6)]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.2)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 5)

        for priority in range(1, 6):
            orch.publish_start(priority=priority)
        assert _wait(orch.system_ready_received, 5, timeout=2.0)
        ready_names = [r["name"] for r in orch.system_ready_received]
        for i in range(1, 6):
            assert f"mod_{i}" in ready_names

        for m in mods: m.stop()
        orch.stop()

    def test_modules_with_same_priority_both_respond(self, in_process_broker):
        """Due moduli con stessa priority rispondono entrambi a system.start."""
        orch = BootOrchestrator(in_process_broker)
        mod_a = FakeModule(in_process_broker, "ui_a", priority=3)
        mod_b = FakeModule(in_process_broker, "ui_b", priority=3)
        orch.start()
        mod_a.start()
        mod_b.start()
        time.sleep(0.15)

        orch.publish_start(priority=3)
        assert _wait(orch.system_ready_received, 2, timeout=1.5)
        names = {r["name"] for r in orch.system_ready_received}
        assert names == {"ui_a", "ui_b"}

        mod_a.stop()
        mod_b.stop()
        orch.stop()


# ---------------------------------------------------------------------------
# Gruppo 3 — boot protocol con channel_manager reale
# ---------------------------------------------------------------------------

class TestChannelManagerBoot:
    """Testa il boot protocol del channel_manager reale sul bus in-process."""

    def _load_cm(self, broker):
        pub_addr, sub_addr = broker
        import modules.channel_manager.main as cm_main
        import importlib
        with patch("shared.bus_client.BusTracer", return_value=MagicMock()):
            import shared.bus_client as _bc
            _bc.BROKER_PUB_ADDR = sub_addr
            _bc.BROKER_SUB_ADDR = pub_addr
            importlib.reload(cm_main)
            # BusClient e logger ri-creati con indirizzi in-process
        return cm_main

    def test_cm_announces_on_readytostart(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        spy = _make_client(in_process_broker, "spy")
        received: list[dict] = []
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.15)

        cm.on_system_readytostart()
        assert _wait(received, 1)
        assert received[0]["name"] == "channel_manager"
        assert received[0]["priority"] == 2

        spy.stop()

    def test_cm_publishes_system_ready_on_start(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        spy = _make_client(in_process_broker, "spy2")
        received: list[dict] = []
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.15)

        cm.on_system_start("system.start", {"priority": 2})
        assert _wait(received, 1)
        assert received[0]["name"] == "channel_manager"
        assert received[0]["priority"] == 2

        spy.stop()

    def test_cm_ignores_wrong_priority(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        spy = _make_client(in_process_broker, "spy3")
        received: list[dict] = []
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.15)

        cm.on_system_start("system.start", {"priority": 1})
        time.sleep(0.3)
        assert len(received) == 0

        spy.stop()

    def test_cm_stop_cleans_session(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        fake_session = MagicMock()
        cm._session = fake_session

        cm.on_system_stop("system.stop", {})
        fake_session.shutdown.assert_called_once()
        assert cm._session is None

    def test_cm_stop_no_session_no_crash(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        cm._session = None
        cm.on_system_stop("system.stop", {})  # must not raise


# ---------------------------------------------------------------------------
# Gruppo 4 — full boot sequence su bus in-process
# ---------------------------------------------------------------------------

class TestFullBootSequence:

    def test_full_boot_two_modules_sequential(self, in_process_broker):
        """Boot completo: readytostart → start p1 → ready p1 → start p2 → ready p2."""
        orch = BootOrchestrator(in_process_broker)
        mod1 = FakeModule(in_process_broker, "config_manager", priority=1)
        mod2 = FakeModule(in_process_broker, "channel_manager", priority=2)
        orch.start()
        mod1.start()
        mod2.start()
        time.sleep(0.2)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 2)

        # Start priority 1 and wait
        orch.publish_start(priority=1)
        assert _wait(orch.system_ready_received, 1, timeout=1.5)
        assert orch.system_ready_received[0]["priority"] == 1

        # Start priority 2 and wait
        orch.publish_start(priority=2)
        assert _wait(orch.system_ready_received, 2, timeout=1.5)
        assert orch.system_ready_received[1]["priority"] == 2

        mod1.stop()
        mod2.stop()
        orch.stop()

    def test_full_boot_then_stop_all(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mods = [
            FakeModule(in_process_broker, "config_manager", priority=1),
            FakeModule(in_process_broker, "channel_manager", priority=2),
        ]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.2)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 2)
        orch.publish_start(priority=1)
        orch.publish_start(priority=2)
        assert _wait(orch.system_ready_received, 2, timeout=2.0)

        orch.publish_stop()
        for m in mods:
            assert _wait(m._stop_events, 1)

        for m in mods: m.stop()
        orch.stop()

    def test_boot_order_verified_by_priority(self, in_process_broker):
        """I system.ready arrivano in ordine di priority."""
        orch = BootOrchestrator(in_process_broker)
        mods = [FakeModule(in_process_broker, f"mod_{p}", priority=p) for p in [3, 1, 2]]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.2)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 3)

        for p in [1, 2, 3]:
            orch.publish_start(priority=p)
            assert _wait(orch.system_ready_received, p, timeout=1.5)
            assert orch.system_ready_received[p - 1]["priority"] == p

        for m in mods: m.stop()
        orch.stop()

    def test_readytostart_before_modules_start(self, in_process_broker):
        """Se readytostart arriva prima che i moduli siano in ascolto, nessuna risposta."""
        orch = BootOrchestrator(in_process_broker)
        orch.start()
        time.sleep(0.1)
        orch.publish_readytostart()
        time.sleep(0.3)
        assert len(orch.module_ready_received) == 0
        orch.stop()

    def test_late_subscriber_misses_readytostart(self, in_process_broker):
        """Modulo avviato dopo readytostart non risponde (ZMQ non fa replay)."""
        orch = BootOrchestrator(in_process_broker)
        orch.start()
        time.sleep(0.1)
        orch.publish_readytostart()
        time.sleep(0.3)

        # modulo avviato in ritardo
        mod = FakeModule(in_process_broker, "late_mod", priority=1)
        mod.start()
        time.sleep(0.2)
        assert len(mod.module_ready_published) == 0

        mod.stop()
        orch.stop()


# ---------------------------------------------------------------------------
# Gruppo 5 — shutdown ordinato e edge cases
# ---------------------------------------------------------------------------

class TestShutdownProtocol:

    def test_stop_received_by_all_before_any_start(self, in_process_broker):
        """system.stop funziona anche senza system.start precedente."""
        orch = BootOrchestrator(in_process_broker)
        mods = [FakeModule(in_process_broker, f"mod_{i}", priority=i) for i in range(1, 4)]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.15)

        orch.publish_stop()
        for m in mods:
            assert _wait(m._stop_events, 1)

        for m in mods: m.stop()
        orch.stop()

    def test_stop_after_partial_boot(self, in_process_broker):
        """Solo priority 1 ha completato il boot, poi arriva stop."""
        orch = BootOrchestrator(in_process_broker)
        mod1 = FakeModule(in_process_broker, "mod_1", priority=1)
        mod2 = FakeModule(in_process_broker, "mod_2", priority=2)
        orch.start()
        mod1.start()
        mod2.start()
        time.sleep(0.15)

        orch.publish_readytostart()
        assert _wait(orch.module_ready_received, 2)
        orch.publish_start(priority=1)
        assert _wait(orch.system_ready_received, 1, timeout=1.0)

        # stop prima di avviare priority 2
        orch.publish_stop()
        assert _wait(mod1._stop_events, 1)
        assert _wait(mod2._stop_events, 1)

        mod1.stop()
        mod2.stop()
        orch.stop()

    def test_double_stop_no_crash(self, in_process_broker):
        """Doppio system.stop non causa crash."""
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_1", priority=1)
        orch.start()
        mod.start()
        time.sleep(0.15)

        orch.publish_stop()
        time.sleep(0.1)
        orch.publish_stop()  # secondo stop
        time.sleep(0.1)     # nessun crash

        mod.stop()
        orch.stop()

    def test_stop_without_readytostart_no_crash(self, in_process_broker):
        """system.stop senza readytostart precedente non causa crash."""
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_1", priority=1)
        orch.start()
        mod.start()
        time.sleep(0.15)

        orch.publish_stop()  # nessun readytostart
        assert _wait(mod._stop_events, 1)

        mod.stop()
        orch.stop()

    def test_module_stopped_flag_is_true_after_stop(self, in_process_broker):
        orch = BootOrchestrator(in_process_broker)
        mod = FakeModule(in_process_broker, "mod_x", priority=1)
        orch.start()
        mod.start()
        time.sleep(0.15)

        assert mod.stopped is False
        orch.publish_stop()
        assert _wait(mod._stop_events, 1)
        assert mod.stopped is True

        mod.stop()
        orch.stop()


# ---------------------------------------------------------------------------
# Gruppo 6 — boot E2E con channel_manager reale + FakeModules
# ---------------------------------------------------------------------------

class TestBootE2E:

    def _load_cm(self, broker):
        pub_addr, sub_addr = broker
        import modules.channel_manager.main as cm_main
        with patch("shared.bus_client.BusTracer", return_value=MagicMock()):
            import shared.bus_client as _bc
            _bc.BROKER_PUB_ADDR = sub_addr
            _bc.BROKER_SUB_ADDR = pub_addr
            importlib.reload(cm_main)
        return cm_main

    def test_cm_participates_in_full_boot(self, in_process_broker):
        """channel_manager reale completa il boot con 2 FakeModules."""
        cm = self._load_cm(in_process_broker)
        orch = BootOrchestrator(in_process_broker)
        mod1 = FakeModule(in_process_broker, "config_manager", priority=1)
        mod3 = FakeModule(in_process_broker, "video_ui", priority=3)
        orch.start()
        mod1.start()
        mod3.start()
        time.sleep(0.2)

        # Simula readytostart: chiama direttamente cm handler + orch pubblica
        orch.publish_readytostart()
        cm.on_system_readytostart()
        # Aspetta module_ready da config_manager, video_ui, channel_manager
        assert _wait(orch.module_ready_received, 3, timeout=2.0)
        names = {r["name"] for r in orch.module_ready_received}
        assert "channel_manager" in names
        assert "config_manager" in names
        assert "video_ui" in names

        # Start priority 1
        orch.publish_start(priority=1)
        assert _wait(orch.system_ready_received, 1, timeout=1.5)

        # Start priority 2 (channel_manager)
        orch.publish_start(priority=2)
        assert _wait(orch.system_ready_received, 2, timeout=1.5)
        p2 = [r for r in orch.system_ready_received if r["priority"] == 2]
        assert p2[0]["name"] == "channel_manager"

        # Start priority 3
        orch.publish_start(priority=3)
        assert _wait(orch.system_ready_received, 3, timeout=1.5)

        mod1.stop()
        mod3.stop()
        orch.stop()

    def test_cm_stop_triggers_session_cleanup(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        fake_session = MagicMock()
        cm._session = fake_session

        cm.on_system_stop("system.stop", {})
        fake_session.shutdown.assert_called_once()
        assert cm._session is None

    def test_cm_announces_priority_2_on_readytostart(self, in_process_broker):
        cm = self._load_cm(in_process_broker)
        spy = _make_client(in_process_broker, "spy_e2e")
        received: list[dict] = []
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.15)

        cm.on_system_readytostart()
        assert _wait(received, 1)
        assert received[0] == {"name": "channel_manager", "priority": 2}

        spy.stop()

    def test_full_e2e_boot_and_stop(self, in_process_broker):
        """Sequenza completa: readytostart → tutte le priority → stop → tutti stopped."""
        cm = self._load_cm(in_process_broker)
        orch = BootOrchestrator(in_process_broker)
        mods = [
            FakeModule(in_process_broker, "config_manager", priority=1),
            FakeModule(in_process_broker, "video_ui", priority=3),
        ]
        orch.start()
        for m in mods: m.start()
        time.sleep(0.2)

        orch.publish_readytostart()
        cm.on_system_readytostart()
        assert _wait(orch.module_ready_received, 3, timeout=2.0)

        for p in [1, 2, 3]:
            orch.publish_start(priority=p)
        assert _wait(orch.system_ready_received, 3, timeout=2.0)

        orch.publish_stop()
        for m in mods:
            assert _wait(m._stop_events, 1, timeout=1.5)

        for m in mods: m.stop()
        orch.stop()
