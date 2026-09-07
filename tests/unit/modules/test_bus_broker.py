# tests/unit/modules/test_bus_broker.py
import pytest
from unittest.mock import MagicMock, patch
from modules.bus_broker.main import BusBrokerModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_bus_broker():
    with patch("shared.base_module.BusClient"), \
         patch("modules.bus_broker.main.zmq.Context") as mock_ctx, \
         patch("modules.bus_broker.main.zmq.proxy"):
        mock_socket = MagicMock()
        mock_ctx.return_value.socket.return_value = mock_socket
        broker = BusBrokerModule()
        yield broker


def test_bus_broker_config_and_schema(mock_bus_broker):
    defaults = mock_bus_broker.get_default_config()
    assert defaults["heartbeat_interval"] == 2.0
    schema = mock_bus_broker.get_schema()
    assert "heartbeat_interval" in schema
    assert schema["heartbeat_interval"].type == "float"


def test_bus_broker_module_events(mock_bus_broker):
    # Event with name, priority, path_prefix, target_url
    mock_bus_broker._on_module_event("system.module_ready", {
        "name": "audio_manager",
        "priority": 3,
        "path_prefix": "/api/audio",
        "target_url": "http://127.0.0.1:8081",
    })
    assert "audio_manager" in mock_bus_broker.bus_registry
    entry = mock_bus_broker.bus_registry["audio_manager"]
    assert entry["priority"] == 3
    assert entry["path_prefix"] == "/api/audio"
    assert entry["target_url"] == "http://127.0.0.1:8081"

    # Route registration event updating existing entry
    mock_bus_broker._on_module_event("proxy.register_route", {
        "path_prefix": "/api/audio",
        "target_url": "http://127.0.0.1:8089",
    })
    assert mock_bus_broker.bus_registry["audio_manager"]["target_url"] == "http://127.0.0.1:8089"


@pytest.mark.asyncio
async def test_bus_broker_lifecycle(mock_bus_broker):
    mock_bus_broker.subscribe = MagicMock()
    with patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        await mock_bus_broker.setup()

        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()
        mock_bus_broker.subscribe.assert_any_call("system.module_ready", mock_bus_broker._on_module_event)
        mock_bus_broker.subscribe.assert_any_call("system.ready", mock_bus_broker._on_module_event)
        mock_bus_broker.subscribe.assert_any_call("proxy.register_route", mock_bus_broker._on_module_event)

    await mock_bus_broker.teardown()
    mock_bus_broker.xsub.close.assert_called_with(linger=0)
    mock_bus_broker.xpub.close.assert_called_with(linger=0)
    mock_bus_broker.bus_ctx.term.assert_called_once()


@pytest.mark.asyncio
async def test_bus_broker_heartbeat_step(mock_bus_broker):
    mock_bus_broker.bus_registry = {
        "test_mod": {"name": "test_mod", "priority": 2}
    }
    mock_bus_broker.publish = MagicMock()
    mock_bus_broker.config["heartbeat_interval"] = 0.1
    mock_bus_broker._last_heartbeat = 0.0
    mock_bus_broker._running = True

    async def stop_loop(_):
        mock_bus_broker._running = False

    with patch("time.time", return_value=1000.0), \
         patch("asyncio.sleep", side_effect=stop_loop):
        await mock_bus_broker.run()

    mock_bus_broker.publish.assert_called_once()
    topic, payload = mock_bus_broker.publish.call_args[0]
    assert topic == "system.heartbeat"
    assert payload["timestamp"] == 1000.0
    assert "test_mod" in payload["modules"]
