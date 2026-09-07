# tests/integration/test_pipeline_input_sensors.py
import pytest
import asyncio
import struct
from tests.integration.harness.environment import IntegrationEnvironment
from tests.integration.harness.bus_monitor import BusMonitor
from tests.integration.harness.mock_phone import MockPhoneClient
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.control.ChannelOpenRequestMessage_pb2 import ChannelOpenRequest
from protos.oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse
from protos.oaa.sensor.SensorChannelMessageIdsEnum_pb2 import SensorChannelMessage
from protos.oaa.sensor.SensorStartRequestMessage_pb2 import SensorStartRequestMessage
from protos.oaa.sensor.SensorStartResponseMessage_pb2 import SensorStartResponseMessage
from protos.oaa.sensor.SensorEventIndicationMessage_pb2 import SensorEventIndication
from protos.oaa.input.InputChannelMessageIdsEnum_pb2 import InputChannelMessage
from protos.oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
from protos.oaa.common.StatusEnum_pb2 import Status

pytestmark = pytest.mark.integration


async def _drain_channel_0_initial(phone: MockPhoneClient, timeout: float = 2.0):
    """Drain initial VERSION_REQUEST sent by HU on connection."""
    try:
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=timeout)
        return ch_id, flags, payload
    except asyncio.TimeoutError:
        return None, None, None


@pytest.mark.asyncio
async def test_touch_injection_to_phone(tmp_path):
    """Verify touch events published to the bus are encoded and delivered to phone via Input channel."""
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

        # Drain HU VERSION_REQUEST on ch 0
        await _drain_channel_0_initial(phone)

        # 1. Single touch press event
        chan_mod.publish("input.event", {
            "type": "press",
            "action": 0,
            "x": 400,
            "y": 300,
            "pointer_id": 0,
        })

        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == 1, f"Expected Input channel (1), got {ch_id}"
        assert len(payload) >= 2
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == InputChannelMessage.Enum.INPUT_EVENT_INDICATION

        ind = InputEventIndication()
        ind.ParseFromString(payload[2:])
        assert ind.touch_event.touch_action == 0
        assert len(ind.touch_event.touch_location) == 1
        assert ind.touch_event.touch_location[0].x == 400
        assert ind.touch_event.touch_location[0].y == 300

        # 2. Multi-touch drag / move event
        chan_mod.publish("input.event", {
            "type": "move",
            "action": 2,
            "pointers": [
                {"x": 150, "y": 250, "pointer_id": 0},
                {"x": 550, "y": 650, "pointer_id": 1},
            ],
        })

        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == 1
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == InputChannelMessage.Enum.INPUT_EVENT_INDICATION

        ind2 = InputEventIndication()
        ind2.ParseFromString(payload[2:])
        assert ind2.touch_event.touch_action == 2
        assert len(ind2.touch_event.touch_location) == 2
        assert ind2.touch_event.touch_location[0].x == 150
        assert ind2.touch_event.touch_location[0].y == 250
        assert ind2.touch_event.touch_location[1].x == 550
        assert ind2.touch_event.touch_location[1].y == 650

        # Teardown
        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()

        for t in [chan_task, tcp_task, broker_task]:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_media_key_injection_to_phone(tmp_path):
    """Verify media button events injected via input handler deliver press & release frames to phone."""
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
        await _drain_channel_0_initial(phone)

        # Inject media key (keycode 85: KEYCODE_MEDIA_PLAY_PAUSE)
        await chan_mod.input_handler.handle_media_key(key_code=85)

        # Expect press frame
        ch_id1, _, payload1 = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id1 == 1
        msg_id1 = struct.unpack(">H", payload1[:2])[0]
        assert msg_id1 == InputChannelMessage.Enum.INPUT_EVENT_INDICATION
        ind_press = InputEventIndication()
        ind_press.ParseFromString(payload1[2:])
        assert len(ind_press.button_event.button_events) == 1
        assert ind_press.button_event.button_events[0].keycode == 85
        assert ind_press.button_event.button_events[0].is_pressed is True

        # Expect release frame
        ch_id2, _, payload2 = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id2 == 1
        msg_id2 = struct.unpack(">H", payload2[:2])[0]
        assert msg_id2 == InputChannelMessage.Enum.INPUT_EVENT_INDICATION
        ind_rel = InputEventIndication()
        ind_rel.ParseFromString(payload2[2:])
        assert len(ind_rel.button_event.button_events) == 1
        assert ind_rel.button_event.button_events[0].keycode == 85
        assert ind_rel.button_event.button_events[0].is_pressed is False

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()

        for t in [chan_task, tcp_task, broker_task]:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_sensor_pipeline(tmp_path):
    """Verify phone can open Sensor channel (ch 2) and receive sensor telemetry indications."""
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
        await _drain_channel_0_initial(phone)

        # 1. Phone requests to open Sensor channel (ch 2)
        sensor_ch = 2
        open_req = ChannelOpenRequest()
        open_req.priority = 0
        open_req.channel_id = sensor_ch
        open_payload = struct.pack(">H", ControlMessage.Enum.CHANNEL_OPEN_REQUEST) + open_req.SerializeToString()
        await phone.send_frame(channel_id=sensor_ch, flags=0x03, payload=open_payload)

        # Phone receives ChannelOpenResponse
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == sensor_ch
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == ControlMessage.Enum.CHANNEL_OPEN_RESPONSE
        open_resp = ChannelOpenResponse()
        open_resp.ParseFromString(payload[2:])
        assert open_resp.status == Status.OK

        # 2. Phone sends SensorStartRequest for DRIVING_STATUS (sensor_type=13)
        start_req = SensorStartRequestMessage()
        start_req.sensor_type = 13  # DRIVING_STATUS
        req_payload = struct.pack(">H", SensorChannelMessage.Enum.SENSOR_REQUEST) + start_req.SerializeToString()
        await phone.send_frame(channel_id=sensor_ch, flags=0x03, payload=req_payload)

        # Phone receives SensorStartResponseMessage (0x8002)
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == sensor_ch
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == SensorChannelMessage.Enum.SENSOR_START_RESPONSE
        start_resp = SensorStartResponseMessage()
        start_resp.ParseFromString(payload[2:])
        assert start_resp.status == Status.OK

        # Phone receives SensorEventIndication (0x8003)
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == sensor_ch
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == SensorChannelMessage.Enum.SENSOR_EVENT_INDICATION
        sensor_event = SensorEventIndication()
        sensor_event.ParseFromString(payload[2:])
        assert len(sensor_event.driving_status) == 1
        assert sensor_event.driving_status[0].status == 0  # UNRESTRICTED

        # 3. Phone sends SensorStartRequest for NIGHT_DATA (sensor_type=10)
        night_req = SensorStartRequestMessage()
        night_req.sensor_type = 10  # NIGHT_DATA
        night_payload = struct.pack(">H", SensorChannelMessage.Enum.SENSOR_REQUEST) + night_req.SerializeToString()
        await phone.send_frame(channel_id=sensor_ch, flags=0x03, payload=night_payload)

        # Phone receives SensorStartResponseMessage
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == sensor_ch
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == SensorChannelMessage.Enum.SENSOR_START_RESPONSE

        # Phone receives SensorEventIndication for night mode
        ch_id, flags, payload = await asyncio.wait_for(phone.read_frame(), timeout=2.0)
        assert ch_id == sensor_ch
        msg_id = struct.unpack(">H", payload[:2])[0]
        assert msg_id == SensorChannelMessage.Enum.SENSOR_EVENT_INDICATION
        night_event = SensorEventIndication()
        night_event.ParseFromString(payload[2:])
        assert len(night_event.night_mode) == 1
        assert night_event.night_mode[0].is_night is False

        await phone.disconnect()
        await monitor.stop()
        await chan_mod.teardown()
        await tcp_mod.teardown()
        await broker.teardown()

        for t in [chan_task, tcp_task, broker_task]:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
