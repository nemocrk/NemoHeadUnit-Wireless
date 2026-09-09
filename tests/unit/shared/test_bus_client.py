"""
test_bus_client.py — Unit tests for shared.bus_client.BusClient
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch
import pytest
import zmq

from shared.bus_client import BusClient

pytestmark = pytest.mark.unit


def test_bus_client_init():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]

        client = BusClient("test_module")
        assert client.module_name == "test_module"
        assert not client._running
        mock_pub.connect.assert_called_once()
        mock_sub.connect.assert_called_once()


def test_bus_client_subscribe():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]

        client = BusClient("test_module")
        cb = MagicMock()
        client.subscribe("custom.topic", cb)

        assert "custom.topic" in client._subscriptions
        assert client._subscriptions["custom.topic"] == cb
        mock_sub.setsockopt_string.assert_called_once_with(zmq.SUBSCRIBE, "custom.topic")


def test_bus_client_publish_success():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]

        client = BusClient("test_module")
        client.publish("event.test", {"data": 123})

        mock_pub.send_multipart.assert_called_once_with([
            b"event.test",
            json.dumps({"data": 123}).encode("utf-8")
        ])


def test_bus_client_publish_hwm_drop():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]
        mock_pub.send_multipart.side_effect = zmq.Again()

        client = BusClient("test_module")
        # Should catch zmq.Again and log warning without raising
        client.publish("event.drop", {"data": "drop"})


def test_bus_client_publish_exception():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]
        mock_pub.send_multipart.side_effect = RuntimeError("socket broken")

        client = BusClient("test_module")
        # Should catch generic exception and log error without raising
        client.publish("event.err", {})


def test_bus_client_listen_loop_and_stop():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]

        received_events = []
        def _cb(topic, payload):
            received_events.append((topic, payload))

        client = BusClient("test_module")
        client.subscribe("test.topic", _cb)

        # Setup mock_sub.poll to return True once then set _running = False
        def _poll(timeout=200):
            if client._running:
                client._running = False
                return True
            return False

        mock_sub.poll.side_effect = _poll
        mock_sub.recv_multipart.return_value = [
            b"test.topic", json.dumps({"hello": "world"}).encode("utf-8")
        ]

        # Run blocking mode
        client.start(blocking=True)

        assert client._running is False
        assert len(received_events) == 1
        assert received_events[0] == ("test.topic", {"hello": "world"})

        client.stop()
        mock_pub.close.assert_called_once()
        mock_sub.close.assert_called_once()
        mock_ctx.destroy.assert_called_once()


def test_bus_client_listen_wildcard_prefix():
    with patch("shared.bus_client.zmq.Context") as mock_ctx_cls:
        mock_ctx = MagicMock()
        mock_ctx_cls.return_value = mock_ctx
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_ctx.socket.side_effect = [mock_pub, mock_sub]

        received = []
        client = BusClient("test_module")
        client.subscribe("aa.frame.*", lambda t, p: received.append((t, p)))

        # Simulate matching topic prefix
        def _poll(timeout=200):
            if client._running:
                client._running = False
                return True
            return False

        mock_sub.poll.side_effect = _poll
        mock_sub.recv_multipart.return_value = [
            b"aa.frame.ch0", b'{"channel_id": 0}',
        ]

        client.start(blocking=True)

        assert len(received) == 1
        assert received[0][0] == "aa.frame.ch0"
        assert received[0][1]["channel_id"] == 0

