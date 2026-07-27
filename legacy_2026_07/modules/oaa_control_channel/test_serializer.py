#!/usr/bin/env python3
"""
Test script for OAA Messenger Protocol Serializer
"""

import sys
sys.path.insert(0, '/home/nemo/NemoHeadUnit-Wireless')

from modules.oaa_control_channel.serializer import Messenger, FrameType, FrameHeader

# Test 1: Small payload (single frame)
print('=== Test 1: Small payload (BULK frame) ===')
m = Messenger()
def hex_string_to_bytes(s):
    return bytes.fromhex(s)
input_frame = {'ssl_active': True, 'payload': '0800', 'channel_id': 2, 'message_id': 8}
frames = m.serialize_and_log(
    channel_id=input_frame['channel_id'],
    message_id=input_frame['message_id'],
    payload=hex_string_to_bytes(input_frame['payload']),
    ssl_active=input_frame['ssl_active']
)
print(f'Generated {len(frames)} frame(s)')
for i, f in enumerate(frames):
    print(f'Frame {i+1}: {f.hex()}')

# Test 2: Large payload (multi-frame)
print()
print('=== Test 2: Large payload (multi-frame) ===')
large_payload = 'A' * (5000)  # 5000 bytes
frames = m.serialize_and_log(
    channel_id=2,
    message_id=0x0007,
    payload=large_payload,
    ssl_active=True
)
print(f'Generated {len(frames)} frame(s)')
for i, f in enumerate(frames):
    print(f'Frame {i+1}: {f.hex()[:80]}...')