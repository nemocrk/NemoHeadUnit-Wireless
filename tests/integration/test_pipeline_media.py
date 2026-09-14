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


@pytest.mark.asyncio
async def test_concurrent_multi_channel_stream_multiplexing(tmp_path):
    """Verify concurrent interleaved packets across Video, Audio, and Control channels."""
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
        await asyncio.wait_for(phone.read_frame(), timeout=2.0)

        video_ch = 3
        audio_ch = 4

        # Prepare payloads
        video_payload = struct.pack(">HQ", 0, 112233) + (b"\x00\x00\x00\x01\x65" + b"\xff" * 64)
        audio_payload = struct.pack(">HQ", 0, 445566) + (b"\x12\x34" * 32)

        ping_req = PingRequest()
        ping_req.timestamp = 555666777
        ping_payload = struct.pack(">H", ControlMessage.Enum.PING_REQUEST) + ping_req.SerializeToString()

        # Send interleaved packets rapidly
        await phone.send_frame(channel_id=video_ch, flags=0x03, payload=video_payload)
        await phone.send_frame(channel_id=0, flags=0x03, payload=ping_payload)
        await phone.send_frame(channel_id=audio_ch, flags=0x03, payload=audio_payload)

        # 1. Verify Video SHM frame received
        video_ev = await monitor.wait_for_event(
            "aa.frame.shm",
            filter_fn=lambda p: p.get("channel_id") == video_ch,
            timeout=2.0
        )
        assert video_ev["timestamp_us"] == 112233

        # 2. Verify Audio SHM frame received
        audio_ev = await monitor.wait_for_event(
            "aa.frame.shm",
            filter_fn=lambda p: p.get("channel_id") == audio_ch,
            timeout=2.0
        )
        assert audio_ev["timestamp_us"] == 445566

        # 3. Verify PingResponse returned on Channel 0
        resp_ch, _, resp_payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert resp_ch == 0
        msg_id = struct.unpack_from(">H", resp_payload, 0)[0]
        assert msg_id == ControlMessage.Enum.PING_RESPONSE

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
        await asyncio.gather(broker_task, tcp_task, chan_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_audio_focus_arbitration_state_machine(tmp_path):
    """Verify AudioFocus requests transition focus state and publish media.audio.focus events."""
    from backend.modules.bus_broker.main import BusBrokerModule
    from backend.modules.tcp_server.main import TCPServerModule
    from backend.modules.channel_manager.main import ChannelManagerModule
    from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
    from protos.oaa.audio.AudioFocusRequestMessage_pb2 import AudioFocusRequest
    from protos.oaa.audio.AudioFocusResponseMessage_pb2 import AudioFocusResponse
    from protos.oaa.audio.AudioFocusTypeEnum_pb2 import AudioFocusType
    from protos.oaa.audio.AudioFocusStateEnum_pb2 import AudioFocusState

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
        await asyncio.wait_for(phone.read_frame(), timeout=2.0)

        # 1. Phone requests Guidance Ducking (GAIN_TRANSIENT_MAY_DUCK)
        req_duck = AudioFocusRequest()
        req_duck.audio_focus_type = AudioFocusType.Enum.GAIN_TRANSIENT_MAY_DUCK
        payload_duck = struct.pack(">H", ControlMessage.Enum.AUDIO_FOCUS_REQUEST) + req_duck.SerializeToString()
        await phone.send_frame(channel_id=0, flags=0x03, payload=payload_duck)

        # Monitor captures media.audio.focus event
        duck_ev = await monitor.wait_for_event(
            "media.audio.focus",
            filter_fn=lambda p: p.get("focus_type") == AudioFocusType.Enum.GAIN_TRANSIENT_MAY_DUCK,
            timeout=2.0
        )
        assert duck_ev["focus_state"] == AudioFocusState.GAIN_TRANSIENT_GUIDANCE_ONLY
        assert duck_ev["is_paused"] is False

        # Read AudioFocusResponse from wire
        resp_ch, _, resp_payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert resp_ch == 0
        resp_msg_id = struct.unpack_from(">H", resp_payload, 0)[0]
        assert resp_msg_id == ControlMessage.Enum.AUDIO_FOCUS_RESPONSE

        resp_proto = AudioFocusResponse()
        resp_proto.ParseFromString(resp_payload[2:])
        assert resp_proto.audio_focus_state == AudioFocusState.GAIN_TRANSIENT_GUIDANCE_ONLY
        assert resp_proto.granted is True

        # 2. Phone releases audio focus (RELEASE)
        req_rel = AudioFocusRequest()
        req_rel.audio_focus_type = AudioFocusType.Enum.RELEASE
        payload_rel = struct.pack(">H", ControlMessage.Enum.AUDIO_FOCUS_REQUEST) + req_rel.SerializeToString()
        await phone.send_frame(channel_id=0, flags=0x03, payload=payload_rel)

        rel_ev = await monitor.wait_for_event(
            "media.audio.focus",
            filter_fn=lambda p: p.get("focus_type") == AudioFocusType.Enum.RELEASE,
            timeout=2.0
        )
        assert rel_ev["focus_state"] == AudioFocusState.LOSS
        assert rel_ev["is_paused"] is True

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()
        for t in [broker_task, tcp_task, chan_task]:
            t.cancel()
        await asyncio.gather(broker_task, tcp_task, chan_task, return_exceptions=True)
