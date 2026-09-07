# tests/integration/test_harness_bus_monitor.py
import pytest
import asyncio
import json
import zmq.asyncio

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_bus_monitor_captures_events():
    from tests.integration.harness.bus_monitor import BusMonitor
    
    ctx = zmq.asyncio.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind("tcp://127.0.0.1:18899")
    
    monitor = BusMonitor("tcp://127.0.0.1:18899")
    await monitor.start()
    await asyncio.sleep(0.05)  # Allow subscription to settle
    
    # Emit test event
    await pub.send_multipart([b"phone.connected", json.dumps({"device": "test_phone"}).encode("utf-8")])
    
    event = await monitor.wait_for_event("phone.connected", timeout=1.0)
    assert event["device"] == "test_phone"
    
    events = monitor.get_events("phone.connected")
    assert len(events) == 1
    assert events[0]["device"] == "test_phone"
    
    await monitor.stop()
    pub.close()
    ctx.term()
