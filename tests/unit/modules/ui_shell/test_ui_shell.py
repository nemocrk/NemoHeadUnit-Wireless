"""
Unit tests for modules/ui_shell/main.py

Covers:
  - _resolve_size / _clamp helpers
  - _compute_geometry for every dock
  - _reflow with single and multiple widgets
  - on_widget_register / on_widget_update / on_widget_unregister
  - _hit_test and on_input_raw routing
  - on_screen_resize reflow
  - dpi_factor included in ui.widget.geometry payload
  - on_request / menu_order / icon fields in WidgetConstraints
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub heavy dependencies before importing the module under test
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


_schema_mod.field_bool  = lambda default=True,  **kw: _FakeField(default)
_schema_mod.field_int   = lambda default=0,     **kw: _FakeField(default)
_schema_mod.field_float = lambda default=1.0,   **kw: _FakeField(default)
sys.modules["shared.config_schema"] = _schema_mod

_REPO_ROOT = Path(__file__).parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib
uis = importlib.import_module("modules.ui_shell.main")

sys.modules.pop("shared.bus_client", None)
sys.modules.pop("shared.config_client", None)
sys.modules.pop("shared.logger", None)
sys.modules.pop("shared.config_schema", None)
sys.modules.pop("shared", None)


# ---------------------------------------------------------------------------
# Helpers to reset module state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    uis._registry.clear()
    uis._screen_w = 1024
    uis._screen_h = 600
    uis._config   = {k: v.default for k, v in uis._SCHEMA.items()}
    _bus_mock.reset_mock()
    yield
    uis._registry.clear()
    uis._screen_w = 1024
    uis._screen_h = 600


# ---------------------------------------------------------------------------
# _clamp
# ---------------------------------------------------------------------------

class TestClamp:
    def test_within_bounds(self):
        assert uis._clamp(50, 10, 100) == 50

    def test_below_minimum(self):
        assert uis._clamp(5, 10, 100) == 10

    def test_above_maximum(self):
        assert uis._clamp(200, 10, 100) == 100

    def test_no_lo(self):
        assert uis._clamp(5, None, 100) == 5

    def test_no_hi(self):
        assert uis._clamp(200, 10, None) == 200

    def test_both_none(self):
        assert uis._clamp(42, None, None) == 42


# ---------------------------------------------------------------------------
# _resolve_size
# ---------------------------------------------------------------------------

class TestResolveSize:
    def test_fixed_overrides_available(self):
        assert uis._resolve_size(1024, 60, None, None) == 60

    def test_fills_available_when_no_fixed(self):
        assert uis._resolve_size(1024, None, None, None) == 1024

    def test_min_constraint(self):
        assert uis._resolve_size(1024, 20, 40, None) == 40

    def test_max_constraint(self):
        assert uis._resolve_size(1024, None, None, 800) == 800


# ---------------------------------------------------------------------------
# _compute_geometry (single-widget helper)
# ---------------------------------------------------------------------------

class TestComputeGeometry:
    def _make_record(self, dock, w=None, h=None, min_w=None, min_h=None, ar=None):
        c = uis.WidgetConstraints(
            name="test", z_order=2, dock=dock,
            width=w, min_width=min_w, height=h, aspect_ratio=ar,
        )
        return uis.WidgetRecord(constraints=c)

    def test_bottom_dock_fills_width(self):
        rec = self._make_record("bottom", h=60)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == 0
        assert g.y == 540
        assert g.w == 1024
        assert g.h == 60

    def test_top_dock(self):
        rec = self._make_record("top", h=50)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == 0
        assert g.y == 0
        assert g.w == 1024
        assert g.h == 50

    def test_left_dock_fills_height(self):
        rec = self._make_record("left", w=80)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == 0
        assert g.y == 0
        assert g.w == 80
        assert g.h == 600

    def test_right_dock(self):
        rec = self._make_record("right", w=80)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == 1024 - 80
        assert g.y == 0
        assert g.w == 80
        assert g.h == 600

    def test_center_dock(self):
        rec = self._make_record("center", w=200, h=100)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == (1024 - 200) // 2
        assert g.y == (600 - 100) // 2

    def test_top_left_dock(self):
        rec = self._make_record("top-left", w=100, h=100)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == 0
        assert g.y == 0

    def test_bottom_right_dock(self):
        rec = self._make_record("bottom-right", w=100, h=100)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.x == 1024 - 100
        assert g.y == 600 - 100

    def test_aspect_ratio_with_fixed_height(self):
        rec = self._make_record("center", h=100, ar=16/9)
        g = uis._compute_geometry(rec, 1024, 600)
        assert g.w == int(100 * (16 / 9))


# ---------------------------------------------------------------------------
# _reflow (multi-widget layout)
# ---------------------------------------------------------------------------

class TestReflow:
    def _register(self, name, dock, z=2, w=None, h=None):
        c = uis.WidgetConstraints(name=name, z_order=z, dock=dock, height=h, width=w)
        uis._registry[name] = uis.WidgetRecord(constraints=c)

    def test_single_bottom_widget(self):
        self._register("navbar", "bottom", h=60)
        geoms = uis._reflow()
        g = geoms["navbar"]
        assert g.y == 600 - 60
        assert g.w == 1024

    def test_two_bottom_widgets_stack(self):
        self._register("navbar",     "bottom", z=2, h=60)
        self._register("statusbar",  "bottom", z=2, h=30)
        geoms = uis._reflow()
        assert geoms["navbar"].h == 60
        assert geoms["statusbar"].h == 30
        assert geoms["statusbar"].y < geoms["navbar"].y

    def test_center_fills_remaining(self):
        self._register("navbar", "bottom", h=60)
        self._register("video",  "center")
        geoms = uis._reflow()
        assert geoms["video"].h == 600 - 60
        assert geoms["video"].w == 1024

    def test_empty_registry(self):
        geoms = uis._reflow()
        assert geoms == {}


# ---------------------------------------------------------------------------
# on_widget_register
# ---------------------------------------------------------------------------

class TestOnWidgetRegister:
    def test_valid_registration(self):
        uis.on_widget_register("", {"name": "navbar", "z_order": 2, "dock": "bottom", "height": 60})
        assert "navbar" in uis._registry
        assert uis._registry["navbar"].constraints.dock == "bottom"

    def test_missing_name_ignored(self):
        uis.on_widget_register("", {"z_order": 2, "dock": "bottom"})
        assert len(uis._registry) == 0

    def test_invalid_dock_ignored(self):
        uis.on_widget_register("", {"name": "bad", "z_order": 2, "dock": "invalid_dock"})
        assert "bad" not in uis._registry

    def test_registration_triggers_geometry_publish(self):
        uis.on_widget_register("", {"name": "navbar", "z_order": 2, "dock": "bottom", "height": 60})
        calls_topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.widget.geometry" in calls_topics

    def test_on_request_field_stored(self):
        uis.on_widget_register("", {
            "name": "bt_ui", "z_order": 2, "dock": "center",
            "on_request": True, "menu_order": 1, "icon": "bluetooth",
        })
        c = uis._registry["bt_ui"].constraints
        assert c.on_request is True
        assert c.menu_order == 1
        assert c.icon == "bluetooth"

    def test_on_request_defaults_to_false(self):
        uis.on_widget_register("", {"name": "navbar", "z_order": 2, "dock": "bottom", "height": 60})
        assert uis._registry["navbar"].constraints.on_request is False

    def test_menu_order_default_99(self):
        uis.on_widget_register("", {"name": "widget_x", "z_order": 2, "dock": "center"})
        assert uis._registry["widget_x"].constraints.menu_order == 99


# ---------------------------------------------------------------------------
# dpi_factor in ui.widget.geometry payload
# ---------------------------------------------------------------------------

class TestDpiFactorInGeometry:
    def test_default_dpi_factor_in_payload(self):
        """ui.widget.geometry must include dpi_factor=1.0 by default."""
        uis.on_widget_register("", {"name": "navbar", "z_order": 2, "dock": "bottom", "height": 60})
        geom_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "ui.widget.geometry" and c.args[1].get("name") == "navbar"
        ]
        assert len(geom_calls) >= 1
        payload = geom_calls[-1].args[1]
        assert "dpi_factor" in payload
        assert payload["dpi_factor"] == 1.0

    def test_custom_dpi_factor_in_payload(self):
        """dpi_factor from config must appear in geometry payload."""
        uis._config["dpi_factor"] = 2.0
        uis.on_widget_register("", {"name": "navbar", "z_order": 2, "dock": "bottom", "height": 60})
        geom_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "ui.widget.geometry" and c.args[1].get("name") == "navbar"
        ]
        payload = geom_calls[-1].args[1]
        assert payload["dpi_factor"] == 2.0


# ---------------------------------------------------------------------------
# on_widget_update
# ---------------------------------------------------------------------------

class TestOnWidgetUpdate:
    def _pre_register(self):
        c = uis.WidgetConstraints(name="navbar", z_order=2, dock="bottom", height=60)
        uis._registry["navbar"] = uis.WidgetRecord(constraints=c)

    def test_update_height(self):
        self._pre_register()
        uis.on_widget_update("", {"name": "navbar", "height": 80})
        assert uis._registry["navbar"].constraints.height == 80

    def test_update_unknown_widget_logs_warning(self):
        uis.on_widget_update("", {"name": "ghost", "height": 80})
        _log_mock.warning.assert_called()

    def test_update_missing_name_logs_warning(self):
        uis.on_widget_update("", {"height": 80})
        _log_mock.warning.assert_called()


# ---------------------------------------------------------------------------
# on_widget_unregister
# ---------------------------------------------------------------------------

class TestOnWidgetUnregister:
    def _pre_register(self):
        c = uis.WidgetConstraints(name="navbar", z_order=2, dock="bottom", height=60)
        uis._registry["navbar"] = uis.WidgetRecord(constraints=c)

    def test_unregister_removes_widget(self):
        self._pre_register()
        uis.on_widget_unregister("", {"name": "navbar"})
        assert "navbar" not in uis._registry

    def test_unregister_unknown_logs_warning(self):
        uis.on_widget_unregister("", {"name": "ghost"})
        _log_mock.warning.assert_called()

    def test_unregister_missing_name_logs_warning(self):
        uis.on_widget_unregister("", {})
        _log_mock.warning.assert_called()


# ---------------------------------------------------------------------------
# _hit_test
# ---------------------------------------------------------------------------

class TestHitTest:
    def _add_widget(self, name, x, y, w, h, z=2):
        c = uis.WidgetConstraints(name=name, z_order=z, dock="bottom")
        rec = uis.WidgetRecord(constraints=c, geometry=uis.WidgetGeometry(x=x, y=y, w=w, h=h))
        uis._registry[name] = rec

    def test_hit_inside_widget(self):
        self._add_widget("navbar", 0, 540, 1024, 60)
        assert uis._hit_test(100, 550) == "navbar"

    def test_miss_outside_widget(self):
        self._add_widget("navbar", 0, 540, 1024, 60)
        assert uis._hit_test(100, 100) is None

    def test_higher_z_order_wins(self):
        self._add_widget("bottom",  0, 540, 1024, 60, z=2)
        self._add_widget("overlay", 0, 530, 1024, 80, z=3)
        assert uis._hit_test(100, 550) == "overlay"

    def test_empty_registry_returns_none(self):
        assert uis._hit_test(0, 0) is None


# ---------------------------------------------------------------------------
# on_input_raw
# ---------------------------------------------------------------------------

class TestOnInputRaw:
    def _add_widget(self, name, x, y, w, h):
        c = uis.WidgetConstraints(name=name, z_order=2, dock="bottom")
        rec = uis.WidgetRecord(constraints=c, geometry=uis.WidgetGeometry(x=x, y=y, w=w, h=h))
        uis._registry[name] = rec

    def test_routes_to_correct_widget(self):
        self._add_widget("navbar", 0, 540, 1024, 60)
        uis.on_input_raw("", {"type": "press", "x_global": 200, "y_global": 550, "timestamp": 1})
        calls_topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "input.event.navbar" in calls_topics

    def test_no_route_when_no_hit(self):
        self._add_widget("navbar", 0, 540, 1024, 60)
        _bus_mock.reset_mock()
        uis.on_input_raw("", {"type": "press", "x_global": 200, "y_global": 100, "timestamp": 1})
        calls_topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert not any(t.startswith("input.event.") for t in calls_topics)

    def test_relative_coords_computed(self):
        self._add_widget("navbar", 0, 540, 1024, 60)
        uis.on_input_raw("", {"type": "press", "x_global": 100, "y_global": 550, "timestamp": 1})
        geometry_calls = [
            c for c in _bus_mock.publish.call_args_list
            if c.args[0] == "input.event.navbar"
        ]
        assert len(geometry_calls) == 1
        payload = geometry_calls[0].args[1]
        assert payload["x"] == 100
        assert payload["y"] == 10


# ---------------------------------------------------------------------------
# on_screen_resize
# ---------------------------------------------------------------------------

class TestOnScreenResize:
    def test_updates_screen_dims(self):
        uis.on_screen_resize(1920, 1080)
        assert uis._screen_w == 1920
        assert uis._screen_h == 1080

    def test_reflow_triggered_on_resize(self):
        c = uis.WidgetConstraints(name="navbar", z_order=2, dock="bottom", height=60)
        uis._registry["navbar"] = uis.WidgetRecord(constraints=c)
        uis.on_screen_resize(800, 480)
        calls_topics = [c.args[0] for c in _bus_mock.publish.call_args_list]
        assert "ui.widget.geometry" in calls_topics


# ---------------------------------------------------------------------------
# Boot protocol
# ---------------------------------------------------------------------------

class TestBootProtocol:
    def test_on_system_readytostart_publishes_module_ready(self):
        uis.on_system_readytostart()
        _bus_mock.publish.assert_called_with(
            "system.module_ready",
            {"name": uis.MODULE_NAME, "priority": uis.PRIORITY},
        )

    def test_on_system_start_wrong_priority_ignored(self):
        _bus_mock.reset_mock()
        uis.on_system_start("", {"priority": 99})
        _bus_mock.publish.assert_not_called()

    def test_on_system_stop_calls_bus_stop(self):
        uis._qt_app = None
        uis.on_system_stop("", {})
        _bus_mock.stop.assert_called_once()
