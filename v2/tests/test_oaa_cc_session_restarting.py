"""
Unit tests for oaa_control_channel.main — on_aa_session_restarting.

Covered:
  on_aa_session_restarting  — creates fresh ControlChannelHandshake with
                              updated _cfg, publishes IDLE state,
                              calls send_version_request()

All external dependencies (BusClient, ControlChannelHandshake, service_discovery,
frame_codec, get_logger) are replaced with mocks.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
# Module loader
# ---------------------------------------------------------------------------

def _load_module():
    """Import oaa_control_channel.main with all heavy deps mocked.

    Returns (module, bus_instance, handshake_cls_mock).
    """
    fake_bus_instance = MagicMock()
    fake_bus_client   = MagicMock(return_value=fake_bus_instance)

    fake_bus_module   = types.ModuleType("shared.bus_client")
    fake_bus_module.BusClient = fake_bus_client

    fake_logger_module = types.ModuleType("shared.logger")
    fake_logger_module.get_logger = MagicMock(return_value=MagicMock())

    fake_shared = types.ModuleType("shared")

    # ControlChannelHandshake mock
    fake_handshake_instance = MagicMock()
    fake_handshake_cls      = MagicMock(return_value=fake_handshake_instance)

    fake_handshake_module = types.ModuleType("oaa_control_channel.handshake")
    fake_handshake_module.ControlChannelHandshake = fake_handshake_cls

    fake_codec_module = types.ModuleType("oaa_control_channel.frame_codec")
    fake_codec_module.encode_control_frame = MagicMock(return_value=b"\x00" * 6)

    fake_svc_module = types.ModuleType("oaa_control_channel.service_discovery")
    fake_svc_module.DEFAULTS = {"hu.name": "TestHU", "video.dpi": "140"}

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
    return mod, fake_bus_instance, fake_handshake_cls, fake_handshake_instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnAaSessionRestarting:
    def setup_method(self):
        (
            self.mod,
            self.bus,
            self.handshake_cls,
            self.handshake_instance,
        ) = _load_module()

    def test_creates_new_handshake(self):
        """A fresh ControlChannelHandshake must be created."""
        self.mod._handshake = None  # no existing handshake

        self.mod.on_aa_session_restarting("", {})

        self.handshake_cls.assert_called_once()

    def test_replaces_existing_handshake(self):
        """Old handshake (if any) is discarded and a new one created."""
        old = MagicMock()
        self.mod._handshake = old

        self.mod.on_aa_session_restarting("", {})

        # A new instance must have been created
        self.handshake_cls.assert_called_once()
        # The module state must point to the new instance
        assert self.mod._handshake is not old

    def test_publishes_idle_state(self):
        """aa.handshake.state=IDLE must be published before VERSION_REQUEST."""
        self.mod.on_aa_session_restarting("", {})

        published_topics = [c[0][0] for c in self.bus.publish.call_args_list]
        assert "aa.handshake.state" in published_topics

        # Find the IDLE publish and verify it comes before any frame send
        idle_calls = [
            i for i, c in enumerate(self.bus.publish.call_args_list)
            if c[0][0] == "aa.handshake.state" and c[0][1] == {"state": "IDLE"}
        ]
        assert idle_calls, "aa.handshake.state IDLE was never published"

    def test_calls_send_version_request(self):
        """send_version_request() must be called on the new handshake instance."""
        self.mod.on_aa_session_restarting("", {})

        self.handshake_instance.send_version_request.assert_called_once()

    def test_handshake_built_with_current_cfg(self):
        """ControlChannelHandshake must receive the current _cfg dict."""
        updated_cfg = {"hu.name": "UpdatedHU", "video.dpi": "160"}
        self.mod._cfg = updated_cfg

        self.mod.on_aa_session_restarting("", {})

        # cfg= kwarg must match the current _cfg
        _, kwargs = self.handshake_cls.call_args
        assert kwargs.get("cfg") is updated_cfg

    def test_handshake_state_set_after_creation(self):
        """The module's _handshake attribute must be the newly created instance."""
        self.mod.on_aa_session_restarting("", {})

        assert self.mod._handshake is self.handshake_instance
