# tests/integration/test_pipeline_media.py
import pytest
import asyncio
import struct
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.bus_monitor import BusMonitor
from tests.integration.harness.mock_phone import MockPhoneClient

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_video_streaming_pipeline(tmp_path):
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

        # Video media packet: message_id 0x0000 (AV_MEDIA_WITH_TIMESTAMP_INDICATION)
        # Message format: [u16 msg_id][u64 timestamp_us][nal_bytes...]
        msg_id = 0
        ts_us = 12345678
        nal_data = b"\x00\x00\x00\x01\x67\x42\x00\x1f\x96\x35\x40"
        media_body = struct.pack(">HQ", msg_id, ts_us) + nal_data

        video_ch = 3
        # Send on channel 3 (VIDEO in default channel_type_map)
        await phone.send_frame(channel_id=video_ch, flags=0x03, payload=media_body)

        # Verify aa.frame.shm published for video
        shm_ev = await monitor.wait_for_event(
            "aa.frame.shm",
            filter_fn=lambda p: p.get("channel_id") == video_ch,
            timeout=2.0
        )
        assert shm_ev is not None
        assert shm_ev["channel_id"] == video_ch
        assert shm_ev["shm_offset"] >= 0
        assert shm_ev["timestamp_us"] == ts_us
        assert shm_ev["payload_len"] == len(nal_data)

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
        await asyncio.gather(broker_task, tcp_task, chan_task, return_exceptions=True)

@pytest.mark.asyncio
async def test_audio_streaming_pipeline(tmp_path):
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

        # Audio media packet: msg_id 0x0000, timestamp_us, PCM data
        msg_id = 0
        ts_us = 99887766
        pcm_data = b"\x00\x01\x00\x02" * 64
        media_body = struct.pack(">HQ", msg_id, ts_us) + pcm_data

        audio_ch = 4
        # Send on channel 4 (AUDIO in default channel_type_map)
        await phone.send_frame(channel_id=audio_ch, flags=0x03, payload=media_body)

        shm_ev = await monitor.wait_for_event(
            "aa.frame.shm",
            filter_fn=lambda p: p.get("channel_id") == audio_ch,
            timeout=2.0
        )
        assert shm_ev is not None
        assert shm_ev["channel_id"] == audio_ch
        assert shm_ev["shm_offset"] >= 0
        assert shm_ev["timestamp_us"] == ts_us
        assert shm_ev["payload_len"] == len(pcm_data)

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
        await asyncio.gather(broker_task, tcp_task, chan_task, return_exceptions=True)
