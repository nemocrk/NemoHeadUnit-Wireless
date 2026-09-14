"""
test_bus_inmemory.py — Unit tests for InMemoryBusHub, InMemoryBusClient, and BusClient facade.
"""

import os
from unittest.mock import MagicMock, patch
import pytest

from shared.bus_inmemory import InMemoryBusHub, InMemoryBusClient
from shared.bus_client import BusClient

pytestmark = pytest.mark.unit


def test_in_memory_bus_pub_sub():
    hub = InMemoryBusHub()
    client1 = InMemoryBusClient("mod1", hub=hub)
    client2 = InMemoryBusClient("mod2", hub=hub)

    client1.start()
    client2.start()

    received = []
    def on_event(topic, payload):
        received.append((topic, payload))

    client2.subscribe("test.topic", on_event)
    client1.publish("test.topic", {"hello": "world"})

    assert len(received) == 1
    assert received[0] == ("test.topic", {"hello": "world"})

    client1.stop()
    client2.stop()


def test_in_memory_bus_prefix_matching():
    hub = InMemoryBusHub()
    client1 = InMemoryBusClient("mod1", hub=hub)
    client2 = InMemoryBusClient("mod2", hub=hub)

    client1.start()
    client2.start()

    received = []
    client2.subscribe("config.", lambda top, pay: received.append((top, pay)))

    client1.publish("config.sync.module_a", {"key": "val1"})
    client1.publish("config.get", {"key": "val2"})
    client1.publish("other.topic", {"key": "ignore"})

    assert len(received) == 2
    assert received[0][0] == "config.sync.module_a"
    assert received[1][0] == "config.get"

    client1.stop()
    client2.stop()


def test_in_memory_bus_unsubscribe():
    hub = InMemoryBusHub()
    client1 = InMemoryBusClient("mod1", hub=hub)
    client2 = InMemoryBusClient("mod2", hub=hub)

    received = []
    cb = lambda top, pay: received.append(top)
    client2.subscribe("test.topic", cb)
    client1.publish("test.topic", {"x": 1})
    assert len(received) == 1

    client2.unsubscribe("test.topic", cb)
    client1.publish("test.topic", {"x": 2})
    assert len(received) == 1


def test_bus_client_facade_multithreading(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")
    client = BusClient("test_module")
    assert isinstance(client._impl, InMemoryBusClient)
    assert not hasattr(client, "_context") or client._context is None


def test_bus_client_facade_multiprocessing(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multiprocessing")
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]

        client = BusClient("test_module")
        assert not isinstance(client._impl, InMemoryBusClient)
