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
        await asyncio.gather(broker_task, tcp_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_rapid_reconnection_churn(tmp_path):
    """Verify system handles rapid disconnect and immediate reconnect on the same TCP port."""
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule

    with IntegrationEnvironment(tmp_path) as env:
        broker = BusBrokerModule()
        broker.config["pub_port"] = 0
        broker.config["router_port"] = 0
        await broker.setup()
        broker_task = asyncio.create_task(broker.run())

        monitor = BusMonitor(broker.xpub_addr)
        await monitor.start()

        test_port = 5291
        tcp_mod = TCPServerModule()
        tcp_mod.config["port"] = test_port
        tcp_mod.config["autostart"] = True
        await tcp_mod.setup()
        tcp_mod.bus.start(blocking=False)
        tcp_task = asyncio.create_task(tcp_mod.run())

        start_ev = await monitor.wait_for_event("tcp.server.started", timeout=2.0)
        actual_port = start_ev["port"]
        assert actual_port == test_port

        chan_mod = ChannelManagerModule()
        await chan_mod.setup()
        chan_mod.bus.start(blocking=False)
        chan_task = asyncio.create_task(chan_mod.run())
        await asyncio.sleep(0.05)

        # 1. Connect Phone 1
        phone1 = MockPhoneClient()
        await phone1.connect("127.0.0.1", actual_port)
        await monitor.wait_for_event("tcp.session.connected", timeout=2.0)

        # Receive VERSION_REQUEST
        ch_id, flags, _ = await asyncio.wait_for(phone1.read_frame(), timeout=2.0)
        assert ch_id == 0

        # Abrupt disconnect of Phone 1 (e.g. WiFi flicker / cable pull)
        await phone1.disconnect()
        await monitor.wait_for_event("tcp.session.closed", timeout=2.0)
        monitor.clear_events()
        # Wait for TCP server listener auto-restart
        restart_ev = await monitor.wait_for_event("tcp.server.started", timeout=2.0)
        assert restart_ev["port"] == actual_port

        # 3. Rapid reconnect Phone 2 (< 50ms)
        phone2 = MockPhoneClient()
        await phone2.connect("127.0.0.1", actual_port)
        await monitor.wait_for_event("tcp.session.connected", timeout=2.0)

        # Verify Phone 2 receives fresh VERSION_REQUEST without socket errors or state conflict
        ch_id2, flags2, payload2 = await asyncio.wait_for(phone2.read_frame(), timeout=2.0)
        assert ch_id2 == 0
        msg_id2 = struct.unpack_from(">H", payload2, 0)[0]
        assert msg_id2 == 1  # ControlMessage.Enum.VERSION_REQUEST

        await phone2.disconnect()
        await monitor.wait_for_event("tcp.session.closed", timeout=2.0)

        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
        await asyncio.gather(broker_task, tcp_task, chan_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_control_ping_pong_keepalive(tmp_path):
    """Verify Channel 0 PingRequest generates matching PingResponse with preserved timestamp."""
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule
    from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
    from protos.oaa.control.PingRequestMessage_pb2 import PingRequest
    from protos.oaa.control.PingResponseMessage_pb2 import PingResponse

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

        chan_mod = ChannelManagerModule()
        await chan_mod.setup()
        chan_mod.bus.start(blocking=False)
        chan_task = asyncio.create_task(chan_mod.run())
        await asyncio.sleep(0.05)

        phone = MockPhoneClient()
        await phone.connect("127.0.0.1", actual_port)
        await monitor.wait_for_event("tcp.session.connected", timeout=2.0)

        # Read VERSION_REQUEST
        ch_id, flags, _ = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == 0

        # Send PingRequest on Channel 0
        ping_req = PingRequest()
        test_timestamp = 987654321
        ping_req.timestamp = test_timestamp
        ping_payload = struct.pack(">H", ControlMessage.Enum.PING_REQUEST) + ping_req.SerializeToString()
        await phone.send_frame(channel_id=0, flags=0x03, payload=ping_payload)

        # Expect PingResponse on Channel 0
        resp_ch, resp_flags, resp_payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert resp_ch == 0
        resp_msg_id = struct.unpack_from(">H", resp_payload, 0)[0]
        assert resp_msg_id == ControlMessage.Enum.PING_RESPONSE

        ping_resp = PingResponse()
        ping_resp.ParseFromString(resp_payload[2:])
        assert ping_resp.timestamp == test_timestamp

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
        await asyncio.gather(broker_task, tcp_task, chan_task, return_exceptions=True)
