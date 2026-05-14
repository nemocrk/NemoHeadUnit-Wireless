"""
NemoHeadUnit-Wireless v2 — BusClient
Reusable helper for modules to publish and subscribe on the ZMQ bus.

Instrumented version:
- injects _trace into JSON payloads on publish
- removes _trace before delivering payload to handlers
- emits non-blocking telemetry to shared.bus_trace.BusTracer
- tracks publish drops, receive latency, callback time, sequence gaps, duplicates
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
import threading
import time
from typing import Callable

import zmq

from shared.logger import get_logger
from shared.bus_trace import BusTracer

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"
BROKER_SUB_ADDR = "ipc:///tmp/nemobus_v2.sub"

# Raised from 1000 to reduce drop probability under heavy video+log traffic.
BUS_HWM = 5000

# How often (seconds) to emit the saturation summary log. 0 = disabled.
BUS_STATS_INTERVAL = 10.0


class BusClient:
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.log = get_logger(module_name)
        self._context = zmq.Context()
        self._subscriptions: dict[str, Callable] = {}
        self._running = False

        # Trace side-channel. It never uses BusClient internally.
        self._tracer = BusTracer(module_name=module_name)
        self._trace_seq: dict[str, int] = defaultdict(int)  # topic -> next seq
        self._trace_last_recv_seq: dict[tuple[str, str], int] = {}  # (src_module, topic) -> seq
        self._trace_blacklist_prefixes = tuple(
            p.strip()
            for p in os.getenv("BUS_TRACE_BLACKLIST_PREFIXES", "").split(",")
            if p.strip()
        )

        # Publisher socket
        self._pub = self._context.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, BUS_HWM)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.connect(BROKER_PUB_ADDR)

        # Subscriber socket
        self._sub = self._context.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, BUS_HWM)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.connect(BROKER_SUB_ADDR)

        # Saturation counters (thread-safe enough via GIL for simple int increments)
        self._stat_pub_ok: int = 0
        self._stat_pub_drop: int = 0
        self._stat_recv: int = 0
        self._stat_next_log: float = time.monotonic() + BUS_STATS_INTERVAL

        # Per-topic drop counters for detailed diagnostics
        self._drop_by_topic: dict[str, int] = {}

        self._tracer.emit(
            "bus_client_init",
            pub_addr=BROKER_PUB_ADDR,
            sub_addr=BROKER_SUB_ADDR,
            bus_hwm=BUS_HWM,
            blacklist_prefixes=list(self._trace_blacklist_prefixes),
        )

    # ------------------------------------------------------------------
    # Trace helpers
    # ------------------------------------------------------------------
    def _trace_topic_enabled(self, topic: str) -> bool:
        return not any(topic.startswith(p) for p in self._trace_blacklist_prefixes)

    def _next_trace_seq(self, topic: str) -> int:
        self._trace_seq[topic] += 1
        return self._trace_seq[topic]

    @staticmethod
    def _payload_size(topic_b: bytes, payload_b: bytes) -> int:
        return len(topic_b) + len(payload_b)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def subscribe(self, topic: str, handler: Callable):
        """Register a handler for a topic. Must be called before start()."""
        self._subscriptions[topic] = handler
        self._sub.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.log.debug(f"Subscribed to topic: {topic}")
        self._tracer.emit(
            "subscribe",
            topic=topic,
            callback=getattr(handler, "__name__", repr(handler)),
            rcvhwm=self._sub.getsockopt(zmq.RCVHWM),
        )

    def publish(self, topic: str, payload: dict | None = None) -> bool:
        """Publish a message on the bus."""
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"value": payload}

        trace_enabled = self._trace_topic_enabled(topic)
        seq: int | None = None

        # Shallow-copy: avoid mutating caller-owned payload.
        out_payload = dict(payload)

        if trace_enabled:
            seq = self._next_trace_seq(topic)
            out_payload["_trace"] = {
                "src_module": self.module_name,
                "topic": topic,
                "seq": seq,
                "ts_ns": time.monotonic_ns(),
            }

        try:
            topic_b = topic.encode("utf-8")
            payload_b = json.dumps(out_payload, separators=(",", ":"), default=str).encode("utf-8")
        except Exception as e:
            if trace_enabled:
                self._tracer.emit(
                    "publish_encode_error",
                    topic=topic,
                    seq=seq,
                    error=repr(e),
                )
            raise

        size = self._payload_size(topic_b, payload_b)

        if trace_enabled:
            self._tracer.emit(
                "publish_attempt",
                topic=topic,
                seq=seq,
                bytes=size,
                sndhwm=self._pub.getsockopt(zmq.SNDHWM),
            )

        send_start_ns = time.monotonic_ns()
        try:
            self._pub.send_multipart([topic_b, payload_b], flags=zmq.NOBLOCK)
            send_us = (time.monotonic_ns() - send_start_ns) / 1000.0
            self._stat_pub_ok += 1

            if trace_enabled:
                self._tracer.emit(
                    "publish_ok",
                    topic=topic,
                    seq=seq,
                    bytes=size,
                    send_us=send_us,
                    sndhwm=self._pub.getsockopt(zmq.SNDHWM),
                )
            return True

        except zmq.Again:
            self._stat_pub_drop += 1
            self._drop_by_topic[topic] = self._drop_by_topic.get(topic, 0) + 1

            if trace_enabled:
                self._tracer.emit(
                    "publish_drop",
                    topic=topic,
                    seq=seq,
                    bytes=size,
                    reason="zmq_again",
                    sndhwm=self._pub.getsockopt(zmq.SNDHWM),
                    total_drops=self._stat_pub_drop,
                    topic_drops=self._drop_by_topic[topic],
                )

            self.log.warning(
                "publish DROPPED (HWM saturated): topic=%s  "
                "[total_drops=%d this_topic=%d]",
                topic,
                self._stat_pub_drop,
                self._drop_by_topic[topic],
            )
            return False

        except Exception as e:
            if trace_enabled:
                self._tracer.emit(
                    "publish_error",
                    topic=topic,
                    seq=seq,
                    bytes=size,
                    error=repr(e),
                )
            raise

    def start(self, blocking: bool = True):
        """Start the receive loop. Set blocking=False to run in a thread."""
        self._running = True
        self._tracer.emit("bus_start", blocking=blocking)
        if blocking:
            self._receive_loop()
            return None
        t = threading.Thread(target=self._receive_loop, daemon=True)
        t.start()
        return t

    def stop(self):
        """Stop the receive loop and close sockets."""
        self._running = False
        self._tracer.emit(
            "bus_stop",
            recv=self._stat_recv,
            pub_ok=self._stat_pub_ok,
            pub_drop=self._stat_pub_drop,
        )
        try:
            self._pub.close(linger=0)
            self._sub.close(linger=0)
            self._context.term()
        finally:
            self._tracer.close()
        self.log.info("BusClient stopped.")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _log_stats(self) -> None:
        total = self._stat_pub_ok + self._stat_pub_drop
        drop_pct = (self._stat_pub_drop / total * 100) if total else 0.0
        top_drops = sorted(
            self._drop_by_topic.items(), key=lambda x: x[1], reverse=True
        )[:3]
        top_str = ", ".join(f"{t}:{n}" for t, n in top_drops) if top_drops else "none"
        self.log.debug(
            "BUS STATS | recv=%d  pub_ok=%d  pub_drop=%d (%.1f%%)  "
            "top_drop_topics=[%s]",
            self._stat_recv,
            self._stat_pub_ok,
            self._stat_pub_drop,
            drop_pct,
            top_str,
        )
        self._tracer.emit(
            "bus_stats",
            recv=self._stat_recv,
            pub_ok=self._stat_pub_ok,
            pub_drop=self._stat_pub_drop,
            drop_pct=drop_pct,
            top_drop_topics=dict(top_drops),
        )

    def _handle_received_message(self, frames: list[bytes]) -> None:
        recv_ns = time.monotonic_ns()

        if len(frames) < 2:
            self._tracer.emit("recv_malformed", frame_count=len(frames))
            return

        topic_b = frames[0]
        payload_b = frames[1]
        topic = topic_b.decode("utf-8", errors="replace")
        size = self._payload_size(topic_b, payload_b)

        try:
            payload = json.loads(payload_b.decode("utf-8"))
        except json.JSONDecodeError:
            self._tracer.emit("recv_invalid_json", topic=topic, bytes=size)
            self.log.warning(
                f"Received invalid JSON payload on topic '{topic}', "
                f"skipping. Payload: {payload_b}"
            )
            return

        if not isinstance(payload, dict):
            # Keep compatibility: handlers currently expect dict.
            payload = {"value": payload}

        # Remove instrumentation before delivering payload to application code.
        trace = payload.pop("_trace", None)

        seq = None
        src_module = None
        latency_us = None
        seq_gap = 0
        duplicate = False
        seq_rewind = False

        if isinstance(trace, dict):
            seq = trace.get("seq")
            src_module = trace.get("src_module")
            src_ts_ns = trace.get("ts_ns")

            if isinstance(src_ts_ns, int):
                latency_us = (recv_ns - src_ts_ns) / 1000.0

            if isinstance(seq, int) and src_module:
                key = (str(src_module), topic)
                last = self._trace_last_recv_seq.get(key)
                if last is not None:
                    if seq == last:
                        duplicate = True
                    elif seq > last + 1:
                        seq_gap = seq - last - 1
                    elif seq < last:
                        # Usually means publisher process restarted and seq began from 1 again.
                        seq_rewind = True
                if last is None or seq > last or seq_rewind:
                    self._trace_last_recv_seq[key] = seq

        self._stat_recv += 1

        handler = self._subscriptions.get(topic)
        if not handler:
            self._tracer.emit(
                "recv_no_handler",
                topic=topic,
                src_module=src_module,
                seq=seq,
                bytes=size,
                latency_us=latency_us,
            )
            return

        callback_name = getattr(handler, "__name__", repr(handler))
        cb_start_ns = time.monotonic_ns()
        try:
            handler(topic, payload)
            callback_us = (time.monotonic_ns() - cb_start_ns) / 1000.0
            self._tracer.emit(
                "recv_ok",
                topic=topic,
                src_module=src_module,
                seq=seq,
                bytes=size,
                latency_us=latency_us,
                callback_us=callback_us,
                seq_gap=seq_gap,
                duplicate=duplicate,
                seq_rewind=seq_rewind,
                rcvhwm=self._sub.getsockopt(zmq.RCVHWM),
                callback=callback_name,
            )
        except Exception as e:
            callback_us = (time.monotonic_ns() - cb_start_ns) / 1000.0
            self._tracer.emit(
                "callback_error",
                topic=topic,
                src_module=src_module,
                seq=seq,
                bytes=size,
                latency_us=latency_us,
                callback_us=callback_us,
                seq_gap=seq_gap,
                duplicate=duplicate,
                seq_rewind=seq_rewind,
                error=repr(e),
                callback=callback_name,
            )
            self.log.error(f"Error in callback for topic '{topic}'")
            self.log.exception(exc_info=e)

    def _receive_loop(self):
        self.log.info("Bus receive loop started.")
        while self._running:
            try:
                if self._sub.poll(timeout=500):  # ms
                    frames = self._sub.recv_multipart()
                    self._handle_received_message(frames)

                # Periodic saturation report
                if BUS_STATS_INTERVAL > 0:
                    now = time.monotonic()
                    if now >= self._stat_next_log:
                        self._log_stats()
                        self._stat_next_log = now + BUS_STATS_INTERVAL

            except KeyboardInterrupt:
                self.log.info("KeyboardInterrupt received — stopping.")
                break
            except zmq.ZMQError as e:
                if self._running:
                    self.log.error(f"ZMQ error: {e}")
                break
        self.log.info("Bus receive loop stopped.")
