# tests/integration/test_harness_mock_phone.py
import pytest
import asyncio

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_mock_phone_wire_framing():
    from tests.integration.harness.mock_phone import MockPhoneClient
    
    received = []
    async def handle_client(reader, writer):
        hdr = await reader.readexactly(4)
        ch_id = hdr[0]
        flags = hdr[1]
        length = int.from_bytes(hdr[2:4], "big")
        payload = await reader.readexactly(length)
        received.append((ch_id, flags, payload))
        writer.write(hdr + payload)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    
    phone = MockPhoneClient()
    await phone.connect("127.0.0.1", port)
    await phone.send_frame(channel_id=1, flags=0x03, payload=b"hello_wire")
    
    ch_id, flags, payload = await phone.read_frame()
    assert ch_id == 1
    assert flags == 0x03
    assert payload == b"hello_wire"
    assert received[0] == (1, 0x03, b"hello_wire")
    
    await phone.disconnect()
    server.close()
    await server.wait_closed()
