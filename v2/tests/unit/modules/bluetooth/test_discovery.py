"""
Unit tests for bluetooth/discovery.py — DiscoverySession

Strategy:
  discovery.py importa solo `dbus` (lazy, dentro _poll_devices) e
  `shared.logger`. Il modulo viene importato una volta sola a livello
  di file con get_logger patchato. I test usano patch.dict(sys.modules,
  {"dbus": mock_dbus}) per controllare la chiamata a _poll_devices.

  L'adapter passato al costruttore è sempre un MagicMock che espone:
    adapter._adapter.StartDiscovery()
    adapter._adapter.StopDiscovery()
    adapter.is_discovering() -> bool
    adapter.reset() -> bool
    adapter._bus

  _run() viene testato in modo sincrono: il thread viene avviato ma
  fermato dopo un breve wait oppure i metodi interni vengono chiamati
  direttamente per isolare la logica.

Covers:
  Section 1  — __init__: stato iniziale
  Section 2  — is_running property: False prima di start, True dopo
  Section 3  — start(): chiamata normale, already running ignorato,
               devices.clear() prima di ogni avvio, thread lanciato
  Section 4  — stop(): setta _running=False
  Section 5  — _start_discovery(): chiamata normale, già in discovery skip,
               InProgress + is_discovering=True skip,
               InProgress + is_discovering=False → reset + retry,
               altra eccezione logga errore
  Section 6  — _stop_discovery(): not discovering skip, chiamata normale,
               bluez "No discovery started" non fatale,
               altra eccezione logga warning
  Section 7  — _poll_devices(): lista vuota, un device, più device,
               solo Device1 inclusi, eccezione → lista vuota
  Section 8  — _run() logic: on_device_cb chiamato per nuovo device,
               device duplicato non riepilogato, on_done_cb chiamato al termine,
               _running=False al termine, stop() interrompe il loop,
               eccezione nel loop chiama comunque on_done_cb
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
    dbus_mod.Interface = MagicMock
    sys.modules.setdefault("dbus", dbus_mod)


_install_stubs()

for _k in list(sys.modules.keys()):
    if "discovery" in _k and "bluetooth" in _k:
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()):
    import modules.bluetooth.discovery as _disc_mod
    importlib.reload(_disc_mod)

DiscoverySession = _disc_mod.DiscoverySession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(is_discovering=False, reset_ok=True):
    adapter = MagicMock()
    adapter.is_discovering.return_value = is_discovering
    adapter.reset.return_value = reset_ok
    adapter._adapter = MagicMock()
    adapter._bus = MagicMock()
    return adapter


def _make_objects(*devices):
    """
    Build a fake GetManagedObjects() dict.
    Each device is a dict with Address, Name, RSSI keys.
    """
    objects = {}
    for i, dev in enumerate(devices):
        objects[f"/org/bluez/hci0/dev_{i}"] = {
            "org.bluez.Device1": {
                "Address": dev.get("Address", f"AA:BB:CC:DD:EE:{i:02X}"),
                "Name":    dev.get("Name", "Unknown"),
                "RSSI":    dev.get("RSSI", -70),
            }
        }
    return objects


# ===========================================================================
# Section 1 — __init__
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_running_is_false(self):
        s = DiscoverySession(_make_adapter())
        assert s._running is False

    @pytest.mark.unit
    def test_devices_is_empty(self):
        s = DiscoverySession(_make_adapter())
        assert s._devices == {}

    @pytest.mark.unit
    def test_thread_is_none(self):
        s = DiscoverySession(_make_adapter())
        assert s._thread is None

    @pytest.mark.unit
    def test_callbacks_stored(self):
        cb_dev = MagicMock()
        cb_done = MagicMock()
        s = DiscoverySession(_make_adapter(), on_device_cb=cb_dev, on_done_cb=cb_done)
        assert s._on_device_cb is cb_dev
        assert s._on_done_cb is cb_done


# ===========================================================================
# Section 2 — is_running property
# ===========================================================================

class TestIsRunning:

    @pytest.mark.unit
    def test_false_before_start(self):
        s = DiscoverySession(_make_adapter())
        assert s.is_running is False

    @pytest.mark.unit
    def test_true_after_start(self):
        s = DiscoverySession(_make_adapter())
        with patch.object(s, "_run"):  # don't actually run the thread loop
            with patch("threading.Thread") as mock_thread:
                mock_thread.return_value.start = MagicMock()
                s.start(duration_sec=5)
        assert s._running is True

    @pytest.mark.unit
    def test_false_after_stop(self):
        s = DiscoverySession(_make_adapter())
        s._running = True
        s.stop()
        assert s.is_running is False


# ===========================================================================
# Section 3 — start()
# ===========================================================================

class TestStart:

    @pytest.mark.unit
    def test_sets_running_true(self):
        s = DiscoverySession(_make_adapter())
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            s.start(5)
        assert s._running is True

    @pytest.mark.unit
    def test_launches_thread(self):
        s = DiscoverySession(_make_adapter())
        mock_t = MagicMock()
        with patch("threading.Thread", return_value=mock_t):
            s.start(5)
        mock_t.start.assert_called_once()

    @pytest.mark.unit
    def test_already_running_ignored(self):
        s = DiscoverySession(_make_adapter())
        s._running = True
        with patch("threading.Thread") as mock_thread:
            s.start(5)
        mock_thread.assert_not_called()

    @pytest.mark.unit
    def test_clears_devices_before_start(self):
        s = DiscoverySession(_make_adapter())
        s._devices = {"AA:BB": {"address": "AA:BB"}}
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            s.start(5)
        assert s._devices == {}


# ===========================================================================
# Section 4 — stop()
# ===========================================================================

class TestStop:

    @pytest.mark.unit
    def test_sets_running_false(self):
        s = DiscoverySession(_make_adapter())
        s._running = True
        s.stop()
        assert s._running is False

    @pytest.mark.unit
    def test_no_crash_when_already_stopped(self):
        s = DiscoverySession(_make_adapter())
        s._running = False
        s.stop()  # must not raise


# ===========================================================================
# Section 5 — _start_discovery()
# ===========================================================================

class TestStartDiscovery:

    @pytest.mark.unit
    def test_calls_start_discovery_on_adapter(self):
        adapter = _make_adapter(is_discovering=False)
        s = DiscoverySession(adapter)
        s._start_discovery()
        adapter._adapter.StartDiscovery.assert_called_once()

    @pytest.mark.unit
    def test_skips_when_already_discovering(self):
        adapter = _make_adapter(is_discovering=True)
        s = DiscoverySession(adapter)
        s._start_discovery()
        adapter._adapter.StartDiscovery.assert_not_called()

    @pytest.mark.unit
    def test_inprogress_with_is_discovering_true_skip(self):
        adapter = _make_adapter(is_discovering=True)
        adapter._adapter.StartDiscovery.side_effect = Exception("org.bluez.Error.InProgress")
        s = DiscoverySession(adapter)
        s._start_discovery()  # must not raise

    @pytest.mark.unit
    def test_inprogress_with_is_discovering_false_triggers_reset(self):
        adapter = _make_adapter(is_discovering=False)
        call_count = [0]
        def start_discovery_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("org.bluez.Error.InProgress")
        adapter._adapter.StartDiscovery.side_effect = start_discovery_side_effect
        s = DiscoverySession(adapter)
        with patch("time.sleep"):
            s._start_discovery()
        adapter.reset.assert_called_once()

    @pytest.mark.unit
    def test_inprogress_reset_retries_start_discovery(self):
        adapter = _make_adapter(is_discovering=False)
        adapter.reset.return_value = True
        call_count = [0]
        def start_discovery_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("org.bluez.Error.InProgress")
        adapter._adapter.StartDiscovery.side_effect = start_discovery_side_effect
        s = DiscoverySession(adapter)
        with patch("time.sleep"):
            s._start_discovery()
        assert adapter._adapter.StartDiscovery.call_count == 2

    @pytest.mark.unit
    def test_other_exception_logged_no_crash(self):
        adapter = _make_adapter()
        adapter._adapter.StartDiscovery.side_effect = Exception("generic error")
        s = DiscoverySession(adapter)
        s._start_discovery()  # must not raise


# ===========================================================================
# Section 6 — _stop_discovery()
# ===========================================================================

class TestStopDiscovery:

    @pytest.mark.unit
    def test_skips_when_not_discovering(self):
        adapter = _make_adapter(is_discovering=False)
        s = DiscoverySession(adapter)
        s._stop_discovery()
        adapter._adapter.StopDiscovery.assert_not_called()

    @pytest.mark.unit
    def test_calls_stop_discovery_when_discovering(self):
        adapter = _make_adapter(is_discovering=True)
        s = DiscoverySession(adapter)
        s._stop_discovery()
        adapter._adapter.StopDiscovery.assert_called_once()

    @pytest.mark.unit
    def test_no_discovery_started_not_fatal(self):
        adapter = _make_adapter(is_discovering=True)
        adapter._adapter.StopDiscovery.side_effect = Exception("org.bluez.Error.Failed: No discovery started")
        s = DiscoverySession(adapter)
        s._stop_discovery()  # must not raise

    @pytest.mark.unit
    def test_other_exception_logged_no_crash(self):
        adapter = _make_adapter(is_discovering=True)
        adapter._adapter.StopDiscovery.side_effect = Exception("generic error")
        s = DiscoverySession(adapter)
        s._stop_discovery()  # must not raise


# ===========================================================================
# Section 7 — _poll_devices()
# ===========================================================================

class TestPollDevices:

    @pytest.mark.unit
    def test_empty_objects_returns_empty(self):
        adapter = _make_adapter()
        s = DiscoverySession(adapter)
        mock_dbus = MagicMock()
        mock_iface = MagicMock()
        mock_iface.GetManagedObjects.return_value = {}
        mock_dbus.Interface.return_value = mock_iface
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = s._poll_devices()
        assert result == []

    @pytest.mark.unit
    def test_returns_one_device(self):
        adapter = _make_adapter()
        s = DiscoverySession(adapter)
        objects = _make_objects({"Address": "AA:BB:CC:DD:EE:FF", "Name": "Phone", "RSSI": -60})
        mock_dbus = MagicMock()
        mock_iface = MagicMock()
        mock_iface.GetManagedObjects.return_value = objects
        mock_dbus.Interface.return_value = mock_iface
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = s._poll_devices()
        assert len(result) == 1
        assert result[0]["address"] == "AA:BB:CC:DD:EE:FF"
        assert result[0]["name"] == "Phone"

    @pytest.mark.unit
    def test_returns_multiple_devices(self):
        adapter = _make_adapter()
        s = DiscoverySession(adapter)
        objects = _make_objects(
            {"Address": "AA:00", "Name": "Dev1"},
            {"Address": "BB:00", "Name": "Dev2"},
        )
        mock_dbus = MagicMock()
        mock_iface = MagicMock()
        mock_iface.GetManagedObjects.return_value = objects
        mock_dbus.Interface.return_value = mock_iface
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = s._poll_devices()
        assert len(result) == 2

    @pytest.mark.unit
    def test_excludes_non_device1_objects(self):
        adapter = _make_adapter()
        s = DiscoverySession(adapter)
        objects = {
            "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
            "/org/bluez/hci0/dev_1": {"org.bluez.Device1": {"Address": "CC:00", "Name": "X", "RSSI": -50}},
        }
        mock_dbus = MagicMock()
        mock_iface = MagicMock()
        mock_iface.GetManagedObjects.return_value = objects
        mock_dbus.Interface.return_value = mock_iface
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = s._poll_devices()
        assert len(result) == 1

    @pytest.mark.unit
    def test_exception_returns_empty(self):
        adapter = _make_adapter()
        s = DiscoverySession(adapter)
        mock_dbus = MagicMock()
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = s._poll_devices()
        assert result == []


# ===========================================================================
# Section 8 — _run() logic (synchronous via direct calls)
# ===========================================================================

class TestRunLogic:

    @pytest.mark.unit
    def test_on_device_cb_called_for_new_device(self):
        cb_device = MagicMock()
        cb_done   = MagicMock()
        adapter = _make_adapter()
        s = DiscoverySession(adapter, on_device_cb=cb_device, on_done_cb=cb_done)

        devs = [{"address": "AA:BB", "name": "Phone", "rssi": -55}]
        with patch.object(s, "_start_discovery"), \
             patch.object(s, "_stop_discovery"), \
             patch.object(s, "_poll_devices", return_value=devs), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 0.5, 999]):
            s._running = True
            s._run(1)

        cb_device.assert_called_once_with("AA:BB", "Phone", -55)

    @pytest.mark.unit
    def test_duplicate_device_not_reported_twice(self):
        cb_device = MagicMock()
        adapter = _make_adapter()
        s = DiscoverySession(adapter, on_device_cb=cb_device)
        s._devices = {"AA:BB": {"address": "AA:BB", "name": "Phone", "rssi": -55}}

        devs = [{"address": "AA:BB", "name": "Phone", "rssi": -55}]
        with patch.object(s, "_start_discovery"), \
             patch.object(s, "_stop_discovery"), \
             patch.object(s, "_poll_devices", return_value=devs), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 0.5, 999]):
            s._running = True
            s._run(1)

        cb_device.assert_not_called()

    @pytest.mark.unit
    def test_on_done_cb_called_at_end(self):
        cb_done = MagicMock()
        adapter = _make_adapter()
        s = DiscoverySession(adapter, on_done_cb=cb_done)
        with patch.object(s, "_start_discovery"), \
             patch.object(s, "_stop_discovery"), \
             patch.object(s, "_poll_devices", return_value=[]), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 999]):
            s._running = True
            s._run(1)
        cb_done.assert_called_once()

    @pytest.mark.unit
    def test_running_is_false_after_run(self):
        adapter = _make_adapter()
        s = DiscoverySession(adapter)
        with patch.object(s, "_start_discovery"), \
             patch.object(s, "_stop_discovery"), \
             patch.object(s, "_poll_devices", return_value=[]), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 999]):
            s._running = True
            s._run(1)
        assert s._running is False

    @pytest.mark.unit
    def test_stop_interrupts_loop(self):
        cb_device = MagicMock()
        adapter = _make_adapter()
        s = DiscoverySession(adapter, on_device_cb=cb_device)

        call_count = [0]
        def poll_side_effect():
            call_count[0] += 1
            s._running = False  # simulate stop() called externally
            return []

        with patch.object(s, "_start_discovery"), \
             patch.object(s, "_stop_discovery"), \
             patch.object(s, "_poll_devices", side_effect=poll_side_effect), \
             patch("time.sleep"), \
             patch("time.monotonic", return_value=0):
            s._running = True
            s._run(60)

        assert call_count[0] == 1

    @pytest.mark.unit
    def test_exception_in_run_still_calls_on_done(self):
        cb_done = MagicMock()
        adapter = _make_adapter()
        s = DiscoverySession(adapter, on_done_cb=cb_done)
        with patch.object(s, "_start_discovery", side_effect=Exception("boom")), \
             patch.object(s, "_stop_discovery"):
            s._running = True
            s._run(1)
        cb_done.assert_called_once()
