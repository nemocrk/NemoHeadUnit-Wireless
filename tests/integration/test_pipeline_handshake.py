# tests/integration/test_pipeline_handshake.py
import pytest
import asyncio
import struct
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.bus_monitor import BusMonitor
from tests.integration.harness.mock_phone import MockPhoneClient
from tests.integration.harness.packet_fixture import PacketFixture

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_handshake_connection_and_disconnect(tmp_path):
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule

    with IntegrationEnvironment(tmp_path) as env:
        broker = BusBrokerModule()
        broker.config["pub_port"] = 0
        broker.config["router_port"] = 0
        await broker.setup()
        broker_task = asyncio.create_task(broker.run())
        await asyncio.sleep(0.05)

        monitor = BusMonitor(broker.xpub_addr)
        await monitor.start()

        tcp_mod = TCPServerModule()
        tcp_mod.config["port"] = 0
        tcp_mod.config["autostart"] = True
        await tcp_mod.setup()
        tcp_mod.bus.start(blocking=False)
        tcp_task = asyncio.create_task(tcp_mod.run())

        # Wait for tcp server to start and publish started event
        start_ev = await monitor.wait_for_event("tcp.server.started", timeout=2.0)
        actual_port = start_ev["port"]
        assert actual_port > 0

        chan_mod = ChannelManagerModule()
        await chan_mod.setup()
        chan_mod.bus.start(blocking=False)
        chan_task = asyncio.create_task(chan_mod.run())
        await asyncio.sleep(0.05)

        # Connect phone
        phone = MockPhoneClient()
        await phone.connect("127.0.0.1", actual_port)

        # 1. Verify TCP session connected on bus
        conn_ev = await monitor.wait_for_event("tcp.session.connected", timeout=2.0)
        assert conn_ev is not None

        # 2. ChannelManager sends VERSION_REQUEST (Channel 0) immediately upon connection
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == 0
        msg_id = struct.unpack_from(">H", payload, 0)[0]
        assert msg_id == 1  # ControlMessage.Enum.VERSION_REQUEST

        # 3. Disconnect phone
        await phone.disconnect()
        closed_ev = await monitor.wait_for_event("tcp.session.closed", timeout=2.0)
        assert closed_ev is not None

        # Teardown
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()

@pytest.mark.asyncio
async def test_wire_resilience_malformed_packets(tmp_path):
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule

    with IntegrationEnvironment(tmp_path) as env:
        broker = BusBrokerModule()
        broker.config["pub_port"] = 0
        broker.config["router_port"] = 0
        await broker.setup()
        broker_task = asyncio.create_task(broker.run())

        monitor = BusMonitor(broker.xpub_addr)
        await monitor.start()

        tcp_mod = TCPServerModule()
        tcp_mod.config["port"] = 0
        tcp_mod.config["autostart"] = True
        await tcp_mod.setup()
        tcp_mod.bus.start(blocking=False)
        tcp_task = asyncio.create_task(tcp_mod.run())

        start_ev = await monitor.wait_for_event("tcp.server.started", timeout=2.0)
        actual_port = start_ev["port"]

        # Build malformed frames with PacketFixture
        fixture = PacketFixture()
        # Normal frame then corrupt length
        frame = fixture.build_frame(channel_id=99, flags=0x03, payload=b"garbage")
        corrupted = fixture.corrupt_declared_length(frame, declared_length=65500)

        # Send corrupted packet directly via stream writer
        reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
        writer.write(corrupted)
        await writer.drain()

        # Server should not crash and should remain alive or close gracefully
        writer.close()
        await asyncio.sleep(0.1)

        await monitor.stop()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task]:
            t.cancel()
