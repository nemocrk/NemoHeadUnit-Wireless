"""
frame_sequences.py — E2E helper: pre-built AA frame sequence libraries.

Provides ready-to-use byte sequences for each phase of the Android Auto
protocol so that E2E smoke tests can drive the oaa_control_channel handshake
without re-implementing the protocol in every test file.

All frame builders return raw bytes that can be fed directly into
TcpPhoneClient.send_frame() or into oaa_control_channel.on_frame_ch0().

No ZMQ / BusClient dependency.

Module layout:
    VersionSequence        — VERSION_REQUEST / VERSION_RESPONSE exchange
    AuthSequence           — AUTH_COMPLETE and TLS handshake stubs
    ServiceDiscoverySeq    — SERVICE_DISCOVERY_REQUEST / RESPONSE exchange
    ChannelOpenSeq         — CHANNEL_OPEN_REQUEST / RESPONSE for each channel type
    MediaSequence          — minimal audio / video frame bursts
    ShutdownSequence       — SHUTDOWN_REQUEST / RESPONSE

Constants (message IDs) mirror oaa_control_channel/serializer.py:
    https://github.com/nemocrk/NemoHeadUnit-Wireless/blob/main/v2/modules/oaa_control_channel/serializer.py
"""

import struct
from typing import List, Tuple

# ---------------------------------------------------------------------------
# AA frame wire constants
# These mirror the values in oaa_control_channel/serializer.py.
# We redeclare them here so helpers have zero import dependency on source code.
# ---------------------------------------------------------------------------

# Channel IDs
CH_CONTROL  = 0    # Control channel — all handshake messages
CH_INPUT    = 1
CH_SENSOR   = 2
CH_VIDEO    = 3
CH_MEDIA    = 4
CH_SPEECH   = 5
CH_BLUETOOTH = 6
CH_WIFI     = 7

# Control channel message IDs (ch0)
MSG_VERSION_REQUEST          = 0x0001
MSG_VERSION_RESPONSE         = 0x0002
MSG_SSL_HANDSHAKE            = 0x0003
MSG_AUTH_COMPLETE            = 0x0004
MSG_SERVICE_DISCOVERY_REQ    = 0x0005
MSG_SERVICE_DISCOVERY_RESP   = 0x0006
MSG_CHANNEL_OPEN_REQ         = 0x0007
MSG_CHANNEL_OPEN_RESP        = 0x0008
MSG_PING_REQUEST             = 0x000B
MSG_PING_RESPONSE            = 0x000C
MSG_NAVIGATION_FOCUS_REQ     = 0x000D
MSG_NAVIGATION_FOCUS_RESP    = 0x000E
MSG_BYTEARRAY_MSG            = 0x000F
MSG_SHUTDOWN_REQUEST         = 0x0100
MSG_SHUTDOWN_RESPONSE        = 0x0101
MSG_VOICE_SESSION_REQ        = 0x000A
MSG_AUDIO_FOCUS_REQ          = 0x000A   # same wire id, different context

# Frame flags
FLAG_ENCRYPTED = 0x08
FLAG_FIRST     = 0x01
FLAG_LAST      = 0x02

# Version constants
AA_MAJOR_VERSION = 1
AA_MINOR_VERSION = 0

# Status codes
STATUS_OK = 0

# Type alias
FrameBytes = bytes
FrameTuple = Tuple[int, int, int, bytes]  # (channel_id, flags, msg_id, body)


# ---------------------------------------------------------------------------
# Internal frame encoder (matches phone_mock.aa_frame_encode)
# ---------------------------------------------------------------------------

def _encode(channel_id: int, msg_id: int, body: bytes = b"", flags: int = 0) -> FrameBytes:
    """Pack one AA frame: channel_id(u8) | flags(u8) | msg_id(u16 BE) | body_len(u16 BE) | body."""
    return struct.pack(">BBHH", channel_id, flags, msg_id, len(body)) + body


# ---------------------------------------------------------------------------
# VERSION exchange (phone → HU, then HU → phone)
# ---------------------------------------------------------------------------

class VersionSequence:
    """
    Helpers for the VERSION_REQUEST / VERSION_RESPONSE exchange.

    The HU sends VERSION_REQUEST first after tcp.session.connected.
    The phone replies with VERSION_RESPONSE.

    Usage in smoke tests:
        frame = client.recv_frame()          # receive HU VERSION_REQUEST
        assert frame[2] == MSG_VERSION_REQUEST
        client.send_frame(
            CH_CONTROL,
            MSG_VERSION_RESPONSE,
            VersionSequence.response_body(),
        )
    """

    @staticmethod
    def request_body(major: int = AA_MAJOR_VERSION, minor: int = AA_MINOR_VERSION) -> bytes:
        """Build VERSION_REQUEST body: major(u16 BE) + minor(u16 BE)."""
        return struct.pack(">HH", major, minor)

    @staticmethod
    def response_body(
        major: int = AA_MAJOR_VERSION,
        minor: int = AA_MINOR_VERSION,
        status: int = STATUS_OK,
    ) -> bytes:
        """Build VERSION_RESPONSE body: major(u16) + minor(u16) + status(u16)."""
        return struct.pack(">HHH", major, minor, status)

    @staticmethod
    def request_frame() -> FrameBytes:
        return _encode(CH_CONTROL, MSG_VERSION_REQUEST, VersionSequence.request_body())

    @staticmethod
    def response_frame() -> FrameBytes:
        return _encode(CH_CONTROL, MSG_VERSION_RESPONSE, VersionSequence.response_body())

    @staticmethod
    def version_mismatch_frame() -> FrameBytes:
        """VERSION_RESPONSE with status=1 (mismatch) to trigger error handling."""
        return _encode(
            CH_CONTROL,
            MSG_VERSION_RESPONSE,
            struct.pack(">HHH", 99, 0, 1),
        )


# ---------------------------------------------------------------------------
# AUTH / TLS stubs
# ---------------------------------------------------------------------------

class AuthSequence:
    """
    Minimal TLS / AUTH stubs for smoke tests that bypass real TLS.

    Real TLS is delegated to tcp_server (AACryptor).  In smoke tests that stub
    the cryptor, these helpers provide the raw bytes to inject into
    oaa_control_channel.on_frame_ch0() or via bus topics.
    """

    @staticmethod
    def ssl_handshake_frame(blob: bytes = b"\x00" * 8) -> FrameBytes:
        """SSL_HANDSHAKE frame with a synthetic TLS ClientHello blob."""
        return _encode(CH_CONTROL, MSG_SSL_HANDSHAKE, blob)

    @staticmethod
    def auth_complete_frame() -> FrameBytes:
        """AUTH_COMPLETE frame (empty body)."""
        return _encode(CH_CONTROL, MSG_AUTH_COMPLETE, b"")

    @staticmethod
    def tls_handshake_payload(blob: bytes = b"\x16\x03\x01" + b"\x00" * 40) -> dict:
        """
        Bus payload for tcp.server.tls_handshake topic
        (injected directly into oaa_control_channel.on_tls_handshake).
        """
        return {"outgoing_hex": blob.hex()}


# ---------------------------------------------------------------------------
# SERVICE_DISCOVERY exchange
# ---------------------------------------------------------------------------

class ServiceDiscoverySeq:
    """
    Helpers for SERVICE_DISCOVERY_REQUEST / RESPONSE.

    The HU sends SERVICE_DISCOVERY_REQUEST after TLS is complete.
    The phone replies with SERVICE_DISCOVERY_RESPONSE listing its supported channels.

    Phone-side response body is a raw protobuf (MediaInfoResponseMessage).
    We provide a minimal stub that passes validation.
    """

    @staticmethod
    def request_body() -> bytes:
        """Minimal SERVICE_DISCOVERY_REQUEST body (phone model/brand)."""
        # Real proto: ServiceDiscoveryRequest{ device_name, device_brand }
        # We use a simple proto field encoding for "TestPhone" as device_name (field 1, wire 2).
        name_bytes = b"TestPhone"
        return bytes([0x0A, len(name_bytes)]) + name_bytes

    @staticmethod
    def request_frame() -> FrameBytes:
        return _encode(CH_CONTROL, MSG_SERVICE_DISCOVERY_REQ, ServiceDiscoverySeq.request_body())

    @staticmethod
    def response_body_minimal() -> bytes:
        """
        Minimal SERVICE_DISCOVERY_RESPONSE body (empty channel list).
        Used for tests that verify the HU handles missing channel config gracefully.
        """
        return b""  # empty proto == valid

    @staticmethod
    def response_body_with_channels(channel_ids: List[int]) -> bytes:
        """
        Build a stub SERVICE_DISCOVERY_RESPONSE listing the given channel IDs.
        Encodes each channel_id as proto field 1 of a ChannelDescriptor sub-message.
        """
        # Simplified: for each channel_id, encode: field 3 (channels, wire 2) containing
        # a sub-message with field 1 (channel_id, wire 0) = channel_id.
        body = b""
        for ch_id in channel_ids:
            # sub-message: field 1 varint ch_id
            sub = bytes([0x08, ch_id])
            # outer field 3 length-delimited
            body += bytes([0x1A, len(sub)]) + sub
        return body

    @staticmethod
    def response_frame_minimal() -> FrameBytes:
        return _encode(CH_CONTROL, MSG_SERVICE_DISCOVERY_RESP, ServiceDiscoverySeq.response_body_minimal())

    @staticmethod
    def response_frame_with_channels(channel_ids: List[int]) -> FrameBytes:
        return _encode(
            CH_CONTROL,
            MSG_SERVICE_DISCOVERY_RESP,
            ServiceDiscoverySeq.response_body_with_channels(channel_ids),
        )


# ---------------------------------------------------------------------------
# CHANNEL_OPEN exchange
# ---------------------------------------------------------------------------

class ChannelOpenSeq:
    """
    Helpers for CHANNEL_OPEN_REQUEST / RESPONSE per-channel.
    """

    @staticmethod
    def request_body(channel_id: int) -> bytes:
        """CHANNEL_OPEN_REQUEST body for the given channel_id."""
        # proto: field 1 varint = channel_id
        return bytes([0x08, channel_id])

    @staticmethod
    def response_body(channel_id: int, status: int = STATUS_OK) -> bytes:
        """CHANNEL_OPEN_RESPONSE body: channel_id + status."""
        return bytes([0x08, channel_id, 0x10, status])

    @staticmethod
    def request_frame(channel_id: int) -> FrameBytes:
        return _encode(CH_CONTROL, MSG_CHANNEL_OPEN_REQ, ChannelOpenSeq.request_body(channel_id))

    @staticmethod
    def response_frame(channel_id: int, status: int = STATUS_OK) -> FrameBytes:
        return _encode(CH_CONTROL, MSG_CHANNEL_OPEN_RESP, ChannelOpenSeq.response_body(channel_id, status))

    @staticmethod
    def open_all_channels_sequence(channel_ids: List[int]) -> List[FrameBytes]:
        """Return list of CHANNEL_OPEN_REQUEST frames for all given channels."""
        return [ChannelOpenSeq.request_frame(ch) for ch in channel_ids]


# ---------------------------------------------------------------------------
# PING exchange
# ---------------------------------------------------------------------------

class PingSequence:
    """
    PING_REQUEST / PING_RESPONSE helpers.
    """

    @staticmethod
    def request_body(timestamp_us: int = 0) -> bytes:
        return struct.pack(">Q", timestamp_us)

    @staticmethod
    def response_body(timestamp_us: int = 0) -> bytes:
        return struct.pack(">Q", timestamp_us)

    @staticmethod
    def request_frame(timestamp_us: int = 0) -> FrameBytes:
        return _encode(CH_CONTROL, MSG_PING_REQUEST, PingSequence.request_body(timestamp_us))

    @staticmethod
    def response_frame(timestamp_us: int = 0) -> FrameBytes:
        return _encode(CH_CONTROL, MSG_PING_RESPONSE, PingSequence.response_body(timestamp_us))


# ---------------------------------------------------------------------------
# MEDIA frames (audio / video bursts)
# ---------------------------------------------------------------------------

class MediaSequence:
    """
    Minimal audio and video frame bursts for the channel layer smoke tests.
    All media frames are on non-control channels (CH_MEDIA, CH_VIDEO).
    """

    @staticmethod
    def audio_frame(channel_id: int = CH_MEDIA, payload: bytes = b"\xAA" * 64) -> FrameBytes:
        """Single audio frame with synthetic PCM-like payload."""
        return _encode(channel_id, 0x0001, payload)

    @staticmethod
    def audio_burst(
        count: int = 5,
        channel_id: int = CH_MEDIA,
        frame_size: int = 64,
    ) -> List[FrameBytes]:
        """Return a list of *count* audio frames with incrementing byte values."""
        return [
            MediaSequence.audio_frame(channel_id, bytes([i % 256] * frame_size))
            for i in range(count)
        ]

    @staticmethod
    def video_idr_frame(channel_id: int = CH_VIDEO, size: int = 512) -> FrameBytes:
        """Synthetic H.264 IDR (keyframe) — starts with 0x00 0x00 0x00 0x01."""
        nal_header = b"\x00\x00\x00\x01\x65"
        payload    = nal_header + b"\xFF" * (size - len(nal_header))
        return _encode(channel_id, 0x0001, payload)

    @staticmethod
    def video_p_frame(channel_id: int = CH_VIDEO, size: int = 256) -> FrameBytes:
        """Synthetic H.264 P-frame."""
        nal_header = b"\x00\x00\x00\x01\x41"
        payload    = nal_header + b"\xCC" * (size - len(nal_header))
        return _encode(channel_id, 0x0001, payload)


# ---------------------------------------------------------------------------
# SHUTDOWN exchange
# ---------------------------------------------------------------------------

class ShutdownSequence:
    """
    SHUTDOWN_REQUEST / RESPONSE helpers.

    HU sends SHUTDOWN_REQUEST before closing the TCP connection.
    Phone should reply with SHUTDOWN_RESPONSE.
    """

    @staticmethod
    def request_body(reason: int = 0) -> bytes:
        """SHUTDOWN_REQUEST body: reason(u32 BE)."""
        return struct.pack(">I", reason)

    @staticmethod
    def response_body(status: int = STATUS_OK) -> bytes:
        return struct.pack(">I", status)

    @staticmethod
    def request_frame(reason: int = 0) -> FrameBytes:
        return _encode(CH_CONTROL, MSG_SHUTDOWN_REQUEST, ShutdownSequence.request_body(reason))

    @staticmethod
    def response_frame(status: int = STATUS_OK) -> FrameBytes:
        return _encode(CH_CONTROL, MSG_SHUTDOWN_RESPONSE, ShutdownSequence.response_body(status))


# ---------------------------------------------------------------------------
# Composite sequences (full multi-frame flows)
# ---------------------------------------------------------------------------

class FullHandshakeSequence:
    """
    Builds the complete phone-side frame sequence for a minimal AA handshake:
        1. VERSION_RESPONSE
        2. SSL_HANDSHAKE (stub blob)
        3. SERVICE_DISCOVERY_REQUEST
        4. CHANNEL_OPEN_REQUEST for each channel
        5. PING_RESPONSE (optional keepalive)

    Used by smoke tests that verify oaa_control_channel reaches ACTIVE state
    with a stubbed cryptor (no real TLS).
    """

    @staticmethod
    def phone_response_sequence(
        channel_ids: List[int] = None,
        include_ping: bool = False,
    ) -> List[FrameBytes]:
        """
        Return the ordered list of frames the phone sends to drive the handshake.
        Order:
          version_response → ssl_handshake_stub → service_discovery_request
          → channel_open_requests → (optional) ping_response
        """
        if channel_ids is None:
            channel_ids = [CH_INPUT, CH_SENSOR, CH_VIDEO, CH_MEDIA]

        frames: List[FrameBytes] = [
            VersionSequence.response_frame(),
            AuthSequence.ssl_handshake_frame(),
            ServiceDiscoverySeq.request_frame(),
        ]
        frames.extend(ChannelOpenSeq.open_all_channels_sequence(channel_ids))
        if include_ping:
            frames.append(PingSequence.response_frame())
        return frames

    @staticmethod
    def as_bus_payloads(
        channel_ids: List[int] = None,
    ) -> List[dict]:
        """
        Convert the phone response sequence into bus payload dicts suitable
        for injecting directly into oaa_control_channel.on_frame_ch0().

        Returns list of {channel_id, message_id, encrypted, payload_hex} dicts.
        """
        if channel_ids is None:
            channel_ids = [CH_INPUT, CH_SENSOR, CH_VIDEO, CH_MEDIA]

        frames = FullHandshakeSequence.phone_response_sequence(channel_ids)
        payloads = []
        for frame_bytes in frames:
            if len(frame_bytes) < 6:
                continue
            ch, flags, msg_id, body_len = struct.unpack_from(">BBHH", frame_bytes)
            body = frame_bytes[6: 6 + body_len]
            payloads.append({
                "channel_id":  ch,
                "message_id":  msg_id,
                "encrypted":   bool(flags & FLAG_ENCRYPTED),
                "payload_hex": body.hex(),
            })
        return payloads
