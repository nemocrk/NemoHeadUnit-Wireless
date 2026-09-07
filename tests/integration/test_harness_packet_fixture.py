# tests/integration/test_harness_packet_fixture.py
import pytest

pytestmark = pytest.mark.integration

def test_packet_fixture_capture_and_corruption():
    from tests.integration.harness.packet_fixture import PacketFixture
    
    fixture = PacketFixture()
    frame = fixture.build_frame(channel_id=1, flags=0x03, payload=b"valid_frame")
    fixture.record_frame(frame)
    
    assert len(fixture.captured) == 1
    assert fixture.captured[0] == frame
    
    corrupt_hdr = fixture.corrupt_header(frame)
    assert corrupt_hdr != frame
    assert len(corrupt_hdr) == len(frame)
    
    corrupt_len = fixture.corrupt_declared_length(frame, declared_length=9999)
    assert corrupt_len[2:4] == (9999).to_bytes(2, "big")
