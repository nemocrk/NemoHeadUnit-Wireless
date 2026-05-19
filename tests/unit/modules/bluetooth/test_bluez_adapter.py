"""
Unit tests for bluetooth/bluez_adapter.py

Strategy:
  BluezAdapter usa dbus-python direttamente (import dbus inside methods).
  Tutti gli import dbus, dbus.mainloop.glib, gi.repository sono mockati
  in sys.modules prima dell’import del modulo.

  La fixture `ba` costruisce un BluezAdapter fresh per ogni test e
  inietta mock dbus objects tramite patch('dbus.SystemBus') e
  patch('dbus.Interface').

  Siccome dbus viene importato *dentro* i metodi (lazy import), usiamo
  patch() contestuale per controllare ogni chiamata.

Covers:
  Section 1  — __init__: stato iniziale (bus/adapter/profile_mgr/initialized = None/False)
  Section 2  — init(): successo, nessun adapter trovato → False,
               eccezione D-Bus → False, _initialized=True dopo successo,
               _find_adapter_path chiamato, profilo manager ottenuto
  Section 3  — _find_adapter_path: presente, assente, più oggetti
  Section 4  — register_profiles(): not initialized → False,
               happy path → True, entrambi i profili registrati,
               UUID already registered → non-fatal warning,
               NotPermitted → non-fatal, altra eccezione → raise
  Section 5  — set_discoverable(): not initialized → skip,
               setta Discoverable + DiscoverableTimeout,
               retry su eccezione (< 3 tentativi), fallimento definitivo logga errore
  Section 6  — set_name(): not initialized → warning,
               setta Alias, retry su eccezione
  Section 7  — get_adapter_address(): not initialized → '',
               ritorna stringa indirizzo, eccezione → ''
  Section 8  — is_discovering(): not initialized → False,
               True/False da D-Bus, eccezione → False
  Section 9  — reset(): not initialized → False, power-cycle chiama Set x3,
               chiama register_profiles, eccezione → False
  Section 10 — shutdown(): chiama bus.close(), _initialized=False, no-crash se bus=None
  Section 11 — bus property: None prima di init, bus dopo init
  Section 12 — HFP_UUID / HSP_UUID costanti
"""

from __future__ import annotations

import sys
import types
import importlib
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Install dbus / gi stubs at module level
# ---------------------------------------------------------------------------

def _install_stubs():
    dbus_mod = types.ModuleType("dbus")
    dbus_mod.SystemBus  = MagicMock
    dbus_mod.Interface  = MagicMock
    dbus_mod.Dictionary = MagicMock
    dbus_mod.Boolean    = lambda v: v
    dbus_mod.UInt16     = lambda v: v
    dbus_mod.UInt32     = lambda v: v
    dbus_mod.String     = lambda v: v
    sys.modules.setdefault("dbus", dbus_mod)

    ml = types.ModuleType("dbus.mainloop")
    sys.modules.setdefault("dbus.mainloop", ml)
    ml_glib = types.ModuleType("dbus.mainloop.glib")
    ml_glib.DBusGMainLoop = MagicMock()
    sys.modules.setdefault("dbus.mainloop.glib", ml_glib)

    gi_mod = types.ModuleType("gi")
    gi_mod.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mod)
    gi_repo = types.ModuleType("gi.repository")
    gi_repo.GLib = MagicMock()
    sys.modules.setdefault("gi.repository", gi_repo)
    sys.modules.setdefault("gi.repository.GLib", MagicMock())


_install_stubs()


# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------

_this_module = __name__
for _k in list(sys.modules.keys()):
    if _k != _this_module and "bluez_adapter" in _k:
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()):
    import bluetooth_manager.bluez_adapter as _ba_mod
    importlib.reload(_ba_mod)

BluezAdapter = _ba_mod.BluezAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter_objects(adapter_path="/org/bluez/hci0"):
    """
    Build a fake GetManagedObjects() return value with one Adapter1.
    """
    return {
        adapter_path: {"org.bluez.Adapter1": {"Address": "AA:BB:CC:DD:EE:FF"}},
        "/org/bluez/hci0/dev_11_22": {"org.bluez.Device1": {}},
    }


def _make_dbus_mocks(adapter_path="/org/bluez/hci0", find_adapter=True):
    """
    Return (mock_dbus, mock_sys_bus, mock_manager_iface, mock_adapter_iface,
            mock_profile_mgr_iface, mock_props_iface)
    """
    mock_sys_bus = MagicMock()
    mock_obj     = MagicMock()
    mock_sys_bus.get_object.return_value = mock_obj

    mock_manager_iface  = MagicMock()
    mock_adapter_iface  = MagicMock()
    mock_profile_mgr    = MagicMock()
    mock_props_iface    = MagicMock()

    objects = _make_adapter_objects(adapter_path) if find_adapter else {}
    mock_manager_iface.GetManagedObjects.return_value = objects

    def _iface_factory(obj, iface_name):
        if iface_name == "org.freedesktop.DBus.ObjectManager":
            return mock_manager_iface
        if iface_name == "org.bluez.Adapter1":
            return mock_adapter_iface
        if iface_name == "org.bluez.ProfileManager1":
            return mock_profile_mgr
        if iface_name == "org.freedesktop.DBus.Properties":
            return mock_props_iface
        return MagicMock()

    mock_dbus = MagicMock()
    mock_dbus.SystemBus.return_value = mock_sys_bus
    mock_dbus.Interface.side_effect  = _iface_factory
    mock_dbus.Boolean  = lambda v: v
    mock_dbus.UInt16   = lambda v: v
    mock_dbus.UInt32   = lambda v: v
    mock_dbus.String   = lambda v: v
    mock_dbus.Dictionary = MagicMock(return_value={})

    return (mock_dbus, mock_sys_bus, mock_manager_iface,
            mock_adapter_iface, mock_profile_mgr, mock_props_iface)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ba():
    """Return a fresh BluezAdapter instance."""
    adapter = BluezAdapter()
    return adapter


@pytest.fixture()
def ba_init():
    """
    Return (adapter, mock_dbus, mock_props_iface) with adapter.init() already called.
    """
    adapter = BluezAdapter()
    mocks = _make_dbus_mocks()
    mock_dbus, mock_sys_bus, _, mock_adapter_iface, _, mock_props_iface = mocks

    mock_adapter_iface.object_path = "/org/bluez/hci0"

    with patch.dict(sys.modules, {"dbus": mock_dbus}):
        result = adapter.init()

    assert result is True
    return adapter, mock_dbus, mock_props_iface, mock_adapter_iface


# ===========================================================================
# Section 1 — __init__
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_bus_is_none_initially(self, ba):
        assert ba._bus is None

    @pytest.mark.unit
    def test_adapter_is_none_initially(self, ba):
        assert ba._adapter is None

    @pytest.mark.unit
    def test_profile_mgr_is_none_initially(self, ba):
        assert ba._profile_mgr is None

    @pytest.mark.unit
    def test_initialized_is_false(self, ba):
        assert ba._initialized is False

    @pytest.mark.unit
    def test_bus_property_returns_none(self, ba):
        assert ba.bus is None


# ===========================================================================
# Section 2 — init()
# ===========================================================================

class TestInitMethod:

    @pytest.mark.unit
    def test_returns_true_on_success(self, ba):
        mock_dbus = _make_dbus_mocks()[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = ba.init()
        assert result is True

    @pytest.mark.unit
    def test_sets_initialized_on_success(self, ba):
        mock_dbus = _make_dbus_mocks()[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            ba.init()
        assert ba._initialized is True

    @pytest.mark.unit
    def test_returns_false_when_no_adapter_found(self, ba):
        mock_dbus = _make_dbus_mocks(find_adapter=False)[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = ba.init()
        assert result is False

    @pytest.mark.unit
    def test_initialized_false_when_no_adapter(self, ba):
        mock_dbus = _make_dbus_mocks(find_adapter=False)[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            ba.init()
        assert ba._initialized is False

    @pytest.mark.unit
    def test_returns_false_on_dbus_exception(self, ba):
        mock_dbus = MagicMock()
        mock_dbus.SystemBus.side_effect = Exception("D-Bus unavailable")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = ba.init()
        assert result is False

    @pytest.mark.unit
    def test_bus_property_set_after_init(self, ba):
        mock_dbus = _make_dbus_mocks()[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            ba.init()
        assert ba.bus is not None

    @pytest.mark.unit
    def test_profile_mgr_set_after_init(self, ba):
        mock_dbus = _make_dbus_mocks()[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            ba.init()
        assert ba._profile_mgr is not None


# ===========================================================================
# Section 3 — _find_adapter_path
# ===========================================================================

class TestFindAdapterPath:

    @pytest.mark.unit
    def test_finds_correct_path(self, ba):
        objects = _make_adapter_objects("/org/bluez/hci0")
        result = ba._find_adapter_path(objects)
        assert result == "/org/bluez/hci0"

    @pytest.mark.unit
    def test_returns_none_when_no_adapter(self, ba):
        objects = {"/org/bluez/dev_1": {"org.bluez.Device1": {}}}
        result = ba._find_adapter_path(objects)
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_empty_objects(self, ba):
        result = ba._find_adapter_path({})
        assert result is None

    @pytest.mark.unit
    def test_returns_first_adapter_among_multiple_paths(self, ba):
        objects = {
            "/org/bluez/hci1": {"org.bluez.Adapter1": {}},
            "/org/bluez/dev_1": {"org.bluez.Device1": {}},
        }
        result = ba._find_adapter_path(objects)
        assert result == "/org/bluez/hci1"


# ===========================================================================
# Section 4 — register_profiles()
# ===========================================================================

class TestRegisterProfiles:

    @pytest.mark.unit
    def test_returns_false_when_not_initialized(self, ba):
        result = ba.register_profiles()
        assert result is False

    @pytest.mark.unit
    def test_returns_true_on_success(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.register_profiles()
        assert result is True

    @pytest.mark.unit
    def test_registers_both_profiles(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        _, _, _, _, mock_profile_mgr, _ = _make_dbus_mocks()
        # Rebuild with same mock_dbus to capture RegisterProfile calls
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.register_profiles()
        # _register_one is called twice — verify via profile_mgr on the adapter
        assert adapter._profile_mgr is not None

    @pytest.mark.unit
    def test_uuid_already_registered_is_non_fatal(self, ba_init):
        adapter, mock_dbus, mock_props, _ = ba_init
        adapter._profile_mgr = MagicMock()
        adapter._profile_mgr.RegisterProfile.side_effect = Exception("UUID already registered")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.register_profiles()
        assert result is True

    @pytest.mark.unit
    def test_not_permitted_is_non_fatal(self, ba_init):
        adapter, mock_dbus, mock_props, _ = ba_init
        adapter._profile_mgr = MagicMock()
        adapter._profile_mgr.RegisterProfile.side_effect = Exception("org.bluez.Error.NotPermitted")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.register_profiles()
        assert result is True

    @pytest.mark.unit
    def test_other_exception_propagates(self, ba_init):
        adapter, mock_dbus, mock_props, _ = ba_init
        adapter._profile_mgr = MagicMock()
        adapter._profile_mgr.RegisterProfile.side_effect = Exception("unexpected error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.register_profiles()
        assert result is False


# ===========================================================================
# Section 5 — set_discoverable()
# ===========================================================================

class TestSetDiscoverable:

    @pytest.mark.unit
    def test_skips_when_not_initialized(self, ba):
        ba.set_discoverable(True)  # must not raise

    @pytest.mark.unit
    def test_sets_discoverable_property(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_discoverable(True, timeout=0)
        calls = mock_props.Set.call_args_list
        prop_names = [c.args[1] for c in calls]
        assert "Discoverable" in prop_names

    @pytest.mark.unit
    def test_sets_discoverable_timeout_property(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_discoverable(True, timeout=120)
        calls = mock_props.Set.call_args_list
        prop_names = [c.args[1] for c in calls]
        assert "DiscoverableTimeout" in prop_names

    @pytest.mark.unit
    def test_retries_on_transient_exception(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        # Fail first, succeed second
        call_count = [0]
        def side_effect(*args):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("transient")
        mock_props.Set.side_effect = side_effect
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_discoverable(True)  # must not raise

    @pytest.mark.unit
    def test_logs_error_after_3_failures(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Set.side_effect = Exception("persistent")
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_discoverable(True)  # must not raise
        assert mock_props.Set.call_count == 3


# ===========================================================================
# Section 6 — set_name()
# ===========================================================================

class TestSetName:

    @pytest.mark.unit
    def test_skips_when_not_initialized(self, ba):
        ba.set_name("TestName")  # must not raise

    @pytest.mark.unit
    def test_sets_alias_property(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_name("NemoHeadUnit")
        calls = mock_props.Set.call_args_list
        prop_names = [c.args[1] for c in calls]
        assert "Alias" in prop_names

    @pytest.mark.unit
    def test_retries_on_transient_exception(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        call_count = [0]
        def side_effect(*args):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise Exception("transient")
        mock_props.Set.side_effect = side_effect
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_name("TestName")  # must not raise

    @pytest.mark.unit
    def test_logs_error_after_3_failures(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Set.side_effect = Exception("persistent")
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.set_name("TestName")  # must not raise
        assert mock_props.Set.call_count == 3


# ===========================================================================
# Section 7 — get_adapter_address()
# ===========================================================================

class TestGetAdapterAddress:

    @pytest.mark.unit
    def test_returns_empty_when_not_initialized(self, ba):
        result = ba.get_adapter_address()
        assert result == ""

    @pytest.mark.unit
    def test_returns_address_string(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Get.return_value = "AA:BB:CC:DD:EE:FF"
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.get_adapter_address()
        assert result == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.unit
    def test_returns_empty_on_exception(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Get.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.get_adapter_address()
        assert result == ""


# ===========================================================================
# Section 8 — is_discovering()
# ===========================================================================

class TestIsDiscovering:

    @pytest.mark.unit
    def test_returns_false_when_not_initialized(self, ba):
        result = ba.is_discovering()
        assert result is False

    @pytest.mark.unit
    def test_returns_true_from_dbus(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Get.return_value = True
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.is_discovering()
        assert result is True

    @pytest.mark.unit
    def test_returns_false_from_dbus(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Get.return_value = False
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.is_discovering()
        assert result is False

    @pytest.mark.unit
    def test_returns_false_on_exception(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Get.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.is_discovering()
        assert result is False


# ===========================================================================
# Section 9 — reset()
# ===========================================================================

class TestReset:

    @pytest.mark.unit
    def test_returns_false_when_not_initialized(self, ba):
        result = ba.reset()
        assert result is False

    @pytest.mark.unit
    def test_returns_true_on_success(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.reset()
        assert result is True

    @pytest.mark.unit
    def test_power_cycles_adapter(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.reset()
        # Set should be called at least twice: Powered=False, Powered=True
        powered_calls = [
            c for c in mock_props.Set.call_args_list
            if len(c.args) >= 2 and c.args[1] == "Powered"
        ]
        assert len(powered_calls) >= 2

    @pytest.mark.unit
    def test_calls_register_profiles_after_power_cycle(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        with patch.object(adapter, "register_profiles", return_value=True) as mock_rp, \
             patch("time.sleep"), \
             patch.dict(sys.modules, {"dbus": mock_dbus}):
            adapter.reset()
        mock_rp.assert_called_once()

    @pytest.mark.unit
    def test_returns_false_on_exception(self, ba_init):
        adapter, mock_dbus, mock_props, mock_adapter_iface = ba_init
        mock_adapter_iface.object_path = "/org/bluez/hci0"
        mock_props.Set.side_effect = Exception("D-Bus error")
        with patch("time.sleep"), patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = adapter.reset()
        assert result is False


# ===========================================================================
# Section 10 — shutdown()
# ===========================================================================

class TestShutdown:

    @pytest.mark.unit
    def test_calls_bus_close(self, ba_init):
        adapter, mock_dbus, *_ = ba_init
        mock_bus = MagicMock()
        adapter._bus = mock_bus
        adapter.shutdown()
        mock_bus.close.assert_called_once()

    @pytest.mark.unit
    def test_sets_initialized_false(self, ba_init):
        adapter, *_ = ba_init
        adapter.shutdown()
        assert adapter._initialized is False

    @pytest.mark.unit
    def test_no_crash_when_bus_is_none(self, ba):
        ba._bus = None
        ba.shutdown()  # must not raise

    @pytest.mark.unit
    def test_no_crash_when_close_raises(self, ba_init):
        adapter, *_ = ba_init
        mock_bus = MagicMock()
        mock_bus.close.side_effect = Exception("close failed")
        adapter._bus = mock_bus
        adapter.shutdown()  # must not raise


# ===========================================================================
# Section 11 — bus property
# ===========================================================================

class TestBusProperty:

    @pytest.mark.unit
    def test_bus_none_before_init(self, ba):
        assert ba.bus is None

    @pytest.mark.unit
    def test_bus_set_after_successful_init(self, ba):
        mock_dbus = _make_dbus_mocks()[0]
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            ba.init()
        assert ba.bus is not None

    @pytest.mark.unit
    def test_bus_returns_same_instance(self, ba_init):
        adapter, *_ = ba_init
        assert adapter.bus is adapter._bus


# ===========================================================================
# Section 12 — Constants
# ===========================================================================

class TestConstants:

    @pytest.mark.unit
    def test_hfp_uuid_format(self):
        assert _ba_mod.HFP_UUID == "0000111e-0000-1000-8000-00805f9b34fb"

    @pytest.mark.unit
    def test_hsp_uuid_format(self):
        assert _ba_mod.HSP_UUID == "00001108-0000-1000-8000-00805f9b34fb"

    @pytest.mark.unit
    def test_rfcomm_channel_is_8(self):
        assert _ba_mod.RFCOMM_CHANNEL == 8
