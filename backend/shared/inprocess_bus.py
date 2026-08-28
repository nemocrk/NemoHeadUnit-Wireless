"""Bounded, loop-owned pub/sub transport for same-process module compositions."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BusMessage:
    topic: str
    payload: Any


@dataclass
class _Subscription:
    topic: str
    callback: Callable[[str, Any], Any]
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[BusMessage]
    task: asyncio.Task | None = None
    dropped: int = 0


class InProcessBus:
    """Dispatches typed messages through bounded queues owned by subscriber loops.

    Publishers never invoke handlers inline. This prevents a slow subscriber from
    blocking an I/O producer and preserves the ownership boundary between modules.
    """

    def __init__(self, default_queue_size: int = 64):
        if default_queue_size < 1:
            raise ValueError("default_queue_size must be positive")
        self._default_queue_size = default_queue_size
        self._subscriptions: list[_Subscription] = []
        self._lock = threading.RLock()
        self._closed = False

    def subscribe(
        self,
        topic: str,
        callback: Callable[[str, Any], Any],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        queue_size: int | None = None,
    ) -> Callable[[], None]:
        if not topic:
            raise ValueError("topic must not be empty")
        target_loop = loop or asyncio.get_running_loop()
        subscription = _Subscription(
            topic=topic,
            callback=callback,
            loop=target_loop,
            queue=asyncio.Queue(maxsize=queue_size or self._default_queue_size),
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("bus is closed")
            self._subscriptions.append(subscription)
        target_loop.call_soon_threadsafe(self._start_subscription, subscription)

        def unsubscribe() -> None:
            with self._lock:
                if subscription in self._subscriptions:
                    self._subscriptions.remove(subscription)
            subscription.loop.call_soon_threadsafe(self._stop_subscription, subscription)

        return unsubscribe

    def publish(self, topic: str, payload: Any) -> None:
        message = BusMessage(topic, payload)
        with self._lock:
            subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            if self._matches(subscription.topic, topic):
                subscription.loop.call_soon_threadsafe(self._enqueue, subscription, message)

    def metrics(self) -> dict[str, dict[str, int]]:
        with self._lock:
            subscriptions = tuple(self._subscriptions)
        return {
            subscription.topic: {
                "queued": subscription.queue.qsize(),
                "dropped": subscription.dropped,
            }
            for subscription in subscriptions
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
        for subscription in subscriptions:
            subscription.loop.call_soon_threadsafe(self._stop_subscription, subscription)

    @staticmethod
    def _matches(subscription_topic: str, topic: str) -> bool:
        return topic.startswith(subscription_topic[:-1]) if subscription_topic.endswith("*") else topic == subscription_topic

    @staticmethod
    def _enqueue(subscription: _Subscription, message: BusMessage) -> None:
        if subscription.queue.full():
            subscription.dropped += 1
            return
        subscription.queue.put_nowait(message)

    @staticmethod
    def _start_subscription(subscription: _Subscription) -> None:
        if subscription.task is None or subscription.task.done():
            subscription.task = asyncio.create_task(InProcessBus._consume(subscription))

    @staticmethod
    def _stop_subscription(subscription: _Subscription) -> None:
        if subscription.task and not subscription.task.done():
            subscription.task.cancel()

    @staticmethod
    async def _consume(subscription: _Subscription) -> None:
        try:
            while True:
                message = await subscription.queue.get()
                result = subscription.callback(message.topic, message.payload)
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            pass
