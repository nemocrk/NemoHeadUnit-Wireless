"""
Unit tests for modules/floating_menu_ui/main.py

Covers:
  - on_request module discovery via on_widget_register
  - on_widget_unregister removes entry from menu
  - mutual exclusivity: opening B closes A
  - _close_active_module sends ui.module.close
  - on_home_pressed closes active module + hides menu
  - on_settings_toggle: first call expands, second collapses
  - dpi_factor updated from ui.widget.geometry payload
  - _visible_count capped at 8
  - _sorted_entries ordering by menu_order
  - arc geometry: _bounding_w / _bounding_h
  - boot protocol
"""

import sys
import types
import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Bootstrap stubs
# ---------------------------------------------------------------------------

_bus_mod = types.ModuleType("shared.bus_client")
_bus_mock = MagicMock()
_bus_mod.BusClient = MagicMock(return_value=_bus_mock)
sys.modules.setdefault("shared", types.ModuleType("shared"))
sys.modules["shared.bus_client"] = _bus_mod

_cfg_mod = types.ModuleType("shared.config_client")
_cfg_mock = MagicMock()
_cfg_mod.ConfigClient = MagicMock(return_value=_cfg_mock)
sys.modules["shared.config_client"] = _cfg_mod

_log_mod = types.ModuleType("shared.logger")
_log_mock = MagicMock()
_log_mod.get_logger = MagicMock(return_value=_log_mock)
sys.modules["shared.logger"] = _log_mod

_schema_mod = types.ModuleType("shared.config_schema")


class _FakeField:
    def __init__(self, default, **kwargs):
        self.default = default


_schema_mod.field_int = lambda default=0, **kw: _FakeField(default)
sys.modules["shared.config_schema"] = _schema_mod

_REPO_ROOT = Path(__file__).parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib
fmu = importlib.import_module("modules.floating_menu_ui.main")


# ---------------------------------------------------------------------------
# Fixture: reset module state before every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    fmu._on_request_modules.clear()
    fmu._active_module  = None
    fmu._menu_visible   = False
    fmu._dpi_factor     = 1.0
    fmu._screen_h       = 600
    fmu._navbar_h       = 60
    fmu._shell_ready    = False
    fmu._geometry_set   = False
    fmu._config         = {k: v.default for k, v in fmu._SCHEMA.items()}
    _bus_mock.reset_mock()
    yield
    fmu._on_request_modules.clear()
    fmu._active_module = None


# ---------------------------------------------------------------------------
# on_request discovery
# ---------------------------------------------------------------------------

class TestOnRequestDiscovery:
    def test_on_request_module_added_to_registry(self):
        fmu.on_widget_register("", {
            "name": "bt_ui", "on_request": True,
            "menu_order": 1, "icon": "bluetooth",
        })
        assert "bt_ui" in fmu._on_request_modules
        assert fmu._on_request_modules["bt_ui"].icon == "bluetooth"
        assert fmu._on_request_modules["bt_ui"].menu_order == 1

    def test_non_on_request_module_ignored(self):
        fmu.on_widget_register("", {
            "name": "navbar_ui", "on_request": False,
            "menu_order": 0, "icon": "",
        })
        assert "navbar_ui" not in fmu._on_request_modules

    def test_self_registration_ignored(self):
        fmu.on_widget_register("", {
            "name": fmu.MODULE_NAME, "on_request": True,
            "menu_order": 0, "icon": "",
        })
        assert fmu.MODULE_NAME not in fmu._on_request_modules

    def test_missing_on_request_field_treated_as_false(self):
        fmu.on_widget_register("", {"name": "video_ui"})
        assert "video_ui" not in fmu._on_request_modules

    def test_menu_order_default_99(self):
        fmu.on_widget_register("", {"name": "config_ui", "on_request": True})
        assert fmu._on_request_modules["config_ui"].menu_order == 99

    def test_icon_defaults_to_empty_string(self):
        fmu.on_widget_register("", {"name": "config_ui", "on_request": True})
        assert fmu._on_request_modules["config_ui"].icon == ""


# ---------------------------------------------------------------------------
# on_widget_unregister
# ---------------------------------------------------------------------------

class TestOnWidgetUnregister:
    def _add(self, name):
        fmu._on_request_modules[name] = fmu.OnRequestEntry(
            name=name, menu_order=1, icon="x"
        )

    def test_removes_entry(self):
        self._add("bt_ui")
        fmu.on_widget_unregister("", {"name": "bt_ui"})
        assert "bt_ui" not in fmu._on_request_modules

    def test_unknown_name_no_crash(self):
        fmu.on_widget_unregister("", {"name": "ghost"})

    def test_missing_name_no_crash(self):
        fmu.on_widget_unregister("", {})


# ---------------------------------------------------------------------------
# Mutual exclusivity
# ---------------------------------------------------------------------------

class TestMutualExclusivity:
    def _add(self, *names):
        for name in names:
            fmu._on_request_modules[name] = fmu.OnRequestEntry(
                name=name, menu_order=1, icon="x"
            )

    def test_open_first_module(self):
        self._add("bt_ui")
        fmu._open_module("bt_ui")
        assert fmu._active_module == "bt_ui"
        topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.module.open" in topics
        open_call = next(c for c in _bus_mock.publish.call_args_list if c.args[0] == "ui.module.open")
        assert open_call.args[1] == {"name": "bt_ui"}

    def test_opening_second_closes_first(self):
        self._add("bt_ui", "config_ui")
        fmu._open_module("bt_ui")
        _bus_mock.reset_mock()
        fmu._open_module("config_ui")

        topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.module.close" in topics
        close_call = next(c for c in _bus_mock.publish.call_args_list if c.args[0] == "ui.module.close")
        assert close_call.args[1] == {"name": "bt_ui"}
        assert fmu._active_module == "config_ui"

    def test_opening_same_module_is_noop(self):
        self._add("bt_ui")
        fmu._active_module = "bt_ui"
        _bus_mock.reset_mock()
        fmu._open_module("bt_ui")
        topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.module.open" not in topics
        assert "ui.module.close" not in topics

    def test_close_active_sends_close(self):
        self._add("bt_ui")
        fmu._active_module = "bt_ui"
        fmu._close_active_module()
        topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.module.close" in topics
        assert fmu._active_module is None

    def test_close_when_none_no_publish(self):
        fmu._active_module = None
        fmu._close_active_module()
        _bus_mock.publish.assert_not_called()


# ---------------------------------------------------------------------------
# ui.home.pressed
# ---------------------------------------------------------------------------

class TestHomePressedHandler:
    def test_closes_active_module(self):
        fmu._active_module  = "bt_ui"
        fmu._menu_visible   = True
        fmu.on_home_pressed("", {})
        topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.module.close" in topics
        assert fmu._active_module is None

    def test_hides_menu(self):
        fmu._menu_visible = True
        fmu.on_home_pressed("", {})
        assert fmu._menu_visible is False

    def test_publishes_widget_update_with_zero_size(self):
        fmu._menu_visible = True
        fmu.on_home_pressed("", {})
        update_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "ui.widget.update"
        ]
        assert len(update_calls) >= 1
        last = update_calls[-1].args[1]
        assert last["width"]  == 0
        assert last["height"] == 0


# ---------------------------------------------------------------------------
# ui.settings.toggle
# ---------------------------------------------------------------------------

class TestSettingsToggle:
    def test_first_toggle_opens_menu(self):
        fmu._menu_visible = False
        fmu.on_settings_toggle("", {})
        assert fmu._menu_visible is True

    def test_second_toggle_closes_menu(self):
        fmu._menu_visible = True
        fmu.on_settings_toggle("", {})
        assert fmu._menu_visible is False

    def test_open_publishes_nonzero_geometry(self):
        fmu._menu_visible = False
        fmu.on_settings_toggle("", {})
        update_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "ui.widget.update"
        ]
        assert len(update_calls) >= 1
        payload = update_calls[-1].args[1]
        assert payload["width"]  > 0
        assert payload["height"] > 0

    def test_close_publishes_zero_geometry(self):
        fmu._menu_visible = True
        fmu.on_settings_toggle("", {})
        update_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "ui.widget.update"
        ]
        assert len(update_calls) >= 1
        payload = update_calls[-1].args[1]
        assert payload["width"]  == 0
        assert payload["height"] == 0


# ---------------------------------------------------------------------------
# dpi_factor
# ---------------------------------------------------------------------------

class TestDpiFactor:
    def test_dpi_factor_updated_from_any_geometry_message(self):
        fmu.on_widget_geometry("", {
            "name": "navbar_ui", "x": 0, "y": 540, "w": 1024, "h": 60,
            "dpi_factor": 2.0,
        })
        assert fmu._dpi_factor == 2.0

    def test_invalid_dpi_factor_ignored(self):
        fmu._dpi_factor = 1.5
        fmu.on_widget_geometry("", {
            "name": "some_widget", "x": 0, "y": 0, "w": 100, "h": 100,
            "dpi_factor": -1,
        })
        assert fmu._dpi_factor == 1.5  # unchanged

    def test_navbar_h_updated_from_navbar_geometry(self):
        fmu.on_widget_geometry("", {
            "name": "navbar_ui", "x": 0, "y": 540, "w": 1024, "h": 72,
            "dpi_factor": 1.0,
        })
        assert fmu._navbar_h == 72


# ---------------------------------------------------------------------------
# Arc geometry helpers
# ---------------------------------------------------------------------------

class TestArcGeometry:
    def test_bounding_w_positive(self):
        assert fmu._bounding_w() > 0

    def test_bounding_h_capped_at_available_screen(self):
        fmu._screen_h = 600
        fmu._navbar_h = 60
        bh = fmu._bounding_h()
        assert bh <= 540  # screen_h - navbar_h

    def test_dpi_scales_bounding_w(self):
        fmu._dpi_factor = 1.0
        w1 = fmu._bounding_w()
        fmu._dpi_factor = 2.0
        w2 = fmu._bounding_w()
        assert w2 == w1 * 2

    def test_icon_center_within_bounding_box(self):
        fmu._dpi_factor = 1.0
        bw = fmu._bounding_w()
        bh = fmu._bounding_h()
        cx, cy = fmu._icon_center(0, 3)
        assert 0 <= cx <= bw
        assert 0 <= cy <= bh


# ---------------------------------------------------------------------------
# _visible_count
# ---------------------------------------------------------------------------

class TestVisibleCount:
    def _add(self, *names):
        for i, name in enumerate(names):
            fmu._on_request_modules[name] = fmu.OnRequestEntry(
                name=name, menu_order=i, icon=""
            )

    def test_zero_when_no_modules(self):
        assert fmu._visible_count() == 0

    def test_capped_at_eight(self):
        self._add(*(f"mod_{i}" for i in range(12)))
        assert fmu._visible_count() <= 8

    def test_equals_total_when_few(self):
        self._add("a", "b", "c")
        assert fmu._visible_count() == 3


# ---------------------------------------------------------------------------
# _sorted_entries
# ---------------------------------------------------------------------------

class TestSortedEntries:
    def test_sorted_by_menu_order(self):
        fmu._on_request_modules["z"] = fmu.OnRequestEntry(name="z", menu_order=5, icon="")
        fmu._on_request_modules["a"] = fmu.OnRequestEntry(name="a", menu_order=1, icon="")
        fmu._on_request_modules["m"] = fmu.OnRequestEntry(name="m", menu_order=3, icon="")
        entries = fmu._sorted_entries()
        orders = [e.menu_order for e in entries]
        assert orders == sorted(orders)

    def test_equal_order_sorted_by_name(self):
        fmu._on_request_modules["z"] = fmu.OnRequestEntry(name="z", menu_order=1, icon="")
        fmu._on_request_modules["a"] = fmu.OnRequestEntry(name="a", menu_order=1, icon="")
        entries = fmu._sorted_entries()
        assert entries[0].name == "a"
        assert entries[1].name == "z"


# ---------------------------------------------------------------------------
# Boot protocol
# ---------------------------------------------------------------------------

class TestBootProtocol:
    def test_on_system_readytostart_publishes_module_ready(self):
        fmu.on_system_readytostart()
        _bus_mock.publish.assert_called_with(
            "system.module_ready",
            {"name": fmu.MODULE_NAME, "priority": fmu.PRIORITY},
        )

    def test_on_system_start_wrong_priority_ignored(self):
        _bus_mock.reset_mock()
        fmu.on_system_start("", {"priority": 99})
        _bus_mock.publish.assert_not_called()

    def test_on_ui_shell_ready_sets_flag_and_registers(self):
        fmu.on_ui_shell_ready("", {})
        assert fmu._shell_ready is True
        topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.widget.register" in topics

    def test_register_payload(self):
        fmu._register()
        reg_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "ui.widget.register"
        ]
        assert len(reg_calls) == 1
        p = reg_calls[0].args[1]
        assert p["name"]    == fmu.MODULE_NAME
        assert p["z_order"] == 3
        assert p["dock"]    == "bottom-right"
        assert p["width"]   == 0
        assert p["height"]  == 0

    def test_on_system_stop_calls_bus_stop(self):
        fmu._qt_app = None
        fmu.on_system_stop("", {})
        _bus_mock.stop.assert_called_once()
