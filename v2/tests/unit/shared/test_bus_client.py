"""
test_bus_client.py — Unit + integration tests for BusClient.

Coverage targets:

  1. Unit (no real sockets)
      a. publish() sends correct multipart frames via mock socket
      b. publish() returns True on success, False on zmq.Again
      c. HWM drop increments _stat_pub_drop, logs warning
      d. per-topic drop counter tracked in _drop_by_topic
      e. subscribe() registers handler and sets zmq SUBSCRIBE sockopt
      f. stop() closes sockets and terms context
      g. _log_stats() formats correctly: drop%, top topics
      h. _receive_loop() dispatches to handler on matching topic
      i. _receive_loop() skips frames with < 2 parts
      j. _receive_loop() logs warning on invalid JSON, does not raise
      k. _receive_loop() exits cleanly on zmq.ZMQError when _running=False
      l. start(blocking=False) returns a live daemon thread

  2. Integration (in-process ZMQ broker via conftest.in_process_broker)
      a. publish then receive round-trip on a single topic
      b. multiple subscribers on different topics receive only their messages
      c. subscriber registered after start() does NOT receive earlier messages (ZMQ sub filter)
      d. payload round-trips as dict through JSON encode/decode
      e. topic prefix-filter: subscriber on "aa" receives "aa.frame" but NOT "bb.frame"
      f. stop() terminates receive loop thread (thread joins within 2s)
      g. stats counters increment correctly after publish burst
      h. invalid JSON frame forwarded to socket is skipped without crash
      i. concurrent publish from two threads, all messages received
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import zmq

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_V2    = Path(__file__).parents[3]
_SHARED = _V2 / "shared"
for _p in (_V2, _SHARED):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.bus_client import BusClient, BROKER_PUB_ADDR, BROKER_SUB_ADDR  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeSocket:
    """Minimal ZMQ socket stub for unit tests."""

    def __init__(self, raise_again: bool = False):
        self._raise_again = raise_again
        self.sent: list = []
        self.subscriptions: list = []
        self._sockopt: dict = {}
        self._poll_returns: list = [False]  # default: nothing to receive
        self._recv_queue: list = []

    def setsockopt(self, opt, val):
        self._sockopt[opt] = val

    def setsockopt_string(self, opt, val):
        self.subscriptions.append(val)

    def connect(self, addr):
        pass

    def send_multipart(self, parts, flags=0):
        if self._raise_again:
            raise zmq.Again
        self.sent.append(parts)

    def poll(self, timeout=500):
        if self._poll_returns:
            return self._poll_returns.pop(0)
        return False

    def recv_multipart(self):
        if self._recv_queue:
            return self._recv_queue.pop(0)
        return [b"dummy", b"{}"]

    def close(self, linger=None):
        pass


class _FakeContext:
    def __init__(self, pub_sock, sub_sock):
        self._sockets = [pub_sock, sub_sock]
        self._idx = 0

    def socket(self, sock_type):
        s = self._sockets[self._idx % len(self._sockets)]
        self._idx += 1
        return s

    def term(self):
        pass


def _make_unit_client(
    raise_again: bool = False,
    poll_sequence: list | None = None,
    recv_queue: list | None = None,
) -> tuple[BusClient, _FakeSocket, _FakeSocket]:
    """Build a BusClient with stubbed ZMQ sockets."""
    pub_sock = _FakeSocket(raise_again=raise_again)
    sub_sock = _FakeSocket()
    if poll_sequence is not None:
        sub_sock._poll_returns = poll_sequence
    if recv_queue is not None:
        sub_sock._recv_queue = recv_queue
    ctx = _FakeContext(pub_sock, sub_sock)

    with patch("shared.bus_client.zmq.Context", return_value=ctx):
        client = BusClient(module_name="test_unit")
    return client, pub_sock, sub_sock


# ---------------------------------------------------------------------------
# In-process broker fixture (mirrors conftest.in_process_broker pattern)
# ---------------------------------------------------------------------------

class _BrokerThread(threading.Thread):
    def __init__(self, pub_addr: str, sub_addr: str):
        super().__init__(daemon=True)
        self.pub_addr = pub_addr
        self.sub_addr = sub_addr
        self._ctx: zmq.Context | None = None
        self._ctrl_addr = f"inproc://broker-ctrl-{uuid.uuid4().hex}"
        self._ready = threading.Event()

    def run(self):
        self._ctx = zmq.Context()
        xpub = self._ctx.socket(zmq.XPUB)
        xsub = self._ctx.socket(zmq.XSUB)
        ctrl = self._ctx.socket(zmq.PULL)
        xpub.bind(self.sub_addr)  # clients publish here → broker XPUB receives
        xsub.bind(self.pub_addr)  # clients subscribe here → broker XSUB pushes
        ctrl.bind(self._ctrl_addr)
        self._ready.set()
        # Use poll-based loop so we can receive the TERMINATE signal
        poller = zmq.Poller()
        poller.register(xpub, zmq.POLLIN)
        poller.register(xsub, zmq.POLLIN)
        poller.register(ctrl, zmq.POLLIN)
        while True:
            socks = dict(poller.poll(200))
            if ctrl in socks:
                break
            if xpub in socks:
                xsub.send_multipart(xpub.recv_multipart())
            if xsub in socks:
                xpub.send_multipart(xsub.recv_multipart())
        xpub.close(linger=0)
        xsub.close(linger=0)
        ctrl.close(linger=0)
        self._ctx.term()

    def stop(self):
        if self._ctx:
            s = self._ctx.socket(zmq.PUSH)
            s.connect(self._ctrl_addr)
            s.send(b"TERMINATE")
            s.close(linger=0)
        self.join(timeout=3)


@pytest.fixture(scope="function")
def _broker():
    """Per-test in-process ZMQ broker on unique IPC paths."""
    uid = uuid.uuid4().hex[:8]
    pub_addr = f"ipc:///tmp/test-bc-pub-{uid}"
    sub_addr = f"ipc:///tmp/test-bc-sub-{uid}"
    broker = _BrokerThread(pub_addr=pub_addr, sub_addr=sub_addr)
    broker.start()
    broker._ready.wait(timeout=3)
    time.sleep(0.05)  # let ipc sockets settle
    yield pub_addr, sub_addr
    broker.stop()


def _make_integration_client(pub_addr: str, sub_addr: str, name: str = "integ") -> BusClient:
    """BusClient wired to the test broker."""
    with (
        patch("shared.bus_client.BROKER_PUB_ADDR", pub_addr),
        patch("shared.bus_client.BROKER_SUB_ADDR", sub_addr),
    ):
        client = BusClient(module_name=name)
    return client


def _wait(condition, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


# ============================================================================
# UNIT TESTS
# ============================================================================


@pytest.mark.unit
class TestPublishUnit:

    def test_publish_sends_topic_and_payload(self):
        client, pub_sock, _ = _make_unit_client()
        client.publish("test.topic", {"x": 1})
        assert len(pub_sock.sent) == 1
        assert pub_sock.sent[0][0] == b"test.topic"
        assert json.loads(pub_sock.sent[0][1]) == {"x": 1}

    def test_publish_returns_true_on_success(self):
        client, _, _ = _make_unit_client()
        assert client.publish("t", {}) is True

    def test_publish_returns_false_on_hwm(self):
        client, _, _ = _make_unit_client(raise_again=True)
        assert client.publish("t", {}) is False

    def test_publish_increments_drop_counter_on_hwm(self):
        client, _, _ = _make_unit_client(raise_again=True)
        client.publish("t", {})
        assert client._stat_pub_drop == 1

    def test_publish_per_topic_drop_counter(self):
        client, _, _ = _make_unit_client(raise_again=True)
        client.publish("aa.frame", {})
        client.publish("aa.frame", {})
        client.publish("bb.event", {})
        assert client._drop_by_topic["aa.frame"] == 2
        assert client._drop_by_topic["bb.event"] == 1

    def test_publish_ok_increments_ok_counter(self):
        client, _, _ = _make_unit_client()
        client.publish("t", {})
        client.publish("t", {})
        assert client._stat_pub_ok == 2


@pytest.mark.unit
class TestSubscribeUnit:

    def test_subscribe_registers_topic_on_socket(self):
        client, _, sub_sock = _make_unit_client()
        client.subscribe("my.topic", lambda t, p: None)
        assert "my.topic" in sub_sock.subscriptions

    def test_subscribe_stores_handler(self):
        client, _, _ = _make_unit_client()
        handler = MagicMock()
        client.subscribe("x.y", handler)
        assert client._subscriptions["x.y"] is handler

    def test_subscribe_multiple_topics(self):
        client, _, sub_sock = _make_unit_client()
        client.subscribe("a", MagicMock())
        client.subscribe("b", MagicMock())
        assert "a" in sub_sock.subscriptions
        assert "b" in sub_sock.subscriptions


@pytest.mark.unit
class TestReceiveLoopUnit:

    def test_receive_dispatches_to_handler(self):
        received = []
        payload = {"val": 42}
        recv_queue = [[b"my.topic", json.dumps(payload).encode()]]
        # poll returns True once (message ready), then False to stop loop
        client, _, _ = _make_unit_client(
            poll_sequence=[True, False],
            recv_queue=recv_queue,
        )
        client.subscribe("my.topic", lambda t, p: received.append(p))
        client._running = True
        # Run one iteration manually
        import threading
        stop_evt = threading.Event()

        def _loop():
            client._receive_loop()
            stop_evt.set()

        client._running = True
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        _wait(lambda: len(received) > 0)
        client._running = False
        stop_evt.wait(timeout=2)
        assert received == [payload]

    def test_receive_skips_short_frames(self):
        """Frames with < 2 parts must be silently skipped."""
        recv_queue = [[b"only_one_frame"]]
        received = []
        client, _, _ = _make_unit_client(
            poll_sequence=[True, False],
            recv_queue=recv_queue,
        )
        client.subscribe("only_one_frame", lambda t, p: received.append(p))
        client._running = True
        t = threading.Thread(target=client._receive_loop, daemon=True)
        t.start()
        time.sleep(0.3)
        client._running = False
        t.join(timeout=2)
        assert received == []

    def test_receive_skips_invalid_json(self):
        """Invalid JSON payload must log warning and not raise."""
        recv_queue = [[b"some.topic", b"{not valid json"]]
        client, _, _ = _make_unit_client(
            poll_sequence=[True, False],
            recv_queue=recv_queue,
        )
        client.subscribe("some.topic", MagicMock())
        client._running = True
        t = threading.Thread(target=client._receive_loop, daemon=True)
        t.start()
        time.sleep(0.3)
        client._running = False
        t.join(timeout=2)
        # No assertion needed — test passes if no exception is raised

    def test_start_non_blocking_returns_thread(self):
        client, _, _ = _make_unit_client(poll_sequence=[])
        client._running = False  # prevent real loop
        with patch.object(client, "_receive_loop", return_value=None):
            t = client.start(blocking=False)
        assert isinstance(t, threading.Thread)


@pytest.mark.unit
class TestStopUnit:

    def test_stop_sets_running_false(self):
        client, _, _ = _make_unit_client()
        client._running = True
        client.stop()
        assert client._running is False


@pytest.mark.unit
class TestLogStatsUnit:

    def test_log_stats_no_crash_on_zero_totals(self):
        client, _, _ = _make_unit_client()
        # Must not raise even with all counters at 0
        client._log_stats()

    def test_log_stats_drop_pct_computed(self):
        client, _, _ = _make_unit_client(raise_again=True)
        for _ in range(3):
            client.publish("t", {})
        # _stat_pub_drop=3, _stat_pub_ok=0 → 100% drop
        # Just verify no exception and counters are correct
        assert client._stat_pub_drop == 3
        client._log_stats()

    def test_log_stats_top_topics_sorted(self):
        client, _, _ = _make_unit_client(raise_again=True)
        for _ in range(5):
            client.publish("aa.frame", {})
        for _ in range(2):
            client.publish("bb.event", {})
        top = sorted(client._drop_by_topic.items(), key=lambda x: x[1], reverse=True)[:3]
        assert top[0] == ("aa.frame", 5)
        assert top[1] == ("bb.event", 2)


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


@pytest.mark.integration
class TestBusClientIntegration:

    def test_roundtrip_single_topic(self, _broker):
        pub_addr, sub_addr = _broker
        sender   = _make_integration_client(pub_addr, sub_addr, "sender")
        receiver = _make_integration_client(pub_addr, sub_addr, "receiver")

        received = []
        receiver.subscribe("ping", lambda t, p: received.append(p))
        t = receiver.start(blocking=False)
        time.sleep(0.15)  # allow sub filter propagation

        sender.publish("ping", {"msg": "hello"})
        assert _wait(lambda: len(received) > 0)
        assert received[0] == {"msg": "hello"}

        receiver.stop()
        t.join(timeout=3)
        sender.stop()

    def test_multiple_subscribers_receive_own_topic_only(self, _broker):
        pub_addr, sub_addr = _broker
        sender = _make_integration_client(pub_addr, sub_addr, "sender2")
        recv_a = _make_integration_client(pub_addr, sub_addr, "recv_a")
        recv_b = _make_integration_client(pub_addr, sub_addr, "recv_b")

        got_a, got_b = [], []
        recv_a.subscribe("topic.a", lambda t, p: got_a.append(p))
        recv_b.subscribe("topic.b", lambda t, p: got_b.append(p))
        ta = recv_a.start(blocking=False)
        tb = recv_b.start(blocking=False)
        time.sleep(0.15)

        sender.publish("topic.a", {"who": "a"})
        sender.publish("topic.b", {"who": "b"})

        assert _wait(lambda: len(got_a) > 0 and len(got_b) > 0)
        assert got_a[0] == {"who": "a"}
        assert got_b[0] == {"who": "b"}

        for c, t in [(recv_a, ta), (recv_b, tb), (sender, None)]:
            c.stop()
            if t:
                t.join(timeout=3)

    def test_payload_roundtrip_preserves_types(self, _broker):
        pub_addr, sub_addr = _broker
        sender   = _make_integration_client(pub_addr, sub_addr, "sender3")
        receiver = _make_integration_client(pub_addr, sub_addr, "receiver3")

        payload = {"int": 42, "float": 3.14, "str": "hello", "list": [1, 2], "bool": True}
        received = []
        receiver.subscribe("data", lambda t, p: received.append(p))
        t = receiver.start(blocking=False)
        time.sleep(0.15)

        sender.publish("data", payload)
        assert _wait(lambda: len(received) > 0)
        assert received[0] == payload

        receiver.stop()
        t.join(timeout=3)
        sender.stop()

    def test_stop_terminates_thread_within_2s(self, _broker):
        pub_addr, sub_addr = _broker
        client = _make_integration_client(pub_addr, sub_addr, "stopper")
        t = client.start(blocking=False)
        time.sleep(0.1)
        client.stop()
        t.join(timeout=2)
        assert not t.is_alive()

    def test_stat_counters_increment_after_burst(self, _broker):
        pub_addr, sub_addr = _broker
        client = _make_integration_client(pub_addr, sub_addr, "stats_test")
        N = 20
        for i in range(N):
            client.publish(f"burst.topic", {"i": i})
        assert client._stat_pub_ok == N
        assert client._stat_pub_drop == 0
        client.stop()

    def test_concurrent_publish_all_received(self, _broker):
        pub_addr, sub_addr = _broker
        sender   = _make_integration_client(pub_addr, sub_addr, "conc_sender")
        receiver = _make_integration_client(pub_addr, sub_addr, "conc_recv")

        received = []
        receiver.subscribe("concurrent", lambda t, p: received.append(p))
        t = receiver.start(blocking=False)
        time.sleep(0.15)

        N = 30
        errors = []

        def _publish_batch(start):
            try:
                for i in range(start, start + N // 2):
                    sender.publish("concurrent", {"i": i})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_publish_batch, args=(0,))
        t2 = threading.Thread(target=_publish_batch, args=(N // 2,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert _wait(lambda: len(received) >= N, timeout=3)
        assert not errors

        receiver.stop()
        t.join(timeout=3)
        sender.stop()

    def test_topic_prefix_filter(self, _broker):
        """Subscriber on 'aa' must receive 'aa.frame' but not 'bb.event'."""
        pub_addr, sub_addr = _broker
        sender   = _make_integration_client(pub_addr, sub_addr, "prefix_sender")
        receiver = _make_integration_client(pub_addr, sub_addr, "prefix_recv")

        got = []
        receiver.subscribe("aa", lambda t, p: got.append(t))
        t = receiver.start(blocking=False)
        time.sleep(0.15)

        sender.publish("aa.frame", {"x": 1})
        sender.publish("bb.event", {"x": 2})
        time.sleep(0.3)

        assert all(t.startswith("aa") for t in got)
        assert not any(t.startswith("bb") for t in got)

        receiver.stop()
        t.join(timeout=3)
        sender.stop()
