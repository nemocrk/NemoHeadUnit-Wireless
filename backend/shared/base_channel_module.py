"""
base_channel_module.py — Abstract base class for Android Auto channel modules.

Extends `BaseBackendModule` to provide standard AA channel lifecycle management,
bus event subscriptions (aa.frame.ch<channel_id>, aa.channel.open, aa.channel.close),
and helper methods for sending wire frames (aa.frame.send).
"""

from abc import ABC, abstractmethod
import struct
from typing import Any, Optional

from shared.base_module import BaseBackendModule
from shared.proto_utils import decode_aa_frame, encode_aa_frame


class BaseChannelModule(BaseBackendModule, ABC):
    """
    Abstract base class for process-isolated AA channel microservices.

    Subclasses must define `CHANNEL_ID` and implement:
      - async on_frame(message_id: int, encrypted: bool, payload: bytes) -> None
      - async on_channel_open(descriptor: dict) -> None (optional override)
      - async on_channel_close() -> None (optional override)
    """

    CHANNEL_ID: int = -1

    def __init__(
        self,
        name: str,
        channel_id: int,
        priority: int = 3,
        path_prefix: Optional[str] = None,
    ):
        super().__init__(name=name, priority=priority, path_prefix=path_prefix)
        self.channel_id = channel_id
        self.is_channel_open = False
        self.channel_descriptor: dict[str, Any] = {}

    async def setup(self) -> None:
        """Register ZMQ subscriptions for AA channel events."""
        # Subscribe to raw frames targeting this channel
        self.subscribe(f"aa.frame.ch{self.channel_id}", self._handle_bus_frame)
        self.subscribe("aa.channel.open", self._handle_channel_open)
        self.subscribe("aa.channel.close", self._handle_channel_close)

    async def _handle_bus_frame(self, data: dict) -> None:
        """Internal callback for aa.frame.ch<channel_id> bus messages."""
        if not isinstance(data, dict):
            return

        payload_hex = data.get("payload_hex", "")
        encrypted = data.get("encrypted", False)
        payload = bytes.fromhex(payload_hex) if payload_hex else b""

        decoded = decode_aa_frame(payload)
        if not decoded:
            self.log.warning("Received malformed frame on channel %d", self.channel_id)
            return

        message_id, body = decoded
        await self.on_frame(message_id, encrypted, body)

    async def _handle_channel_open(self, data: dict) -> None:
        """Internal handler for aa.channel.open event."""
        target_ch = data.get("channel_id")
        if target_ch == self.channel_id:
            self.is_channel_open = True
            self.channel_descriptor = data.get("descriptor", {})
            self.log.info("Channel %d opened", self.channel_id)
            await self.on_channel_open(self.channel_descriptor)

    async def _handle_channel_close(self, data: dict) -> None:
        """Internal handler for aa.channel.close event."""
        target_ch = data.get("channel_id")
        if target_ch == self.channel_id:
            self.is_channel_open = False
            self.log.info("Channel %d closed", self.channel_id)
            await self.on_channel_close()

    async def send_frame(self, message_id: int, proto_body: bytes, control: bool = False) -> None:
        """
        Helper method to publish an outgoing frame to 'aa.frame.send'.
        """
        frame_dict = encode_aa_frame(
            channel_id=self.channel_id,
            message_id=message_id,
            proto_body=proto_body,
            control=control,
        )
        self.publish("aa.frame.send", frame_dict)

    @abstractmethod
    async def on_frame(self, message_id: int, encrypted: bool, payload: bytes) -> None:
        """Called when a complete AA frame arrives for this channel."""
        pass

    async def on_channel_open(self, descriptor: dict) -> None:
        """Hook called when channel open notification arrives."""
        pass

    async def on_channel_close(self) -> None:
        """Hook called when channel close notification arrives."""
        pass
