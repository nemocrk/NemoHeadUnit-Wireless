import pytest
from unittest.mock import MagicMock, patch
from shared.base_channel_module import BaseChannelModule
from shared.proto_utils import encode_aa_frame

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_bus():
    with patch("shared.base_module.BusClient") as mock:
        yield mock


class SampleChannelModule(BaseChannelModule):
    def __init__(self, ch_id=5):
        super().__init__(name="sample_ch", channel_id=ch_id, priority=3)
        self.received_frames = []

    async def run(self):
        pass

    async def teardown(self):
        pass

    async def on_frame(self, message_id: int, encrypted: bool, payload: bytes):
        self.received_frames.append((message_id, encrypted, payload))


@pytest.mark.asyncio
async def test_channel_module_lifecycle():
    mod = SampleChannelModule(ch_id=4)
    assert mod.channel_id == 4
    assert mod.is_channel_open is False

    await mod._handle_channel_open({"channel_id": 4, "descriptor": {"name": "video"}})
    assert mod.is_channel_open is True
    assert mod.channel_descriptor == {"name": "video"}

    await mod._handle_channel_close({"channel_id": 4})
    assert mod.is_channel_open is False


@pytest.mark.asyncio
async def test_channel_module_frame_dispatch():
    mod = SampleChannelModule(ch_id=7)
    frame = encode_aa_frame(channel_id=7, message_id=0x1234, proto_body=b"audio_bytes")

    await mod._handle_bus_frame({
        "payload_hex": frame["payload_hex"],
        "encrypted": True,
    })

    assert len(mod.received_frames) == 1
    msg_id, enc, data = mod.received_frames[0]
    assert msg_id == 0x1234
    assert enc is True
    assert data == b"audio_bytes"


@pytest.mark.asyncio
async def test_channel_module_send_frame():
    mod = SampleChannelModule(ch_id=2)
    mod.publish = MagicMock()

    await mod.send_frame(message_id=0x0008, proto_body=b"\x08\x00", control=True)

    mod.publish.assert_called_once()
    topic, frame_data = mod.publish.call_args[0]
    assert topic == "aa.frame.send"
    assert frame_data["channel_id"] == 2
