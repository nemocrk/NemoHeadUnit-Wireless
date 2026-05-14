"""
Unit tests for bluetooth/main.py

Strategy:
  bluetooth/main.py imports dbus-heavy helpers (BluezAdapter, DiscoverySession,
  PairingAgent, paired_devices) and gi.repository.GLib.
  All external dependencies are mocked before import:
    - dbus, dbus.mainloop.glib    → sys.modules stubs
    - gi / gi.repository.GLib    → sys.modules stubs
    - bluetooth_manager.bluez_adapter.BluezAdapter
    - bluetooth_manager.discovery.DiscoverySession
    - bluetooth_manager.pairing.PairingAgent
    - bluetooth_manager.paired_devices    (module-level mock)
    - shared.bus_client.BusClient
    - shared.logger.get_logger
    - shared.config_client.ConfigClient
    - shared.config_schema.*      (field_bool/int/string as SimpleNamespace with .default)

  The `bt` fixture reloads the module per-test with all singletons reset.

Covers:
  Section 1  — _SCHEMA: keys and defaults
  Section 2  — _apply_config: calls adapter.set_name + set_discoverable, skips when adapter=None
  Section 3  — _on_config_loaded: merge, defaults, calls _apply_config, registers pairing agent,
               publishes system.ready, starts autoconnect
  Section 4  — _on_config_changed: known key, unknown key ignored, structural value rejected,
               each affected branch (adapter_name, discoverable, autoconnect_enabled)
  Section 5  — Boot: on_system_readytostart, on_system_start priority filter,
               on_system_start success path calls adapter.init/register_profiles,
               on_system_start dbus failure publishes bluetooth_manager.error,
               on_system_stop calls adapter shutdown + bus.stop
  Section 6  — on_discover: adapter None → error, discovery already running → ignored,
               happy path creates DiscoverySession + start, custom duration used
  Section 7  — on_pair: pairing=None → error, missing address → error, happy path
  Section 8  — on_confirm_pairing / on_reject_pairing: missing fields → error, happy path,
               reject no pairing no crash
  Section 9  — on_rfcomm_connected: calls _stop_autoconnect (sets event)
  Section 10 — on_try_autoconnect: calls _start_autoconnect
  Section 11 — on_paired_list: adapter=None → error, happy path publishes devices
  Section 12 — on_paired_remove: missing address → error, adapter=None → error,
               success → paired.removed, failure → paired.failed
  Section 13 — on_paired_connect / on_paired_disconnect: missing address, adapter=None,
               happy path calls paired_devices.connect/disconnect
  Section 14 — Internal callbacks: _on_device_found, _on_discovery_done,
               _on_pin_requested, _on_pairing_completed, _on_pairing_failed
  Section 15 — _start_autoconnect / _stop_autoconnect: disabled by config, already active guard,
               stop sets event
"""

from __future__ import annotations

import sys
import types
import importlib
import threading
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Install stubs for dbus / gi BEFORE any import of the module under test
# ---------------------------------------------------------------------------

def _install_dbus_stubs():
    dbus_mod = types.ModuleType("dbus")
    dbus_mod.SystemBus       = MagicMock
    dbus_mod.Interface       = MagicMock
    dbus_mod.Dictionary      = MagicMock
    dbus_mod.Boolean         = lambda v: v
    dbus_mod.UInt16          = lambda v: v
    dbus_mod.UInt32          = lambda v: v
    dbus_mod.String          = lambda v: v
    sys.modules.setdefault("dbus", dbus_mod)

    dbus_ml = types.ModuleType("dbus.mainloop")
    sys.modules.setdefault("dbus.mainloop", dbus_ml)
    dbus_ml_glib = types.ModuleType("dbus.mainloop.glib")
    dbus_ml_glib.DBusGMainLoop = MagicMock()
    sys.modules.setdefault("dbus.mainloop.glib", dbus_ml_glib)

    gi_mod = types.ModuleType("gi")
    gi_mod.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mod)
    gi_repo = types.ModuleType("gi.repository")
    gi_repo.GLib = MagicMock()
    sys.modules.setdefault("gi.repository", gi_repo)
    sys.modules.setdefault("gi.repository.GLib", MagicMock())


_install_dbus_stubs()

_MOD = "bluetooth_manager.main"


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


def _make_schema_field(default):
    f = MagicMock()
    f.default = default
    return f


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def bt():
    """
    Reload bluetooth/main.py with all external deps mocked.
    Returns (mod, mock_bus, mock_adapter_cls, mock_adapter_inst,
             mock_discovery_cls, mock_pairing_cls, mock_paired_devices).
    """
    mock_bus       = MagicMock()
    mock_log       = MagicMock()
    mock_cfg_inst  = MagicMock()

    mock_adapter_inst = MagicMock()
    mock_adapter_inst.bus = MagicMock()
    mock_adapter_inst.init.return_value = True
    mock_adapter_inst.register_profiles.return_value = True
    mock_adapter_cls = MagicMock(return_value=mock_adapter_inst)

    mock_discovery_inst = MagicMock()
    mock_discovery_inst.is_running = False
    mock_discovery_cls = MagicMock(return_value=mock_discovery_inst)

    mock_pairing_inst = MagicMock()
    mock_pairing_cls  = MagicMock(return_value=mock_pairing_inst)

    mock_paired = MagicMock()
    mock_paired.list_paired.return_value = []
    mock_paired.remove.return_value = True

    # Schema field stubs
    schema_defaults = {
        "discoverable":                  True,
        "discoverable_timeout":          0,
        "discovery_duration_sec":        10,
        "adapter_name":                  "NemoHeadUnit",
        "autoconnect_enabled":           True,
        "autoconnect_connect_timeout_s": 8,
        "autoconnect_backoff_initial_s": 5,
        "autoconnect_backoff_cap_s":     60,
    }

    def _field_stub(default, **kwargs):
        f = MagicMock()
        f.default = default
        return f

    for key in list(sys.modules.keys()):
        if "bluetooth" in key and "test" not in key:
            del sys.modules[key]

    # Pre-populate sys.modules with the mocked paired_devices module
    sys.modules["bluetooth_manager.paired_devices"] = mock_paired

    with patch("bluetooth_manager.bluez_adapter.BluezAdapter",  mock_adapter_cls), \
         patch("bluetooth_manager.discovery.DiscoverySession",  mock_discovery_cls), \
         patch("bluetooth_manager.pairing.PairingAgent",        mock_pairing_cls), \
         patch("shared.bus_client.BusClient", return_value=mock_bus), \
         patch("shared.logger.get_logger", return_value=mock_log), \
         patch("shared.config_client.ConfigClient", return_value=mock_cfg_inst), \
         patch("shared.config_schema.field_bool",   side_effect=lambda default=True,  **kw: _field_stub(default)), \
         patch("shared.config_schema.field_int",    side_effect=lambda default=0,     **kw: _field_stub(default)), \
         patch("shared.config_schema.field_string", side_effect=lambda default="",    **kw: _field_stub(default)):
        import bluetooth_manager.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus
        mod.log = mock_log
        mod.cfg = mock_cfg_inst
        # Reset schema defaults manually
        mod._config = {k: v for k, v in schema_defaults.items()}
        mod._adapter   = None
        mod._discovery = None
        mod._pairing   = None
        mod._glib_loop = None
        mod._autoconnect_active = False
        mod._autoconnect_stop.clear()
        # Inject class refs for assertion
        mod._BluezAdapter_cls     = mock_adapter_cls
        mod._DiscoverySession_cls = mock_discovery_cls
        mod._PairingAgent_cls     = mock_pairing_cls
        mod._paired_devices_mod   = mock_paired
        yield (mod, mock_bus, mock_adapter_cls, mock_adapter_inst,
               mock_discovery_cls, mock_discovery_inst,
               mock_pairing_cls, mock_pairing_inst, mock_paired)


# ===========================================================================
# Section 1 — _SCHEMA keys and defaults
# ===========================================================================

class TestSchema:

    @pytest.mark.unit
    def test_all_keys_present(self, bt):
        mod, *_ = bt
        for key in ("discoverable", "discoverable_timeout", "discovery_duration_sec",
                    "adapter_name", "autoconnect_enabled",
                    "autoconnect_connect_timeout_s",
                    "autoconnect_backoff_initial_s", "autoconnect_backoff_cap_s"):
            assert key in mod._SCHEMA

    @pytest.mark.unit
    def test_config_seeded_from_defaults(self, bt):
        mod, *_ = bt
        assert mod._config["adapter_name"] == "NemoHeadUnit"
        assert mod._config["discoverable"] is True
        assert mod._config["autoconnect_enabled"] is True


# ===========================================================================
# Section 2 — _apply_config
# ===========================================================================

class TestApplyConfig:

    @pytest.mark.unit
    def test_skips_when_adapter_none(self, bt):
        mod, *_ = bt
        mod._adapter = None
        mod._apply_config()  # must not raise

    @pytest.mark.unit
    def test_calls_set_name(self, bt):
        mod, _, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        mod._config["adapter_name"] = "TestHead"
        mod._apply_config()
        mock_adapter_inst.set_name.assert_called_with("TestHead")

    @pytest.mark.unit
    def test_calls_set_discoverable(self, bt):
        mod, _, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        mod._config["discoverable"] = False
        mod._config["discoverable_timeout"] = 30
        mod._apply_config()
        mock_adapter_inst.set_discoverable.assert_called_with(False, timeout=30)


# ===========================================================================
# Section 3 — _on_config_loaded
# ===========================================================================

class TestOnConfigLoaded:

    @pytest.mark.unit
    def test_empty_config_uses_defaults(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, mock_pairing_inst, _ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_start_autoconnect"):
            mod._on_config_loaded({})
        assert mod._config["adapter_name"] == "NemoHeadUnit"

    @pytest.mark.unit
    def test_merges_persisted_values(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, _ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_start_autoconnect"):
            mod._on_config_loaded({"adapter_name": "CustomName"})
        assert mod._config["adapter_name"] == "CustomName"

    @pytest.mark.unit
    def test_rejects_structural_values(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, _ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_start_autoconnect"):
            mod._on_config_loaded({"adapter_name": {"nested": "bad"}})
        assert mod._config["adapter_name"] == "NemoHeadUnit"

    @pytest.mark.unit
    def test_registers_pairing_agent_when_adapter_ready(self, bt):
        mod, _, _, mock_adapter_inst, _, _, mock_pairing_cls, mock_pairing_inst, _ = bt
        mod._adapter = mock_adapter_inst
        mod._pairing = None
        with patch.object(mod, "_start_autoconnect"), \
             patch.object(mod, "BluezAdapter", mock_adapter_inst, create=True):
            mock_pairing_cls.reset_mock()
            with patch("bluetooth_manager.pairing.PairingAgent", mock_pairing_cls):
                mod._on_config_loaded({})
        mock_pairing_inst.register.assert_called()

    @pytest.mark.unit
    def test_publishes_system_ready(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, _ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_start_autoconnect"):
            mock_bus.publish.reset_mock()
            mod._on_config_loaded({})
        assert "system.ready" in _topics(mock_bus)

    @pytest.mark.unit
    def test_calls_start_autoconnect(self, bt):
        mod, _, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_start_autoconnect") as mock_ac:
            mod._on_config_loaded({})
        mock_ac.assert_called_once()


# ===========================================================================
# Section 4 — _on_config_changed
# ===========================================================================

class TestOnConfigChanged:

    @pytest.mark.unit
    def test_unknown_key_ignored(self, bt):
        mod, *_ = bt
        mod._on_config_changed("not_a_key", "val")
        assert "not_a_key" not in mod._config

    @pytest.mark.unit
    def test_structural_value_rejected(self, bt):
        mod, *_ = bt
        original = mod._config["adapter_name"]
        mod._on_config_changed("adapter_name", {"bad": True})
        assert mod._config["adapter_name"] == original

    @pytest.mark.unit
    def test_updates_config_value(self, bt):
        mod, _, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        mod._on_config_changed("adapter_name", "NewName")
        assert mod._config["adapter_name"] == "NewName"

    @pytest.mark.unit
    def test_calls_apply_config_on_change(self, bt):
        mod, _, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_apply_config") as mock_ac:
            mod._on_config_changed("discoverable", False)
        mock_ac.assert_called_once()

    @pytest.mark.unit
    def test_no_crash_without_adapter(self, bt):
        mod, *_ = bt
        mod._adapter = None
        mod._on_config_changed("adapter_name", "TestName")  # must not raise


# ===========================================================================
# Section 5 — Boot handlers
# ===========================================================================

class TestBootHandlers:

    @pytest.mark.unit
    def test_readytostart_publishes_module_ready(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod.on_system_readytostart()
        payload = _payload(mock_bus, "system.module_ready")
        assert payload == {"name": "bluetooth", "priority": 1}

    @pytest.mark.unit
    def test_system_start_wrong_priority_ignored(self, bt):
        mod, mock_bus, mock_adapter_cls, *_ = bt
        mock_bus.publish.reset_mock()
        mock_adapter_cls.reset_mock()
        mod.on_system_start("system.start", {"priority": 99})
        mock_adapter_cls.assert_not_called()

    @pytest.mark.unit
    def test_system_start_creates_adapter(self, bt):
        mod, _, mock_adapter_cls, mock_adapter_inst, *_ = bt
        with patch.object(mod, "_start_glib_mainloop"):
            mod.on_system_start("system.start", {"priority": 1})
        mock_adapter_cls.assert_called_once()

    @pytest.mark.unit
    def test_system_start_calls_adapter_init(self, bt):
        mod, _, mock_adapter_cls, mock_adapter_inst, *_ = bt
        with patch.object(mod, "_start_glib_mainloop"):
            mod.on_system_start("system.start", {"priority": 1})
        mock_adapter_inst.init.assert_called_once()

    @pytest.mark.unit
    def test_system_start_dbus_failure_publishes_error(self, bt):
        mod, mock_bus, mock_adapter_cls, mock_adapter_inst, *_ = bt
        mock_adapter_inst.init.return_value = False
        with patch.object(mod, "_start_glib_mainloop"):
            mock_bus.publish.reset_mock()
            mod.on_system_start("system.start", {"priority": 1})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_system_stop_calls_bus_stop(self, bt):
        mod, mock_bus, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_stop_glib_mainloop"), \
             patch.object(mod, "_stop_autoconnect"):
            mod.on_system_stop("system.stop", {})
        mock_bus.stop.assert_called()

    @pytest.mark.unit
    def test_system_stop_calls_adapter_shutdown(self, bt):
        mod, mock_bus, _, mock_adapter_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        with patch.object(mod, "_stop_glib_mainloop"), \
             patch.object(mod, "_stop_autoconnect"):
            mod.on_system_stop("system.stop", {})
        mock_adapter_inst.shutdown.assert_called()

    @pytest.mark.unit
    def test_system_stop_no_crash_without_adapter(self, bt):
        mod, mock_bus, *_ = bt
        mod._adapter = None
        mod._pairing = None
        with patch.object(mod, "_stop_glib_mainloop"), \
             patch.object(mod, "_stop_autoconnect"):
            mod.on_system_stop("system.stop", {})  # must not raise


# ===========================================================================
# Section 6 — on_discover
# ===========================================================================

class TestOnDiscover:

    @pytest.mark.unit
    def test_adapter_none_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mod._adapter = None
        mock_bus.publish.reset_mock()
        mod.on_discover("bluetooth_manager.discover", {"duration_sec": 10})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_discovery_already_running_ignored(self, bt):
        mod, mock_bus, _, mock_adapter_inst, mock_discovery_cls, mock_discovery_inst, *_ = bt
        mod._adapter   = mock_adapter_inst
        mock_disc      = MagicMock()
        mock_disc.is_running = True
        mod._discovery = mock_disc
        mock_discovery_cls.reset_mock()
        mod.on_discover("bluetooth_manager.discover", {"duration_sec": 5})
        mock_discovery_cls.assert_not_called()

    @pytest.mark.unit
    def test_creates_discovery_session(self, bt):
        mod, _, _, mock_adapter_inst, mock_discovery_cls, mock_discovery_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        mock_discovery_cls.reset_mock()
        mod.on_discover("bluetooth_manager.discover", {"duration_sec": 10})
        mock_discovery_cls.assert_called_once()

    @pytest.mark.unit
    def test_uses_custom_duration(self, bt):
        mod, _, _, mock_adapter_inst, mock_discovery_cls, mock_discovery_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        mod.on_discover("bluetooth_manager.discover", {"duration_sec": 25})
        mock_discovery_inst.start.assert_called_with(duration_sec=25)

    @pytest.mark.unit
    def test_uses_config_duration_as_default(self, bt):
        mod, _, _, mock_adapter_inst, mock_discovery_cls, mock_discovery_inst, *_ = bt
        mod._adapter = mock_adapter_inst
        mod._config["discovery_duration_sec"] = 15
        mod.on_discover("bluetooth_manager.discover", {})
        mock_discovery_inst.start.assert_called_with(duration_sec=15)


# ===========================================================================
# Section 7 — on_pair
# ===========================================================================

class TestOnPair:

    @pytest.mark.unit
    def test_pairing_none_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mod._pairing = None
        mock_bus.publish.reset_mock()
        mod.on_pair("bluetooth_manager.pair", {"device_address": "AA:BB:CC:DD:EE:FF"})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_missing_address_publishes_error(self, bt):
        mod, mock_bus, _, _, _, _, _, mock_pairing_inst, _ = bt
        mod._pairing = mock_pairing_inst
        mock_bus.publish.reset_mock()
        mod.on_pair("bluetooth_manager.pair", {})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_calls_pairing_pair(self, bt):
        mod, _, _, _, _, _, _, mock_pairing_inst, _ = bt
        mod._pairing = mock_pairing_inst
        mod.on_pair("bluetooth_manager.pair", {"device_address": "AA:BB:CC:DD:EE:FF"})
        mock_pairing_inst.pair.assert_called_once_with("AA:BB:CC:DD:EE:FF")


# ===========================================================================
# Section 8 — on_confirm_pairing / on_reject_pairing
# ===========================================================================

class TestConfirmRejectPairing:

    @pytest.mark.unit
    def test_confirm_missing_fields_publishes_error(self, bt):
        mod, mock_bus, _, _, _, _, _, mock_pairing_inst, _ = bt
        mod._pairing = mock_pairing_inst
        mock_bus.publish.reset_mock()
        mod.on_confirm_pairing("bluetooth_manager.confirm_pairing", {"device_address": "AA:BB"})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_confirm_happy_path(self, bt):
        mod, _, _, _, _, _, _, mock_pairing_inst, _ = bt
        mod._pairing = mock_pairing_inst
        mod.on_confirm_pairing(
            "bluetooth_manager.confirm_pairing",
            {"device_address": "AA:BB:CC:DD:EE:FF", "pin": "1234"}
        )
        mock_pairing_inst.confirm.assert_called_once_with("AA:BB:CC:DD:EE:FF", "1234")

    @pytest.mark.unit
    def test_reject_calls_pairing_reject(self, bt):
        mod, _, _, _, _, _, _, mock_pairing_inst, _ = bt
        mod._pairing = mock_pairing_inst
        mod.on_reject_pairing(
            "bluetooth_manager.reject_pairing",
            {"device_address": "AA:BB:CC:DD:EE:FF"}
        )
        mock_pairing_inst.reject.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    @pytest.mark.unit
    def test_reject_no_pairing_no_crash(self, bt):
        mod, *_ = bt
        mod._pairing = None
        mod.on_reject_pairing("bluetooth_manager.reject_pairing", {"device_address": "AA:BB"})  # must not raise

    @pytest.mark.unit
    def test_reject_missing_address_no_call(self, bt):
        mod, _, _, _, _, _, _, mock_pairing_inst, _ = bt
        mod._pairing = mock_pairing_inst
        mod.on_reject_pairing("bluetooth_manager.reject_pairing", {})
        mock_pairing_inst.reject.assert_not_called()


# ===========================================================================
# Section 9 — on_rfcomm_connected
# ===========================================================================

class TestOnRfcommConnected:

    @pytest.mark.unit
    def test_sets_autoconnect_stop_event(self, bt):
        mod, *_ = bt
        mod._autoconnect_stop.clear()
        mod.on_rfcomm_connected("bluetooth_manager.rfcomm.connected", {"device_address": "AA:BB"})
        assert mod._autoconnect_stop.is_set()


# ===========================================================================
# Section 10 — on_try_autoconnect
# ===========================================================================

class TestOnTryAutoconnect:

    @pytest.mark.unit
    def test_calls_start_autoconnect(self, bt):
        mod, *_ = bt
        with patch.object(mod, "_start_autoconnect") as mock_ac:
            mod.on_try_autoconnect("bluetooth_manager.try_autoconnect", {})
        mock_ac.assert_called_once()


# ===========================================================================
# Section 11 — on_paired_list
# ===========================================================================

class TestOnPairedList:

    @pytest.mark.unit
    def test_adapter_none_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mod._adapter = None
        mock_bus.publish.reset_mock()
        mod.on_paired_list("bluetooth_manager.paired.list", {})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_publishes_paired_devices(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, mock_paired = bt
        mod._adapter = mock_adapter_inst
        mock_paired.list_paired.return_value = [{"address": "AA:BB", "name": "Phone"}]
        mock_bus.publish.reset_mock()
        mod.on_paired_list("bluetooth_manager.paired.list", {})
        payload = _payload(mock_bus, "bluetooth_manager.paired.devices")
        assert payload["devices"] == [{"address": "AA:BB", "name": "Phone"}]

    @pytest.mark.unit
    def test_empty_list_published(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, mock_paired = bt
        mod._adapter = mock_adapter_inst
        mock_paired.list_paired.return_value = []
        mock_bus.publish.reset_mock()
        mod.on_paired_list("bluetooth_manager.paired.list", {})
        payload = _payload(mock_bus, "bluetooth_manager.paired.devices")
        assert payload["devices"] == []


# ===========================================================================
# Section 12 — on_paired_remove
# ===========================================================================

class TestOnPairedRemove:

    @pytest.mark.unit
    def test_missing_address_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod.on_paired_remove("bluetooth_manager.paired.remove", {})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_adapter_none_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mod._adapter = None
        mock_bus.publish.reset_mock()
        mod.on_paired_remove("bluetooth_manager.paired.remove", {"device_address": "AA:BB"})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_success_publishes_removed(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, mock_paired = bt
        mod._adapter = mock_adapter_inst
        mock_paired.remove.return_value = True
        mock_bus.publish.reset_mock()
        mod.on_paired_remove("bluetooth_manager.paired.remove", {"device_address": "AA:BB"})
        payload = _payload(mock_bus, "bluetooth_manager.paired.removed")
        assert payload["device_address"] == "AA:BB"

    @pytest.mark.unit
    def test_failure_publishes_failed(self, bt):
        mod, mock_bus, _, mock_adapter_inst, _, _, _, _, mock_paired = bt
        mod._adapter = mock_adapter_inst
        mock_paired.remove.return_value = False
        mock_bus.publish.reset_mock()
        mod.on_paired_remove("bluetooth_manager.paired.remove", {"device_address": "AA:BB"})
        assert "bluetooth_manager.paired.failed" in _topics(mock_bus)


# ===========================================================================
# Section 13 — on_paired_connect / on_paired_disconnect
# ===========================================================================

class TestOnPairedConnectDisconnect:

    @pytest.mark.unit
    def test_connect_missing_address_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod.on_paired_connect("bluetooth_manager.paired.connect", {})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_connect_adapter_none_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mod._adapter = None
        mock_bus.publish.reset_mock()
        mod.on_paired_connect("bluetooth_manager.paired.connect", {"device_address": "AA:BB"})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_connect_calls_paired_devices_connect(self, bt):
        mod, _, _, mock_adapter_inst, _, _, _, _, mock_paired = bt
        mod._adapter = mock_adapter_inst
        mock_paired.connect.reset_mock()
        mod.on_paired_connect("bluetooth_manager.paired.connect", {"device_address": "AA:BB"})
        mock_paired.connect.assert_called_once()
        call_kwargs = mock_paired.connect.call_args
        assert call_kwargs.args[1] == "AA:BB" or call_kwargs.kwargs.get("address") == "AA:BB" \
            or call_kwargs.args[0] is mock_adapter_inst.bus

    @pytest.mark.unit
    def test_disconnect_missing_address_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod.on_paired_disconnect("bluetooth_manager.paired.disconnect", {})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_disconnect_adapter_none_publishes_error(self, bt):
        mod, mock_bus, *_ = bt
        mod._adapter = None
        mock_bus.publish.reset_mock()
        mod.on_paired_disconnect("bluetooth_manager.paired.disconnect", {"device_address": "AA:BB"})
        assert "bluetooth_manager.error" in _topics(mock_bus)

    @pytest.mark.unit
    def test_disconnect_calls_paired_devices_disconnect(self, bt):
        mod, _, _, mock_adapter_inst, _, _, _, _, mock_paired = bt
        mod._adapter = mock_adapter_inst
        mock_paired.disconnect.reset_mock()
        mod.on_paired_disconnect("bluetooth_manager.paired.disconnect", {"device_address": "AA:BB"})
        mock_paired.disconnect.assert_called_once()


# ===========================================================================
# Section 14 — Internal callbacks
# ===========================================================================

class TestInternalCallbacks:

    @pytest.mark.unit
    def test_on_device_found_publishes(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod._on_device_found("AA:BB:CC:DD:EE:FF", "MyPhone", -55)
        payload = _payload(mock_bus, "bluetooth_manager.device.found")
        assert payload == {"address": "AA:BB:CC:DD:EE:FF", "name": "MyPhone", "rssi": -55}

    @pytest.mark.unit
    def test_on_discovery_done_publishes(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        devices = [{"address": "AA:BB", "name": "Phone"}]
        mod._on_discovery_done(devices)
        payload = _payload(mock_bus, "bluetooth_manager.discovery.completed")
        assert payload["devices"] == devices

    @pytest.mark.unit
    def test_on_pin_requested_publishes(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod._on_pin_requested("AA:BB:CC:DD:EE:FF", "1234")
        payload = _payload(mock_bus, "bluetooth_manager.pairing.pin")
        assert payload == {"device_address": "AA:BB:CC:DD:EE:FF", "pin": "1234"}

    @pytest.mark.unit
    def test_on_pairing_completed_publishes(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod._on_pairing_completed("AA:BB:CC:DD:EE:FF")
        payload = _payload(mock_bus, "bluetooth_manager.pairing.completed")
        assert payload == {"device_address": "AA:BB:CC:DD:EE:FF"}

    @pytest.mark.unit
    def test_on_pairing_failed_publishes(self, bt):
        mod, mock_bus, *_ = bt
        mock_bus.publish.reset_mock()
        mod._on_pairing_failed("AA:BB:CC:DD:EE:FF", "auth failed")
        payload = _payload(mock_bus, "bluetooth_manager.pairing.failed")
        assert payload == {"device_address": "AA:BB:CC:DD:EE:FF", "error": "auth failed"}


# ===========================================================================
# Section 15 — _start_autoconnect / _stop_autoconnect
# ===========================================================================

class TestAutoconnect:

    @pytest.mark.unit
    def test_start_skips_when_disabled_by_config(self, bt):
        mod, *_ = bt
        mod._config["autoconnect_enabled"] = False
        with patch("threading.Thread") as mock_thread:
            mod._start_autoconnect()
        mock_thread.assert_not_called()

    @pytest.mark.unit
    def test_start_skips_when_already_active(self, bt):
        mod, *_ = bt
        mod._autoconnect_active = True
        with patch("threading.Thread") as mock_thread:
            mod._start_autoconnect()
        mock_thread.assert_not_called()

    @pytest.mark.unit
    def test_start_sets_active_flag(self, bt):
        mod, *_ = bt
        mod._autoconnect_active = False
        mock_t = MagicMock()
        with patch("threading.Thread", return_value=mock_t):
            mod._start_autoconnect()
        assert mod._autoconnect_active is True

    @pytest.mark.unit
    def test_start_launches_thread(self, bt):
        mod, *_ = bt
        mock_t = MagicMock()
        with patch("threading.Thread", return_value=mock_t):
            mod._start_autoconnect()
        mock_t.start.assert_called_once()

    @pytest.mark.unit
    def test_stop_sets_event(self, bt):
        mod, *_ = bt
        mod._autoconnect_stop.clear()
        mod._stop_autoconnect("test")
        assert mod._autoconnect_stop.is_set()
