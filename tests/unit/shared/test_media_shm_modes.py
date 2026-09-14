"""
test_media_shm_modes.py — Unit tests for InMemoryRingBuffer and execution mode switching in BidirectionalMediaSHM.
"""

import os
import pytest
from shared.media_shm import (
    InMemoryRingBuffer,
    RingSharedMemoryBuffer,
    BidirectionalMediaSHM,
)

pytestmark = pytest.mark.unit


def test_in_memory_ring_buffer_write_read():
    buf = InMemoryRingBuffer(name="test_inmem", size=1024 * 1024)
    data = b"\x00\x00\x00\x01\x65\x88\x84\x00"
    ts = 987654321
    offset = buf.write_frame(stream_type=2, timestamp_us=ts, payload=data)

    assert offset >= 0
    st, out_ts, out_payload = buf.read_frame(offset)
    assert st == 2
    assert out_ts == ts & 0xFFFFFFFF
    assert out_payload == data
    buf.close()


def test_in_memory_ring_buffer_wrap_around():
    # 12-byte header + 20-byte payload = 32 bytes per frame
    buf = InMemoryRingBuffer(name="test_wrap", size=64)
    payload1 = b"A" * 20
    payload2 = b"B" * 20

    off1 = buf.write_frame(1, 100, payload1)
    assert off1 == 0

    # Next frame fits at offset 32
    off2 = buf.write_frame(1, 200, payload2)
    assert off2 == 32

    # Third frame exceeds remaining (64 - 64 = 0), so wraps to 0
    payload3 = b"C" * 20
    off3 = buf.write_frame(1, 300, payload3)
    assert off3 == 0

    _, _, data3 = buf.read_frame(off3)
    assert data3 == payload3
    buf.close()


def test_bidirectional_media_shm_multithreading(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multithreading")
    shm_host = BidirectionalMediaSHM(create=True)
    shm_client = BidirectionalMediaSHM(create=False)

    assert isinstance(shm_host.downstream, InMemoryRingBuffer)
    assert isinstance(shm_client.downstream, InMemoryRingBuffer)

    # In multithreading mode, same-name buffers share the in-memory instance
    offset = shm_host.downstream.write_frame(0, 1234, b"test_stream")
    _, _, data = shm_client.downstream.read_frame(offset)
    assert data == b"test_stream"

    shm_host.close()
    shm_client.close()


def test_bidirectional_media_shm_multiprocessing(monkeypatch):
    monkeypatch.setenv("NEMO_EXECUTION_MODE", "multiprocessing")
    shm = BidirectionalMediaSHM(create=True)
    assert isinstance(shm.downstream, RingSharedMemoryBuffer)
    shm.close()
