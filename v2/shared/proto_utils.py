"""
proto_utils.py — Generalised Protobuf utilities for NemoHeadUnit-Wireless.

Public API
----------
decode_proto(proto_class, raw_bytes) → Message | None
encode_proto(msg) → bytes
proto_to_dict(msg) → dict
dict_to_proto(msg, data) → None
schema_from_proto_message(descriptor) → dict[str, AnyFieldSchema]
channels_from_sdr_bytes(sdr_bytes_hex) → list[dict]
audio_config_from_sdr_bytes(sdr_bytes_hex, channel_id) → dict | None

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

dict_to_proto
-------------
Recursively populates a proto message from a nested plain dict.
Keys must match proto field names exactly.
Enum values are accepted as string names (e.g. "VIDEO_1280x720") and
resolved via the field's enum_type descriptor.
Repeated fields expect a list of values or dicts.

channels_from_sdr_bytes
-----------------------
Parse a hex-encoded ServiceDiscoveryResponse and return the channel list
as plain dicts.  Each dict contains at minimum:
    {"channel_id": <int>, "<oneof_field>": {}}
For av_channel the dict exposes "av_type" (AVStreamType int) and, for
AUDIO channels, "audio_type" (AudioType int) so that consumers can
distinguish VIDEO from the three audio stream types without importing
proto enums.

audio_config_from_sdr_bytes
---------------------------
Convenience wrapper: given a hex-encoded SDR and a channel_id, returns
a dict with keys sample_rate, bit_depth, channel_count, codec (string
enum name) for the first audio_config entry of that channel, or None if
the channel is not found / has no audio_config.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Type, TypeVar

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


def dict_to_proto(msg: Any, data: dict) -> None:
    """Recursively populate a proto message from a nested plain dict.

    Keys in *data* must match proto field names exactly.
    Enum values are accepted as string names (e.g. "VIDEO_1280x720") and
    resolved via the field's enum_type descriptor.
    Repeated fields expect a list of values or dicts.

    Unknown keys are silently skipped (logged at DEBUG level).
    """
    descriptor = msg.DESCRIPTOR
    fields_by_name = descriptor.fields_by_name

    for key, value in data.items():
        if key not in fields_by_name:
            log.debug("dict_to_proto(%s): unknown field %r — skipped", descriptor.name, key)
            continue

        field_desc = fields_by_name[key]
        is_repeated = field_desc.label == field_desc.LABEL_REPEATED

        if is_repeated:
            proto_list = getattr(msg, key)
            for item in (value if isinstance(value, list) else []):
                if field_desc.message_type is not None:
                    entry = proto_list.add()
                    if isinstance(item, dict):
                        dict_to_proto(entry, item)
                else:
                    proto_list.append(_coerce_scalar(field_desc, item))
            continue

        if field_desc.message_type is not None:
            if isinstance(value, dict) and value:
                dict_to_proto(getattr(msg, key), value)
            continue

        try:
            setattr(msg, key, _coerce_scalar(field_desc, value))
        except (AttributeError, ValueError, TypeError) as exc:
            log.warning(
                "dict_to_proto(%s): cannot set %r=%r — %s",
                descriptor.name, key, value, exc,
            )


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
    oneof_names: dict[str, str] = {}  # field_name → oneof_group_name
    for oneof in descriptor.oneofs:
        for f in oneof.fields:
            oneof_names[f.name] = oneof.name

    schema: dict[str, AnyFieldSchema] = {}
    processed_oneofs: set[str] = set()

    for field_desc in descriptor.fields:
        fname = field_desc.name
        is_repeated = field_desc.label == _descriptor.FieldDescriptor.LABEL_REPEATED

        # --- oneof handling ---
        if fname in oneof_names:
            group_name = oneof_names[fname]
            if group_name in processed_oneofs:
                continue
            processed_oneofs.add(group_name)

            oneof_obj = descriptor.oneofs_by_name[group_name]
            branches: dict[str, AnyFieldSchema] = {}
            for branch_field in oneof_obj.fields:
                if branch_field.message_type is not None:
                    branch_fields = schema_from_proto_message(branch_field.message_type, visited)
                    branches[branch_field.name] = ConfigFieldMessage(
                        fields=branch_fields,
                        optional=True,
                    )
                else:
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
            schema[fname] = ConfigFieldMessage(fields=nested_fields, optional=True)
            continue

        # --- scalar field ---
        schema[fname] = _scalar_field(field_desc)

    return schema


# ---------------------------------------------------------------------------
# SDR channel helpers
# ---------------------------------------------------------------------------

# Oneof field names present in ChannelDescriptor
_ONEOF_CHANNEL_FIELDS = (
    "av_channel",
    "sensor_channel",
    "input_channel",
    "bluetooth_channel",
    "wifi_channel",
    "navigation_channel",
    "media_info_channel",
    "av_input_channel",
    "phone_status_channel",
)


def channels_from_sdr_bytes(sdr_bytes_hex: str) -> list[dict]:
    """Parse a hex-encoded ServiceDiscoveryResponse and return the channel list
    as plain dicts.

    Each dict contains at minimum:
        {"channel_id": <int>, "<oneof_field>": {}}

    For av_channel the dict exposes:
        "av_type"    — AVStreamType int  (VIDEO vs AUDIO)
        "audio_type" — AudioType int     (MEDIA / SPEECH / SYSTEM, only when av_type == AUDIO)
        "audio_configs" — list of dicts with keys:
                         sample_rate, bit_depth, channel_count, codec (enum name string)

    Args:
        sdr_bytes_hex: hex string of the serialised ServiceDiscoveryResponse.

    Returns:
        List of channel dicts, one per ChannelDescriptor in the SDR.
        Returns an empty list on parse errors.
    """
    # Lazy imports: proto classes only available when protos are compiled.
    try:
        _repo_root = Path(__file__).parent.parent.parent
        _proto_root = _repo_root / "v2" / "protos"
        for _p in (_repo_root, _proto_root):
            if str(_p) not in sys.path:
                sys.path.insert(0, str(_p))

        from v2.protos.oaa.control.ServiceDiscoveryResponseMessage_pb2 import (  # noqa: PLC0415
            ServiceDiscoveryResponse,
        )
        from v2.protos.oaa.av.AVStreamTypeEnum_pb2 import AVStreamType  # noqa: PLC0415
    except ImportError as exc:
        log.error("channels_from_sdr_bytes: proto import failed — %s", exc)
        return []

    try:
        sdr_bytes = bytes.fromhex(sdr_bytes_hex)
        resp = ServiceDiscoveryResponse()
        resp.ParseFromString(sdr_bytes)
    except Exception as exc:
        log.error("channels_from_sdr_bytes: parse error — %s", exc)
        return []

    result: list[dict] = []
    for ch in resp.channels:
        entry: dict = {"channel_id": ch.channel_id}

        for field_name in _ONEOF_CHANNEL_FIELDS:
            if ch.HasField(field_name):
                sub = getattr(ch, field_name)
                if field_name == "av_channel":
                    av_dict: dict = {"av_type": sub.stream_type}
                    if sub.stream_type == AVStreamType.AUDIO:
                        av_dict["audio_type"] = sub.audio_type
                        av_dict["audio_configs"] = [
                            {
                                "sample_rate":   ac.sample_rate,
                                "bit_depth":     ac.bit_depth,
                                "channel_count": ac.channel_count,
                                "codec":         _enum_name(ac, "codec"),
                            }
                            for ac in sub.audio_configs
                        ]
                    entry["av_channel"] = av_dict
                else:
                    entry[field_name] = {}
                break

        result.append(entry)

    return result


def audio_config_from_sdr_bytes(sdr_bytes_hex: str, channel_id: int) -> dict | None:
    """Return the first audio_config dict for *channel_id* from the SDR, or None.

    The returned dict has keys:
        sample_rate   (int)  — e.g. 48000
        bit_depth     (int)  — e.g. 16
        channel_count (int)  — e.g. 2
        codec         (str)  — e.g. "MEDIA_CODEC_AUDIO_AAC_LC_ADTS"

    Args:
        sdr_bytes_hex: hex string of the serialised ServiceDiscoveryResponse.
        channel_id:    the integer channel ID to look up.

    Returns:
        dict with audio config fields, or None if not found.
    """
    for ch in channels_from_sdr_bytes(sdr_bytes_hex):
        if ch.get("channel_id") != channel_id:
            continue
        av = ch.get("av_channel", {})
        configs = av.get("audio_configs", [])
        if configs:
            return configs[0]
    return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _enum_name(msg: Any, field_name: str) -> str:
    """Return the string enum name for *field_name* in *msg*, or empty string."""
    try:
        field_desc = msg.DESCRIPTOR.fields_by_name[field_name]
        value_int = getattr(msg, field_name)
        return field_desc.enum_type.values_by_number[value_int].name
    except (KeyError, AttributeError):
        return ""


def _scalar_field(field_desc: _descriptor.FieldDescriptor) -> AnyFieldSchema:
    """Map a scalar (non-message) proto field to a ConfigFieldSchema."""
    t = field_desc.type

    if t == _descriptor.FieldDescriptor.TYPE_BOOL:
        return field_bool(default=False)

    if t == _descriptor.FieldDescriptor.TYPE_STRING:
        return field_string(default="")

    if t in (_descriptor.FieldDescriptor.TYPE_BYTES,):
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

    log.warning(
        "_scalar_field: unrecognised proto field type %d for field %r — falling back to string",
        t,
        field_desc.name,
    )
    return field_string(default="")


def _coerce_scalar(field_desc: Any, value: Any) -> Any:
    """Coerce *value* to the correct Python type for *field_desc*.

    Enum fields accept either an integer or a string name.
    """
    if field_desc.type == _descriptor.FieldDescriptor.TYPE_ENUM:
        if isinstance(value, str):
            try:
                # 1. Prova il matching esatto del nome
                return field_desc.enum_type.values_by_name[value].number
            except KeyError:
                # 2. Prova un matching case-insensitive come fallback
                for val in field_desc.enum_type.values:
                    if val.name.upper() == value.upper():
                        return val.number
                # 3. Se è una stringa numerica, convertila in int
                return int(value)
        return int(value)

    if field_desc.type == _descriptor.FieldDescriptor.TYPE_BOOL:
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "on"}
        return bool(value)

    if field_desc.type in _INT_TYPES:
        if isinstance(value, str):
            if value.isdigit() or (value.startswith('-') and value[1:].isdigit()):
                return int(value)
        return int(value)

    if field_desc.type in _FLOAT_TYPES:
        return float(value)

    return str(value)  # STRING / BYTES fallback
