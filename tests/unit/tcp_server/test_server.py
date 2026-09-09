"""
test_server.py — Unit tests for TCPServer (modules.tcp_server.server)
"""

import socket
import threading
import time
import pytest
from modules.tcp_server.server import TCPServer

pytestmark = pytest.mark.unit


def test_tcp_server_start_and_stop():
    server = TCPServer(host="127.0.0.1", port=0)
    assert server._running is False
    assert server._server_sock is None

    success = server.start()
    assert success is True
    assert server._running is True
    assert server._server_sock is not None
    assert server.port > 0

    server.stop()
    assert server._running is False
    assert server._server_sock is None


def test_tcp_server_context_manager():
    with TCPServer(host="127.0.0.1", port=0) as server:
        assert server._running is True
        assert server.port > 0
    assert server._running is False
    assert server._server_sock is None


def test_tcp_server_accept_success():
    with TCPServer(host="127.0.0.1", port=0) as server:
        port = server.port

        client_sock = None
        def _connect_client():
            nonlocal client_sock
            time.sleep(0.05)
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(("127.0.0.1", port))

        t = threading.Thread(target=_connect_client, daemon=True)
        t.start()

        res = server.accept(timeout=2)
        assert res is not None
        conn, addr = res
        assert conn is not None
        assert "127.0.0.1" in addr

        conn.close()
        if client_sock:
            client_sock.close()


def test_tcp_server_accept_timeout():
    with TCPServer(host="127.0.0.1", port=0) as server:
        res = server.accept(timeout=0.1)
        assert res is None


def test_tcp_server_start_failure():
    # Attempt to bind to an invalid host
    server = TCPServer(host="999.999.999.999", port=5288)
    success = server.start()
    assert success is False
    assert server._running is False
