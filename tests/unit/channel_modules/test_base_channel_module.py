"""
test_base_channel_module.py — Unit tests for BaseChannelModule.

Coverage targets (§1.3 TEST_SUITE_ARCHITECTURE):
  1.  Boot protocol
        a. _on_channel_manager_module_readytostart publishes module_ready_to_start
        b. _on_channel_manager_module_start (matching priority) calls _init() and _try_publish_ready
        c. _on_channel_manager_module_start (wrong priority) is a no-op
        d. _on_channel_manager_module_stop calls _cleanup(), publishes module_stopped, calls bus.stop

  2.  Readiness gate (_try_publish_ready)
        a. channel_manager.module_ready published only when ALL conditions met:
             init_done + config_loaded (if schema) + _is_ready() + channel_config not None
        b. Not published twice (idempotent)
        c. channel_config=None blocks publication even when all else is ready
        d. Schema-less module (get_schema=={}) does not wait for config_loaded
        e. _is_ready()==False blocks publication
        f. config_loaded=False blocks publication when schema non-empty

  3.  Config callbacks
        a. on_config_loaded merges persisted values over defaults; unknown/structural keys dropped
        b. on_config_loaded with empty dict keeps schema defaults
        c. on_config_changed updates _config[key]
        d. on_config_changed ignores unknown keys
        e. on_config_changed rejects dict/list values

  4.  AA channel lifecycle
        a. _on_aa_channel_open with matching channel_id sets _channel_open, calls on_channel_open
        b. _on_aa_channel_open with wrong channel_id is a no-op
        c. _on_aa_channel_close with matching channel_id clears _channel_open, calls on_channel_close
        d. _on_aa_channel_close with wrong channel_id is a no-op

  5.  Frame dispatch (_on_aa_frame)
        a. Frame delivered to on_frame when channel is open
        b. Frame dropped (warning) when channel is not open
        c. Malformed payload (missing keys) does not raise
        d. payload_hex decoded to bytes before on_frame
        e. message_id and encrypted forwarded correctly

  6.  send_frame
        a. Publishes on aa.frame.send with correct channel_id, message_id, payload_hex
        b. encrypted=True by default
        c. proto_body serialised as hex (no message_id prepended)

  7.  MODULE_NAME / CHANNEL_ID override via _CLI_ARGS
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------

class _ConcreteModule:  # built fresh inside each helper to avoid class-level state
    pass


def _make_concrete_class(schema: dict | None = None, is_ready_val: bool = True):
    """Return a fresh ConcreteModule class each call to avoid shared class state."""
    from channel_modules.base_channel_module import BaseChannelModule
    from shared.config_schema import field_int

    _schema = schema if schema is not None else {"max_unacked": field_int(default=1, min=1, max=16)}
    _ready  = is_ready_val

    class ConcreteModule(BaseChannelModule):
        MODULE_NAME = "test_channel"
        CHANNEL_ID  = 5
        PRIORITY    = 1

        on_channel_open  = MagicMock()
        on_channel_close = MagicMock()
        on_frame         = MagicMock()

        def get_schema(self) -> dict:
            return dict(_schema)

        def _is_ready(self) -> bool:
            return _ready

    return ConcreteModule


def _build_module(
    schema:          dict | None = None,
    is_ready_val:    bool        = True,
    channel_config:  dict | None = None,
    channel_id:      int         = 5,
) -> tuple[Any, MagicMock]:
    """
    Instantiate a mocked ConcreteModule.
    Returns (module, mock_bus).
    """
    import channel_modules.base_channel_module as bcm_mod

    mock_bus = MagicMock()
    mock_bus.publish   = MagicMock()
    mock_bus.subscribe = MagicMock()
    mock_bus.stop      = MagicMock()
    mock_bus.start     = MagicMock(return_value=MagicMock())

    fake_cli = types.SimpleNamespace(
        module_name=None,
        channel_id=channel_id,
        sdr_bytes_hex="",
    )

    ConcreteModule = _make_concrete_class(schema=schema, is_ready_val=is_ready_val)

    with (
        patch.object(bcm_mod, "_CLI_ARGS", fake_cli),
        patch("channel_modules.base_channel_module.BusClient",   return_value=mock_bus),
        patch("channel_modules.base_channel_module.ConfigClient", return_value=MagicMock()),
        patch("channel_modules.base_channel_module.get_logger",   return_value=MagicMock()),
        patch("channel_modules.base_channel_module.channel_config_from_sdr", return_value=None),
    ):
        module = ConcreteModule()

    # Inject channel_config
    module.channel_config = channel_config or {"channel_id": channel_id, "av_channel": {}}
    # Reset mock call history accumulated during __init__
    mock_bus.publish.reset_mock()
    return module, mock_bus


# Helper: first call args for a given topic
def _published(bus: MagicMock, topic: str) -> list:
    return [
        c.args[1] for c in bus.publish.call_args_list
        if c.args[0] == topic
    ]


# ============================================================================
# TEST CLASSES
# ============================================================================


class TestBootProtocol:

    @pytest.mark.unit
    def test_readytostart_publishes_ready_to_start(self):
        module, bus = _build_module()
        module._on_channel_manager_module_readytostart()
        payloads = _published(bus, "channel_manager.module_ready_to_start")
        assert payloads
        assert payloads[0]["name"]     == "test_channel"
        assert payloads[0]["priority"] == 1

    @pytest.mark.unit
    def test_module_start_matching_priority_calls_init(self):
        module, _ = _build_module()
        init_called = []
        module._init = lambda: init_called.append(True)
        module._on_channel_manager_module_start("", {"priority": 1})
        assert init_called

    @pytest.mark.unit
    def test_module_start_wrong_priority_is_noop(self):
        module, bus = _build_module()
        init_called = []
        module._init = lambda: init_called.append(True)
        module._on_channel_manager_module_start("", {"priority": 99})
        assert not init_called
        assert not _published(bus, "channel_manager.module_ready")

    @pytest.mark.unit
    def test_module_stop_calls_cleanup(self):
        module, _ = _build_module()
        cleanup_called = []
        module._cleanup = lambda: cleanup_called.append(True)
        module._on_channel_manager_module_stop("", {})
        assert cleanup_called

    @pytest.mark.unit
    def test_module_stop_publishes_module_stopped(self):
        module, bus = _build_module()
        module._cleanup = lambda: None
        module._on_channel_manager_module_stop("", {})
        payloads = _published(bus, "channel_manager.module_stopped")
        assert payloads
        assert payloads[0]["name"] == "test_channel"

    @pytest.mark.unit
    def test_module_stop_calls_bus_stop(self):
        module, bus = _build_module()
        module._cleanup = lambda: None
        module._on_channel_manager_module_stop("", {})
        bus.stop.assert_called_once()


class TestReadinessGate:

    @pytest.mark.unit
    def test_ready_published_when_all_conditions_met(self):
        module, bus = _build_module()
        module._init_done     = True
        module._config_loaded = True
        module._try_publish_ready()
        payloads = _published(bus, "channel_manager.module_ready")
        assert payloads
        assert payloads[0]["name"] == "test_channel"

    @pytest.mark.unit
    def test_ready_not_published_twice(self):
        module, bus = _build_module()
        module._init_done     = True
        module._config_loaded = True
        module._try_publish_ready()
        module._try_publish_ready()  # second call must be no-op
        assert len(_published(bus, "channel_manager.module_ready")) == 1

    @pytest.mark.unit
    def test_ready_blocked_when_channel_config_none(self):
        module, bus = _build_module()
        module.channel_config = None
        module._init_done     = True
        module._config_loaded = True
        module._try_publish_ready()
        assert not _published(bus, "channel_manager.module_ready")

    @pytest.mark.unit
    def test_ready_blocked_when_init_not_done(self):
        module, bus = _build_module()
        module._init_done     = False
        module._config_loaded = True
        module._try_publish_ready()
        assert not _published(bus, "channel_manager.module_ready")

    @pytest.mark.unit
    def test_ready_blocked_when_config_not_loaded_with_schema(self):
        from shared.config_schema import field_int
        module, bus = _build_module(schema={"key": field_int(default=0)})
        module._init_done     = True
        module._config_loaded = False   # schema exists but config not loaded yet
        module._try_publish_ready()
        assert not _published(bus, "channel_manager.module_ready")

    @pytest.mark.unit
    def test_ready_published_without_config_load_when_no_schema(self):
        module, bus = _build_module(schema={})
        module._init_done     = True
        module._config_loaded = False   # no schema → config_ok is True regardless
        module._try_publish_ready()
        assert _published(bus, "channel_manager.module_ready")

    @pytest.mark.unit
    def test_ready_blocked_when_is_ready_false(self):
        module, bus = _build_module(is_ready_val=False)
        module._init_done     = True
        module._config_loaded = True
        module._try_publish_ready()
        assert not _published(bus, "channel_manager.module_ready")

    @pytest.mark.unit
    def test_ready_payload_has_priority(self):
        module, bus = _build_module()
        module._init_done     = True
        module._config_loaded = True
        module._try_publish_ready()
        payloads = _published(bus, "channel_manager.module_ready")
        assert payloads[0]["priority"] == 1


class TestConfigCallbacks:

    @pytest.mark.unit
    def test_config_loaded_merges_persisted_values(self):
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_loaded({"max_unacked": 8})
        assert module._config["max_unacked"] == 8

    @pytest.mark.unit
    def test_config_loaded_empty_dict_keeps_defaults(self):
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_loaded({})
        assert module._config["max_unacked"] == 1  # default

    @pytest.mark.unit
    def test_config_loaded_unknown_key_ignored(self):
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_loaded({"unknown_key": 99, "max_unacked": 2})
        assert "unknown_key" not in module._config
        assert module._config["max_unacked"] == 2

    @pytest.mark.unit
    def test_config_loaded_structural_value_dropped(self):
        """dict / list values are rejected by the merge — schema default preserved."""
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_loaded({"max_unacked": {"nested": 3}})
        assert module._config["max_unacked"] == 1  # structural rejected, default kept

    @pytest.mark.unit
    def test_config_loaded_sets_config_loaded_flag(self):
        module, _ = _build_module()
        module.on_config_loaded({})
        assert module._config_loaded is True

    @pytest.mark.unit
    def test_config_changed_updates_key(self):
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_changed("max_unacked", 5)
        assert module._config["max_unacked"] == 5

    @pytest.mark.unit
    def test_config_changed_unknown_key_noop(self):
        module, _ = _build_module()
        # Must not raise and must not insert the key
        module.on_config_changed("nonexistent_key", 42)
        assert "nonexistent_key" not in module._config

    @pytest.mark.unit
    def test_config_changed_dict_value_rejected(self):
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_changed("max_unacked", {"val": 3})
        assert module._config["max_unacked"] == 1  # unchanged

    @pytest.mark.unit
    def test_config_changed_list_value_rejected(self):
        from shared.config_schema import field_int
        module, _ = _build_module(schema={"max_unacked": field_int(default=1, min=1, max=16)})
        module.on_config_changed("max_unacked", [1, 2, 3])
        assert module._config["max_unacked"] == 1  # unchanged


class TestAAChannelLifecycle:

    @pytest.mark.unit
    def test_channel_open_matching_id_sets_flag(self):
        module, _ = _build_module()
        module.on_channel_open = MagicMock()
        module._on_aa_channel_open("", {"channel_id": 5})
        assert module._channel_open is True

    @pytest.mark.unit
    def test_channel_open_matching_id_calls_on_channel_open(self):
        module, _ = _build_module()
        spy = MagicMock()
        module.on_channel_open = spy
        module._on_aa_channel_open("", {"channel_id": 5})
        spy.assert_called_once_with(5, {"channel_id": 5})

    @pytest.mark.unit
    def test_channel_open_wrong_id_is_noop(self):
        module, _ = _build_module()
        spy = MagicMock()
        module.on_channel_open = spy
        module._on_aa_channel_open("", {"channel_id": 99})
        assert module._channel_open is False
        spy.assert_not_called()

    @pytest.mark.unit
    def test_channel_close_matching_id_clears_flag(self):
        module, _ = _build_module()
        module._channel_open = True
        module.on_channel_close = MagicMock()
        module._on_aa_channel_close("", {"channel_id": 5})
        assert module._channel_open is False

    @pytest.mark.unit
    def test_channel_close_calls_on_channel_close(self):
        module, _ = _build_module()
        module._channel_open = True
        spy = MagicMock()
        module.on_channel_close = spy
        module._on_aa_channel_close("", {"channel_id": 5})
        spy.assert_called_once_with(5)

    @pytest.mark.unit
    def test_channel_close_wrong_id_is_noop(self):
        module, _ = _build_module()
        module._channel_open = True
        spy = MagicMock()
        module.on_channel_close = spy
        module._on_aa_channel_close("", {"channel_id": 77})
        assert module._channel_open is True
        spy.assert_not_called()


class TestFrameDispatch:

    @pytest.mark.unit
    def test_frame_delivered_when_channel_open(self):
        module, _ = _build_module()
        module._channel_open = True
        spy = MagicMock()
        module.on_frame = spy
        payload = {
            "channel_id":  5,
            "message_id":  0x0001,
            "encrypted":   True,
            "payload_hex": "deadbeef",
        }
        module._on_aa_frame("", payload)
        spy.assert_called_once_with(5, 0x0001, True, bytes.fromhex("deadbeef"))

    @pytest.mark.unit
    def test_frame_dropped_when_channel_closed(self):
        module, _ = _build_module()
        module._channel_open = False
        spy = MagicMock()
        module.on_frame = spy
        payload = {
            "channel_id":  5,
            "message_id":  0x0001,
            "encrypted":   False,
            "payload_hex": "aabb",
        }
        module._on_aa_frame("", payload)
        spy.assert_not_called()

    @pytest.mark.unit
    def test_malformed_payload_does_not_raise(self):
        module, _ = _build_module()
        module._channel_open = True
        # Missing required keys — must not raise, just log error
        module._on_aa_frame("", {})

    @pytest.mark.unit
    def test_payload_hex_decoded_to_bytes(self):
        module, _ = _build_module()
        module._channel_open = True
        received = []
        module.on_frame = lambda ch, mid, enc, data: received.append(data)
        module._on_aa_frame("", {
            "channel_id":  5,
            "message_id":  1,
            "encrypted":   False,
            "payload_hex": "0102030405",
        })
        assert received == [b"\x01\x02\x03\x04\x05"]

    @pytest.mark.unit
    def test_encrypted_flag_forwarded(self):
        module, _ = _build_module()
        module._channel_open = True
        received_enc = []
        module.on_frame = lambda ch, mid, enc, data: received_enc.append(enc)
        module._on_aa_frame("", {
            "channel_id":  5,
            "message_id":  1,
            "encrypted":   True,
            "payload_hex": "",
        })
        assert received_enc == [True]

    @pytest.mark.unit
    def test_message_id_forwarded(self):
        module, _ = _build_module()
        module._channel_open = True
        received_mid = []
        module.on_frame = lambda ch, mid, enc, data: received_mid.append(mid)
        module._on_aa_frame("", {
            "channel_id":  5,
            "message_id":  0xABCD,
            "encrypted":   False,
            "payload_hex": "",
        })
        assert received_mid == [0xABCD]


class TestSendFrame:

    @pytest.mark.unit
    def test_publishes_on_aa_frame_send(self):
        module, bus = _build_module()
        module.send_frame(0x0008, b"\x08\x00")
        calls = _published(bus, "aa.frame.send")
        assert calls

    @pytest.mark.unit
    def test_channel_id_in_payload(self):
        module, bus = _build_module(channel_id=7)
        module.send_frame(0x0001, b"")
        calls = _published(bus, "aa.frame.send")
        assert calls[0]["channel_id"] == 7

    @pytest.mark.unit
    def test_message_id_in_payload(self):
        module, bus = _build_module()
        module.send_frame(0x00FF, b"")
        calls = _published(bus, "aa.frame.send")
        assert calls[0]["message_id"] == 0x00FF

    @pytest.mark.unit
    def test_proto_body_as_hex_no_msg_id_prepended(self):
        module, bus = _build_module()
        body = b"\x01\x02\x03"
        module.send_frame(0x0001, body)
        calls = _published(bus, "aa.frame.send")
        assert calls[0]["payload_hex"] == body.hex()

    @pytest.mark.unit
    def test_encrypted_default_is_true(self):
        module, bus = _build_module()
        module.send_frame(0x0001, b"")
        calls = _published(bus, "aa.frame.send")
        assert calls[0]["encrypted"] is True

    @pytest.mark.unit
    def test_encrypted_can_be_overridden(self):
        module, bus = _build_module()
        module.send_frame(0x0001, b"", encrypted=False)
        calls = _published(bus, "aa.frame.send")
        assert calls[0]["encrypted"] is False
