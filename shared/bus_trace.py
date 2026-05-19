"""
NemoHeadUnit-Wireless v2 — Bus Trace helper

Non-blocking instrumentation side-channel for BusClient.

Design goals:
- NEVER use BusClient internally (avoids recursion)
- NEVER block module threads
- NEVER share a ZMQ socket across threads
- Drop trace events rather than perturbing realtime paths
- Collector is optional: if it is not running, application still works

Transport:
- modules: PUSH connect ipc:///tmp/nemobus_v2.trace
- tracer module: PULL bind ipc:///tmp/nemobus_v2.trace
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import threading
import time
from typing import Any

import zmq

TRACE_ADDR = os.getenv("BUS_TRACE_ADDR", "ipc:///tmp/nemobus_v2.trace")
TRACE_ENABLED = os.getenv("BUS_TRACE", "1").lower() not in {"0", "false", "no", "off"}
TRACE_QUEUE_MAX = int(os.getenv("BUS_TRACE_QUEUE_MAX", "10000"))
TRACE_HWM = int(os.getenv("BUS_TRACE_HWM", "10000"))


class BusTracer:
    """Thread-safe, non-blocking trace event emitter."""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.enabled = TRACE_ENABLED
        self.addr = TRACE_ADDR
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=TRACE_QUEUE_MAX)
        self._dropped_local = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._ctx: zmq.Context | None = None
        self._push: zmq.Socket | None = None
        self._lock = threading.Lock()

        if self.enabled:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._drain_loop,
                name=f"bus-trace-{self.module_name}",
                daemon=True,
            )
            self._thread.start()
            atexit.register(self.close)

    def close(self) -> None:
        with self._lock:
            self._running = False

        # Do not wait indefinitely: trace must never slow shutdown.
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)

        try:
            if self._push is not None:
                self._push.close(linger=0)
        except Exception:
            pass
        try:
            if self._ctx is not None:
                self._ctx.term()
        except Exception:
            pass

    def emit(self, event_type: str, **fields: Any) -> None:
        """Queue one trace event. Never blocks. Never raises."""
        if not self.enabled:
            return

        ev = {
            "type": event_type,
            "module": self.module_name,
            "ts_ns": time.monotonic_ns(),
            **fields,
        }
        try:
            self._queue.put_nowait(ev)
        except queue.Full:
            self._dropped_local += 1

    def dropped_local(self) -> int:
        return self._dropped_local

    def _drain_loop(self) -> None:
        try:
            self._ctx = zmq.Context()
            self._push = self._ctx.socket(zmq.PUSH)
            self._push.setsockopt(zmq.SNDHWM, TRACE_HWM)
            self._push.setsockopt(zmq.LINGER, 0)
            self._push.connect(self.addr)
        except Exception:
            self.enabled = False
            return

        last_drop_report = 0
        while self._running:
            try:
                ev = self._queue.get(timeout=0.25)
            except queue.Empty:
                # Periodically report local trace drops if any occurred.
                if self._dropped_local != last_drop_report:
                    last_drop_report = self._dropped_local
                    self._send_now({
                        "type": "trace_local_drop",
                        "module": self.module_name,
                        "ts_ns": time.monotonic_ns(),
                        "dropped": self._dropped_local,
                    })
                continue
            self._send_now(ev)

    def _send_now(self, ev: dict[str, Any]) -> None:
        try:
            data = json.dumps(ev, separators=(",", ":"), default=str).encode("utf-8")
            self._push.send(data, flags=zmq.NOBLOCK)
        except Exception:
            # Trace is best-effort by design.
            self._dropped_local += 1
