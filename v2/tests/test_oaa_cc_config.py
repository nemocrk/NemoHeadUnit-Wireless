"""
Unit tests for oaa_control_channel.main — config handlers.

Covered:
  on_config_response  — populates _cfg, publishes system.ready;
                        ignores payloads not addressed to this module
  on_config_changed   — updates _cfg key, nulls _handshake,
                        publishes aa.session.shutdown + aa.session.restart
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_HERE    = Path(__file__).parent
_V2      = _HERE.parent
_MODULES = _V2 / "modules"

for p in (_V2, _MODULES):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


# ---------------------------------------------------------------------------
# Module loader (reused from test_oaa_cc_session_restarting pattern)
# ---------------------------------------------------------------------------

def _load_module():
    fake_bus_instance = MagicMock()
    fake_bus_client   = MagicMock(return_value=fake_bus_instance)

    fake_bus_module = types.ModuleType("shared.bus_client")
    fake_bus_module.BusClient = fake_bus_client

    fake_logger_module = types.ModuleType("shared.logger")
    fake_logger_module.get_logger = MagicMock(return_value=MagicMock())

    fake_shared = types.ModuleType("shared")

    fake_handshake_instance = MagicMock()
    fake_handshake_cls      = MagicMock(return_value=fake_handshake_instance)
    fake_handshake_module   = types.ModuleType("oaa_control_channel.handshake")
    fake_handshake_module.ControlChannelHandshake = fake_handshake_cls

    fake_codec_module = types.ModuleType("oaa_control_channel.frame_codec")
    fake_codec_module.encode_control_frame = MagicMock(return_value=b"\x00" * 6)

    fake_svc_module = types.ModuleType("oaa_control_channel.service_discovery")
    fake_svc_module.DEFAULTS = {"hu.name": "NemoHeadUnit", "video.dpi": "140"}

    fake_oaa_pkg = types.ModuleType("oaa_control_channel")

    mocks = {
        "shared":                              fake_shared,
        "shared.bus_client":                   fake_bus_module,
        "shared.logger":                       fake_logger_module,
        "oaa_control_channel":                 fake_oaa_pkg,
        "oaa_control_channel.handshake":       fake_handshake_module,
        "oaa_control_channel.frame_codec":     fake_codec_module,
        "oaa_control_channel.service_discovery": fake_svc_module,
    }

    for key in list(sys.modules.keys()):
        if key in mocks or key == "oaa_control_channel.main":
            del sys.modules[key]

    sys.modules.update(mocks)

    import oaa_control_channel.main as mod
    return mod, fake_bus_instance


# ---------------------------------------------------------------------------
# Tests: on_config_response
# ---------------------------------------------------------------------------

class TestOnConfigResponse:
    def setup_method(self):
        self.mod, self.bus = _load_module()
        self.mod._cfg_loaded = False
        self.mod._cfg = dict(self.mod._CFG_DEFAULTS) if hasattr(self.mod, "_CFG_DEFAULTS") else {}

    def test_populates_cfg_from_response(self):
        """_cfg must be updated with values from config.response."""
        self.mod.on_config_response("", {
            "module":    "oaa_control_channel",
            "requester": "oaa_control_channel",
            "config":    {"hu.name": "MyHU", "video.dpi": "200"},
        })
        assert self.mod._cfg["hu.name"]   == "MyHU"
        assert self.mod._cfg["video.dpi"] == "200"

    def test_sets_cfg_loaded_flag(self):
        """_cfg_loaded must be True after a valid response."""
        self.mod.on_config_response("", {
            "module":    "oaa_control_channel",
            "requester": "oaa_control_channel",
            "config":    {"hu.name": "X"},
        })
        assert self.mod._cfg_loaded is True

    def test_publishes_system_ready(self):
        """system.ready must be published after config is loaded."""
        self.mod.on_config_response("", {
            "module":    "oaa_control_channel",
            "requester": "oaa_control_channel",
            "config":    {"hu.name": "X"},
        })
        topics = [c[0][0] for c in self.bus.publish.call_args_list]
        assert "system.ready" in topics

    def test_ignores_response_for_different_module(self):
        """Payloads addressed to another module must be silently ignored."""
        self.mod.on_config_response("", {
            "module":    "other_module",
            "requester": "other_module",
            "config":    {"hu.name": "ShouldNotApply"},
        })
        assert self.mod._cfg_loaded is False
        self.bus.publish.assert_not_called()

    def test_ignores_response_for_different_requester(self):
        """Payloads with a different requester must be silently ignored."""
        self.mod.on_config_response("", {
            "module":    "oaa_control_channel",
            "requester": "someone_else",
            "config":    {"hu.name": "ShouldNotApply"},
        })
        assert self.mod._cfg_loaded is False
        self.bus.publish.assert_not_called()

    def test_handles_empty_config_gracefully(self):
        """Empty config dict must still set _cfg_loaded and publish system.ready."""
        self.mod.on_config_response("", {
            "module":    "oaa_control_channel",
            "requester": "oaa_control_channel",
            "config":    {},
        })
        assert self.mod._cfg_loaded is True
        topics = [c[0][0] for c in self.bus.publish.call_args_list]
        assert "system.ready" in topics


# ---------------------------------------------------------------------------
# Tests: on_config_changed
# ---------------------------------------------------------------------------

class TestOnConfigChanged:
    def setup_method(self):
        self.mod, self.bus = _load_module()
        self.mod._cfg = {"hu.name": "NemoHeadUnit", "video.dpi": "140"}

    def test_updates_cfg_key(self):
        """_cfg must reflect the new value after on_config_changed."""
        self.mod.on_config_changed("", {
            "module": "oaa_control_channel",
            "key":    "hu.name",
            "value":  "NewName",
        })
        assert self.mod._cfg["hu.name"] == "NewName"

    def test_publishes_session_restart(self):
        """aa.session.restart must be published."""
        self.mod.on_config_changed("", {
            "module": "oaa_control_channel",
            "key":    "video.dpi",
            "value":  "160",
        })
        topics = [c[0][0] for c in self.bus.publish.call_args_list]
        assert "aa.session.restart" in topics

    def test_nulls_handshake_when_active(self):
        """If a handshake is active, it must be set to None."""
        self.mod._handshake = MagicMock()
        self.mod.on_config_changed("", {
            "module": "oaa_control_channel",
            "key":    "hu.name",
            "value":  "X",
        })
        assert self.mod._handshake is None

    def test_publishes_session_shutdown_when_handshake_active(self):
        """aa.session.shutdown must be published when a handshake was active."""
        self.mod._handshake = MagicMock()
        self.mod.on_config_changed("", {
            "module": "oaa_control_channel",
            "key":    "hu.name",
            "value":  "X",
        })
        topics = [c[0][0] for c in self.bus.publish.call_args_list]
        assert "aa.session.shutdown" in topics

    def test_no_shutdown_when_no_handshake(self):
        """aa.session.shutdown must NOT be published if no handshake was active."""
        self.mod._handshake = None
        self.mod.on_config_changed("", {
            "module": "oaa_control_channel",
            "key":    "hu.name",
            "value":  "X",
        })
        topics = [c[0][0] for c in self.bus.publish.call_args_list]
        assert "aa.session.shutdown" not in topics

    def test_ignores_change_for_different_module(self):
        """Changes for a different module must be silently ignored."""
        original_cfg = dict(self.mod._cfg)
        self.mod.on_config_changed("", {
            "module": "other_module",
            "key":    "hu.name",
            "value":  "ShouldNotApply",
        })
        assert self.mod._cfg == original_cfg
        self.bus.publish.assert_not_called()
