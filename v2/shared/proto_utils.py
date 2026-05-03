"""
proto_utils.py — Generalised Protobuf deserialisation utility for NemoHeadUnit-Wireless.

Usage:
    from shared.proto_utils import decode_proto, encode_proto

    msg = decode_proto(ServiceDiscoveryRequestMessage_pb2.ServiceDiscoveryRequest, raw_bytes)
    raw = encode_proto(response_msg)
"""

from __future__ import annotations

import logging
from typing import Type, TypeVar

from google.protobuf.message import Message, DecodeError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=Message)


def decode_proto(proto_class: Type[T], data: bytes) -> T | None:
    """Deserialise *data* into an instance of *proto_class*.

    Returns the populated message on success, or None if parsing fails.
    Errors are logged at WARNING level so callers don't need try/except.
    """
    if not data:
        log.warning("decode_proto(%s): empty payload", proto_class.DESCRIPTOR.name)
        return None
    try:
        msg = proto_class()
        msg.ParseFromString(data)
        return msg
    except DecodeError as exc:
        log.warning(
            "decode_proto(%s): ParseFromString failed — %s  (payload_hex=%s)",
            proto_class.DESCRIPTOR.name,
            exc,
            data.hex(),
        )
        return None


def encode_proto(msg: Message) -> bytes:
    """Serialise *msg* to bytes.  Returns empty bytes on error."""
    try:
        return msg.SerializeToString()
    except Exception as exc:  # pragma: no cover
        log.error("encode_proto(%s): serialisation failed — %s", type(msg).__name__, exc)
        return b""


def proto_to_dict(msg: Message) -> dict:
    """Convert a protobuf message to a plain Python dict (for logging / DBus publishing).

    Uses the built-in MessageToDict from google.protobuf.json_format so all
    field types (enums, bytes, nested messages) are handled automatically.
    """
    from google.protobuf.json_format import MessageToDict  # lazy import

    return MessageToDict(msg, preserving_proto_field_name=True, including_default_value_fields=False)
