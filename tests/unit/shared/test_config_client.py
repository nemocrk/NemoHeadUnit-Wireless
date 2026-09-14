import pytest
from shared.config_client import ConfigClient
from shared.config_schema import field_string

pytestmark = pytest.mark.unit


def test_config_client_defaults_fallback(mock_bus):
    defaults = {"host": "127.0.0.1", "port": 8080}
    client = ConfigClient("test_mod", mock_bus, default_config=defaults)

    assert client.config_data == defaults
    assert client.has_remote_config is False

    res = client.fetch_config()
    assert res == defaults
    mock_bus.publish.assert_called_once()
    topic, payload = mock_bus.publish.call_args[0]
    assert topic == "config.get"
    assert payload["module"] == "test_mod"
    assert payload["defaults"] == defaults


def test_config_client_update_and_callback(mock_bus):
    defaults = {"volume": 50, "muted": False}
    client = ConfigClient("audio_mod", mock_bus, default_config=defaults)

    received_updates = []
    client.on_update(lambda cfg: received_updates.append(dict(cfg)))

    # Response for another module should be ignored
    client.handle_config_response("config.response", {"module": "other_mod", "config": {"volume": 90}})
    assert client.has_remote_config is False
    assert len(received_updates) == 0

    # Response for this module
    client.handle_config_response("config.response", {"module": "audio_mod", "config": {"volume": 75}})
    assert client.has_remote_config is True
    assert client.config_data == {"volume": 75, "muted": False}
    assert len(received_updates) == 1
    assert received_updates[0]["volume"] == 75


def test_config_client_callback_exception_safety(mock_bus):
    client = ConfigClient("safe_mod", mock_bus, default_config={"x": 1})

    def bad_cb(cfg):
        raise RuntimeError("Callback explosion")

    client.on_update(bad_cb)
    # Should not raise exception
    client.handle_config_response("config.response", {"module": "safe_mod", "config": {"x": 2}})
    assert client.config_data["x"] == 2
