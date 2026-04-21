"""
Tests for v2/modules/config_ui/main.py

Strategy:
- BusClient fully mocked — no ZMQ broker needed
- QApplication created once per session
- Window slots called directly to simulate ZMQ-thread _invoke calls
- bus.publish calls captured and asserted

Run:
    pytest v2/modules/config_ui/tests/ -v
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_HERE    = Path(__file__).parent
_MODULE  = _HERE.parent
_MODULES = _MODULE.parent
_V2      = _MODULES.parent

for p in (str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Stub heavy dependencies
# ---------------------------------------------------------------------------

_logger_mod = types.ModuleType("shared.logger")
_logger_mod.get_logger = lambda name: MagicMock()
sys.modules.setdefault("shared", types.ModuleType("shared"))
sys.modules["shared.logger"] = _logger_mod

_mock_bus = MagicMock()
_mock_bus.publish  = MagicMock()
_mock_bus.subscribe = MagicMock()
_mock_bus.start    = MagicMock()
_mock_bus.stop     = MagicMock()

_bus_mod = types.ModuleType("shared.bus_client")
_bus_mod.BusClient = MagicMock(return_value=_mock_bus)
sys.modules["shared.bus_client"] = _bus_mod

import v2.modules.config_ui.main as cfg_ui  # noqa: E402

cfg_ui.bus = _mock_bus

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
    from v2.modules.config_ui.main import ConfigWindow
    w = ConfigWindow()
    cfg_ui._window = w
    _mock_bus.publish.reset_mock()
    yield w
    cfg_ui._window = None
    w.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_tab(window, name="bluetooth", pid=1234, status="active"):
    window.add_or_update_module_tab(name, pid, status)
    return window._tabs[name]


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_no_tabs_at_start(self, window):
        assert window._tab_widget.count() == 0

    def test_status_shows_waiting(self, window):
        assert window._status.currentMessage() != ""


# ---------------------------------------------------------------------------
# system.start handler
# ---------------------------------------------------------------------------

class TestSystemStart:
    def test_publishes_get_modules(self, window):
        cfg_ui.on_system_start("system.start", {})
        _mock_bus.publish.assert_called_with("system.get_modules", {})


# ---------------------------------------------------------------------------
# system.modules_response → tabs created
# ---------------------------------------------------------------------------

class TestModulesResponse:
    def test_tab_created_for_each_module(self, window):
        cfg_ui.on_modules_response("system.modules_response", {
            "modules": [
                {"name": "bluetooth", "pid": 101, "status": "active"},
                {"name": "config_manager", "pid": 102, "status": "active"},
            ]
        })
        assert "bluetooth" in window._tabs
        assert "config_manager" in window._tabs

    def test_tab_count_matches_modules(self, window):
        cfg_ui.on_modules_response("system.modules_response", {
            "modules": [
                {"name": "mod_a", "pid": 1, "status": "active"},
                {"name": "mod_b", "pid": 2, "status": "active"},
            ]
        })
        assert window._tab_widget.count() == 2

    def test_config_get_published_per_module(self, window):
        _mock_bus.publish.reset_mock()
        cfg_ui.on_modules_response("system.modules_response", {
            "modules": [{"name": "bluetooth", "pid": 101, "status": "active"}]
        })
        calls = [c.args for c in _mock_bus.publish.call_args_list]
        assert ("config.get", {"module": "bluetooth"}) in calls

    def test_duplicate_module_does_not_add_tab(self, window):
        window.add_or_update_module_tab("bluetooth", 101, "active")
        window.add_or_update_module_tab("bluetooth", 101, "active")
        assert window._tab_widget.count() == 1

    def test_status_updated_on_duplicate_module(self, window):
        window.add_or_update_module_tab("bluetooth", 101, "active")
        window.add_or_update_module_tab("bluetooth", 101, "exited (0)")
        tab = window._tabs["bluetooth"]
        assert "exited" in tab._lbl_status.text()


# ---------------------------------------------------------------------------
# config.response → tab populated
# ---------------------------------------------------------------------------

class TestConfigResponse:
    def test_fields_created_for_each_key(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"pin": "1234", "enabled": "true"}))
        tab = window._tabs["bluetooth"]
        assert "pin" in tab._fields
        assert "enabled" in tab._fields

    def test_field_value_matches_config(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"pin": "9999"}))
        assert window._tabs["bluetooth"]._fields["pin"].text() == "9999"

    def test_save_button_enabled_after_populate(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"pin": "1234"}))
        assert window._tabs["bluetooth"]._btn_save.isEnabled()

    def test_save_button_disabled_for_empty_config(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({}))
        assert not window._tabs["bluetooth"]._btn_save.isEnabled()

    def test_unknown_module_response_is_ignored(self, window):
        # no crash if module tab doesn't exist
        cfg_ui.on_config_response("config.response", {"module": "ghost", "config": {"x": 1}})


# ---------------------------------------------------------------------------
# Save action → config.set published only for changed keys
# ---------------------------------------------------------------------------

class TestSaveAction:
    def test_save_publishes_only_changed_keys(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"pin": "1234", "enabled": "true"}))
        tab = window._tabs["bluetooth"]
        tab._fields["pin"].setText("5678")   # changed
        # enabled left as-is
        _mock_bus.publish.reset_mock()
        tab._on_save()
        _mock_bus.publish.assert_called_once_with(
            "config.set", {"module": "bluetooth", "key": "pin", "value": "5678"}
        )

    def test_save_noop_when_nothing_changed(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"pin": "1234"}))
        _mock_bus.publish.reset_mock()
        window._tabs["bluetooth"]._on_save()
        _mock_bus.publish.assert_not_called()

    def test_save_updates_originals(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"pin": "1234"}))
        tab = window._tabs["bluetooth"]
        tab._fields["pin"].setText("0000")
        tab._on_save()
        # second save should be no-op
        _mock_bus.publish.reset_mock()
        tab._on_save()
        _mock_bus.publish.assert_not_called()

    def test_save_publishes_multiple_changed_keys(self, window):
        _add_tab(window, "bluetooth")
        window.populate_module_config("bluetooth", json.dumps({"a": "1", "b": "2", "c": "3"}))
        tab = window._tabs["bluetooth"]
        tab._fields["a"].setText("10")
        tab._fields["c"].setText("30")
        _mock_bus.publish.reset_mock()
        tab._on_save()
        published_keys = {
            call.args[1]["key"] for call in _mock_bus.publish.call_args_list
        }
        assert published_keys == {"a", "c"}


# ---------------------------------------------------------------------------
# Refresh actions
# ---------------------------------------------------------------------------

class TestRefreshActions:
    def test_refresh_all_publishes_get_modules(self, window):
        _mock_bus.publish.reset_mock()
        window._on_refresh_all()
        _mock_bus.publish.assert_called_with("system.get_modules", {})

    def test_tab_refresh_publishes_config_get(self, window):
        tab = _add_tab(window, "bluetooth")
        _mock_bus.publish.reset_mock()
        tab._on_refresh()
        _mock_bus.publish.assert_called_with("config.get", {"module": "bluetooth"})


# ---------------------------------------------------------------------------
# system.stop
# ---------------------------------------------------------------------------

class TestSystemStop:
    def test_stop_calls_bus_stop(self, window):
        _mock_bus.stop.reset_mock()
        cfg_ui.on_system_stop("system.stop", {})
        _mock_bus.stop.assert_called_once()
