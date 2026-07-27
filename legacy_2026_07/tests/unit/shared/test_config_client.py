"""
test_config_client.py — Unit tests for ConfigClient.

Coverage targets:

  1. register()
        a. Subscribes to config.response on the bus
        b. Subscribes to config.changed on the bus
        c. No extra subscriptions (exactly 2)

  2. get()
        a. Publishes config.get with module + requester fields
        b. No "defaults" key when defaults=None
        c. "defaults" key present when defaults passed
        d. No "schema" key when schema=None
        e. "schema" key present (serialised via schema_to_dict) when schema passed
        f. schema_to_dict result is used (not raw dict)
        g. module name and requester are always the same value

  3. set()
        a. Publishes config.set with module, key, value
        b. value can be any JSON-serialisable type (int, str, bool, list)
        c. key forwarded verbatim

  4. _on_config_response routing
        a. on_config_loaded called when module + requester match
        b. on_config_loaded NOT called when module mismatch
        c. on_config_loaded NOT called when requester mismatch
        d. on_config_loaded receives config sub-dict (payload["config"])
        e. on_config_loaded receives {} when "config" key absent
        f. on_config_loaded=None does not raise
        g. Both module AND requester must match (AND logic)

  5. _on_config_changed routing
        a. on_config_changed called with (key, value) when module matches
        b. on_config_changed NOT called when module mismatch
        c. on_config_changed=None does not raise
        d. key and value forwarded from payload verbatim
        e. missing "key" / "value" forwards None without raising

  6. Multiple ConfigClient instances on same bus
        a. Each instance filters independently by module_name
        b. Response for module A does not trigger module B callback

  7. register() called twice (idempotency check)
        a. Double-subscribe is a no-op for routing logic (bus may receive
           duplicate subscriptions, but callbacks are still called once
           per message since handler dict keys overwrite)
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from shared.config_client import ConfigClient          # noqa: E402
from shared.config_schema import field_int, field_enum  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(module_name: str = "my_module") -> tuple[ConfigClient, MagicMock]:
    """Return (ConfigClient, mock_bus)."""
    bus = MagicMock()
    bus.publish   = MagicMock()
    bus.subscribe = MagicMock()
    client = ConfigClient(bus=bus, module_name=module_name)
    return client, bus


def _published(bus: MagicMock, topic: str) -> list[dict]:
    """Return all payload dicts published on *topic*."""
    return [
        c.args[1] for c in bus.publish.call_args_list
        if c.args[0] == topic
    ]


def _subscribed_handlers(bus: MagicMock) -> dict[str, callable]:
    """Map topic → handler from bus.subscribe call list."""
    result = {}
    for c in bus.subscribe.call_args_list:
        result[c.args[0]] = c.args[1]
    return result


# ============================================================================
# TEST CLASSES
# ============================================================================


@pytest.mark.unit
class TestRegister:

    def test_subscribes_to_config_response(self):
        client, bus = _make_client()
        client.register()
        topics = [c.args[0] for c in bus.subscribe.call_args_list]
        assert "config.response" in topics

    def test_subscribes_to_config_changed(self):
        client, bus = _make_client()
        client.register()
        topics = [c.args[0] for c in bus.subscribe.call_args_list]
        assert "config.changed" in topics

    def test_exactly_two_subscriptions(self):
        client, bus = _make_client()
        client.register()
        assert bus.subscribe.call_count == 2


@pytest.mark.unit
class TestGet:

    def test_publishes_config_get(self):
        client, bus = _make_client()
        client.get()
        payloads = _published(bus, "config.get")
        assert len(payloads) == 1

    def test_module_field_set(self):
        client, bus = _make_client(module_name="video")
        client.get()
        payloads = _published(bus, "config.get")
        assert payloads[0]["module"] == "video"

    def test_requester_equals_module_name(self):
        client, bus = _make_client(module_name="audio")
        client.get()
        payloads = _published(bus, "config.get")
        p = payloads[0]
        assert p["requester"] == p["module"] == "audio"

    def test_no_defaults_key_when_none(self):
        client, bus = _make_client()
        client.get(defaults=None)
        payloads = _published(bus, "config.get")
        assert "defaults" not in payloads[0]

    def test_defaults_key_present_when_passed(self):
        client, bus = _make_client()
        client.get(defaults={"volume": 80})
        payloads = _published(bus, "config.get")
        assert payloads[0]["defaults"] == {"volume": 80}

    def test_no_schema_key_when_none(self):
        client, bus = _make_client()
        client.get(schema=None)
        payloads = _published(bus, "config.get")
        assert "schema" not in payloads[0]

    def test_schema_key_present_when_passed(self):
        client, bus = _make_client()
        schema = {"volume": field_int(default=80, min=0, max=100)}
        client.get(schema=schema)
        payloads = _published(bus, "config.get")
        assert "schema" in payloads[0]

    def test_schema_serialised_via_schema_to_dict(self):
        """schema_to_dict output must be a plain dict (JSON-serialisable)."""
        client, bus = _make_client()
        schema = {"volume": field_int(default=80, min=0, max=100)}
        client.get(schema=schema)
        payloads = _published(bus, "config.get")
        assert isinstance(payloads[0]["schema"], dict)

    def test_schema_with_enum_field(self):
        client, bus = _make_client()
        schema = {"mode": field_enum(default="A", choices=["A", "B", "C"])}
        client.get(schema=schema)
        payloads = _published(bus, "config.get")
        assert "mode" in payloads[0]["schema"]


@pytest.mark.unit
class TestSet:

    def test_publishes_config_set(self):
        client, bus = _make_client()
        client.set("volume", 75)
        payloads = _published(bus, "config.set")
        assert len(payloads) == 1

    def test_module_field_set(self):
        client, bus = _make_client(module_name="audio")
        client.set("volume", 75)
        payloads = _published(bus, "config.set")
        assert payloads[0]["module"] == "audio"

    def test_key_forwarded(self):
        client, bus = _make_client()
        client.set("pin", "1234")
        payloads = _published(bus, "config.set")
        assert payloads[0]["key"] == "pin"

    def test_value_int(self):
        client, bus = _make_client()
        client.set("volume", 42)
        payloads = _published(bus, "config.set")
        assert payloads[0]["value"] == 42

    def test_value_bool(self):
        client, bus = _make_client()
        client.set("enabled", True)
        payloads = _published(bus, "config.set")
        assert payloads[0]["value"] is True

    def test_value_string(self):
        client, bus = _make_client()
        client.set("name", "hello")
        payloads = _published(bus, "config.set")
        assert payloads[0]["value"] == "hello"


@pytest.mark.unit
class TestConfigResponseRouting:

    def _trigger_response(self, client: ConfigClient, module: str, requester: str, config: dict) -> None:
        """Simulate a config.response message arriving on the bus."""
        client._on_config_response("config.response", {
            "module":    module,
            "requester": requester,
            "config":    config,
        })

    def test_on_config_loaded_called_when_match(self):
        client, _ = _make_client(module_name="video")
        cb = MagicMock()
        client.on_config_loaded = cb
        self._trigger_response(client, "video", "video", {"key": "val"})
        cb.assert_called_once()

    def test_on_config_loaded_receives_config_dict(self):
        client, _ = _make_client(module_name="video")
        received = []
        client.on_config_loaded = lambda cfg: received.append(cfg)
        self._trigger_response(client, "video", "video", {"volume": 80})
        assert received == [{"volume": 80}]

    def test_on_config_loaded_receives_empty_dict_when_config_absent(self):
        client, _ = _make_client(module_name="video")
        received = []
        client.on_config_loaded = lambda cfg: received.append(cfg)
        client._on_config_response("config.response", {
            "module": "video", "requester": "video"
            # no "config" key
        })
        assert received == [{}]

    def test_on_config_loaded_not_called_module_mismatch(self):
        client, _ = _make_client(module_name="video")
        cb = MagicMock()
        client.on_config_loaded = cb
        self._trigger_response(client, "audio", "video", {})
        cb.assert_not_called()

    def test_on_config_loaded_not_called_requester_mismatch(self):
        client, _ = _make_client(module_name="video")
        cb = MagicMock()
        client.on_config_loaded = cb
        self._trigger_response(client, "video", "config_ui", {})
        cb.assert_not_called()

    def test_on_config_loaded_none_does_not_raise(self):
        client, _ = _make_client(module_name="video")
        client.on_config_loaded = None
        # Must not raise
        self._trigger_response(client, "video", "video", {})

    def test_both_module_and_requester_must_match(self):
        client, _ = _make_client(module_name="video")
        cb = MagicMock()
        client.on_config_loaded = cb
        # module matches but requester doesn't
        self._trigger_response(client, "video", "other", {})
        # requester matches but module doesn't
        self._trigger_response(client, "other", "video", {})
        cb.assert_not_called()


@pytest.mark.unit
class TestConfigChangedRouting:

    def _trigger_changed(self, client: ConfigClient, module: str, key: str, value) -> None:
        client._on_config_changed("config.changed", {
            "module": module,
            "key":    key,
            "value":  value,
        })

    def test_on_config_changed_called_when_module_matches(self):
        client, _ = _make_client(module_name="audio")
        cb = MagicMock()
        client.on_config_changed = cb
        self._trigger_changed(client, "audio", "volume", 90)
        cb.assert_called_once_with("volume", 90)

    def test_on_config_changed_not_called_module_mismatch(self):
        client, _ = _make_client(module_name="audio")
        cb = MagicMock()
        client.on_config_changed = cb
        self._trigger_changed(client, "video", "volume", 90)
        cb.assert_not_called()

    def test_on_config_changed_none_does_not_raise(self):
        client, _ = _make_client(module_name="audio")
        client.on_config_changed = None
        self._trigger_changed(client, "audio", "volume", 90)

    def test_key_and_value_forwarded_verbatim(self):
        client, _ = _make_client(module_name="audio")
        received = []
        client.on_config_changed = lambda k, v: received.append((k, v))
        self._trigger_changed(client, "audio", "pin", "9999")
        assert received == [("pin", "9999")]

    def test_missing_key_and_value_forwarded_as_none(self):
        """Malformed payload must not raise — None forwarded to callback."""
        client, _ = _make_client(module_name="audio")
        received = []
        client.on_config_changed = lambda k, v: received.append((k, v))
        client._on_config_changed("config.changed", {"module": "audio"})
        assert received == [(None, None)]


@pytest.mark.unit
class TestMultipleInstances:

    def test_response_for_a_does_not_trigger_b(self):
        client_a, _ = _make_client(module_name="module_a")
        client_b, _ = _make_client(module_name="module_b")
        cb_a = MagicMock()
        cb_b = MagicMock()
        client_a.on_config_loaded = cb_a
        client_b.on_config_loaded = cb_b

        # Simulate response for module_a
        client_a._on_config_response("config.response", {
            "module": "module_a", "requester": "module_a", "config": {"x": 1}
        })
        client_b._on_config_response("config.response", {
            "module": "module_a", "requester": "module_a", "config": {"x": 1}
        })

        cb_a.assert_called_once()
        cb_b.assert_not_called()

    def test_changed_for_a_does_not_trigger_b(self):
        client_a, _ = _make_client(module_name="module_a")
        client_b, _ = _make_client(module_name="module_b")
        cb_a = MagicMock()
        cb_b = MagicMock()
        client_a.on_config_changed = cb_a
        client_b.on_config_changed = cb_b

        client_a._on_config_changed("config.changed", {"module": "module_a", "key": "k", "value": 1})
        client_b._on_config_changed("config.changed", {"module": "module_a", "key": "k", "value": 1})

        cb_a.assert_called_once()
        cb_b.assert_not_called()


@pytest.mark.unit
class TestRegisterIdempotency:

    def test_double_register_still_routes_correctly(self):
        """Calling register() twice must not break routing."""
        client, bus = _make_client(module_name="video")
        client.register()
        client.register()  # second call

        cb = MagicMock()
        client.on_config_loaded = cb

        # Simulate a valid response — callback must fire exactly once
        # (BusClient stores one handler per topic; double-subscribe overwrites, not appends)
        handlers = _subscribed_handlers(bus)
        handlers["config.response"]("config.response", {
            "module": "video", "requester": "video", "config": {}
        })
        cb.assert_called_once()
