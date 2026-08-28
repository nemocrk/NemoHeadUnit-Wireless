import asyncio
import threading
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from shared.inprocess_bus import InProcessBus


def test_inprocess_bus_delivers_typed_payload_on_subscriber_loop():
    async def scenario():
        bus = InProcessBus()
        received = []
        done = asyncio.Event()

        async def on_message(topic, payload):
            received.append((topic, payload, threading.get_ident()))
            done.set()

        bus.subscribe("projection.frame", on_message)
        bus.publish("projection.frame", {"sequence": 1})
        await asyncio.wait_for(done.wait(), timeout=1)

        assert received == [("projection.frame", {"sequence": 1}, threading.get_ident())]
        bus.close()

    asyncio.run(scenario())


def test_inprocess_bus_uses_explicit_wildcards_and_reports_drops():
    async def scenario():
        bus = InProcessBus(default_queue_size=1)
        gate = asyncio.Event()

        async def blocked_handler(topic, payload):
            await gate.wait()

        bus.subscribe("telemetry.*", blocked_handler)
        bus.publish("telemetry.one", 1)
        bus.publish("telemetry.two", 2)
        bus.publish("telemetry.three", 3)
        await asyncio.sleep(0)

        assert bus.metrics()["telemetry.*"]["dropped"] >= 1
        gate.set()
        bus.close()

    asyncio.run(scenario())
