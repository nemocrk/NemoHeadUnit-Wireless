# tests/integration/harness/bus_monitor.py
import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional
import zmq.asyncio

logger = logging.getLogger(__name__)

class BusMonitor:
    """Subscribes to the ZMQ IPC bus and provides awaitable event barriers."""
    def __init__(self, bus_pub_address: str):
        self.bus_pub_address = bus_pub_address
        self.ctx = zmq.asyncio.Context()
        self.sub_sock: Optional[zmq.asyncio.Socket] = None
        self.running = False
        self.listen_task: Optional[asyncio.Task] = None
        self.events: List[tuple[str, Dict[str, Any]]] = []
        self._waiters: List[tuple[str, Optional[Callable[[Dict[str, Any]], bool]], asyncio.Future]] = []

    async def start(self):
        self.sub_sock = self.ctx.socket(zmq.SUB)
        self.sub_sock.connect(self.bus_pub_address)
        self.sub_sock.setsockopt_string(zmq.SUBSCRIBE, "")
        self.running = True
        self.listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self):
        while self.running:
            try:
                msg = await self.sub_sock.recv_multipart()
                if not msg:
                    continue
                topic = msg[0].decode("utf-8", errors="ignore")
                payload = {}
                if len(msg) > 1:
                    try:
                        payload = json.loads(msg[1].decode("utf-8", errors="ignore"))
                    except Exception:
                        payload = {"raw": msg[1]}
                self.events.append((topic, payload))
                
                # Check waiters
                for waiter in list(self._waiters):
                    w_topic, filter_fn, fut = waiter
                    if not fut.done() and (w_topic == "" or topic == w_topic):
                        if filter_fn is None or filter_fn(payload):
                            fut.set_result(payload)
                            self._waiters.remove(waiter)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    logger.error(f"Error in BusMonitor listen loop: {e}")

    async def wait_for_event(self, topic: str, filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None, timeout: float = 3.0) -> Dict[str, Any]:
        # Check existing events first
        for ev_topic, ev_payload in self.events:
            if ev_topic == topic and (filter_fn is None or filter_fn(ev_payload)):
                return ev_payload

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        waiter = (topic, filter_fn, fut)
        self._waiters.append(waiter)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            raise TimeoutError(f"Timed out waiting for bus event on topic '{topic}' after {timeout}s")

    def clear_events(self):
        """Clears captured historical events."""
        self.events.clear()

    def get_events(self, topic: Optional[str] = None) -> List[Dict[str, Any]]:
        if topic is None:
            return [payload for _, payload in self.events]
        return [payload for t, payload in self.events if t == topic]

    async def stop(self):
        self.running = False
        if self.listen_task:
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass
        if self.sub_sock:
            self.sub_sock.close(linger=0)
        try:
            self.ctx.destroy(linger=0)
        except Exception:
            pass
