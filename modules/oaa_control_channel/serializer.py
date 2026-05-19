"""
OAA Messenger Protocol Serializer
End-to-end Python implementation matching C++ Messenger.cpp

Supports:
- All message types (Control, Specific, ChannelOpenResponse)
- Encryption types (Plain, Encrypted)
- BULK framing with configurable fragmentation
- Big-endian encoding for sizes
- Console logging for testing/debugging
"""

import struct
import logging
from typing import List, Optional

# Configuration
DEFAULT_FRAME_SIZE_THRESHOLD = 4096
DEFAULT_LOG_LEVEL = logging.INFO


class MessageType:
    """Message type flags as per C++ implementation"""
    SPECIFIC = 0x00
    CONTROL = 0x04
    CHANNEL_OPEN_RESPONSE = 0x04  # ChannelOpenResponse uses CONTROL


class EncryptionType:
    """Encryption type flags as per C++ implementation"""
    PLAIN = 0x00
    ENCRYPTED = 0x08


class FrameType:
    """Frame type values as per C++ implementation"""
    FIRST = 0x01
    MIDDLE = 0x02
    LAST = 0x03
    BULK = FIRST | LAST


class ChannelId:
    """Channel ID constants"""
    CONTROL = 0
    INPUT = 1
    SENSOR = 2
    VIDEO = 3
    MEDIA_AUDIO = 4
    SPEECH_AUDIO = 5
    SYSTEM_AUDIO = 6
    AV_INPUT = 7
    BLUETOOTH = 8
    NAVIGATION = 9
    MEDIA_STATUS = 10
    PHONE_STATUS = 11
    WIFI = 12


class FrameHeader:
    """
    Frame header: 2 bytes
    - Byte 0: Channel ID
    - Byte 1: FrameType | EncryptionType | MessageType (flags)
    """

    def __init__(self, channel_id: int, frame_type: int, 
                 encryption_type: int, message_type: int):
        self.channel_id = channel_id
        self.frame_type = frame_type
        self.encryption_type = encryption_type
        self.message_type = message_type

    def serialize(self) -> bytes:
        """Serialize header to 2 bytes"""
        # Byte 0: Channel ID
        header_byte0 = self.channel_id
        # Byte 1: FrameType | EncryptionType | MessageType
        header_byte1 = (
            self.frame_type |
            self.encryption_type |
            self.message_type
        )
        return struct.pack('<BB', header_byte0, header_byte1)

    @classmethod
    def parse(cls, data: bytes) -> 'FrameHeader':
        """Parse header from 2-byte data"""
        if len(data) < 2:
            raise ValueError("Header must be at least 2 bytes")
        
        channel_id = data[0]
        flags = data[1]
        
        return cls(
            channel_id=channel_id,
            frame_type=flags & 0x03,
            encryption_type=flags & 0x08,
            message_type=flags & 0x04
        )


class FrameSerializer:
    """
    End-to-end frame serializer matching C++ FrameSerializer.cpp
    
    Supports:
    - Single-frame (BULK) for messages <= threshold
    - Multi-frame (FIRST/MIDDLE/LAST) for larger messages
    - Configurable fragmentation threshold
    - Big-endian size fields
    """

    def __init__(self, frame_size_threshold: int = DEFAULT_FRAME_SIZE_THRESHOLD,
                 log_level: int = DEFAULT_LOG_LEVEL):
        self.frame_size_threshold = frame_size_threshold
        self.log_level = log_level
        self._setup_logging()

    def _setup_logging(self):
        """Configure logging for console output"""
        logger = logging.getLogger('oaa.serializer')
        logger.setLevel(self.log_level)
        handler = logging.StreamHandler()
        handler.setLevel(self.log_level)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        if not logger.hasHandlers():
            logger.addHandler(handler)

    def _log(self, message: str, level: int = logging.INFO):
        """Log message at specified level"""
        logger = logging.getLogger('oaa.serializer')
        if logger.isEnabledFor(level):
            logger.log(level, message)

    def _build_frame(self, header: FrameHeader, payload: bytes,
                     total_size: Optional[int] = None) -> bytes:
        """
        Build a single frame with header, size field, and payload.
        
        Args:
            header: Frame header
            payload: Payload data
            total_size: For FIRST frames, total message size (payload + message ID)
        
        Returns:
            Serialized frame bytes
        """
        # Header (2 bytes)
        frame = header.serialize()
        
        # Size field
        if header.frame_type == FrameType.FIRST:
            # FIRST: 2B frame payload size (BE) + 4B total message size (BE)
            frame_payload_size = struct.pack('>H', len(payload))
            total_size_be = struct.pack('>I', total_size)
            frame += frame_payload_size + total_size_be
        else:
            # BULK/MIDDLE/LAST: 2B frame payload size (BE)
            frame_payload_size = struct.pack('>H', len(payload))
            frame += frame_payload_size
        
        # Payload
        frame += payload
        
        return frame

    def _serialize_single_frame(self, header: FrameHeader, payload: bytes) -> bytes:
        """
        Serialize as single BULK frame (for messages <= threshold)
        """
        return self._build_frame(header, payload)

    def _serialize_multi_frame(self, header: FrameHeader, payload: bytes,
                                total_size: int) -> List[bytes]:
        """
        Serialize as FIRST/MIDDLE/LAST frames (for messages > threshold)
        
        Returns list of frames in transmission order
        """
        frames = []
        offset = 0
        chunk_size = self.frame_size_threshold
        
        # FIRST frame
        header_first = FrameHeader(
            channel_id=header.channel_id,
            frame_type=FrameType.FIRST,
            encryption_type=header.encryption_type,
            message_type=header.message_type
        )
        first_payload = payload[offset:offset + chunk_size]
        frames.append(self._build_frame(header_first, first_payload, total_size))
        offset += chunk_size
        
        # MIDDLE frames
        while total_size - offset > chunk_size:
            header_middle = FrameHeader(
                channel_id=header.channel_id,
                frame_type=FrameType.MIDDLE,
                encryption_type=header.encryption_type,
                message_type=header.message_type
            )
            middle_payload = payload[offset:offset + chunk_size]
            frames.append(self._build_frame(header_middle, middle_payload))
            offset += chunk_size
        
        # LAST frame
        header_last = FrameHeader(
            channel_id=header.channel_id,
            frame_type=FrameType.LAST,
            encryption_type=header.encryption_type,
            message_type=header.message_type
        )
        last_payload = payload[offset:]
        frames.append(self._build_frame(header_last, last_payload))
        
        return frames

    def serialize(self, channel_id: int, message_type: int,
                  encryption_type: int, payload: bytes) -> List[bytes]:
        """
        End-to-end serialization matching C++ FrameSerializer::serialize()
        
        Args:
            channel_id: Channel ID (0-255)
            message_type: Message type (0x00=Specific, 0x04=Control, 0x04=ChannelOpenResponse)
            encryption_type: Encryption type (0x00=Plain, 0x08=Encrypted)
            payload: Payload data (bytes)
        
        Returns:
            List of frames (bytes) in transmission order
        """
        # Validate inputs
        if not isinstance(channel_id, int) or not 0 <= channel_id <= 255:
            raise ValueError("Channel ID must be 0-255")
        
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("Payload must be bytes or bytearray")
        
        # Determine frame type based on payload size
        if len(payload) <= self.frame_size_threshold:
            # Single BULK frame
            header = FrameHeader(
                channel_id=channel_id,
                frame_type=FrameType.BULK,
                encryption_type=encryption_type,
                message_type=message_type
            )
            return [self._serialize_single_frame(header, payload)]
        
        # Multi-frame: FIRST/MIDDLE/LAST
        header = FrameHeader(
            channel_id=channel_id,
            frame_type=FrameType.FIRST,
            encryption_type=encryption_type,
            message_type=message_type
        )
        total_size = len(payload)
        return self._serialize_multi_frame(header, payload, total_size)

    def serialize_raw(self, channel_id: int, frame_type: int,
                       encryption_type: int, message_type: int,
                       payload: bytes) -> bytes:
        """
        Raw serialization matching C++ Messenger::sendRaw()
        
        Args:
            channel_id: Channel ID
            frame_type: Frame type (First, Middle, Bulk, Last)
            encryption_type: Encryption type
            message_type: Message type
            payload: Payload data
        
        Returns:
            Single frame bytes
        """
        header = FrameHeader(
            channel_id=channel_id,
            frame_type=frame_type,
            encryption_type=encryption_type,
            message_type=message_type
        )
        
        frame = header.serialize()
        
        # Size field
        if frame_type == FrameType.FIRST:
            # FIRST: 2B frame payload size + 4B total message size
            frame_payload_size = struct.pack('>H', len(payload))
            total_size = struct.pack('>I', len(payload))
            frame += frame_payload_size + total_size
        else:
            # BULK/MIDDLE/LAST: 2B frame payload size
            frame_payload_size = struct.pack('>H', len(payload))
            frame += frame_payload_size
        
        # Payload
        frame += payload
        
        return frame


class Messenger:
    """
    End-to-end messenger matching C++ Messenger.cpp
    
    This class provides the complete flow from payload to serialized frames,
    including:
    - Message type determination
    - Encryption policy
    - Frame serialization
    - Console logging of hex representation
    """

    def __init__(self, frame_size_threshold: int = DEFAULT_FRAME_SIZE_THRESHOLD,
                 log_level: int = DEFAULT_LOG_LEVEL):
        self.serializer = FrameSerializer(frame_size_threshold, log_level)
        self._setup_logging()

    def _setup_logging(self):
        """Configure logging"""
        logger = logging.getLogger('oaa.messenger')
        logger.setLevel(self.serializer.log_level)

    def _log(self, message: str, level: int = logging.INFO):
        """Log message"""
        logger = logging.getLogger('oaa.messenger')
        logger.log(level, message)

    def _get_message_type(self, channel_id: int, message_id: int) -> int:
        """
        Determine message type based on C++ Messenger.cpp logic
        """
        if channel_id == 0:
            return MessageType.SPECIFIC
        elif message_id == 0x0008:
            return MessageType.CONTROL  # ChannelOpenResponse
        else:
            return MessageType.SPECIFIC

    def _get_encryption_type(self, channel_id: int, message_id: int,
                              ssl_active: bool) -> int:
        """
        Determine encryption type based on C++ Messenger.cpp logic
        """
        if not ssl_active:
            return EncryptionType.PLAIN
        
        # Control channel exceptions
        if channel_id == 0:
            if message_id in (0x0001, 0x0002, 0x0003, 0x0004, 0x000b, 0x000c):
                return EncryptionType.PLAIN
        
        return EncryptionType.ENCRYPTED

    def serialize_and_log(self, channel_id: int, message_id: int,
                          payload: bytes, ssl_active: bool = False) -> List[bytes]:
        """
        Complete end-to-end serialization with console logging.
        
        Args:
            channel_id: Channel ID
            message_id: Message ID (for type determination)
            payload: Payload data (hex string input converted to bytes)
            ssl_active: Whether SSL/TLS is active
        
        Returns:
            List of frames (bytes)
        """

        full_payload = f"{message_id:04x}" + payload.hex() if isinstance(payload, bytes) else payload
        # Convert hex string to bytes if needed
        if isinstance(full_payload, str):
            payload = bytes.fromhex(full_payload)
        
        # Determine message type
        msg_type = self._get_message_type(channel_id, message_id)
        
        # Determine encryption type
        enc_type = self._get_encryption_type(channel_id, message_id, ssl_active)
        
        # Serialize
        frames = self.serializer.serialize(
            channel_id=channel_id,
            message_type=msg_type,
            encryption_type=enc_type,
            payload=bytes(payload)  # Ensure bytes
        )
        
        # Log hex representation to console
        self._log(f"Serializing channel {channel_id}, msg_id {message_id:04x}")
        self._log(f"Message type: {msg_type}, Encryption: {enc_type}")
        
        for i, frame in enumerate(frames):
            hex_repr = frame.hex()
            self._log(f"Frame {i+1}: {len(frame)} bytes")
            self._log(f"  Hex: {hex_repr[:64]}{'...' if len(hex_repr) > 68 else ''}")
        
        return frames


def main():
    """
    Example usage demonstrating end-to-end serialization
    
    Usage:
        python -c "from v2.modules.oaa_control_channel.serializer import Messenger; m = Messenger(); frames = m.serialize_and_log(0, 0x0003, 'hello')"
    """
    from v2.modules.oaa_control_channel.serializer import Messenger
    
    # Example: Serialize a control channel message (channel 0, message 0x0003)
    # Input: hex string payload
    payload_hex = "48656c6c6f20576f726c64"  # "Hello World" in hex
    
    messenger = Messenger()
    frames = messenger.serialize_and_log(
        channel_id=0,
        message_id=0x0003,  # SSL_HANDSHAKE
        payload=payload_hex,
        ssl_active=True
    )
    
    # Log all frames
    logging.info(f"Generated {len(frames)} frames")
    for i, frame in enumerate(frames):
        logging.info(f"Frame {i+1}: {frame.hex()[:64]}...")


if __name__ == "__main__":
    main()