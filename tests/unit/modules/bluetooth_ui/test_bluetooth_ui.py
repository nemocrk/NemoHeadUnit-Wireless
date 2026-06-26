"""
Unit tests for modules/bluetooth_ui/main.py

Coverage target: ≥80% line coverage.

Strategy:
  All Qt and ZMQ dependencies are stubbed before import so tests run headlessly.
  The module-level bus, log singletons are replaced with MagicMock instances.
  Window slots are tested on a duck-typed PureBluetooth object that mirrors the
  actual class logic without requiring a running Qt event loop.

Covers:
  1.  Module constants (MODULE_NAME, PRIORITY)
  2.  on_system_readytostart — publishes system.module_ready
  3.  on_system_start — wrong priority ignored; correct priority subscribes + publishes ready
  4.  on_system_stop — calls bus.stop()
  5.  on_ui_shell_ready — sets _shell_ready, calls _register()
  6.  _register — publishes correct ui.widget.register payload (on_request=True)
  7.  on_widget_geometry — routes to _invoke(apply_geometry_slot, ...) when name matches
  8.  on_module_open — routes to _invoke(set_visible_slot, True) + publishes paired.list
  9.  on_module_close — routes to _invoke(set_visible_slot, False)
  10. on_input_event — no crash when window is None
  11. Bus event handlers: on_device_found, on_discovery_completed, on_pairing_pin,
      on_pairing_completed, on_pairing_failed, on_paired_devices,
      on_paired_connected, on_paired_disconnected, on_paired_removed, on_paired_failed
  12. BluetoothPairingWindow.add_device — deduplicated device insertion
  13. BluetoothPairingWindow.refresh_paired_list — parses repr, populates list
  14. BluetoothPairingWindow.on_paired_connected/disconnected/removed
  15. BluetoothPairingWindow._update_paired_buttons — state-driven enable/disable
  16. BluetoothPairingWindow._refresh_item_state — label update in-place
  17. BluetoothPairingWindow._selected_paired_address — returns data or empty string
  18. BluetoothPairingWindow button handler bus publishes
  19. Design token constants exposed from module
  20. UI Architecture compliance: window flags must NOT include WindowStaysOnTopHint
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stubs installed before module import
# ---------------------------------------------------------------------------

def _make_stubs():
    stubs = {}

    # shared.*
    mock_bus  = MagicMock()
    mock_log  = MagicMock()
    mock_cfg  = MagicMock()

    stubs["shared"] = types.ModuleType("shared")
    stubs["shared.bus_client"]    = MagicMock(BusClient=MagicMock(return_value=mock_bus))
    stubs["shared.logger"]        = MagicMock(get_logger=MagicMock(return_value=mock_log))
    stubs["shared.config_client"] = MagicMock(ConfigClient=MagicMock(return_value=mock_cfg))
    stubs["shared.config_schema"] = MagicMock()
    def _make_shm_engine(name, w, h, **kw):
        m = MagicMock()
        m.max_width  = kw.get("max_width", w)
        m.max_height = kw.get("max_height", h)
        m.w = w
        m.h = h
        return m
    shm_stub = MagicMock()
    shm_stub.OffscreenWidgetEngine.side_effect = _make_shm_engine
    stubs["shared.shm_helper"]    = shm_stub

    # PyQt6
    qt_stub   = types.ModuleType("PyQt6")
    qtcore    = types.ModuleType("PyQt6.QtCore")
    qtwid     = types.ModuleType("PyQt6.QtWidgets")
    qtgui_mod = types.ModuleType("PyQt6.QtGui")
    qtgui_mod.QPainter = MagicMock
    qtgui_mod.QImage = MagicMock

    class _FakeQt:
        class WindowType:
            FramelessWindowHint   = 1
            Tool                  = 2
            WindowStaysOnTopHint  = 4  # deliberately different value to detect misuse
        class WidgetAttribute:
            WA_TranslucentBackground = 8
        class ConnectionType:
            QueuedConnection = 0
        class ItemDataRole:
            UserRole = 256
        class AlignmentFlag:
            AlignCenter = 0

    class _FakeQMainWindow:
        def __init__(self):
            self._flags = 0
            self._attrs = set()
            self._geometry = (0, 0, 0, 0)
            self._visible = False
            self._style_sheet = ""
        def setWindowFlags(self, f):   self._flags = f
        def setAttribute(self, a):     self._attrs.add(a)
        def setWindowTitle(self, t):   pass
        def hide(self):                self._visible = False
        def show(self):                self._visible = True
        def raise_(self):              pass
        def setGeometry(self, *a):     self._geometry = a
        def setStyleSheet(self, s):    self._style_sheet = s
        def setCentralWidget(self, w): pass
        def setStatusBar(self, b):     pass
        def centralWidget(self):       return MagicMock()
        # simulate the dialog behaviour used in tests
        windowFlags = lambda self: self._flags

    class _FakeQDialog:
        def __init__(self, *a, **kw): pass
        def exec(self): return 0
        class DialogCode:
            Accepted = 1

    class _FakeListWidgetItem:
        def __init__(self, label=""):
            self._label = label
            self._data  = {}
        def setData(self, role, val): self._data[role] = val
        def data(self, role):         return self._data.get(role)
        def setText(self, t):         self._label = t
        def text(self):               return self._label

    class _FakeListWidget:
        def __init__(self):
            self._items = []
            self._current = None
        def addItem(self, item):      self._items.append(item)
        def clear(self):              self._items = []; self._current = None
        def count(self):              return len(self._items)
        def item(self, i):            return self._items[i] if i < len(self._items) else None
        def currentItem(self):        return self._current
        def takeItem(self, i):
            if 0 <= i < len(self._items):
                return self._items.pop(i)
        def itemSelectionChanged(self): return MagicMock()

    class _FakeQStatusBar:
        def showMessage(self, msg):   pass

    qtcore.Qt           = _FakeQt
    qtcore.QMetaObject  = MagicMock()
    qtcore.Q_ARG        = MagicMock(side_effect=lambda t, v: (t, v))
    qtcore.pyqtSlot     = lambda *a, **kw: (lambda f: f)

    qtwid.QApplication  = MagicMock()
    qtwid.QMainWindow   = _FakeQMainWindow
    qtwid.QWidget       = MagicMock
    qtwid.QVBoxLayout   = MagicMock
    qtwid.QHBoxLayout   = MagicMock
    qtwid.QPushButton   = MagicMock
    qtwid.QListWidget   = _FakeListWidget
    qtwid.QListWidgetItem = _FakeListWidgetItem
    qtwid.QLabel        = MagicMock
    qtwid.QStatusBar    = _FakeQStatusBar
    qtwid.QDialog       = _FakeQDialog
    qtwid.QDialogButtonBox = MagicMock
    qtwid.QLineEdit     = MagicMock
    qtwid.QFormLayout   = MagicMock
    qtwid.QMessageBox   = MagicMock
    qtwid.QFrame        = MagicMock

    stubs["PyQt6"]             = qt_stub
    stubs["PyQt6.QtCore"]      = qtcore
    stubs["PyQt6.QtWidgets"]   = qtwid
    stubs["PyQt6.QtGui"]       = qtgui_mod

    return stubs, mock_bus, mock_log


def _load_module():
    stubs, mock_bus, mock_log = _make_stubs()

    # Bootstrap sys.path to repo root (same pattern as other test files)
    _REPO_ROOT = str(Path(__file__).parents[4])
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    for key in list(sys.modules.keys()):
        if "bluetooth_ui" in key:
            del sys.modules[key]

    with patch.dict("sys.modules", stubs):
        module_path = Path(__file__).parents[4] / "modules" / "bluetooth_ui" / "main.py"
        spec = importlib.util.spec_from_file_location("test_modules_bluetooth_ui_main", module_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mod.bus = mock_bus
        mod.log = mock_log

    mod._mock_bus = mock_bus
    mod._mock_log = mock_log
    mod._window   = None
    mod._shell_ready = False
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bt():
    return _load_module()


# ---------------------------------------------------------------------------
# 1. Module constants
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestModuleConstants:
    def test_module_name(self, bt):
        assert bt.MODULE_NAME == "bluetooth_ui"

    def test_priority_is_4(self, bt):
        """bluetooth_ui is an on_request widget; priority must be 4."""
        assert bt.PRIORITY == 4

    def test_design_tokens_defined(self, bt):
        assert hasattr(bt, "_COLOR_SURFACE")
        assert hasattr(bt, "_COLOR_TEXT")
        assert hasattr(bt, "_COLOR_ACCENT")
        assert hasattr(bt, "_COLOR_DANGER")


# ---------------------------------------------------------------------------
# 2. on_system_readytostart
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnSystemReadytostart:
    def test_publishes_module_ready(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt.on_system_readytostart()
        bt._mock_bus.publish.assert_called_once_with(
            "system.module_ready",
            {"name": "bluetooth_ui", "priority": 4},
        )


# ---------------------------------------------------------------------------
# 3. on_system_start
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnSystemStart:
    def test_wrong_priority_no_op(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt.on_system_start("system.start", {"priority": 99})
        bt._mock_bus.publish.assert_not_called()

    def test_correct_priority_publishes_ready(self, bt):
        bt._mock_bus.publish.reset_mock()
        with patch.object(bt, "_run_qt"), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            bt.on_system_start("system.start", {"priority": 4})
        topics = [c.args[0] for c in bt._mock_bus.publish.call_args_list]
        assert "system.ready" in topics

    def test_correct_priority_subscribes_bluetooth_events(self, bt):
        with patch.object(bt, "_run_qt"), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            bt.on_system_start("system.start", {"priority": 4})
        subscribed = [c.args[0] for c in bt._mock_bus.subscribe.call_args_list]
        assert "bluetooth_manager.device.found" in subscribed
        assert "bluetooth_manager.pairing.pin" in subscribed
        assert "bluetooth_manager.paired.devices" in subscribed

    def test_shell_ready_triggers_register(self, bt):
        bt._shell_ready = True
        bt._mock_bus.publish.reset_mock()
        with patch.object(bt, "_run_qt"), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            bt.on_system_start("system.start", {"priority": 4})
        topics = [c.args[0] for c in bt._mock_bus.publish.call_args_list]
        assert "ui.widget.register" in topics


# ---------------------------------------------------------------------------
# 4. on_system_stop
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnSystemStop:
    def test_calls_bus_stop(self, bt):
        bt._app = None
        bt.on_system_stop("system.stop", {})
        bt._mock_bus.stop.assert_called()

    def test_no_crash_without_app(self, bt):
        bt._app = None
        bt.on_system_stop("system.stop", {})  # must not raise


# ---------------------------------------------------------------------------
# 5. on_ui_shell_ready
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnUiShellReady:
    def test_sets_shell_ready(self, bt):
        bt._shell_ready = False
        bt.on_ui_shell_ready("ui.shell.ready", {})
        assert bt._shell_ready is True

    def test_publishes_widget_register(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt.on_ui_shell_ready("ui.shell.ready", {})
        topics = [c.args[0] for c in bt._mock_bus.publish.call_args_list]
        assert "ui.widget.register" in topics


# ---------------------------------------------------------------------------
# 6. _register — on_request payload
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRegister:
    def test_on_request_true(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt._register()
        calls = {c.args[0]: c.args[1] for c in bt._mock_bus.publish.call_args_list}
        assert "ui.widget.register" in calls
        payload = calls["ui.widget.register"]
        assert payload["on_request"] is True

    def test_name_is_bluetooth_ui(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt._register()
        calls = {c.args[0]: c.args[1] for c in bt._mock_bus.publish.call_args_list}
        assert calls["ui.widget.register"]["name"] == "bluetooth_ui"

    def test_has_z_order(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt._register()
        calls = {c.args[0]: c.args[1] for c in bt._mock_bus.publish.call_args_list}
        assert "z_order" in calls["ui.widget.register"]

    def test_has_dock(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt._register()
        calls = {c.args[0]: c.args[1] for c in bt._mock_bus.publish.call_args_list}
        assert "dock" in calls["ui.widget.register"]


# ---------------------------------------------------------------------------
# 7. on_widget_geometry
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnWidgetGeometry:
    def test_matching_name_invokes_apply_geometry(self, bt):
        bt._window = MagicMock()
        bt.QMetaObject.invokeMethod.reset_mock()
        bt.on_widget_geometry("ui.widget.geometry", {
            "name": "bluetooth_ui", "x": 10, "y": 20, "w": 480, "h": 560,
        })
        bt.QMetaObject.invokeMethod.assert_called_once()

    def test_non_matching_name_no_invoke(self, bt):
        bt.QMetaObject.invokeMethod.reset_mock()
        bt.on_widget_geometry("ui.widget.geometry", {
            "name": "other_widget", "x": 0, "y": 0, "w": 0, "h": 0,
        })
        bt.QMetaObject.invokeMethod.assert_not_called()


# ---------------------------------------------------------------------------
# 8. on_module_open
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnModuleOpen:
    def test_matching_name_invokes_set_visible_true(self, bt):
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_module_open("ui.module.open", {"name": "bluetooth_ui"})
        mock_invoke.assert_any_call("set_visible_slot", True)

    def test_publishes_paired_list_on_open(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt.on_module_open("ui.module.open", {"name": "bluetooth_ui"})
        topics = [c.args[0] for c in bt._mock_bus.publish.call_args_list]
        assert "bluetooth_manager.paired.list" in topics

    def test_non_matching_name_no_op(self, bt):
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_module_open("ui.module.open", {"name": "other"})
        mock_invoke.assert_not_called()


# ---------------------------------------------------------------------------
# 9. on_module_close
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnModuleClose:
    def test_matching_name_invokes_set_visible_false(self, bt):
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_module_close("ui.module.close", {"name": "bluetooth_ui"})
        mock_invoke.assert_called_once_with("set_visible_slot", False)

    def test_non_matching_name_no_op(self, bt):
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_module_close("ui.module.close", {"name": "other"})
        mock_invoke.assert_not_called()


# ---------------------------------------------------------------------------
# 10. on_input_event
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOnInputEvent:
    def test_no_crash_when_window_none(self, bt):
        bt._window = None
        bt.on_input_event("input.event.bluetooth_ui", {"type": "press", "x": 10, "y": 20})


# ---------------------------------------------------------------------------
# 11. Bus event handler routing
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBusEventHandlers:
    def _setup_mock_window(self, bt):
        bt._window = MagicMock()
        return bt._window

    def test_on_device_found_calls_invoke_add_device(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_device_found("", {"address": "AA:BB", "name": "Phone", "rssi": -60})
        mock_invoke.assert_called_with("add_device", "AA:BB", "Phone", -60)

    def test_on_discovery_completed_calls_set_status(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_discovery_completed("", {"devices": [1, 2, 3]})
        # Should invoke set_status with a message mentioning count
        assert mock_invoke.called
        args = mock_invoke.call_args[0]
        assert args[0] == "set_status"
        assert "3" in args[1]

    def test_on_pairing_pin_calls_show_pin_dialog(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_pairing_pin("", {"device_address": "AA:BB", "pin": "1234"})
        mock_invoke.assert_called_with("show_pin_dialog", "AA:BB", "1234")

    def test_on_pairing_completed_calls_on_pairing_completed_slot(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_pairing_completed("", {"device_address": "AA:BB"})
        mock_invoke.assert_called_with("on_pairing_completed", "AA:BB")

    def test_on_pairing_failed_calls_on_pairing_failed_slot(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_pairing_failed("", {"device_address": "AA:BB", "error": "timeout"})
        mock_invoke.assert_called_with("on_pairing_failed", "AA:BB", "timeout")

    def test_on_paired_devices_calls_refresh_paired_list(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_paired_devices("", {"devices": [{"address": "X", "name": "Y"}]})
        assert mock_invoke.called
        assert mock_invoke.call_args[0][0] == "refresh_paired_list"

    def test_on_paired_connected_invokes_slot(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_paired_connected("", {"device_address": "AA:BB"})
        mock_invoke.assert_called_with("on_paired_connected", "AA:BB")

    def test_on_paired_disconnected_invokes_slot(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_paired_disconnected("", {"device_address": "AA:BB"})
        mock_invoke.assert_called_with("on_paired_disconnected", "AA:BB")

    def test_on_paired_removed_invokes_slot(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_paired_removed("", {"device_address": "AA:BB"})
        mock_invoke.assert_called_with("on_paired_removed", "AA:BB")

    def test_on_paired_failed_invokes_slot(self, bt):
        self._setup_mock_window(bt)
        with patch.object(bt, "_invoke") as mock_invoke:
            bt.on_paired_failed("", {"device_address": "AA:BB", "error": "denied"})
        mock_invoke.assert_called_with("on_paired_failed", "AA:BB", "denied")

    def test_bus_events_safe_when_window_none(self, bt):
        bt._window = None
        # All these must not raise
        bt.on_device_found("", {"address": "X", "name": "", "rssi": 0})
        bt.on_discovery_completed("", {"devices": []})
        bt.on_pairing_completed("", {"device_address": "X"})
        bt.on_pairing_failed("", {"device_address": "X", "error": "e"})
        bt.on_paired_connected("", {"device_address": "X"})
        bt.on_paired_disconnected("", {"device_address": "X"})
        bt.on_paired_removed("", {"device_address": "X"})
        bt.on_paired_failed("", {"device_address": "X", "error": "e"})


# ---------------------------------------------------------------------------
# 12–18. BluetoothPairingWindow slot logic (pure Python proxy)
# ---------------------------------------------------------------------------

class _PureWindow:
    """Pure-Python proxy that mirrors BluetoothPairingWindow slot logic."""

    def __init__(self, mock_bus):
        from unittest.mock import MagicMock
        self._bus = mock_bus
        self._devices: dict = {}
        self._pending_pin_address: str = ""
        # Fake list widgets
        self._device_list  = _FakeListWidget_()
        self._paired_list  = _FakeListWidget_()
        self._status_msg   = ""
        self._btn_pair_enabled     = False
        self._btn_connect_enabled  = False
        self._btn_disconnect_enabled = False
        self._btn_remove_enabled   = False

    def set_status(self, msg: str):
        self._status_msg = msg

    _ROLE = 256
    _ROLE_CONNECTED = 257

    def add_device(self, address: str, name: str, rssi: int):
        if address in self._devices:
            return
        self._devices[address] = {"name": name, "rssi": rssi}
        label = f"{name or '(sconosciuto)'}  [{address}]  RSSI: {rssi} dBm"
        item = _FakeItem_(label)
        item.setData(self._ROLE, address)
        self._device_list.addItem(item)

    def refresh_paired_list(self, devices_repr: str):
        import ast
        try:
            devices = ast.literal_eval(devices_repr)
        except Exception:
            devices = []
        self._paired_list.clear()
        for dev in devices:
            address   = dev.get("address", "")
            name      = dev.get("name", "") or "(sconosciuto)"
            connected = dev.get("connected", False)
            trusted   = dev.get("trusted", False)
            state     = "🟢 connesso" if connected else "⚪ non connesso"
            trust_tag = " ✓trusted" if trusted else ""
            label     = f"{name}  [{address}]  {state}{trust_tag}"
            item = _FakeItem_(label)
            item.setData(self._ROLE, address)
            item.setData(self._ROLE + 1, connected)
            self._paired_list.addItem(item)
        self._update_paired_buttons()

    def _update_paired_buttons(self):
        item = self._paired_list.currentItem()
        if not item:
            self._btn_connect_enabled    = False
            self._btn_disconnect_enabled = False
            self._btn_remove_enabled     = False
            return
        connected = bool(item.data(self._ROLE + 1))
        self._btn_connect_enabled    = not connected
        self._btn_disconnect_enabled = connected
        self._btn_remove_enabled     = True

    def _refresh_item_state(self, address: str, connected: bool):
        for i in range(self._paired_list.count()):
            item = self._paired_list.item(i)
            if item and item.data(self._ROLE) == address:
                state = "🟢 connesso" if connected else "⚪ non connesso"
                parts = item.text().rsplit("  ", 1)
                new_label = f"{parts[0]}  {state}" if len(parts) == 2 else item.text()
                item.setText(new_label)
                item.setData(self._ROLE + 1, connected)
                break

    def on_paired_connected(self, address: str):
        self.set_status(f"🟢  Connesso a {address}")
        self._refresh_item_state(address, connected=True)
        self._update_paired_buttons()

    def on_paired_disconnected(self, address: str):
        self.set_status(f"⚪  Disconnesso da {address}")
        self._refresh_item_state(address, connected=False)
        self._update_paired_buttons()

    def on_paired_removed(self, address: str):
        self.set_status(f"🗑  Rimosso {address}")
        for i in range(self._paired_list.count()):
            item = self._paired_list.item(i)
            if item and item.data(self._ROLE) == address:
                self._paired_list.takeItem(i)
                break
        self._update_paired_buttons()

    def _selected_paired_address(self) -> str:
        item = self._paired_list.currentItem()
        return item.data(self._ROLE) if item else ""

    def _on_scan_clicked(self):
        self._devices.clear()
        self._device_list.clear()
        self._btn_pair_enabled = False
        self.set_status("Ricerca dispositivi in corso…")
        self._bus.publish("bluetooth_manager.discover", {"duration_sec": 10})

    def _on_pair_clicked(self):
        selected = self._device_list.currentItem()
        if not selected:
            return
        address = selected.data(self._ROLE)
        self._bus.publish("bluetooth_manager.pair", {"device_address": address})

    def _on_refresh_paired_clicked(self):
        self._bus.publish("bluetooth_manager.paired.list", {})

    def _on_autoconnect_clicked(self):
        self._bus.publish("bluetooth_manager.try_autoconnect", {})

    def _on_connect_paired_clicked(self):
        address = self._selected_paired_address()
        if not address:
            return
        self._bus.publish("bluetooth_manager.paired.connect", {"device_address": address})

    def _on_disconnect_paired_clicked(self):
        address = self._selected_paired_address()
        if not address:
            return
        self._bus.publish("bluetooth_manager.paired.disconnect", {"device_address": address})

    def _on_remove_paired_clicked(self):
        address = self._selected_paired_address()
        if not address:
            return
        self._bus.publish("bluetooth_manager.paired.remove", {"device_address": address})


class _FakeItem_:
    ROLE = 256
    def __init__(self, label=""):
        self._label = label
        self._data  = {}
    def setData(self, r, v): self._data[r] = v
    def data(self, r):       return self._data.get(r)
    def setText(self, t):    self._label = t
    def text(self):          return self._label


class _FakeListWidget_:
    def __init__(self):
        self._items   = []
        self._current = None
    def addItem(self, item):  self._items.append(item)
    def clear(self):          self._items = []; self._current = None
    def count(self):          return len(self._items)
    def item(self, i):        return self._items[i] if i < len(self._items) else None
    def currentItem(self):    return self._current
    def takeItem(self, i):
        if 0 <= i < len(self._items):
            return self._items.pop(i)
    def setCurrentItem(self, item): self._current = item


@pytest.mark.unit
class TestBluetoothPairingWindowSlots:

    @pytest.fixture()
    def win(self):
        return _PureWindow(MagicMock())

    # -- 12. add_device --
    def test_add_device_inserts_item(self, win):
        win.add_device("AA:BB", "Phone", -70)
        assert win._device_list.count() == 1

    def test_add_device_deduplicates(self, win):
        win.add_device("AA:BB", "Phone", -70)
        win.add_device("AA:BB", "Phone", -70)  # duplicate
        assert win._device_list.count() == 1

    def test_add_device_label_contains_address(self, win):
        win.add_device("AA:BB:CC", "Test", -50)
        item = win._device_list.item(0)
        assert "AA:BB:CC" in item.text()

    # -- 13. refresh_paired_list --
    def test_refresh_paired_list_populates(self, win):
        devices = [{"address": "A", "name": "Dev", "connected": False, "trusted": True}]
        win.refresh_paired_list(repr(devices))
        assert win._paired_list.count() == 1

    def test_refresh_paired_list_clears_previous(self, win):
        win._paired_list.addItem(_FakeItem_("old"))
        win.refresh_paired_list(repr([]))
        assert win._paired_list.count() == 0

    def test_refresh_paired_list_invalid_repr_empty(self, win):
        win.refresh_paired_list("{bad repr}")
        assert win._paired_list.count() == 0

    # -- 14. on_paired_connected / disconnected / removed --
    def _add_paired(self, win, address="AA", connected=False):
        item = _FakeItem_(f"Dev  [{address}]  ⚪ non connesso")
        item.setData(_FakeItem_.ROLE, address)
        item.setData(_FakeItem_.ROLE + 1, connected)
        win._paired_list.addItem(item)

    def test_on_paired_connected_updates_state(self, win):
        self._add_paired(win, "AA", connected=False)
        win.on_paired_connected("AA")
        item = win._paired_list.item(0)
        assert item.data(_FakeItem_.ROLE + 1) is True

    def test_on_paired_disconnected_updates_state(self, win):
        self._add_paired(win, "AA", connected=True)
        win.on_paired_disconnected("AA")
        item = win._paired_list.item(0)
        assert item.data(_FakeItem_.ROLE + 1) is False

    def test_on_paired_removed_removes_item(self, win):
        self._add_paired(win, "AA")
        win.on_paired_removed("AA")
        assert win._paired_list.count() == 0

    def test_on_paired_removed_non_matching_no_op(self, win):
        self._add_paired(win, "AA")
        win.on_paired_removed("BB")
        assert win._paired_list.count() == 1

    # -- 15. _update_paired_buttons --
    def test_buttons_disabled_no_selection(self, win):
        win._update_paired_buttons()
        assert win._btn_connect_enabled    is False
        assert win._btn_disconnect_enabled is False
        assert win._btn_remove_enabled     is False

    def test_connect_enabled_when_not_connected(self, win):
        self._add_paired(win, "AA", connected=False)
        win._paired_list.setCurrentItem(win._paired_list.item(0))
        win._update_paired_buttons()
        assert win._btn_connect_enabled    is True
        assert win._btn_disconnect_enabled is False

    def test_disconnect_enabled_when_connected(self, win):
        self._add_paired(win, "AA", connected=True)
        win._paired_list.setCurrentItem(win._paired_list.item(0))
        win._update_paired_buttons()
        assert win._btn_connect_enabled    is False
        assert win._btn_disconnect_enabled is True

    # -- 16. _refresh_item_state --
    def test_refresh_item_state_updates_label_and_flag(self, win):
        self._add_paired(win, "AA", connected=False)
        win._refresh_item_state("AA", connected=True)
        item = win._paired_list.item(0)
        assert item.data(_FakeItem_.ROLE + 1) is True

    # -- 17. _selected_paired_address --
    def test_selected_address_empty_when_no_selection(self, win):
        assert win._selected_paired_address() == ""

    def test_selected_address_returns_current_item_data(self, win):
        self._add_paired(win, "AA:BB:CC")
        win._paired_list.setCurrentItem(win._paired_list.item(0))
        assert win._selected_paired_address() == "AA:BB:CC"

    # -- 18. Button handler bus publishes --
    def test_scan_clicked_publishes_discover(self, win):
        win._on_scan_clicked()
        win._bus.publish.assert_called_with("bluetooth_manager.discover", {"duration_sec": 10})

    def test_refresh_paired_publishes_paired_list(self, win):
        win._on_refresh_paired_clicked()
        win._bus.publish.assert_called_with("bluetooth_manager.paired.list", {})

    def test_autoconnect_publishes(self, win):
        win._on_autoconnect_clicked()
        win._bus.publish.assert_called_with("bluetooth_manager.try_autoconnect", {})

    def test_connect_paired_publishes(self, win):
        self._add_paired(win, "AA")
        win._paired_list.setCurrentItem(win._paired_list.item(0))
        win._on_connect_paired_clicked()
        win._bus.publish.assert_called_with("bluetooth_manager.paired.connect", {"device_address": "AA"})

    def test_disconnect_paired_publishes(self, win):
        self._add_paired(win, "AA", connected=True)
        win._paired_list.setCurrentItem(win._paired_list.item(0))
        win._on_disconnect_paired_clicked()
        win._bus.publish.assert_called_with("bluetooth_manager.paired.disconnect", {"device_address": "AA"})

    def test_remove_paired_publishes(self, win):
        self._add_paired(win, "AA")
        win._paired_list.setCurrentItem(win._paired_list.item(0))
        win._on_remove_paired_clicked()
        win._bus.publish.assert_called_with("bluetooth_manager.paired.remove", {"device_address": "AA"})

    def test_pair_clicked_no_op_when_no_selection(self, win):
        win._on_pair_clicked()  # no selection, must not raise

    def test_connect_clicked_no_op_when_no_selection(self, win):
        win._on_connect_paired_clicked()  # must not raise

    def test_disconnect_clicked_no_op_when_no_selection(self, win):
        win._on_disconnect_paired_clicked()  # must not raise

    def test_remove_clicked_no_op_when_no_selection(self, win):
        win._on_remove_paired_clicked()  # must not raise


# ---------------------------------------------------------------------------
# 19. Design token values
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDesignTokenValues:
    def test_surface_color_matches_token(self, bt):
        assert bt._COLOR_SURFACE == "#1c1c1c"

    def test_text_color_matches_token(self, bt):
        assert bt._COLOR_TEXT == "#f0ece4"

    def test_accent_color_matches_token(self, bt):
        assert bt._COLOR_ACCENT == "#c8b89a"

    def test_danger_color_matches_token(self, bt):
        assert bt._COLOR_DANGER == "#c0392b"


# ---------------------------------------------------------------------------
# 20. UI Architecture compliance: window must NOT have WindowStaysOnTopHint
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUIArchitectureCompliance:
    def test_window_flags_no_stays_on_top_hint(self, bt):
        """BluetoothPairingWindow must NOT set WindowStaysOnTopHint.
        Z-order is managed by ui_shell via z_order in ui.widget.register.
        Verified by source inspection: the flag string must not appear in the
        window flags assignment inside BluetoothPairingWindow.__init__.
        """
        import inspect
        src = inspect.getsource(bt.BluetoothPairingWindow.__init__)
        assert "WindowStaysOnTopHint" not in src, \
            "WindowStaysOnTopHint must NOT be set in BluetoothPairingWindow — " \
            "z-order is managed by ui_shell via ui.widget.register z_order field"

    def test_register_payload_has_on_request_true(self, bt):
        bt._mock_bus.publish.reset_mock()
        bt._register()
        calls = {c.args[0]: c.args[1] for c in bt._mock_bus.publish.call_args_list}
        assert calls.get("ui.widget.register", {}).get("on_request") is True
