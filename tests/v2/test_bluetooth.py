"""
Unit tests for v2/modules/bluetooth/main.py

Tested behaviours:
  - on_system_start: initialises adapter, registers profiles, applies config, starts RFCOMM
  - on_system_start: publishes bluetooth.error when D-Bus init fails
  - on_system_start: publishes bluetooth.error when profile registration fails
  - on_system_start: publishes bluetooth.error when RFCOMM listener fails
  - on_system_stop: stops RFCOMM, unregisters pairing agent, shuts down adapter
  - on_discover: uses configured discovery_duration_sec as fallback
  - on_discover: honours duration_sec from payload when provided
  - on_discover: publishes bluetooth.error when adapter is not ready
  - on_pair: delegates to PairingAgent.pair()
  - on_pair: publishes bluetooth.error when device_address is missing
  - on_confirm_pairing: delegates to PairingAgent.confirm()
  - on_confirm_pairing: publishes bluetooth.error when fields are missing
  - _on_config_loaded: merges persisted config with defaults, ignores unknown keys
  - _on_config_changed: updates single key, ignores unknown keys
  - _apply_config: calls set_name and set_discoverable on the adapter
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_TESTS   = Path(__file__).parent
_ROOT    = _TESTS.parent.parent
_V2      = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in (str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Stub shared dependencies before import
# ---------------------------------------------------------------------------

_bus_instance = MagicMock()
_bus_class    = MagicMock(return_value=_bus_instance)

_cfg_instance = MagicMock()
_cfg_class    = MagicMock(return_value=_cfg_instance)

_shared_pkg        = types.ModuleType("shared")
_bus_client_mod    = types.ModuleType("shared.bus_client")
_config_client_mod = types.ModuleType("shared.config_client")
_logger_mod        = types.ModuleType("shared.logger")

_bus_client_mod.BusClient       = _bus_class
_config_client_mod.ConfigClient = _cfg_class
_logger_mod.get_logger          = MagicMock(return_value=MagicMock())

sys.modules.setdefault("shared",               _shared_pkg)
sys.modules["shared.bus_client"]    = _bus_client_mod
sys.modules["shared.config_client"] = _config_client_mod
sys.modules["shared.logger"]        = _logger_mod

# Stub bluetooth sub-helpers
_bt_pkg            = types.ModuleType("bluetooth")
_bluez_mod         = types.ModuleType("bluetooth.bluez_adapter")
_discovery_mod     = types.ModuleType("bluetooth.discovery")
_pairing_mod       = types.ModuleType("bluetooth.pairing")
_rfcomm_mod        = types.ModuleType("bluetooth.rfcomm")

_MockBluezAdapter    = MagicMock()
_MockDiscoverySession = MagicMock()
_MockPairingAgent    = MagicMock()
_MockRfcommListener  = MagicMock()

_bluez_mod.BluezAdapter     = _MockBluezAdapter
_discovery_mod.DiscoverySession = _MockDiscoverySession
_pairing_mod.PairingAgent   = _MockPairingAgent
_rfcomm_mod.RfcommListener  = _MockRfcommListener

sys.modules.setdefault("bluetooth",              _bt_pkg)
sys.modules["bluetooth.bluez_adapter"] = _bluez_mod
sys.modules["bluetooth.discovery"]     = _discovery_mod
sys.modules["bluetooth.pairing"]       = _pairing_mod
sys.modules["bluetooth.rfcomm"]        = _rfcomm_mod

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------

import v2.modules.bluetooth.main as bt  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset all module-level singletons and mocks before each test."""
    bt._adapter   = None
    bt._discovery = None
    bt._pairing   = None
    bt._rfcomm    = None
    bt._config    = dict(bt._DEFAULTS)
    bt.bus.publish.reset_mock()
    _MockBluezAdapter.reset_mock()
    _MockDiscoverySession.reset_mock()
    _MockPairingAgent.reset_mock()
    _MockRfcommListener.reset_mock()


def _make_adapter(init_ok=True, profiles_ok=True, rfcomm_ok=True):
    """Return a fresh BluezAdapter mock with configurable return values."""
    adapter = MagicMock()
    adapter.init.return_value            = init_ok
    adapter.register_profiles.return_value = profiles_ok
    return adapter


def _make_rfcomm(start_ok=True):
    rfcomm = MagicMock()
    rfcomm.start.return_value = start_ok
    return rfcomm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _published(topic):
    return [
        args[1]
        for args, _ in bt.bus.publish.call_args_list
        if args[0] == topic
    ]


# ---------------------------------------------------------------------------
# Tests — system.start
# ---------------------------------------------------------------------------

class TestSystemStart:
    def test_happy_path_initialises_subsystems(self):
        adapter = _make_adapter()
        pairing = MagicMock()
        rfcomm  = _make_rfcomm()

        _MockBluezAdapter.return_value  = adapter
        _MockPairingAgent.return_value  = pairing
        _MockRfcommListener.return_value = rfcomm

        bt.on_system_start("system.start", {})

        adapter.init.assert_called_once()
        adapter.register_profiles.assert_called_once()
        adapter.set_name.assert_called_once_with(bt._DEFAULTS["adapter_name"])
        adapter.set_discoverable.assert_called_once()
        pairing.register.assert_called_once()
        rfcomm.start.assert_called_once()

    def test_dbus_init_failure_publishes_error(self):
        _MockBluezAdapter.return_value = _make_adapter(init_ok=False)
        bt.on_system_start("system.start", {})
        assert len(_published("bluetooth.error")) == 1

    def test_profile_registration_failure_publishes_error(self):
        _MockBluezAdapter.return_value = _make_adapter(profiles_ok=False)
        bt.on_system_start("system.start", {})
        assert len(_published("bluetooth.error")) == 1

    def test_rfcomm_failure_publishes_error(self):
        _MockBluezAdapter.return_value  = _make_adapter()
        _MockPairingAgent.return_value  = MagicMock()
        _MockRfcommListener.return_value = _make_rfcomm(start_ok=False)
        bt.on_system_start("system.start", {})
        assert len(_published("bluetooth.error")) == 1


# ---------------------------------------------------------------------------
# Tests — system.stop
# ---------------------------------------------------------------------------

class TestSystemStop:
    def test_teardown_calls_stop_on_all_subsystems(self):
        bt._adapter  = MagicMock()
        bt._pairing  = MagicMock()
        bt._rfcomm   = MagicMock()

        bt.on_system_stop("system.stop", {})

        bt._rfcomm.stop.assert_called_once()
        bt._pairing.unregister.assert_called_once()
        bt._adapter.set_discoverable.assert_called_once_with(False)
        bt._adapter.shutdown.assert_called_once()
        bt.bus.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — bluetooth.discover
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_no_adapter_publishes_error(self):
        bt.on_discover("bluetooth.discover", {})
        assert len(_published("bluetooth.error")) == 1

    def test_uses_config_duration_as_default(self):
        bt._adapter = MagicMock()
        bt._config["discovery_duration_sec"] = 15
        session = MagicMock()
        _MockDiscoverySession.return_value = session

        bt.on_discover("bluetooth.discover", {})

        session.start.assert_called_once_with(duration_sec=15)

    def test_payload_duration_overrides_config(self):
        bt._adapter = MagicMock()
        session = MagicMock()
        _MockDiscoverySession.return_value = session

        bt.on_discover("bluetooth.discover", {"duration_sec": 5})

        session.start.assert_called_once_with(duration_sec=5)


# ---------------------------------------------------------------------------
# Tests — bluetooth.pair
# ---------------------------------------------------------------------------

class TestPair:
    def test_no_pairing_agent_publishes_error(self):
        bt.on_pair("bluetooth.pair", {"device_address": "AA:BB:CC:DD:EE:FF"})
        assert len(_published("bluetooth.error")) == 1

    def test_missing_address_publishes_error(self):
        bt._pairing = MagicMock()
        bt.on_pair("bluetooth.pair", {})
        assert len(_published("bluetooth.error")) == 1

    def test_delegates_to_pairing_agent(self):
        bt._pairing = MagicMock()
        bt.on_pair("bluetooth.pair", {"device_address": "AA:BB:CC:DD:EE:FF"})
        bt._pairing.pair.assert_called_once_with("AA:BB:CC:DD:EE:FF")


# ---------------------------------------------------------------------------
# Tests — bluetooth.confirm_pairing
# ---------------------------------------------------------------------------

class TestConfirmPairing:
    def test_no_pairing_agent_publishes_error(self):
        bt.on_confirm_pairing("bluetooth.confirm_pairing",
                              {"device_address": "AA:BB", "pin": "123456"})
        assert len(_published("bluetooth.error")) == 1

    def test_missing_fields_publishes_error(self):
        bt._pairing = MagicMock()
        bt.on_confirm_pairing("bluetooth.confirm_pairing", {"device_address": "AA:BB"})
        assert len(_published("bluetooth.error")) == 1

    def test_delegates_to_pairing_agent(self):
        bt._pairing = MagicMock()
        bt.on_confirm_pairing("bluetooth.confirm_pairing",
                              {"device_address": "AA:BB", "pin": "123456"})
        bt._pairing.confirm.assert_called_once_with("AA:BB", "123456")


# ---------------------------------------------------------------------------
# Tests — config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_on_config_loaded_merges_with_defaults(self):
        bt._on_config_loaded({"discoverable": False, "adapter_name": "MyUnit"})
        assert bt._config["discoverable"] is False
        assert bt._config["adapter_name"] == "MyUnit"
        # keys not in persisted config stay at default
        assert bt._config["discovery_duration_sec"] == bt._DEFAULTS["discovery_duration_sec"]

    def test_on_config_loaded_ignores_unknown_keys(self):
        bt._on_config_loaded({"unknown_key": "value"})
        assert "unknown_key" not in bt._config

    def test_on_config_changed_updates_key(self):
        bt._on_config_changed("adapter_name", "NewName")
        assert bt._config["adapter_name"] == "NewName"

    def test_on_config_changed_ignores_unknown_key(self):
        original = dict(bt._config)
        bt._on_config_changed("nonexistent", 99)
        assert bt._config == original

    def test_apply_config_calls_adapter_methods(self):
        bt._adapter = MagicMock()
        bt._config["adapter_name"]         = "TestUnit"
        bt._config["discoverable"]          = False
        bt._config["discoverable_timeout"]  = 60
        bt._apply_config()
        bt._adapter.set_name.assert_called_once_with("TestUnit")
        bt._adapter.set_discoverable.assert_called_once_with(False, timeout=60)

    def test_apply_config_noop_when_no_adapter(self):
        # Must not raise when adapter is None
        bt._apply_config()
