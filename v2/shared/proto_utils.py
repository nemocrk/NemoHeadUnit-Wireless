"""
proto_utils.py — Generalised Protobuf utilities for NemoHeadUnit-Wireless.

Public API
----------
decode_proto(proto_class, raw_bytes) → Message | None
encode_proto(msg) → bytes
proto_to_dict(msg) → dict
schema_from_proto_message(descriptor) → dict[str, AnyFieldSchema]

schema_from_proto_message
-------------------------
Traverses a protobuf MessageDescriptor recursively and returns a
config_schema-compatible schema dict.

Mapping rules:
    repeated MESSAGE field  → ConfigFieldList(item_schema=ConfigFieldMessage(...))
    repeated scalar field   → ConfigFieldList(item_schema=<scalar ConfigFieldSchema>)
    oneof group             → ConfigFieldOneof(branches={name: ConfigFieldMessage}, active_branch=first)
    optional MESSAGE field  → ConfigFieldMessage(fields=..., optional=True)
    required MESSAGE field  → ConfigFieldMessage(fields=..., optional=False)
    ENUM field              → field_enum(default=first_value, choices=[all_value_names])
    STRING / BYTES field    → field_string(default="")
    BOOL field              → field_bool(default=False)
    INT32/INT64/UINT*/SINT* → field_int(default=0)
    FLOAT / DOUBLE          → field_float(default=0.0)

Cyclic references (self-referential messages) are broken by tracking
visited descriptor full_names and returning an empty ConfigFieldMessage.
"""

from __future__ import annotations

import logging
from typing import Type, TypeVar

from google.protobuf import descriptor as _descriptor
from google.protobuf.message import Message, DecodeError

from shared.config_schema import (
    AnyFieldSchema,
    ConfigFieldList,
    ConfigFieldMessage,
    ConfigFieldOneof,
    field_bool,
    field_enum,
    field_float,
    field_int,
    field_string,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=Message)

# Proto field type codes that map to int
_INT_TYPES = {
    _descriptor.FieldDescriptor.TYPE_INT32,
    _descriptor.FieldDescriptor.TYPE_INT64,
    _descriptor.FieldDescriptor.TYPE_UINT32,
    _descriptor.FieldDescriptor.TYPE_UINT64,
    _descriptor.FieldDescriptor.TYPE_FIXED32,
    _descriptor.FieldDescriptor.TYPE_FIXED64,
    _descriptor.FieldDescriptor.TYPE_SFIXED32,
    _descriptor.FieldDescriptor.TYPE_SFIXED64,
    _descriptor.FieldDescriptor.TYPE_SINT32,
    _descriptor.FieldDescriptor.TYPE_SINT64,
}

# Proto field type codes that map to float
_FLOAT_TYPES = {
    _descriptor.FieldDescriptor.TYPE_FLOAT,
    _descriptor.FieldDescriptor.TYPE_DOUBLE,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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


def schema_from_proto_message(
    descriptor: _descriptor.Descriptor,
    visited: set[str] | None = None,
) -> dict[str, AnyFieldSchema]:
    """Recursively build a config_schema-compatible schema from a proto MessageDescriptor.

    Args:
        descriptor: the DESCRIPTOR of a proto Message class
                    (e.g. ServiceDiscoveryResponse.DESCRIPTOR).
        visited:    set of descriptor full_names already being processed;
                    used internally to break cyclic references.

    Returns:
        dict mapping each field name to its AnyFieldSchema.

    Example::

        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import (
            ServiceDiscoveryResponse,
        )
        from shared.proto_utils import schema_from_proto_message

        _SCHEMA = schema_from_proto_message(ServiceDiscoveryResponse.DESCRIPTOR)
    """
    if visited is None:
        visited = set()

    # Guard against cyclic message references
    if descriptor.full_name in visited:
        log.debug(
            "schema_from_proto_message: cyclic reference detected at %s — returning empty message",
            descriptor.full_name,
        )
        return {}

    visited = visited | {descriptor.full_name}  # immutable copy per recursion branch

    # --- Collect oneof group names so we can group their fields ---
    # oneof_decl gives us the oneof objects; we map each field to its oneof name.
    oneof_names: dict[str, str] = {}  # field_name → oneof_group_name
    for oneof in descriptor.oneofs:
        for f in oneof.fields:
            oneof_names[f.name] = oneof.name

    schema: dict[str, AnyFieldSchema] = {}
    processed_oneofs: set[str] = set()  # oneof group names already emitted

    for field_desc in descriptor.fields:
        fname = field_desc.name
        is_repeated = field_desc.label == _descriptor.FieldDescriptor.LABEL_REPEATED

        # --- oneof handling ---
        if fname in oneof_names:
            group_name = oneof_names[fname]
            if group_name in processed_oneofs:
                continue  # already emitted this oneof group
            processed_oneofs.add(group_name)

            # Collect all branches in this oneof group
            oneof_obj = descriptor.oneofs_by_name[group_name]
            branches: dict[str, AnyFieldSchema] = {}
            for branch_field in oneof_obj.fields:
                if branch_field.message_type is not None:
                    branch_fields = schema_from_proto_message(branch_field.message_type, visited)
                    branches[branch_field.name] = ConfigFieldMessage(
                        fields=branch_fields,
                        optional=True,  # oneof branches are always optional by definition
                    )
                else:
                    # Scalar oneof branch (rare but valid)
                    branches[branch_field.name] = _scalar_field(branch_field)

            first_branch = next(iter(branches))
            schema[group_name] = ConfigFieldOneof(
                branches=branches,
                active_branch=first_branch,
            )
            continue

        # --- repeated field ---
        if is_repeated:
            if field_desc.message_type is not None:
                item_fields = schema_from_proto_message(field_desc.message_type, visited)
                item_schema = ConfigFieldMessage(fields=item_fields, optional=False)
            else:
                item_schema = _scalar_field(field_desc)
            schema[fname] = ConfigFieldList(item_schema=item_schema, default=[])
            continue

        # --- nested message (non-repeated, non-oneof) ---
        if field_desc.message_type is not None:
            nested_fields = schema_from_proto_message(field_desc.message_type, visited)
            # Non-repeated standalone messages are optional by default (checkbox in UI)
            schema[fname] = ConfigFieldMessage(fields=nested_fields, optional=True)
            continue

        # --- scalar field ---
        schema[fname] = _scalar_field(field_desc)

    return schema


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _scalar_field(field_desc: _descriptor.FieldDescriptor) -> AnyFieldSchema:
    """Map a scalar (non-message) proto field to a ConfigFieldSchema."""
    t = field_desc.type

    if t == _descriptor.FieldDescriptor.TYPE_BOOL:
        return field_bool(default=False)

    if t == _descriptor.FieldDescriptor.TYPE_STRING:
        return field_string(default="")

    if t in (_descriptor.FieldDescriptor.TYPE_BYTES,):
        # Bytes represented as empty hex string
        return field_string(default="")

    if t == _descriptor.FieldDescriptor.TYPE_ENUM:
        enum_type = field_desc.enum_type
        choices = [v.name for v in enum_type.values]
        default = choices[0] if choices else ""
        if default not in choices:
            return field_string(default="")
        return field_enum(default=default, choices=choices)

    if t in _INT_TYPES:
        return field_int(default=0)

    if t in _FLOAT_TYPES:
        return field_float(default=0.0)

    # Fallback: unknown type treated as string
    log.warning(
        "_scalar_field: unrecognised proto field type %d for field %r — falling back to string",
        t,
        field_desc.name,
    )
    return field_string(default="")
