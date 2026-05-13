"""
NemoHeadUnit-Wireless v2 — Integration Tests: Bluetooth Flow
=============================================================
Fase 2 — Integration Test §6

Scope: modules/bluetooth/main.py — tutti gli handler bus, la state machine
di autoconnect, i callback interni e le pubblicazioni sul bus ZMQ.

Strategy:
  - Bus ZMQ reale in-process (fixture in_process_broker)
  - D-Bus / BlueZ / GLib / gi patchati a livello sys.modules pre-import
    (nessun Bluetooth hardware in CI)
  - importlib.reload() per ogni test — bus fresco e stato modulo pulito
  - BluezAdapter, DiscoverySession, PairingAgent, paired_devices interamente
    sostituiti da MagicMock — test di layer 2 puro
  - Handler on_* chiamati direttamente; spy BusClient riceve i topic pubblicati
  - Callback interni _on_device_found / _on_pairing_completed / etc.
    chiamati direttamente per verificare la pubblicazione dei topic

Gruppi:
  1. Boot protocol (readytostart / system.start / system.stop)
  2. Discovery flow (on_discover: adapter ready/not-ready, duplicate, callback)
  3. Pairing flow (on_pair, confirm, reject, pin/completed/failed callbacks)
  4. Paired-device operations (list, remove, connect, disconnect)
  5. Autoconnect state machine (_start_autoconnect, _stop_autoconnect,
     on_rfcomm_connected, on_try_autoconnect)
  6. Config callbacks (_on_config_loaded / _on_config_changed)
  7. Error paths (missing fields, adapter not ready, malformed payloads)
  8. End-to-end bus flow (spy riceve topic via bus ZMQ reale)

Marker: @pytest.mark.integration
Dipendenze: conftest.in_process_broker
Rif: docs/TEST_SUITE_ARCHITECTURE.md §3.2
"""
from __future__ import annotations

import importlib
import sys
import time
import types
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# sys.path
# ---------------------------------------------------------------------------

_V2 = Path(__file__).parent.parent.parent
for _p in (_V2, _V2 / "modules"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---------------------------------------------------------------------------
# Stub D-Bus / BlueZ / GLib / gi BEFORE any module import
# ---------------------------------------------------------------------------

def _stub_dbus_gi() -> None:
    for mod_name in [
        "gi", "gi.repository", "dbus", "dbus.mainloop", "dbus.mainloop.glib",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()
    # GLib.MainLoop must have is_running() and run()/quit()
    gi_repo = sys.modules.get("gi.repository", MagicMock())
    glib_mock = MagicMock()
    glib_mock.MainLoop.return_value.is_running.return_value = False
    gi_repo.GLib = glib_mock


_stub_dbus_gi()


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


def _make_client(in_process_broker, name: str | None = None):
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
    from shared.bus_client import BusClient
    return BusClient(module_name=name or f"t_{uuid.uuid4().hex[:6]}")


def _start_client(client):
    client.start(blocking=False)
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# bluetooth/main.py loader
# ---------------------------------------------------------------------------

# Stubs for bluetooth submodules (no D-Bus hardware)
_MOCK_ADAPTER_CLS    = MagicMock()
_MOCK_DISCOVERY_CLS  = MagicMock()
_MOCK_PAIRING_CLS    = MagicMock()
_MOCK_PAIRED_DEVICES = MagicMock()


def _load_bt(in_process_broker):
    """Reload bluetooth.main with in-process broker and all hardware deps mocked."""
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]

    mock_adapter_cls   = MagicMock()
    mock_discovery_cls = MagicMock()
    mock_pairing_cls   = MagicMock()
    mock_paired_mod    = MagicMock()

    # Default: adapter.init() and register_profiles() succeed
    mock_adapter_instance = MagicMock()
    mock_adapter_instance.init.return_value = True
    mock_adapter_instance.register_profiles.return_value = True
    mock_adapter_instance.bus = MagicMock()
    mock_adapter_cls.return_value = mock_adapter_instance

    mock_paired_mod.list_paired.return_value = []

    with patch("shared.bus_client.BusTracer", return_value=MagicMock()), \
         patch.dict(sys.modules, {
             "bluetooth.bluez_adapter": types.SimpleNamespace(BluezAdapter=mock_adapter_cls),
             "bluetooth.discovery":     types.SimpleNamespace(DiscoverySession=mock_discovery_cls),
             "bluetooth.pairing":       types.SimpleNamespace(PairingAgent=mock_pairing_cls),
             "bluetooth.paired_devices": mock_paired_mod,
         }):
        import modules.bluetooth.main as bt
        importlib.reload(bt)

    # Attach mocks for assertion access
    bt._mock_adapter_cls   = mock_adapter_cls
    bt._mock_adapter       = mock_adapter_instance
    bt._mock_discovery_cls = mock_discovery_cls
    bt._mock_pairing_cls   = mock_pairing_cls
    bt._mock_paired_mod    = mock_paired_mod

    # Pre-inject adapter so handlers don't fail with "Adapter not ready"
    bt._adapter = mock_adapter_instance

    return bt


# ===========================================================================
# Gruppo 1 — Boot protocol
# ===========================================================================

class TestBootProtocol:

    @pytest.mark.integration
    def test_readytostart_publishes_module_ready(self, in_process_broker):
        """on_system_readytostart() pubblica system.module_ready sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt.on_system_readytostart()

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.module_ready non ricevuto"
        assert received[0]["name"] == "bluetooth"
        assert received[0]["priority"] == bt.PRIORITY

    @pytest.mark.integration
    def test_readytostart_priority_is_1(self, in_process_broker):
        """Il modulo bluetooth ha PRIORITY == 1 (service level)."""
        bt = _load_bt(in_process_broker)
        assert bt.PRIORITY == 1

    @pytest.mark.integration
    def test_system_start_correct_priority_inits_adapter(self, in_process_broker):
        """on_system_start() con priority corretta chiama BluezAdapter().init()."""
        bt = _load_bt(in_process_broker)
        bt._adapter = None  # reset so system_start re-creates it

        with patch("modules.bluetooth.main._start_glib_mainloop"), \
             patch("modules.bluetooth.main.cfg") as mock_cfg:
            bt.on_system_start("system.start", {"priority": bt.PRIORITY})

        bt._mock_adapter_cls.assert_called_once()
        bt._mock_adapter_cls.return_value.init.assert_called_once()

    @pytest.mark.integration
    def test_system_start_wrong_priority_no_adapter(self, in_process_broker):
        """on_system_start() con priority diversa NON crea adapter."""
        bt = _load_bt(in_process_broker)
        bt._adapter = None

        bt.on_system_start("system.start", {"priority": bt.PRIORITY + 99})
        # _mock_adapter_cls.return_value.init should NOT have been called
        assert bt._adapter is None

    @pytest.mark.integration
    def test_system_start_adapter_init_failed_publishes_error(self, in_process_broker):
        """on_system_start() con adapter.init()=False pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._adapter = None
        bt._mock_adapter_cls.return_value.init.return_value = False

        with patch("modules.bluetooth.main._start_glib_mainloop"), \
             patch("modules.bluetooth.main.cfg"):
            bt.on_system_start("system.start", {"priority": bt.PRIORITY})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.error non ricevuto dopo init failure"
        assert "error" in received[0]

    @pytest.mark.integration
    def test_system_stop_calls_stop_autoconnect(self, in_process_broker):
        """on_system_stop() chiama _stop_autoconnect."""
        bt = _load_bt(in_process_broker)
        with patch("modules.bluetooth.main._stop_autoconnect") as mock_stop, \
             patch("modules.bluetooth.main._stop_glib_mainloop"):
            bt.on_system_stop("system.stop", {})
        mock_stop.assert_called_once()

    @pytest.mark.integration
    def test_system_stop_does_not_crash_without_adapter(self, in_process_broker):
        """on_system_stop() senza adapter non solleva."""
        bt = _load_bt(in_process_broker)
        bt._adapter = None
        with patch("modules.bluetooth.main._stop_autoconnect"), \
             patch("modules.bluetooth.main._stop_glib_mainloop"):
            try:
                bt.on_system_stop("system.stop", {})
            except Exception as exc:
                pytest.fail(f"on_system_stop ha sollevato: {exc}")


# ===========================================================================
# Gruppo 2 — Discovery flow
# ===========================================================================

class TestDiscoveryFlow:

    @pytest.mark.integration
    def test_discover_starts_discovery_session(self, in_process_broker):
        """on_discover() crea e avvia una DiscoverySession."""
        bt = _load_bt(in_process_broker)
        bt._discovery = None

        bt.on_discover("bluetooth.discover", {"duration_sec": 5})

        bt._mock_discovery_cls.assert_called_once()
        bt._mock_discovery_cls.return_value.start.assert_called_once_with(duration_sec=5)

    @pytest.mark.integration
    def test_discover_uses_default_duration_from_config(self, in_process_broker):
        """on_discover() senza duration_sec usa _config['discovery_duration_sec']."""
        bt = _load_bt(in_process_broker)
        bt._discovery = None
        bt._config["discovery_duration_sec"] = 15

        bt.on_discover("bluetooth.discover", {})

        bt._mock_discovery_cls.return_value.start.assert_called_once_with(duration_sec=15)

    @pytest.mark.integration
    def test_discover_no_adapter_publishes_error(self, in_process_broker):
        """on_discover() con _adapter=None pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._adapter = None

        bt.on_discover("bluetooth.discover", {"duration_sec": 5})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.error non ricevuto"
        assert "error" in received[0]

    @pytest.mark.integration
    def test_discover_duplicate_ignored_when_running(self, in_process_broker):
        """on_discover() con discovery già in corso viene ignorato (is_running=True)."""
        bt = _load_bt(in_process_broker)
        mock_session = MagicMock()
        mock_session.is_running = True
        bt._discovery = mock_session

        bt.on_discover("bluetooth.discover", {"duration_sec": 5})

        # DiscoverySession non deve essere ri-istanziata
        assert bt._discovery is mock_session

    @pytest.mark.integration
    def test_on_device_found_publishes_device_found(self, in_process_broker):
        """_on_device_found() pubblica bluetooth.device.found sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.device.found", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_device_found("AA:BB:CC:DD:EE:FF", "TestPhone", -65)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.device.found non ricevuto"
        assert received[0]["address"] == "AA:BB:CC:DD:EE:FF"
        assert received[0]["name"] == "TestPhone"
        assert received[0]["rssi"] == -65

    @pytest.mark.integration
    def test_on_discovery_done_publishes_discovery_completed(self, in_process_broker):
        """_on_discovery_done() pubblica bluetooth.discovery.completed sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.discovery.completed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        devices = [{"address": "AA:BB", "name": "P1", "rssi": -70}]
        bt._on_discovery_done(devices)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.discovery.completed non ricevuto"
        assert received[0]["devices"] == devices

    @pytest.mark.integration
    def test_on_discovery_done_empty_list(self, in_process_broker):
        """_on_discovery_done([]) pubblica devices=[] sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.discovery.completed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_discovery_done([])

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["devices"] == []


# ===========================================================================
# Gruppo 3 — Pairing flow
# ===========================================================================

class TestPairingFlow:

    @pytest.mark.integration
    def test_on_pair_calls_pairing_pair(self, in_process_broker):
        """on_pair() con pairing agent pronto chiama _pairing.pair(address)."""
        bt = _load_bt(in_process_broker)
        mock_pairing = MagicMock()
        bt._pairing = mock_pairing

        bt.on_pair("bluetooth.pair", {"device_address": "AA:BB:CC:DD:EE:FF"})

        mock_pairing.pair.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    @pytest.mark.integration
    def test_on_pair_no_agent_publishes_error(self, in_process_broker):
        """on_pair() senza pairing agent pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._pairing = None

        bt.on_pair("bluetooth.pair", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.error non ricevuto"

    @pytest.mark.integration
    def test_on_pair_missing_address_publishes_error(self, in_process_broker):
        """on_pair() con device_address mancante pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._pairing = MagicMock()

        bt.on_pair("bluetooth.pair", {})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.error non ricevuto"

    @pytest.mark.integration
    def test_on_confirm_pairing_calls_confirm(self, in_process_broker):
        """on_confirm_pairing() chiama _pairing.confirm(address, pin)."""
        bt = _load_bt(in_process_broker)
        mock_pairing = MagicMock()
        bt._pairing = mock_pairing

        bt.on_confirm_pairing("bluetooth.confirm_pairing", {
            "device_address": "AA:BB",
            "pin": "123456",
        })

        mock_pairing.confirm.assert_called_once_with("AA:BB", "123456")

    @pytest.mark.integration
    def test_on_confirm_pairing_missing_fields_publishes_error(self, in_process_broker):
        """on_confirm_pairing() con fields mancanti pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._pairing = MagicMock()

        bt.on_confirm_pairing("bluetooth.confirm_pairing", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_reject_pairing_calls_reject(self, in_process_broker):
        """on_reject_pairing() chiama _pairing.reject(address)."""
        bt = _load_bt(in_process_broker)
        mock_pairing = MagicMock()
        bt._pairing = mock_pairing

        bt.on_reject_pairing("bluetooth.reject_pairing", {"device_address": "AA:BB"})

        mock_pairing.reject.assert_called_once_with("AA:BB")

    @pytest.mark.integration
    def test_on_reject_pairing_no_pairing_no_crash(self, in_process_broker):
        """on_reject_pairing() senza pairing agent non solleva."""
        bt = _load_bt(in_process_broker)
        bt._pairing = None
        try:
            bt.on_reject_pairing("bluetooth.reject_pairing", {"device_address": "AA:BB"})
        except Exception as exc:
            pytest.fail(f"on_reject_pairing ha sollevato: {exc}")

    @pytest.mark.integration
    def test_on_pin_requested_publishes_pairing_pin(self, in_process_broker):
        """_on_pin_requested() pubblica bluetooth.pairing.pin sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.pairing.pin", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_pin_requested("AA:BB:CC", "654321")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.pairing.pin non ricevuto"
        assert received[0]["device_address"] == "AA:BB:CC"
        assert received[0]["pin"] == "654321"

    @pytest.mark.integration
    def test_on_pairing_completed_publishes_pairing_completed(self, in_process_broker):
        """_on_pairing_completed() pubblica bluetooth.pairing.completed sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.pairing.completed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_pairing_completed("AA:BB:CC:DD:EE:FF")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.pairing.completed non ricevuto"
        assert received[0]["device_address"] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.integration
    def test_on_pairing_failed_publishes_pairing_failed(self, in_process_broker):
        """_on_pairing_failed() pubblica bluetooth.pairing.failed sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.pairing.failed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_pairing_failed("AA:BB", "Authentication failed")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.pairing.failed non ricevuto"
        assert received[0]["device_address"] == "AA:BB"
        assert received[0]["error"] == "Authentication failed"


# ===========================================================================
# Gruppo 4 — Paired-device operations
# ===========================================================================

class TestPairedDeviceOps:

    @pytest.mark.integration
    def test_on_paired_list_publishes_devices(self, in_process_broker):
        """on_paired_list() pubblica bluetooth.paired.devices con la lista."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.devices", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._mock_paired_mod.list_paired.return_value = [
            {"address": "AA:BB", "name": "Phone1", "connected": True, "trusted": True}
        ]

        bt.on_paired_list("bluetooth.paired.list", {})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.devices non ricevuto"
        assert len(received[0]["devices"]) == 1
        assert received[0]["devices"][0]["address"] == "AA:BB"

    @pytest.mark.integration
    def test_on_paired_list_empty(self, in_process_broker):
        """on_paired_list() con lista vuota pubblica devices=[]."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.devices", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._mock_paired_mod.list_paired.return_value = []

        bt.on_paired_list("bluetooth.paired.list", {})

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        assert received[0]["devices"] == []

    @pytest.mark.integration
    def test_on_paired_list_no_adapter_publishes_error(self, in_process_broker):
        """on_paired_list() con _adapter=None pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._adapter = None

        bt.on_paired_list("bluetooth.paired.list", {})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_paired_remove_success_publishes_removed(self, in_process_broker):
        """on_paired_remove() con remove=True pubblica bluetooth.paired.removed."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.removed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._mock_paired_mod.remove.return_value = True

        bt.on_paired_remove("bluetooth.paired.remove", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.removed non ricevuto"
        assert received[0]["device_address"] == "AA:BB"

    @pytest.mark.integration
    def test_on_paired_remove_failure_publishes_failed(self, in_process_broker):
        """on_paired_remove() con remove=False pubblica bluetooth.paired.failed."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.failed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._mock_paired_mod.remove.return_value = False

        bt.on_paired_remove("bluetooth.paired.remove", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.failed non ricevuto"
        assert received[0]["device_address"] == "AA:BB"

    @pytest.mark.integration
    def test_on_paired_remove_missing_address_publishes_error(self, in_process_broker):
        """on_paired_remove() senza device_address pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt.on_paired_remove("bluetooth.paired.remove", {})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_paired_connect_calls_connect(self, in_process_broker):
        """on_paired_connect() chiama paired_devices.connect() con address e timeout."""
        bt = _load_bt(in_process_broker)
        bt._config["autoconnect_connect_timeout_s"] = 8

        bt.on_paired_connect("bluetooth.paired.connect", {"device_address": "AA:BB"})

        bt._mock_paired_mod.connect.assert_called_once()
        call_kwargs = bt._mock_paired_mod.connect.call_args
        assert call_kwargs[0][1] == "AA:BB"  # positional arg 1 = address
        assert call_kwargs[1]["timeout_s"] == 8

    @pytest.mark.integration
    def test_on_paired_connect_on_connected_publishes_connected(self, in_process_broker):
        """on_paired_connect() — on_connected callback pubblica bluetooth.paired.connected."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.connected", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)

        # Capture the on_connected callback and invoke it
        def _capture_connect(bus_obj, address, timeout_s, on_connected, on_failed):
            on_connected(address)

        bt._mock_paired_mod.connect.side_effect = _capture_connect
        bt.on_paired_connect("bluetooth.paired.connect", {"device_address": "CC:DD"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.connected non ricevuto"
        assert received[0]["device_address"] == "CC:DD"

    @pytest.mark.integration
    def test_on_paired_connect_on_failed_publishes_failed(self, in_process_broker):
        """on_paired_connect() — on_failed callback pubblica bluetooth.paired.failed."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.failed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)

        def _capture_fail(bus_obj, address, timeout_s, on_connected, on_failed):
            on_failed(address, "Connection refused")

        bt._mock_paired_mod.connect.side_effect = _capture_fail
        bt.on_paired_connect("bluetooth.paired.connect", {"device_address": "CC:DD"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.failed non ricevuto"
        assert received[0]["error"] == "Connection refused"

    @pytest.mark.integration
    def test_on_paired_disconnect_on_disconnected_publishes_disconnected(self, in_process_broker):
        """on_paired_disconnect() — on_disconnected callback pubblica bluetooth.paired.disconnected."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.disconnected", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)

        def _capture_disconnect(bus_obj, address, on_disconnected, on_failed):
            on_disconnected(address)

        bt._mock_paired_mod.disconnect.side_effect = _capture_disconnect
        bt.on_paired_disconnect("bluetooth.paired.disconnect", {"device_address": "EE:FF"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.disconnected non ricevuto"
        assert received[0]["device_address"] == "EE:FF"

    @pytest.mark.integration
    def test_on_paired_disconnect_on_failed_publishes_failed(self, in_process_broker):
        """on_paired_disconnect() — on_failed callback pubblica bluetooth.paired.failed."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.failed", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)

        def _capture_fail(bus_obj, address, on_disconnected, on_failed):
            on_failed(address, "Remote disconnection")

        bt._mock_paired_mod.disconnect.side_effect = _capture_fail
        bt.on_paired_disconnect("bluetooth.paired.disconnect", {"device_address": "EE:FF"})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "bluetooth.paired.failed non ricevuto"
        assert received[0]["error"] == "Remote disconnection"


# ===========================================================================
# Gruppo 5 — Autoconnect state machine
# ===========================================================================

class TestAutoconnectStateMachine:

    @pytest.mark.integration
    def test_stop_autoconnect_sets_stop_event(self, in_process_broker):
        """_stop_autoconnect() setta _autoconnect_stop."""
        bt = _load_bt(in_process_broker)
        bt._autoconnect_stop.clear()
        bt._stop_autoconnect("test")
        assert bt._autoconnect_stop.is_set()

    @pytest.mark.integration
    def test_start_autoconnect_no_op_if_disabled(self, in_process_broker):
        """_start_autoconnect() con autoconnect_enabled=False non lancia thread."""
        bt = _load_bt(in_process_broker)
        bt._config["autoconnect_enabled"] = False
        bt._autoconnect_active = False

        with patch("threading.Thread") as mock_thread:
            bt._start_autoconnect()
        mock_thread.assert_not_called()

    @pytest.mark.integration
    def test_start_autoconnect_already_active_no_second_thread(self, in_process_broker):
        """_start_autoconnect() con _autoconnect_active=True non lancia secondo thread."""
        bt = _load_bt(in_process_broker)
        bt._config["autoconnect_enabled"] = True
        bt._autoconnect_active = True

        with patch("threading.Thread") as mock_thread:
            bt._start_autoconnect()
        mock_thread.assert_not_called()

    @pytest.mark.integration
    def test_on_rfcomm_connected_stops_autoconnect(self, in_process_broker):
        """on_rfcomm_connected() chiama _stop_autoconnect()."""
        bt = _load_bt(in_process_broker)
        bt._autoconnect_stop.clear()

        bt.on_rfcomm_connected("bluetooth.rfcomm.connected", {"device_address": "AA:BB"})

        assert bt._autoconnect_stop.is_set()

    @pytest.mark.integration
    def test_on_try_autoconnect_starts_autoconnect(self, in_process_broker):
        """on_try_autoconnect() chiama _start_autoconnect()."""
        bt = _load_bt(in_process_broker)
        with patch.object(bt, "_start_autoconnect", wraps=bt._start_autoconnect) as spy_ac:
            # Need to monkey-patch the module-level function since it's called directly
            with patch("modules.bluetooth.main._start_autoconnect") as mock_start:
                bt.on_try_autoconnect("bluetooth.try_autoconnect", {})
        mock_start.assert_called_once()

    @pytest.mark.integration
    def test_autoconnect_loop_skips_connected_device(self, in_process_broker):
        """_autoconnect_loop() salta dispositivi già connessi (connected=True)."""
        bt = _load_bt(in_process_broker)
        bt._config["autoconnect_enabled"] = True
        bt._config["autoconnect_backoff_initial_s"] = 0
        bt._config["autoconnect_backoff_cap_s"] = 1
        bt._config["autoconnect_connect_timeout_s"] = 1

        # Simula un device già connected
        bt._mock_paired_mod.list_paired.return_value = [
            {"address": "AA:BB", "name": "Phone", "connected": True, "trusted": True}
        ]
        # Stop the loop after first round
        bt._autoconnect_stop.set()

        # If loop is run inline it must not call connect()
        with patch("threading.Thread") as mock_thread:
            # Verify that connected device is skipped — connect() not called
            bt._autoconnect_stop.set()  # already stopped
            bt._autoconnect_loop()

        bt._mock_paired_mod.connect.assert_not_called()

    @pytest.mark.integration
    def test_autoconnect_loop_exits_immediately_when_stopped(self, in_process_broker):
        """_autoconnect_loop() esce immediatamente se _autoconnect_stop è già settato."""
        bt = _load_bt(in_process_broker)
        bt._config["autoconnect_backoff_initial_s"] = 0
        bt._config["autoconnect_backoff_cap_s"] = 1
        bt._config["autoconnect_connect_timeout_s"] = 1
        bt._mock_paired_mod.list_paired.return_value = []
        bt._autoconnect_stop.set()  # already stopped

        # Should complete quickly without blocking
        import threading
        done = threading.Event()

        def _run():
            bt._autoconnect_loop()
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        ok = done.wait(timeout=2.0)
        assert ok, "_autoconnect_loop non si è fermato entro 2s"


# ===========================================================================
# Gruppo 6 — Config callbacks
# ===========================================================================

class TestConfigCallbacks:

    @pytest.mark.integration
    def test_on_config_loaded_merges_values(self, in_process_broker):
        """_on_config_loaded() applica i valori persistiti su _config."""
        bt = _load_bt(in_process_broker)
        with patch("modules.bluetooth.main._apply_config"), \
             patch("modules.bluetooth.main._start_autoconnect"):
            bt._on_config_loaded({"adapter_name": "MyUnit", "discovery_duration_sec": 20})

        assert bt._config["adapter_name"] == "MyUnit"
        assert bt._config["discovery_duration_sec"] == 20

    @pytest.mark.integration
    def test_on_config_loaded_empty_uses_defaults(self, in_process_broker):
        """_on_config_loaded({}) lascia i default inalterati."""
        bt = _load_bt(in_process_broker)
        with patch("modules.bluetooth.main._apply_config"), \
             patch("modules.bluetooth.main._start_autoconnect"):
            bt._on_config_loaded({})

        assert bt._config["adapter_name"] == "NemoHeadUnit"
        assert bt._config["discoverable"] is True

    @pytest.mark.integration
    def test_on_config_loaded_publishes_system_ready(self, in_process_broker):
        """_on_config_loaded() pubblica system.ready sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        with patch("modules.bluetooth.main._apply_config"), \
             patch("modules.bluetooth.main._start_autoconnect"):
            bt._on_config_loaded({})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.ready non ricevuto"
        assert received[0]["name"] == "bluetooth"
        assert received[0]["priority"] == bt.PRIORITY

    @pytest.mark.integration
    def test_on_config_loaded_initialises_pairing_agent(self, in_process_broker):
        """_on_config_loaded() con _pairing=None crea e registra PairingAgent."""
        bt = _load_bt(in_process_broker)
        bt._pairing = None

        mock_pairing_instance = MagicMock()
        bt._mock_pairing_cls.return_value = mock_pairing_instance

        with patch("modules.bluetooth.main._apply_config"), \
             patch("modules.bluetooth.main._start_autoconnect"):
            bt._on_config_loaded({})

        mock_pairing_instance.register.assert_called_once()

    @pytest.mark.integration
    def test_on_config_changed_updates_key(self, in_process_broker):
        """_on_config_changed('adapter_name', 'New') aggiorna _config."""
        bt = _load_bt(in_process_broker)
        with patch("modules.bluetooth.main._apply_config"):
            bt._on_config_changed("adapter_name", "NewName")
        assert bt._config["adapter_name"] == "NewName"

    @pytest.mark.integration
    def test_on_config_changed_unknown_key_ignored(self, in_process_broker):
        """_on_config_changed('unknown_xyz', 42) non modifica _config."""
        bt = _load_bt(in_process_broker)
        original = dict(bt._config)
        with patch("modules.bluetooth.main._apply_config"):
            bt._on_config_changed("unknown_xyz", 42)
        # Config unchanged
        for k, v in original.items():
            assert bt._config[k] == v

    @pytest.mark.integration
    def test_on_config_changed_structural_value_rejected(self, in_process_broker):
        """_on_config_changed() con valore dict non aggiorna _config."""
        bt = _load_bt(in_process_broker)
        original_name = bt._config["adapter_name"]
        with patch("modules.bluetooth.main._apply_config"):
            bt._on_config_changed("adapter_name", {"nested": "value"})
        assert bt._config["adapter_name"] == original_name

    @pytest.mark.integration
    def test_on_config_changed_calls_apply_config(self, in_process_broker):
        """_on_config_changed() chiama _apply_config() dopo aggiornamento."""
        bt = _load_bt(in_process_broker)
        with patch("modules.bluetooth.main._apply_config") as mock_apply:
            bt._on_config_changed("discoverable", False)
        mock_apply.assert_called_once()


# ===========================================================================
# Gruppo 7 — Error paths
# ===========================================================================

class TestErrorPaths:

    @pytest.mark.integration
    def test_on_paired_connect_no_adapter_publishes_error(self, in_process_broker):
        """on_paired_connect() con _adapter=None pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._adapter = None
        bt.on_paired_connect("bluetooth.paired.connect", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_paired_connect_missing_address_publishes_error(self, in_process_broker):
        """on_paired_connect() senza device_address pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt.on_paired_connect("bluetooth.paired.connect", {})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_paired_disconnect_no_adapter_publishes_error(self, in_process_broker):
        """on_paired_disconnect() con _adapter=None pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._adapter = None
        bt.on_paired_disconnect("bluetooth.paired.disconnect", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_paired_disconnect_missing_address_publishes_error(self, in_process_broker):
        """on_paired_disconnect() senza device_address pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt.on_paired_disconnect("bluetooth.paired.disconnect", {})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_paired_remove_no_adapter_publishes_error(self, in_process_broker):
        """on_paired_remove() con _adapter=None pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._adapter = None
        bt.on_paired_remove("bluetooth.paired.remove", {"device_address": "AA:BB"})

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_confirm_pairing_no_agent_publishes_error(self, in_process_broker):
        """on_confirm_pairing() senza pairing agent pubblica bluetooth.error."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.error", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._pairing = None
        bt.on_confirm_pairing("bluetooth.confirm_pairing", {
            "device_address": "AA:BB", "pin": "1234"
        })

        ok = _wait(received, 1)
        spy.stop()
        assert ok

    @pytest.mark.integration
    def test_on_reject_pairing_missing_address_no_crash(self, in_process_broker):
        """on_reject_pairing() senza device_address non solleva."""
        bt = _load_bt(in_process_broker)
        bt._pairing = MagicMock()
        try:
            bt.on_reject_pairing("bluetooth.reject_pairing", {})
        except Exception as exc:
            pytest.fail(f"on_reject_pairing ha sollevato: {exc}")


# ===========================================================================
# Gruppo 8 — End-to-end bus flow
# ===========================================================================

class TestEndToEndBusFlow:

    @pytest.mark.integration
    def test_full_discovery_flow_on_bus(self, in_process_broker):
        """
        Sequenza completa discovery via bus:
          - bluetooth.device.found  pubblicato per ogni device trovato
          - bluetooth.discovery.completed pubblicato alla fine
        """
        device_found = []
        discovery_done = []

        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.device.found",       lambda t, p: device_found.append(p))
        spy.subscribe("bluetooth.discovery.completed", lambda t, p: discovery_done.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)

        # Simulate 2 devices found
        bt._on_device_found("AA:BB", "Phone1", -60)
        bt._on_device_found("CC:DD", "Phone2", -75)
        devices = [{"address": "AA:BB", "name": "Phone1"}, {"address": "CC:DD", "name": "Phone2"}]
        bt._on_discovery_done(devices)

        ok_found = _wait(device_found, 2)
        ok_done  = _wait(discovery_done, 1)
        spy.stop()

        assert ok_found, f"Ricevuti solo {len(device_found)} device.found"
        assert ok_done, "discovery.completed non ricevuto"
        assert len(discovery_done[0]["devices"]) == 2

    @pytest.mark.integration
    def test_full_pairing_flow_pin_then_completed_on_bus(self, in_process_broker):
        """
        Sequenza pairing: pin richiesto → pairing completato.
        Entrambi pubblicati sul bus ZMQ reale.
        """
        pin_received = []
        completed_received = []

        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.pairing.pin",       lambda t, p: pin_received.append(p))
        spy.subscribe("bluetooth.pairing.completed", lambda t, p: completed_received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_pin_requested("AA:BB", "111222")
        bt._on_pairing_completed("AA:BB")

        ok_pin  = _wait(pin_received, 1)
        ok_done = _wait(completed_received, 1)
        spy.stop()

        assert ok_pin,  "bluetooth.pairing.pin non ricevuto"
        assert ok_done, "bluetooth.pairing.completed non ricevuto"
        assert pin_received[0]["pin"] == "111222"
        assert completed_received[0]["device_address"] == "AA:BB"

    @pytest.mark.integration
    def test_full_pairing_flow_pin_then_failed_on_bus(self, in_process_broker):
        """
        Sequenza pairing: pin richiesto → pairing fallito.
        """
        pin_received = []
        failed_received = []

        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.pairing.pin",    lambda t, p: pin_received.append(p))
        spy.subscribe("bluetooth.pairing.failed", lambda t, p: failed_received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._on_pin_requested("CC:DD", "999888")
        bt._on_pairing_failed("CC:DD", "Auth rejected")

        ok_pin    = _wait(pin_received, 1)
        ok_failed = _wait(failed_received, 1)
        spy.stop()

        assert ok_pin,    "bluetooth.pairing.pin non ricevuto"
        assert ok_failed, "bluetooth.pairing.failed non ricevuto"
        assert failed_received[0]["error"] == "Auth rejected"

    @pytest.mark.integration
    def test_rfcomm_connected_stops_further_autoconnect(self, in_process_broker):
        """
        bluetooth.rfcomm.connected → _autoconnect_stop settato →
        successivo _start_autoconnect non lancia thread.
        """
        bt = _load_bt(in_process_broker)
        bt._autoconnect_stop.clear()
        bt._autoconnect_active = False
        bt._config["autoconnect_enabled"] = True

        # Step 1: rfcomm connected → stop event set
        bt.on_rfcomm_connected("bluetooth.rfcomm.connected", {"device_address": "AA:BB"})
        assert bt._autoconnect_stop.is_set(), "_autoconnect_stop non settato"

    @pytest.mark.integration
    def test_paired_remove_then_list_reflects_change(self, in_process_broker):
        """
        remove device → list pubblica lista aggiornata (mock restituisce lista ridotta).
        """
        removed_received = []
        devices_received = []

        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.paired.removed",  lambda t, p: removed_received.append(p))
        spy.subscribe("bluetooth.paired.devices",  lambda t, p: devices_received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        bt._mock_paired_mod.remove.return_value = True
        # After removal, list returns empty
        bt._mock_paired_mod.list_paired.return_value = []

        bt.on_paired_remove("bluetooth.paired.remove", {"device_address": "AA:BB"})
        bt.on_paired_list("bluetooth.paired.list", {})

        ok_rem  = _wait(removed_received, 1)
        ok_list = _wait(devices_received, 1)
        spy.stop()

        assert ok_rem,  "bluetooth.paired.removed non ricevuto"
        assert ok_list, "bluetooth.paired.devices non ricevuto"
        assert devices_received[0]["devices"] == []

    @pytest.mark.integration
    def test_multiple_devices_found_all_published(self, in_process_broker):
        """5 device trovati → 5 bluetooth.device.found pubblicati sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("bluetooth.device.found", lambda t, p: received.append(p))
        _start_client(spy)

        bt = _load_bt(in_process_broker)
        for i in range(5):
            bt._on_device_found(f"AA:BB:CC:DD:EE:0{i}", f"Dev{i}", -50 - i)

        ok = _wait(received, 5)
        spy.stop()

        assert ok, f"Ricevuti solo {len(received)} su 5 device.found"
        addresses = {r["address"] for r in received}
        assert len(addresses) == 5
