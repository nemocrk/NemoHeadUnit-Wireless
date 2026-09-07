import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.subscriptions = {}

    def _subscribe(topic, cb):
        bus.subscriptions[topic] = cb

    bus.subscribe.side_effect = _subscribe
    return bus
