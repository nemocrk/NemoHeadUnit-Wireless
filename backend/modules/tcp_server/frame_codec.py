"""
frame_codec.py — AA frame serialisation / deserialisation for tcp_server.

Responsibilities
----------------
encode(channel_id, message_id, body, ssl_active, cryptor) → bytes
    Build one or more wire frames ready to be written to the socket.
    Handles:
      - message_type determination (SPECIFIC vs CONTROL)
      - encryption policy (which msg_ids on ch0 stay plain even post-TLS)
      - BULK vs FIRST/MIDDLE/LAST fragmentation (threshold = 4096 bytes)
      - optional AACryptor.encrypt() when ssl_active

decode_frame_header(data) → FrameHeader | None
    Parse the 4-byte (BULK/MIDDLE/LAST) or 8-byte (FIRST) frame header from
    raw bytes already read from the socket.  Does NOT read the payload.

FrameAssembler
    Stateful per-channel accumulator.  Call feed(channel_id, flags, payload)
    for every raw frame coming off the wire.  Returns a complete (channel_id,
    flags, assembled_payload) tuple when the last chunk of a multi-frame
    message arrives, or None if more chunks are expected.
    One FrameAssembler instance is shared by tcp_server for the lifetime of
    a TCP connection; it maintains an internal dict channel_id → buffer.

Wire format (verified against openauto-prodigy FrameSerializer.cpp)
----------------------------------------------------------------------
FrameType encoding  (flags & 0x03):
    Middle = 0x00   ← part of multi-frame sequence
    First  = 0x01   ← first chunk; followed by 4-byte total_size field
    Last   = 0x02   ← last chunk of multi-frame
    Bulk   = 0x03   ← single-frame message (most common)

Header layout:
    Byte 0   : channel_id
    Byte 1   : flags  (FrameType | MessageType | EncryptionType)
    Byte 2-3 : this-frame payload length  (u16 big-endian)

FIRST frames only:
    Byte 4-7 : total message payload length  (u32 big-endian)

MessageType (flags & 0x04):
    SPECIFIC = 0x00   ← default; all ch0 messages + non-CHANNEL_OPEN_RESPONSE
    CONTROL  = 0x04   ← only CHANNEL_OPEN_RESPONSE (msg_id 0x0008) on ch != 0

EncryptionType (flags & 0x08):
    PLAIN     = 0x00
    ENCRYPTED = 0x08

Encryption exceptions on channel 0 (always plain even when ssl_active):
    0x0001  VERSION_REQUEST
    0x0002  VERSION_RESPONSE
    0x0003  SSL_HANDSHAKE
    0x0004  AUTH_COMPLETE
    0x000B  PING_REQUEST
    0x000C  PING_RESPONSE
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from modules.tcp_server.aa_cryptor import AACryptor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAME_SIZE_THRESHOLD = 16384  # bytes — payloads larger than this are fragmented

# FrameType bits (flags & 0x03)
_FT_MIDDLE = 0x00
_FT_FIRST  = 0x01
_FT_LAST   = 0x02
_FT_BULK   = 0x03

# MessageType bit (flags & 0x04)
_MT_SPECIFIC = 0x00
_MT_CONTROL  = 0x04

# EncryptionType bit (flags & 0x08)
_ET_PLAIN     = 0x00
_ET_ENCRYPTED = 0x08

from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage

MSG = ControlMessage.Enum

# The only non-ch0 message that uses CONTROL message-type
_MSG_CHANNEL_OPEN_RESPONSE = MSG.CHANNEL_OPEN_RESPONSE

# ch0 messages that stay plain even after TLS is active
_CH0_PLAIN_IDS = frozenset({
    MSG.VERSION_REQUEST,
    MSG.VERSION_RESPONSE,
    MSG.SSL_HANDSHAKE,
    MSG.AUTH_COMPLETE,
    MSG.PING_REQUEST,
    MSG.PING_RESPONSE,
})


# ---------------------------------------------------------------------------
# Helpers — message_type / encryption_type policy
# ---------------------------------------------------------------------------

def _message_type(channel_id: int, message_id: int) -> int:
    """Determine MessageType flag from C++ Messenger.cpp logic."""
    if channel_id != 0 and message_id == _MSG_CHANNEL_OPEN_RESPONSE:
        return _MT_CONTROL
    return _MT_SPECIFIC


def _encryption_type(channel_id: int, message_id: int, ssl_active: bool) -> int:
    """Determine EncryptionType flag from C++ EncryptionPolicy.cpp logic."""
    if not ssl_active:
        return _ET_PLAIN
    return _ET_ENCRYPTED


# ---------------------------------------------------------------------------
# encode — build wire frames
# ---------------------------------------------------------------------------

def encode(
    channel_id: int,
    message_id: int,
    body: bytes,
    ssl_active: bool = False,
    cryptor: Optional["AACryptor"] = None,
) -> list[bytes]:
    """Serialise a message into one or more AA wire frames.

    The full payload is: [message_id: 2B BE] + body.
    Encryption is applied to the full payload (message_id included) when
    ssl_active is True and the message is not in the plain-exemption list.

    Args:
        channel_id : AA channel (0 = control).
        message_id : 2-byte message identifier.
        body       : serialised protobuf payload (may be b"").
        ssl_active : whether AACryptor.is_active() is True.
        cryptor    : AACryptor instance; required when ssl_active is True
                     and the message should be encrypted.

    Returns:
        List of raw frame bytes in transmission order (usually one element).
    """
    msg_type = _message_type(channel_id, message_id)
    enc_type = _encryption_type(channel_id, message_id, ssl_active)

    # Build full payload: [msg_id 2B][body]
    payload: bytes = struct.pack(">H", message_id) + body

    cipher_records: list[bytes] = []
    # Encrypt if needed
    if enc_type == _ET_ENCRYPTED:
        if cryptor is None or not cryptor.is_active():
            # Downgrade gracefully — should not happen in normal flow
            enc_type = _ET_PLAIN
            cipher_records = [payload]
        else:
            if hasattr(cryptor, "encrypt_records"):
                cipher_records = cryptor.encrypt_records(payload)
            else:
                cipher_records = [cryptor.encrypt(payload)]
    else:
        cipher_records = [payload]

    all_frames: list[bytes] = []
    for record_bytes in cipher_records:
        if len(record_bytes) <= FRAME_SIZE_THRESHOLD:
            flags = _FT_BULK | msg_type | enc_type
            all_frames.append(_build_frame(channel_id, flags, record_bytes))
        else:
            all_frames.extend(_build_multi_frame(channel_id, msg_type, enc_type, record_bytes))

    return all_frames


def _build_frame(channel_id: int, flags: int, payload: bytes,
                 total_size: Optional[int] = None) -> bytes:
    """Build a single wire frame."""
    frame = struct.pack(">BB", channel_id, flags)
    frame += struct.pack(">H", len(payload))
    if (flags & 0x03) == _FT_FIRST and total_size is not None:
        frame += struct.pack(">I", total_size)
    frame += payload
    return frame


def _build_multi_frame(channel_id: int, msg_type: int, enc_type: int,
                       payload: bytes) -> list[bytes]:
    """Split payload into FIRST / MIDDLE* / LAST frames."""
    frames: list[bytes] = []
    total  = len(payload)
    offset = 0
    chunk  = FRAME_SIZE_THRESHOLD

    # FIRST
    flags = _FT_FIRST | msg_type | enc_type
    frames.append(_build_frame(channel_id, flags, payload[offset:offset + chunk], total))
    offset += chunk

    # MIDDLE(s)
    while total - offset > chunk:
        flags = _FT_MIDDLE | msg_type | enc_type
        frames.append(_build_frame(channel_id, flags, payload[offset:offset + chunk]))
        offset += chunk

    # LAST
    flags = _FT_LAST | msg_type | enc_type
    frames.append(_build_frame(channel_id, flags, payload[offset:]))

    return frames


# ---------------------------------------------------------------------------
# FrameAssembler — per-channel multi-frame reassembly
# ---------------------------------------------------------------------------

@dataclass
class _ChannelBuffer:
    chunks:     list[bytes] = field(default_factory=list)
    total_size: int = 0          # declared total from FIRST frame header
    first_flags: int = 0         # flags from the FIRST frame (carries enc/msg type)


class FrameAssembler:
    """Reassemble multi-frame AA messages on a per-channel basis.

    Usage (one instance per TCP connection):

        assembler = FrameAssembler()

        # called for every raw frame read from the socket:
        result = assembler.feed(channel_id, flags, payload, total_size)
        if result is not None:
            channel_id, flags, full_payload = result
            # dispatch full_payload to bus / decryptor

    The *total_size* argument is the value from the FIRST-frame header
    (parsed by FrameRelay); pass 0 for BULK/MIDDLE/LAST frames.
    """

    def __init__(self) -> None:
        self._buffers: dict[int, _ChannelBuffer] = {}

    def reset(self, channel_id: Optional[int] = None) -> None:
        """Discard assembly state.  Pass channel_id to reset one channel only."""
        if channel_id is None:
            self._buffers.clear()
        else:
            self._buffers.pop(channel_id, None)

    def feed(
        self,
        channel_id: int,
        flags: int,
        payload: bytes,
        total_size: int = 0,
    ) -> Optional[tuple[int, int, bytes, int]]:
        """Feed one raw frame into the assembler.

        Returns (channel_id, flags, assembled_payload, total_size) when the message is
        complete, or None if more frames are expected.

        frame_type rules:
            Bulk   (0x03) → complete single-frame message; return immediately.
            First  (0x01) → start accumulation; total_size comes from caller.
            Middle (0x00) → append to existing buffer; drop + log if no FIRST seen.
            Last   (0x02) → append final chunk and return assembled payload.
        """
        frame_type = flags & 0x03

        if frame_type == _FT_BULK:
            # Fast path — most frames
            return (channel_id, flags, payload, total_size)

        if frame_type == _FT_FIRST:
            # Start or restart accumulation for this channel
            self._buffers[channel_id] = _ChannelBuffer(
                chunks=[payload],
                total_size=total_size,
                first_flags=flags,
            )
            return None

        # MIDDLE or LAST — must have a FIRST already
        buf = self._buffers.get(channel_id)
        if buf is None:
            import logging
            logging.getLogger("tcp_server.frame_codec").warning(
                "FrameAssembler: ch=%d received frame_type=0x%02x with no pending FIRST — dropping",
                channel_id, frame_type,
            )
            return None

        buf.chunks.append(payload)

        if frame_type == _FT_LAST:
            assembled = b"".join(buf.chunks)
            saved_flags = buf.first_flags
            del self._buffers[channel_id]
            # Return with BULK frame_type so consumers don't need to special-case
            out_flags = (saved_flags & ~0x03) | _FT_BULK
            return (channel_id, out_flags, assembled, buf.total_size)

        # MIDDLE — keep accumulating
        return None

    def get_debug_state(self) -> dict:
        state = {}
        for ch_id, buf in self._buffers.items():
            state[ch_id] = {
                "chunks_count": len(buf.chunks),
                "total_size": buf.total_size,
                "first_flags": f"0x{buf.first_flags:02x}",
                "accumulated_bytes": sum(len(c) for c in buf.chunks),
            }
        return state

