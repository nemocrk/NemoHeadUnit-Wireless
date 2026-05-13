"""
Unit tests for bluetooth/paired_devices.py

Strategy:
  paired_devices.py ha import dbus lazy (dentro le funzioni). Il modulo
  viene importato una volta sola a livello di file con stub in sys.modules
  e get_logger patchato. I test usano patch.dict(sys.modules, {"dbus": mock_dbus})
  per controllare le chiamate D-Bus.

  _get_managed_objects, _resolve_device_path, _resolve_adapter_path sono
  helpers interni testati indirettamente e direttamente.

Covers:
  Section 1  — _device_to_dict(): tutti i campi, valori default, connected/trusted/paired bool
  Section 2  — _get_managed_objects(): successo, eccezione → propagata
  Section 3  — _resolve_device_path(): trovato, non trovato, eccezione → None
  Section 4  — _resolve_adapter_path(): trovato, non trovato, eccezione → None
  Section 5  — list_paired(): lista vuota, solo Paired=True, solo Trusted=True,
               entrambi, eccezione → []
  Section 6  — get_info(): trovato, non trovato, eccezione → None
  Section 7  — remove(): no adapter → False, device not found → False,
               success → True, eccezione → False
  Section 8  — connect(): device not found → on_failed, D-Bus Pair dispatched,
               reply_handler → on_connected, error_handler AlreadyConnected → on_connected,
               error_handler generic → on_failed, watchdog fires on_failed after timeout,
               reply_handler dopo watchdog → ignorato (no double call),
               dispatch exception → on_failed
  Section 9  — disconnect(): device not found → on_failed, D-Bus Disconnect dispatched,
               reply_handler → on_disconnected, NotConnected → on_disconnected,
               error_handler generic → on_failed, dispatch exception → on_failed
"""

from __future__ import annotations

import sys
import types
import importlib
import threading
import time
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _install_stubs():
    dbus_mod = types.ModuleType("dbus")
    dbus_mod.Interface   = MagicMock
    dbus_mod.Boolean     = lambda v: v
    sys.modules.setdefault("dbus", dbus_mod)


_install_stubs()

for _k in list(sys.modules.keys()):
    if "paired_devices" in _k:
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()):
    import modules.bluetooth.paired_devices as _pd_mod
    importlib.reload(_pd_mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_objects(devices=None, adapters=None):
    """
    Build a fake GetManagedObjects dict.
    devices: list of (path, props_dict)
    adapters: list of paths
    """
    objects = {}
    for path, props in (devices or []):
        objects[path] = {"org.bluez.Device1": props}
    for path in (adapters or []):
        objects[path] = {"org.bluez.Adapter1": {}}
    return objects


def _make_bus(objects):
    """Return (mock_bus, mock_dbus) with GetManagedObjects returning objects."""
    mock_bus = MagicMock()
    mock_dbus = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.GetManagedObjects.return_value = objects
    mock_dbus.Interface.return_value = mock_mgr
    mock_dbus.Boolean = lambda v: v
    return mock_bus, mock_dbus


def _dev_props(address="AA:BB", name="Phone", connected=False, trusted=True, paired=True):
    return {
        "Address": address, "Name": name,
        "Connected": connected, "Trusted": trusted, "Paired": paired,
    }


# ===========================================================================
# Section 1 — _device_to_dict()
# ===========================================================================

class TestDeviceToDict:

    @pytest.mark.unit
    def test_all_fields_present(self):
        props = _dev_props("AA:BB", "Phone", True, True, True)
        result = _pd_mod._device_to_dict(props)
        assert set(result.keys()) == {"address", "name", "connected", "trusted", "paired"}

    @pytest.mark.unit
    def test_address_mapped(self):
        result = _pd_mod._device_to_dict({"Address": "AA:BB"})
        assert result["address"] == "AA:BB"

    @pytest.mark.unit
    def test_name_falls_back_to_alias(self):
        result = _pd_mod._device_to_dict({"Alias": "HeadUnit"})
        assert result["name"] == "HeadUnit"

    @pytest.mark.unit
    def test_name_defaults_to_unknown(self):
        result = _pd_mod._device_to_dict({})
        assert result["name"] == "Unknown"

    @pytest.mark.unit
    def test_connected_false_default(self):
        result = _pd_mod._device_to_dict({})
        assert result["connected"] is False

    @pytest.mark.unit
    def test_paired_bool_true(self):
        result = _pd_mod._device_to_dict({"Paired": True})
        assert result["paired"] is True


# ===========================================================================
# Section 2 — _get_managed_objects()
# ===========================================================================

class TestGetManagedObjects:

    @pytest.mark.unit
    def test_returns_objects_on_success(self):
        objects = {"obj": {}}
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._get_managed_objects(mock_bus)
        assert result == objects

    @pytest.mark.unit
    def test_exception_propagates(self):
        mock_bus = MagicMock()
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            with pytest.raises(Exception):
                _pd_mod._get_managed_objects(mock_bus)


# ===========================================================================
# Section 3 — _resolve_device_path()
# ===========================================================================

class TestResolveDevicePath:

    @pytest.mark.unit
    def test_returns_path_when_found(self):
        objects = _make_objects(devices=[("/dev/1", {"Address": "AA:BB"})])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._resolve_device_path(mock_bus, "AA:BB")
        assert result == "/dev/1"

    @pytest.mark.unit
    def test_returns_none_when_not_found(self):
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._resolve_device_path(mock_bus, "ZZ:ZZ")
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        mock_bus = MagicMock()
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("boom")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._resolve_device_path(mock_bus, "AA:BB")
        assert result is None


# ===========================================================================
# Section 4 — _resolve_adapter_path()
# ===========================================================================

class TestResolveAdapterPath:

    @pytest.mark.unit
    def test_returns_adapter_path(self):
        objects = _make_objects(adapters=["/org/bluez/hci0"])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._resolve_adapter_path(mock_bus)
        assert result == "/org/bluez/hci0"

    @pytest.mark.unit
    def test_returns_none_when_no_adapter(self):
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._resolve_adapter_path(mock_bus)
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        mock_bus = MagicMock()
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("boom")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod._resolve_adapter_path(mock_bus)
        assert result is None


# ===========================================================================
# Section 5 — list_paired()
# ===========================================================================

class TestListPaired:

    @pytest.mark.unit
    def test_empty_returns_empty_list(self):
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.list_paired(mock_bus)
        assert result == []

    @pytest.mark.unit
    def test_includes_paired_true(self):
        objects = _make_objects(devices=[("/dev/1", _dev_props("AA:BB", paired=True, trusted=False))])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.list_paired(mock_bus)
        assert len(result) == 1

    @pytest.mark.unit
    def test_includes_trusted_true(self):
        objects = _make_objects(devices=[("/dev/1", _dev_props("AA:BB", paired=False, trusted=True))])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.list_paired(mock_bus)
        assert len(result) == 1

    @pytest.mark.unit
    def test_excludes_not_paired_not_trusted(self):
        objects = _make_objects(devices=[("/dev/1", _dev_props("AA:BB", paired=False, trusted=False))])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.list_paired(mock_bus)
        assert result == []

    @pytest.mark.unit
    def test_returns_empty_on_exception(self):
        mock_bus = MagicMock()
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("boom")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.list_paired(mock_bus)
        assert result == []


# ===========================================================================
# Section 6 — get_info()
# ===========================================================================

class TestGetInfo:

    @pytest.mark.unit
    def test_returns_dict_when_found(self):
        objects = _make_objects(devices=[("/dev/1", _dev_props("AA:BB"))])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.get_info(mock_bus, "AA:BB")
        assert result is not None
        assert result["address"] == "AA:BB"

    @pytest.mark.unit
    def test_returns_none_when_not_found(self):
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.get_info(mock_bus, "ZZ:ZZ")
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        mock_bus = MagicMock()
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("boom")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.get_info(mock_bus, "AA:BB")
        assert result is None


# ===========================================================================
# Section 7 — remove()
# ===========================================================================

class TestRemove:

    @pytest.mark.unit
    def test_returns_false_when_no_adapter(self):
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.remove(mock_bus, "AA:BB")
        assert result is False

    @pytest.mark.unit
    def test_returns_false_when_device_not_found(self):
        objects = _make_objects(adapters=["/org/bluez/hci0"])
        mock_bus, mock_dbus = _make_bus(objects)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.remove(mock_bus, "AA:BB")
        assert result is False

    @pytest.mark.unit
    def test_returns_true_on_success(self):
        objects = _make_objects(
            devices=[("/dev/1", {"Address": "AA:BB"})],
            adapters=["/org/bluez/hci0"],
        )
        mock_bus, mock_dbus = _make_bus(objects)
        mock_adapter_iface = MagicMock()
        orig_factory = mock_dbus.Interface.side_effect
        mock_dbus.Interface.side_effect = None
        mock_dbus.Interface.return_value = mock_adapter_iface
        mock_adapter_iface.GetManagedObjects.return_value = objects
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.remove(mock_bus, "AA:BB")
        assert result is True

    @pytest.mark.unit
    def test_returns_false_on_exception(self):
        mock_bus = MagicMock()
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("boom")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = _pd_mod.remove(mock_bus, "AA:BB")
        assert result is False


# ===========================================================================
# Section 8 — connect()
# ===========================================================================

class TestConnect:

    @pytest.mark.unit
    def test_device_not_found_calls_on_failed(self):
        cb_fail = MagicMock()
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.connect(mock_bus, "AA:BB", on_failed=cb_fail)
        cb_fail.assert_called_once_with("AA:BB", "Device not found in BlueZ")

    @pytest.mark.unit
    def test_dispatches_device_connect(self):
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        mock_dbus.Interface.side_effect = iface_factory
        with patch("threading.Thread") as mock_t, \
             patch.dict(sys.modules, {"dbus": mock_dbus}):
            mock_t.return_value.start = MagicMock()
            _pd_mod.connect(mock_bus, address)
        mock_device_iface.Connect.assert_called_once()

    @pytest.mark.unit
    def test_reply_handler_calls_on_connected(self):
        cb_ok = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        captured = {}
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        def connect_capture(**kwargs):
            captured["reply"] = kwargs["reply_handler"]
            captured["error"] = kwargs["error_handler"]
        mock_device_iface.Connect.side_effect = connect_capture
        mock_dbus.Interface.side_effect = iface_factory
        with patch("threading.Thread") as mock_t, \
             patch.dict(sys.modules, {"dbus": mock_dbus}):
            mock_t.return_value.start = MagicMock()
            _pd_mod.connect(mock_bus, address, on_connected=cb_ok)
        captured["reply"]()
        cb_ok.assert_called_once_with(address)

    @pytest.mark.unit
    def test_error_handler_already_connected_calls_on_connected(self):
        cb_ok = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        captured = {}
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        def connect_capture(**kwargs):
            captured["reply"] = kwargs["reply_handler"]
            captured["error"] = kwargs["error_handler"]
        mock_device_iface.Connect.side_effect = connect_capture
        mock_dbus.Interface.side_effect = iface_factory
        with patch("threading.Thread") as mock_t, \
             patch.dict(sys.modules, {"dbus": mock_dbus}):
            mock_t.return_value.start = MagicMock()
            _pd_mod.connect(mock_bus, address, on_connected=cb_ok)
        captured["error"](Exception("AlreadyConnected"))
        cb_ok.assert_called_once_with(address)

    @pytest.mark.unit
    def test_error_handler_generic_calls_on_failed(self):
        cb_fail = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        captured = {}
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        def connect_capture(**kwargs):
            captured["reply"] = kwargs["reply_handler"]
            captured["error"] = kwargs["error_handler"]
        mock_device_iface.Connect.side_effect = connect_capture
        mock_dbus.Interface.side_effect = iface_factory
        with patch("threading.Thread") as mock_t, \
             patch.dict(sys.modules, {"dbus": mock_dbus}):
            mock_t.return_value.start = MagicMock()
            _pd_mod.connect(mock_bus, address, on_failed=cb_fail)
        captured["error"](Exception("generic error"))
        cb_fail.assert_called_once()

    @pytest.mark.unit
    def test_dispatch_exception_calls_on_failed(self):
        cb_fail = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        mock_device_iface.Connect.side_effect = Exception("dispatch failed")
        mock_dbus.Interface.side_effect = iface_factory
        with patch("threading.Thread") as mock_t, \
             patch.dict(sys.modules, {"dbus": mock_dbus}):
            mock_t.return_value.start = MagicMock()
            _pd_mod.connect(mock_bus, address, on_failed=cb_fail)
        cb_fail.assert_called_once()


# ===========================================================================
# Section 9 — disconnect()
# ===========================================================================

class TestDisconnect:

    @pytest.mark.unit
    def test_device_not_found_calls_on_failed(self):
        cb_fail = MagicMock()
        mock_bus, mock_dbus = _make_bus({})
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.disconnect(mock_bus, "AA:BB", on_failed=cb_fail)
        cb_fail.assert_called_once_with("AA:BB", "Device not found in BlueZ")

    @pytest.mark.unit
    def test_dispatches_device_disconnect(self):
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        mock_dbus.Interface.side_effect = iface_factory
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.disconnect(mock_bus, address)
        mock_device_iface.Disconnect.assert_called_once()

    @pytest.mark.unit
    def test_reply_handler_calls_on_disconnected(self):
        cb_ok = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        captured = {}
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        def disconnect_capture(**kwargs):
            captured["reply"] = kwargs["reply_handler"]
            captured["error"] = kwargs["error_handler"]
        mock_device_iface.Disconnect.side_effect = disconnect_capture
        mock_dbus.Interface.side_effect = iface_factory
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.disconnect(mock_bus, address, on_disconnected=cb_ok)
        captured["reply"]()
        cb_ok.assert_called_once_with(address)

    @pytest.mark.unit
    def test_not_connected_calls_on_disconnected(self):
        cb_ok = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        captured = {}
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        def disconnect_capture(**kwargs):
            captured["reply"] = kwargs["reply_handler"]
            captured["error"] = kwargs["error_handler"]
        mock_device_iface.Disconnect.side_effect = disconnect_capture
        mock_dbus.Interface.side_effect = iface_factory
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.disconnect(mock_bus, address, on_disconnected=cb_ok)
        captured["error"](Exception("NotConnected"))
        cb_ok.assert_called_once_with(address)

    @pytest.mark.unit
    def test_error_handler_generic_calls_on_failed(self):
        cb_fail = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        captured = {}
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        def disconnect_capture(**kwargs):
            captured["reply"] = kwargs["reply_handler"]
            captured["error"] = kwargs["error_handler"]
        mock_device_iface.Disconnect.side_effect = disconnect_capture
        mock_dbus.Interface.side_effect = iface_factory
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.disconnect(mock_bus, address, on_failed=cb_fail)
        captured["error"](Exception("generic error"))
        cb_fail.assert_called_once()

    @pytest.mark.unit
    def test_dispatch_exception_calls_on_failed(self):
        cb_fail = MagicMock()
        address = "AA:BB"
        objects = _make_objects(devices=[("/dev/1", {"Address": address})])
        mock_bus, mock_dbus = _make_bus(objects)
        mock_device_iface = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        def iface_factory(obj, iface_name):
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_device_iface
        mock_device_iface.Disconnect.side_effect = Exception("dispatch failed")
        mock_dbus.Interface.side_effect = iface_factory
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            _pd_mod.disconnect(mock_bus, address, on_failed=cb_fail)
        cb_fail.assert_called_once()
