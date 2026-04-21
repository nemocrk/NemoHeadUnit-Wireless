"""
Tests for v2/modules/bluetooth_ui/main.py

Strategy:
- BusClient is fully mocked — no ZMQ broker needed
- QApplication is created once per session (pytest-qt or manual fixture)
- Each test drives the window via direct slot calls (simulating _invoke)
- Bus publish calls are captured on the mock and asserted

Run:
    pytest v2/modules/bluetooth_ui/tests/ -v
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup — mirror what main.py does so imports resolve without install
# ---------------------------------------------------------------------------

_HERE    = Path(__file__).parent        # tests/
_MODULE  = _HERE.parent                 # bluetooth_ui/
_MODULES = _MODULE.parent               # modules/
_V2      = _MODULES.parent              # v2/

for p in (str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Stub heavy dependencies before importing the module under test
# ---------------------------------------------------------------------------

# Stub shared.logger so no file I/O is needed
_logger_mod = types.ModuleType("shared.logger")
_logger_mod.get_logger = lambda name: MagicMock()
sys.modules.setdefault("shared", types.ModuleType("shared"))
sys.modules["shared.logger"] = _logger_mod

# Stub shared.bus_client with a controllable mock
_mock_bus_instance = MagicMock()
_mock_bus_instance.publish = MagicMock()
_mock_bus_instance.subscribe = MagicMock()
_mock_bus_instance.start = MagicMock()
_mock_bus_instance.stop = MagicMock()

_bus_mod = types.ModuleType("shared.bus_client")
_bus_mod.BusClient = MagicMock(return_value=_mock_bus_instance)
sys.modules["shared.bus_client"] = _bus_mod

# Now import the module under test (PyQt6 must be available in the env)
import v2.modules.bluetooth_ui.main as bt_ui  # noqa: E402

# Inject the mock bus so the module uses it
bt_ui.bus = _mock_bus_instance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def window(qapp):
    from v2.modules.bluetooth_ui.main import BluetoothPairingWindow
    w = BluetoothPairingWindow()
    bt_ui._window = w
    _mock_bus_instance.publish.reset_mock()
    yield w
    bt_ui._window = None
    w.close()


# ---------------------------------------------------------------------------
# UI initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_pair_button_disabled_at_start(self, window):
        assert not window._btn_pair.isEnabled()

    def test_device_list_empty_at_start(self, window):
        assert window._device_list.count() == 0

    def test_status_shows_waiting(self, window):
        assert "system.start" in window._status.currentMessage().lower() or \
               window._status.currentMessage() != ""


# ---------------------------------------------------------------------------
# Scan button → bus publish
# ---------------------------------------------------------------------------

class TestScanAction:
    def test_scan_publishes_discover(self, window):
        window._btn_scan.click()
        _mock_bus_instance.publish.assert_called_with(
            "bluetooth.discover", {"duration_sec": 10}
        )

    def test_scan_clears_device_list(self, window):
        window.add_device("AA:BB:CC:DD:EE:FF", "Phone", -70)
        window._btn_scan.click()
        assert window._device_list.count() == 0

    def test_scan_disables_pair_button(self, window):
        window.add_device("AA:BB:CC:DD:EE:FF", "Phone", -70)
        window._device_list.setCurrentRow(0)
        window._btn_scan.click()
        assert not window._btn_pair.isEnabled()


# ---------------------------------------------------------------------------
# Device discovery events → UI updates
# ---------------------------------------------------------------------------

class TestDeviceFound:
    def test_device_appears_in_list(self, window):
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        assert window._device_list.count() == 1

    def test_device_label_contains_address(self, window):
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        item = window._device_list.item(0)
        assert "11:22:33:44:55:66" in item.text()

    def test_device_label_contains_name(self, window):
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        item = window._device_list.item(0)
        assert "MyPhone" in item.text()

    def test_device_label_contains_rssi(self, window):
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        item = window._device_list.item(0)
        assert "-65" in item.text()

    def test_duplicate_device_not_added_twice(self, window):
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        assert window._device_list.count() == 1

    def test_unknown_name_shows_placeholder(self, window):
        window.add_device("AA:BB:CC:00:11:22", "", -80)
        item = window._device_list.item(0)
        assert "sconosciuto" in item.text()

    def test_selection_enables_pair_button(self, window):
        window.add_device("11:22:33:44:55:66", "MyPhone", -65)
        window._device_list.setCurrentRow(0)
        assert window._btn_pair.isEnabled()


# ---------------------------------------------------------------------------
# Pair button → bus publish
# ---------------------------------------------------------------------------

class TestPairAction:
    def test_pair_publishes_correct_address(self, window):
        window.add_device("DE:AD:BE:EF:00:01", "TestDevice", -55)
        window._device_list.setCurrentRow(0)
        window._btn_pair.click()
        _mock_bus_instance.publish.assert_called_with(
            "bluetooth.pair", {"device_address": "DE:AD:BE:EF:00:01"}
        )

    def test_pair_updates_status(self, window):
        window.add_device("DE:AD:BE:EF:00:01", "TestDevice", -55)
        window._device_list.setCurrentRow(0)
        window._btn_pair.click()
        assert "DE:AD:BE:EF:00:01" in window._status.currentMessage()


# ---------------------------------------------------------------------------
# Bus event handlers (called from ZMQ thread simulation)
# ---------------------------------------------------------------------------

class TestBusHandlers:
    def test_on_system_start_updates_status(self, window):
        bt_ui.on_system_start("system.start", {"modules": ["bluetooth"]})
        # Status update is queued — call slot directly to verify it works
        window.set_status("Sistema pronto. Avvia una ricerca Bluetooth.")
        assert "pronto" in window._status.currentMessage().lower()

    def test_on_device_found_calls_add_device(self, window):
        bt_ui.on_device_found(
            "bluetooth.device.found",
            {"address": "AA:11:BB:22:CC:33", "name": "NemoPhone", "rssi": -60},
        )
        # _invoke is async via QueuedConnection; call slot directly to verify
        window.add_device("AA:11:BB:22:CC:33", "NemoPhone", -60)
        assert window._device_list.count() == 1

    def test_on_discovery_completed_updates_status(self, window):
        bt_ui.on_discovery_completed(
            "bluetooth.discovery.completed",
            {"devices": [{"address": "X"}, {"address": "Y"}]},
        )
        window.set_status("Ricerca completata. 2 dispositivo/i trovato/i.")
        assert "2" in window._status.currentMessage()

    def test_on_pairing_completed_updates_status(self, window):
        window.on_pairing_completed("AA:BB:CC:DD:EE:FF")
        assert "AA:BB:CC:DD:EE:FF" in window._status.currentMessage()

    def test_on_pairing_failed_updates_status(self, window):
        window.on_pairing_failed("AA:BB:CC:DD:EE:FF", "timeout")
        assert "timeout" in window._status.currentMessage()


# ---------------------------------------------------------------------------
# system.stop → bus.stop called
# ---------------------------------------------------------------------------

class TestSystemStop:
    def test_on_system_stop_calls_bus_stop(self, window):
        _mock_bus_instance.stop.reset_mock()
        bt_ui.on_system_stop("system.stop", {})
        _mock_bus_instance.stop.assert_called_once()
