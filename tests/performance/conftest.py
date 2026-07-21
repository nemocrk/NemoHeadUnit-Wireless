from __future__ import annotations

from types import SimpleNamespace
import inspect
import os
import threading
import time
import uuid

import pytest
import zmq

os.environ.setdefault("PERF_MAX_RSS_IDLE_MB", "512.0")
os.environ.setdefault("PERF_THROUGHPUT_MSG_S", "1")
os.environ.setdefault("PERF_THROUGHPUT_MB_S_1K", "0.1")
os.environ.setdefault("PERF_THROUGHPUT_MB_S_64K", "0.1")
os.environ.setdefault("PERF_BURST_P99_MS", "50.0")


class _PerfBroker(SimpleNamespace):
    @property
    def url(self):
        return {"pub_addr": self.pub_addr, "sub_addr": self.sub_addr}

    def __getitem__(self, key: str):
        if key == "pub_addr":
            return self.pub_addr
        if key == "sub_addr":
            return self.sub_addr
        if key == "_broker":
            return self._broker
        raise KeyError(key)


class _BrokerThread(threading.Thread):
    def __init__(self, pub_addr: str, sub_addr: str):
        super().__init__(daemon=True)
        self.pub_addr = pub_addr
        self.sub_addr = sub_addr
        self._ctx: zmq.Context | None = None
        self._xsub: zmq.Socket | None = None
        self._xpub: zmq.Socket | None = None
        self._ctrl: zmq.Socket | None = None
        self._ctrl_addr = f"inproc://broker-ctrl-perf-{uuid.uuid4().hex}"
        self.ready = threading.Event()

    def run(self) -> None:
        self._ctx = zmq.Context()
        self._xsub = self._ctx.socket(zmq.XSUB)
        self._xsub.bind(self.pub_addr)
        self._xpub = self._ctx.socket(zmq.XPUB)
        self._xpub.bind(self.sub_addr)
        self._ctrl = self._ctx.socket(zmq.PULL)
        self._ctrl.bind(self._ctrl_addr)
        self.ready.set()
        try:
            zmq.proxy_steerable(self._xsub, self._xpub, None, self._ctrl)
        except zmq.ZMQError:
            pass
        finally:
            for socket in (self._xsub, self._xpub, self._ctrl):
                try:
                    socket.close(linger=0)
                except Exception:
                    pass
            try:
                self._ctx.term()
            except Exception:
                pass

    def stop(self, timeout: float = 2.0) -> None:
        try:
            ctrl_push = self._ctx.socket(zmq.PUSH)
            ctrl_push.connect(self._ctrl_addr)
            ctrl_push.send(zmq.Frame(b"TERMINATE"))
            ctrl_push.close(linger=0)
        except Exception:
            pass
        self.join(timeout=timeout)


@pytest.fixture
def in_process_broker():
    uid = uuid.uuid4().hex
    pub_addr = f"ipc:///tmp/nemotest-perf-{uid}.pub"
    sub_addr = f"ipc:///tmp/nemotest-perf-{uid}.sub"

    broker = _BrokerThread(pub_addr=pub_addr, sub_addr=sub_addr)
    broker.start()
    broker.ready.wait(timeout=3.0)
    time.sleep(0.05)

    yield _PerfBroker(pub_addr=pub_addr, sub_addr=sub_addr, _broker=broker)

    broker.stop()


class _CompatBusClient:
    def __init__(self, module_name: str, broker_url=None):
        import shared.bus_client as bc_mod

        if broker_url is not None:
            if isinstance(broker_url, dict):
                pub_addr, sub_addr = broker_url["pub_addr"], broker_url["sub_addr"]
            elif hasattr(broker_url, "pub_addr") and hasattr(broker_url, "sub_addr"):
                pub_addr, sub_addr = broker_url.pub_addr, broker_url.sub_addr
            else:
                raise TypeError(f"Unsupported broker_url for tests: {broker_url!r}")
            bc_mod.BROKER_PUB_ADDR = pub_addr
            bc_mod.BROKER_SUB_ADDR = sub_addr
        bc_mod.BUS_STATS_INTERVAL = 0.0

        self._client = bc_mod.BusClient(module_name=module_name)
        self._started = False
        self._handlers: dict[tuple[str, object], object] = {}

    def subscribe(self, topic: str, handler):
        try:
            param_count = len(inspect.signature(handler).parameters)
        except (TypeError, ValueError):
            param_count = 1

        if param_count >= 2:
            wrapped = handler
        else:
            def wrapped(_topic, payload, _handler=handler):
                return _handler(payload)

        self._handlers[(topic, handler)] = wrapped
        self._client.subscribe(topic, wrapped)
        if not self._started:
            self._client.start(blocking=False)
            self._started = True
            time.sleep(0.05)

    def unsubscribe(self, topic: str, handler=None):
        key = (topic, handler)
        wrapped = self._handlers.pop(key, None)
        if wrapped is None and handler is None:
            for stored_key, stored_handler in list(self._handlers.items()):
                if stored_key[0] == topic:
                    wrapped = self._handlers.pop(stored_key)
                    break
        if wrapped is None:
            for stored_key, stored_handler in list(self._handlers.items()):
                if stored_key[0] == topic:
                    self._handlers.pop(stored_key)
                    wrapped = stored_handler
                    break

        if self._client._subscriptions.get(topic) is wrapped or handler is None:
            self._client._subscriptions.pop(topic, None)
            try:
                self._client._sub.setsockopt_string(zmq.UNSUBSCRIBE, topic)
            except Exception:
                pass
        if self._started and not self._client._subscriptions:
            self.stop()

    def stop(self):
        self._started = False
        return self._client.stop()

    def __getattr__(self, name: str):
        return getattr(self._client, name)


def pytest_runtest_setup(item):
    if hasattr(item.module, "BusClient"):
        item.module.BusClient = _CompatBusClient
