"""
test_base_channel_module.py — Unit tests for BaseChannelModule.

Coverage targets (§1.2 TEST_SUITE_ARCHITECTURE):
  1. Boot protocol (readytostart → ready_to_start → start → ready → stop → stopped)
  2. _try_publish_ready() — all 4 gate conditions (_init_done, config_ok, _is_ready, channel_config)
  3. AA channel lifecycle (_on_aa_channel_open, _on_aa_channel_close) with channel_id filtering
  4. _on_aa_frame — happy path, malformed payload, guard _channel_open
  5. send_frame() — publishes correct aa.frame.send payload
  6. on_config_loaded / on_config_changed — schema merging and unknown key guard
  7. _on_channel_manager_module_start — priority filtering
  8. _on_channel_manager_module_stop — cleanup + module_stopped + bus.stop

Test strategy:
  - _MockBus from conftest.py is injected via monkeypatching BusClient and ConfigClient
    constructors BEFORE BaseChannelModule.__init__ runs.
  - _ConcreteModule is a minimal concrete subclass that records calls to abstract methods.
  - channel_config is set directly on the instance after construction to bypass CLI parsing.
  - CLI arg parsing (_CLI_ARGS) is patched to control --channel-id / --sdr-bytes-hex.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap so the module under test is importable without install
# ---------------------------------------------------------------------------
_V2 = Path(__file__).parents[3]   # v2/
_MODULES = _V2 / "modules"
_CHANNEL_MODS = _MODULES / "channel_modules"
for _p in (_V2, _MODULES, _CHANNEL_MODS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---------------------------------------------------------------------------
# Helpers to build a testable instance without real ZMQ / CLI args
# ---------------------------------------------------------------------------

def _make_mock_bus() -> MagicMock:
    """Return a MagicMock that quacks like BusClient."""
    bus = MagicMock()
    bus.publish = MagicMock()
    bus.subscribe = MagicMock()
    bus.stop = MagicMock()
    bus.start = MagicMock(return_value=MagicMock())  # returns a thread-like
    return bus


def _make_mock_cfg() -> MagicMock:
    """Return a MagicMock that quacks like ConfigClient."""
    cfg = MagicMock()
    cfg.register = MagicMock()
    cfg.get = MagicMock()
    return cfg


def _make_fake_cli_args(
    module_name: str = "test_module",
    channel_id: int = 7,
    sdr_bytes_hex: str = "",
):
    """Return a namespace that mimics _CLI_ARGS."""
    ns = types.SimpleNamespace(
        module_name=module_name,
        channel_id=channel_id,
        sdr_bytes_hex=sdr_bytes_hex,
    )
    return ns


def _build_module(
    channel_id: int = 7,
    extra_schema: dict | None = None,
    is_ready_result: bool = True,
) -> tuple["_ConcreteModule", MagicMock, MagicMock]:
    """
    Build a _ConcreteModule with fully mocked BusClient and ConfigClient.
    Returns (module, mock_bus, mock_cfg).

    channel_config is set to a non-None sentinel so the readiness gate
    based on channel_config passes by default.
    """
    mock_bus = _make_mock_bus()
    mock_cfg = _make_mock_cfg()
    fake_cli = _make_fake_cli_args(channel_id=channel_id)

    import channel_modules.base_channel_module as bcm_mod

    with (
        patch.object(bcm_mod, "_CLI_ARGS", fake_cli),
        patch("channel_modules.base_channel_module.BusClient", return_value=mock_bus),
        patch("channel_modules.base_channel_module.ConfigClient", return_value=mock_cfg),
        patch("channel_modules.base_channel_module.get_logger", return_value=MagicMock()),
        patch("channel_modules.base_channel_module.channel_config_from_sdr", return_value=None),
    ):
        module = _ConcreteModule(
            extra_schema=extra_schema,
            is_ready_result=is_ready_result,
        )

    # Inject a non-None channel_config so readiness gate condition #4 passes.
    module.channel_config = {"channel_id": channel_id}
    return module, mock_bus, mock_cfg


# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------

class _ConcreteModule:
    """
    Concrete subclass of BaseChannelModule used exclusively in tests.

    Constructed after patching to avoid real BusClient / ConfigClient.
    Records all abstract method calls for assertion.
    """

    def __new__(cls, extra_schema=None, is_ready_result=True):
        # Defer actual subclass creation until patching is active.
        # We use __new__ + __init__ separation to support the with-patch pattern.
        from channel_modules.base_channel_module import BaseChannelModule

        class _Inner(BaseChannelModule):
            MODULE_NAME = "test_module"
            CHANNEL_ID  = -1
            PRIORITY    = 1

            def __init__(self, extra_schema=None, is_ready_result=True) -> None:
                self._extra_schema     = extra_schema or {}
                self._is_ready_result  = is_ready_result
                self.open_calls:  list = []
                self.close_calls: list = []
                self.frame_calls: list = []
                super().__init__()

            def get_schema(self) -> dict:
                return self._extra_schema

            def _is_ready(self) -> bool:
                return self._is_ready_result

            def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
                self.open_calls.append((channel_id, descriptor))

            def on_channel_close(self, channel_id: int) -> None:
                self.close_calls.append(channel_id)

            def on_frame(self, channel_id: int, message_id: int, encrypted: bool, data: bytes) -> None:
                self.frame_calls.append((channel_id, message_id, encrypted, data))

        return _Inner(extra_schema=extra_schema, is_ready_result=is_ready_result)


# ============================================================================
# TEST CLASSES
# ============================================================================


class TestBootProtocol:
    """Boot protocol: readytostart → ready_to_start → start → ready → stop → stopped."""

    @pytest.mark.unit
    def test_readytostart_publishes_ready_to_start(self):
        module, bus, _ = _build_module()
        module._on_channel_manager_module_readytostart()
        bus.publish.assert_any_call(
            "channel_manager.module_ready_to_start",
            {"name": "test_module", "priority": 1},
        )

    @pytest.mark.unit
    def test_module_start_wrong_priority_is_ignored(self):
        module, bus, _ = _build_module()
        # Set _init_done manually to detect if it changes
        module._init_done = False
        module._on_channel_manager_module_start(
            "channel_manager.module_start", {"priority": 99}
        )
        assert not module._init_done

    @pytest.mark.unit
    def test_module_start_correct_priority_calls_init(self):
        module, bus, _ = _build_module()
        init_called = []

        def _mock_init():
            init_called.append(True)

        module._init = _mock_init
        module._on_channel_manager_module_start(
            "channel_manager.module_start", {"priority": 1}
        )
        assert init_called, "_init() should be called on module_start"
        assert module._init_done is True

    @pytest.mark.unit
    def test_module_stop_calls_cleanup_and_publishes_stopped(self):
        module, bus, _ = _build_module()
        cleanup_called = []
        module._cleanup = lambda: cleanup_called.append(True)

        module._on_channel_manager_module_stop(
            "channel_manager.module_stop", {}
        )
        assert cleanup_called
        bus.publish.assert_any_call(
            "channel_manager.module_stopped", {"name": "test_module"}
        )

    @pytest.mark.unit
    def test_module_stop_calls_bus_stop(self):
        module, bus, _ = _build_module()
        module._on_channel_manager_module_stop("channel_manager.module_stop", {})
        bus.stop.assert_called_once()


class TestReadinessGate:
    """_try_publish_ready() — all 4 gate conditions."""

    @pytest.mark.unit
    def test_ready_not_published_without_init_done(self):
        module, bus, _ = _build_module()
        # channel_config is set, but _init_done is False
        module._init_done = False
        module._config_loaded = True
        module._try_publish_ready()
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "channel_manager.module_ready" not in topics

    @pytest.mark.unit
    def test_ready_not_published_without_channel_config(self):
        module, bus, _ = _build_module()
        module._init_done = True
        module._config_loaded = True
        module.channel_config = None  # gate condition #4 fails
        module._try_publish_ready()
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "channel_manager.module_ready" not in topics

    @pytest.mark.unit
    def test_ready_not_published_when_is_ready_false(self):
        module, bus, _ = _build_module(is_ready_result=False)
        module._init_done = True
        module._config_loaded = True
        module._try_publish_ready()
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "channel_manager.module_ready" not in topics

    @pytest.mark.unit
    def test_ready_not_published_without_config_loaded_when_schema_exists(self):
        from shared.config_schema import field_int
        module, bus, _ = _build_module(extra_schema={"vol": field_int(default=80)})
        module._init_done = True
        module._config_loaded = False  # config not yet received
        module._try_publish_ready()
        topics = [c.args[0] for c in bus.publish.call_args_list]
        assert "channel_manager.module_ready" not in topics

    @pytest.mark.unit
    def test_ready_published_when_all_conditions_met(self):
        module, bus, _ = _build_module()
        module._init_done = True
        module._config_loaded = True
        module._try_publish_ready()
        bus.publish.assert_any_call(
            "channel_manager.module_ready",
            {"name": "test_module", "priority": 1},
        )

    @pytest.mark.unit
    def test_ready_published_only_once(self):
        module, bus, _ = _build_module()
        module._init_done = True
        module._config_loaded = True
        module._try_publish_ready()
        module._try_publish_ready()
        module._try_publish_ready()
        ready_calls = [
            c for c in bus.publish.call_args_list
            if c.args[0] == "channel_manager.module_ready"
        ]
        assert len(ready_calls) == 1

    @pytest.mark.unit
    def test_ready_published_without_schema_ignores_config_loaded(self):
        """Module with empty schema: config_ok is always True."""
        module, bus, _ = _build_module(extra_schema=None)  # no schema
        module._init_done = True
        module._config_loaded = False  # irrelevant — no schema
        module._try_publish_ready()
        bus.publish.assert_any_call(
            "channel_manager.module_ready",
            {"name": "test_module", "priority": 1},
        )


class TestAAChannelLifecycle:
    """_on_aa_channel_open / _on_aa_channel_close with channel_id filtering."""

    @pytest.mark.unit
    def test_channel_open_matching_id_calls_on_channel_open(self):
        module, _, _ = _build_module(channel_id=7)
        module._on_aa_channel_open(
            "aa.channel.open", {"channel_id": 7, "av_type": "video"}
        )
        assert module._channel_open is True
        assert len(module.open_calls) == 1
        ch, desc = module.open_calls[0]
        assert ch == 7
        assert desc["av_type"] == "video"

    @pytest.mark.unit
    def test_channel_open_wrong_id_is_ignored(self):
        module, _, _ = _build_module(channel_id=7)
        module._on_aa_channel_open(
            "aa.channel.open", {"channel_id": 99}
        )
        assert module._channel_open is False
        assert module.open_calls == []

    @pytest.mark.unit
    def test_channel_close_matching_id_calls_on_channel_close(self):
        module, _, _ = _build_module(channel_id=7)
        # First open it
        module._on_aa_channel_open("aa.channel.open", {"channel_id": 7})
        module._on_aa_channel_close("aa.channel.close", {"channel_id": 7})
        assert module._channel_open is False
        assert module.close_calls == [7]

    @pytest.mark.unit
    def test_channel_close_wrong_id_is_ignored(self):
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = True  # pretend it was open
        module._on_aa_channel_close("aa.channel.close", {"channel_id": 99})
        assert module._channel_open is True  # unchanged
        assert module.close_calls == []


class TestAAFrame:
    """_on_aa_frame — happy path, malformed payload, channel_open guard."""

    @pytest.mark.unit
    def test_frame_dispatched_when_channel_open(self):
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = True
        payload = {
            "channel_id": 7,
            "message_id": 0x0401,
            "encrypted":  True,
            "payload_hex": b"hello".hex(),
        }
        module._on_aa_frame("aa.frame.ch7", payload)
        assert len(module.frame_calls) == 1
        ch, mid, enc, data = module.frame_calls[0]
        assert ch == 7
        assert mid == 0x0401
        assert enc is True
        assert data == b"hello"

    @pytest.mark.unit
    def test_frame_dropped_when_channel_not_open(self):
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = False
        payload = {
            "channel_id": 7,
            "message_id": 0x0401,
            "encrypted":  False,
            "payload_hex": b"data".hex(),
        }
        module._on_aa_frame("aa.frame.ch7", payload)
        assert module.frame_calls == []

    @pytest.mark.unit
    def test_frame_malformed_missing_key_is_dropped(self):
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = True
        # Missing 'payload_hex'
        payload = {"channel_id": 7, "message_id": 0x0401}
        module._on_aa_frame("aa.frame.ch7", payload)
        assert module.frame_calls == []

    @pytest.mark.unit
    def test_frame_malformed_bad_hex_is_dropped(self):
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = True
        payload = {
            "channel_id": 7,
            "message_id": 0x0401,
            "encrypted":  False,
            "payload_hex": "NOT_VALID_HEX!!!",
        }
        module._on_aa_frame("aa.frame.ch7", payload)
        assert module.frame_calls == []

    @pytest.mark.unit
    def test_frame_encrypted_false_echoed_correctly(self):
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = True
        payload = {
            "channel_id": 7,
            "message_id": 0x0100,
            "encrypted":  False,
            "payload_hex": b"".hex(),
        }
        module._on_aa_frame("aa.frame.ch7", payload)
        assert module.frame_calls[0][2] is False  # encrypted echoed

    @pytest.mark.unit
    def test_frame_defaults_encrypted_to_false_when_missing(self):
        """encrypted key is optional — defaults to False."""
        module, _, _ = _build_module(channel_id=7)
        module._channel_open = True
        payload = {
            "channel_id": 7,
            "message_id": 0x0100,
            "payload_hex": b"".hex(),
            # no 'encrypted' key
        }
        module._on_aa_frame("aa.frame.ch7", payload)
        assert module.frame_calls[0][2] is False


class TestSendFrame:
    """send_frame() — publishes correct aa.frame.send payload."""

    @pytest.mark.unit
    def test_send_frame_publishes_correct_topic(self):
        module, bus, _ = _build_module(channel_id=7)
        module.send_frame(0x0400, b"\x08\x01")
        bus.publish.assert_any_call(
            "aa.frame.send",
            {
                "channel_id":  7,
                "message_id":  0x0400,
                "payload_hex": b"\x08\x01".hex(),
                "encrypted":   True,
            },
        )

    @pytest.mark.unit
    def test_send_frame_encrypted_false(self):
        module, bus, _ = _build_module(channel_id=7)
        module.send_frame(0x0400, b"", encrypted=False)
        call_kwargs = bus.publish.call_args_list[-1]
        published = call_kwargs.args[1]
        assert published["encrypted"] is False

    @pytest.mark.unit
    def test_send_frame_empty_payload(self):
        module, bus, _ = _build_module(channel_id=7)
        module.send_frame(0x0001, b"")
        call_kwargs = bus.publish.call_args_list[-1]
        published = call_kwargs.args[1]
        assert published["payload_hex"] == ""
        assert published["channel_id"] == 7

    @pytest.mark.unit
    def test_send_frame_uses_module_channel_id(self):
        """channel_id in the published payload must equal self.CHANNEL_ID."""
        module, bus, _ = _build_module(channel_id=42)
        module.send_frame(0x0001, b"abc")
        call_kwargs = bus.publish.call_args_list[-1]
        published = call_kwargs.args[1]
        assert published["channel_id"] == 42


class TestConfig:
    """on_config_loaded / on_config_changed — schema merging and validation."""

    @pytest.mark.unit
    def test_on_config_loaded_applies_persisted_values(self):
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module.on_config_loaded({"max_unacked": 4})
        assert module._config["max_unacked"] == 4
        assert module._config_loaded is True

    @pytest.mark.unit
    def test_on_config_loaded_empty_uses_defaults(self):
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module.on_config_loaded({})
        assert module._config["max_unacked"] == 1
        assert module._config_loaded is True

    @pytest.mark.unit
    def test_on_config_loaded_ignores_extra_keys(self):
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module.on_config_loaded({"max_unacked": 2, "unknown_key": "garbage"})
        assert "unknown_key" not in module._config
        assert module._config["max_unacked"] == 2

    @pytest.mark.unit
    def test_on_config_loaded_ignores_structural_values(self):
        """dict / list values in persisted config should be ignored (they come from nested proto)."""
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module.on_config_loaded({"max_unacked": {"nested": "dict"}})
        # structural value is ignored — default is preserved
        assert module._config["max_unacked"] == 1

    @pytest.mark.unit
    def test_on_config_changed_updates_known_key(self):
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module._config["max_unacked"] = 1
        module.on_config_changed("max_unacked", 8)
        assert module._config["max_unacked"] == 8

    @pytest.mark.unit
    def test_on_config_changed_ignores_unknown_key(self):
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module.on_config_changed("nonexistent", "value")
        assert "nonexistent" not in module._config

    @pytest.mark.unit
    def test_on_config_changed_ignores_structural_value(self):
        """dict / list values should be silently rejected by on_config_changed."""
        from shared.config_schema import field_int
        module, _, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module._config["max_unacked"] = 3
        module.on_config_changed("max_unacked", {"nested": "evil"})
        assert module._config["max_unacked"] == 3  # unchanged

    @pytest.mark.unit
    def test_on_config_loaded_triggers_try_publish_ready(self):
        """on_config_loaded should call _try_publish_ready() — integration of the two."""
        from shared.config_schema import field_int
        module, bus, _ = _build_module(extra_schema={"max_unacked": field_int(default=1)})
        module._init_done = True
        module.on_config_loaded({"max_unacked": 2})
        # channel_manager.module_ready should have been published
        bus.publish.assert_any_call(
            "channel_manager.module_ready",
            {"name": "test_module", "priority": 1},
        )
