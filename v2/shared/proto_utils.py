"""
proto_utils.py — Generalised Protobuf utilities for NemoHeadUnit-Wireless.

Public API
----------
decode_proto(proto_class, raw_bytes) → Message | None
encode_proto(msg) → bytes
parse_media_with_timestamp(body) → tuple[int, bytes]
build_media_with_timestamp(ts_us, data) → bytes
proto_to_dict(msg) → dict
dict_to_proto(msg, data) → None
schema_from_proto_message(descriptor) → dict[str, AnyFieldSchema]
channels_from_sdr_bytes(sdr_bytes_hex) → list[dict]
channel_config_from_sdr(sdr_bytes_hex, channel_id) → dict | None
encode_aa_frame(channel_id, message_id, proto_body, *, control=False) → dict
decode_aa_frame(data) → tuple[int, bytes] | None

parse_media_with_timestamp
--------------------------
Manual protobuf parser for AV_MEDIA_WITH_TIMESTAMP_INDICATION frames.
No generated pb2 class exists for this message (neither in openauto-prodigy
nor in this project): the phone sends a raw fixed64 timestamp prefix followed
by the codec payload, without a formal .proto definition.  All open-source
implementations parse this manually.

Returns (timestamp_us, audio_or_video_data).
Used by both audio and video channel modules.

build_media_with_timestamp
--------------------------
Symmetric counterpart of parse_media_with_timestamp, used by outgoing
AV_MEDIA_WITH_TIMESTAMP frames (e.g. av_input mic stream).
Packs [8-byte BE timestamp][raw PCM/codec bytes] as required by the
Android Auto wire format (matches openauto-prodigy AVInputChannelHandler
sendMicData implementation).

Returns bytes ready to be passed directly to send_frame() as proto_body.

encode_aa_frame / decode_aa_frame
----------------------------------
Low-level AA frame framing helpers shared by all channel modules.
Every outgoing AA frame is a 2-byte big-endian message_id followed by the
serialised proto body, wrapped in a dict understood by aa.frame.send.
Every incoming raw frame is decoded by stripping the same 2-byte header.

  encode_aa_frame(channel_id, message_id, proto_body, *, control=False) → dict
      Returns {channel_id, flags, payload_hex} ready for bus.publish("aa.frame.send").

      The `control` flag documents that the message_id belongs to the
      ControlMessage namespace (e.g. CHANNEL_OPEN_RESPONSE = 0x0008) rather
      than an AV-specific namespace, even when channel_id != 0.
      On the wire the flags byte is identical (0x0B) for both namespaces, so
      `control` has no runtime effect — it exists solely to make the intent
      explicit at every call site that sends a Control-namespace message on a
      non-zero channel.

  decode_aa_frame(data) → (message_id, body) | None
      Splits a raw frame into its message_id and body.
      Returns None on frames shorter than 2 bytes.

These replace the duplicated _encode_frame / _decode_frame static methods
that previously lived in each channel module class.

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
For av_channel the dict exposes "codec" (MediaCodecType int) and, for
AUDIO channels, "audio_type" (AudioType int) so that consumers can
distinguish VIDEO from the three audio stream types without importing
proto enums.

channel_config_from_sdr
------------------------
Convenience wrapper: given a hex-encoded SDR and a channel_id, returns
the full channel dict for that channel_id as produced by
channels_from_sdr_bytes(), or None if the channel is not found.
"""

from __future__ import annotations

import logging
import struct
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

# AA frame flags — used by encode_aa_frame
_FLAG_FIRST     = 0x01
_FLAG_LAST      = 0x02
_FLAG_ENCRYPTED = 0x08
_FLAG_CONTROL   = 0x04  
_FLAG_FULL      = _FLAG_FIRST | _FLAG_LAST  # 0x0B


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


def encode_aa_frame(
    channel_id: int,
    message_id: int,
    proto_body: bytes,
    *,
    control: bool = False,
) -> dict:
    """Wrap a serialised proto body into an aa.frame.send payload dict.

    Every outgoing AA frame is a 2-byte big-endian message_id followed by the
    serialised proto body.  The returned dict is understood directly by the
    aa.frame.send bus topic.

    Args:
        channel_id:  the AA channel this frame belongs to.
        message_id:  2-byte big-endian AA message identifier.
        proto_body:  serialised protobuf payload (may be empty bytes).
        control:     set to True when message_id belongs to the ControlMessage
                     namespace (e.g. CHANNEL_OPEN_RESPONSE = 0x0008) even
                     though channel_id != 0.  On the wire the flags byte is
                     identical (0x0B) for both Control and AV namespaces, so
                     this parameter has NO runtime effect.  It exists solely
                     to document intent at call sites that send a Control-
                     namespace message on a non-zero channel — specifically
                     ChannelOpenResponse, which is the only such case in the
                     AA protocol.

    Returns:
        {"channel_id": int, "flags": int, "payload_hex": str}
    """
    payload = struct.pack(">H", message_id) + proto_body
    return {
        "channel_id":  channel_id,
        "flags":       _FLAG_FULL | (_FLAG_CONTROL if control else 0) | _FLAG_ENCRYPTED,
        "payload_hex": payload.hex(),
    }


def decode_aa_frame(data: bytes) -> tuple[int, bytes] | None:
    """Split a raw AA frame into (message_id, body).

    The first 2 bytes are the big-endian message_id; everything after is the
    proto body.  Returns None if *data* is shorter than 2 bytes.

    Args:
        data: raw bytes as received from the AA transport layer.

    Returns:
        (message_id, body) on success, None on truncated input.
    """
    if len(data) < 2:
        return None
    message_id = struct.unpack_from(">H", data, 0)[0]
    return message_id, data[2:]


def parse_media_with_timestamp(body: bytes) -> tuple[int, bytes]:
    """Parse an AV_MEDIA_WITH_TIMESTAMP_INDICATION frame body.

    No generated pb2 class exists for this message.  The phone may send:
        field 1 (wire type 1, fixed64) — timestamp in microseconds
        field 2 (wire type 2, bytes)   — codec payload (AAC / H.264 / PCM)

    Some implementations use a compact [uint64 BE timestamp][raw codec bytes]
    tuple instead.  The timestamp is extracted as an informational value; the
    codec payload is what callers actually need.

    Used by both AudioModule and VideoModule.

    Args:
        body: raw frame body after the 2-byte message_id header has been
              stripped by the channel frame dispatcher.

    Returns:
        (timestamp_us, payload) where timestamp_us is the phone-side
        presentation timestamp in microseconds, and payload is the raw
        codec data to pass to the decoder.
        Returns (0, b"") on malformed input.
    """
    ts_us: int  = 0
    data:  bytes = b""

    if len(body) >= 8:
        raw_ts_us = struct.unpack_from(">Q", body, 0)[0]
        raw_data = body[8:]
    else:
        raw_ts_us = 0
        raw_data = b""
    pos:   int  = 0

    while pos < len(body):
        if pos >= len(body):
            break
        tag_byte     = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07

        if field_number == 1 and wire_type == 1:          # fixed64 — timestamp
            if pos + 8 > len(body):
                break
            ts_us = struct.unpack_from("<Q", body, pos)[0]
            pos += 8

        elif field_number == 2 and wire_type == 2:        # bytes — codec payload
            length, pos = _read_varint(body, pos)
            if length is None:
                break
            data = body[pos: pos + length]
            pos += length

        else:                                             # skip unknown fields
            pos = _skip_field(body, pos, wire_type)
            if pos is None:
                break

    if data:
        return ts_us, data

    # Some AA media implementations send the timestamped media body as a
    # compact [uint64 BE timestamp][raw codec bytes] tuple instead of protobuf
    # fields.  Large video/audio frames in the wild commonly use this form.
    if raw_data:
        return raw_ts_us, raw_data

    return ts_us, data


def build_media_with_timestamp(ts_us: int, data: bytes) -> bytes:
    """Pack an outgoing AV_MEDIA_WITH_TIMESTAMP frame body.

    Symmetric counterpart of parse_media_with_timestamp, used when the HU
    sends media upstream to the phone (e.g. av_input mic stream).

    Wire format (matches openauto-prodigy AVInputChannelHandler::sendMicData):
        [8-byte big-endian uint64 timestamp_us][raw PCM / codec bytes]

    The returned bytes are passed directly as proto_body to send_frame();
    no further serialisation is needed.

    Args:
        ts_us: presentation timestamp in microseconds (monotonic clock).
        data:  raw PCM or codec payload bytes.

    Returns:
        Packed frame body ready for send_frame(AV_MEDIA_WITH_TIMESTAMP, ...).
    """
    return struct.pack(">Q", ts_us) + data


def proto_to_dict(msg: Message) -> dict:
    """Convert a protobuf message to a plain Python dict (for logging / DBus publishing).

    Uses the built-in MessageToDict from google.protobuf.json_format so all
    field types (enums, bytes, nested messages) are handled automatically.
    """
    from google.protobuf.json_format import MessageToDict  # lazy import

    return MessageToDict(msg, preserving_proto_field_name=True)


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
        "codec"    — MediaCodecType int  (MEDIA_CODEC_AUDIO_PCM, MEDIA_CODEC_AUDIO_AAC_LC, MEDIA_CODEC_AUDIO_AAC_LC_ADTS, MEDIA_CODEC_VIDEO_H264_BP, etc)
        "audio_type" — AudioType int     (MEDIA / SPEECH / SYSTEM, only when codec == MEDIA_CODEC_AUDIO_PCM or MEDIA_CODEC_AUDIO_AAC_LC or MEDIA_CODEC_AUDIO_AAC_LC_ADTS)
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
        from v2.protos.oaa.av.MediaCodecTypeEnum_pb2 import MediaCodecType  # noqa: PLC0415
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
                    av_dict: dict = {"av_type": sub.codec}
                    if sub.codec == MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC_ADTS or sub.codec == MediaCodecType.MEDIA_CODEC_AUDIO_PCM or sub.codec == MediaCodecType.MEDIA_CODEC_AUDIO_AAC_LC:
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


def channel_config_from_sdr(sdr_bytes_hex: str, channel_id: int) -> dict | None:
    """Return the full channel dict for *channel_id* from the SDR, or None.

    The returned dict has the same structure as a channels_from_sdr_bytes()
    entry, i.e. at minimum:
        {"channel_id": <int>, "<oneof_field>": <dict>}

    For av_channel (audio) the nested dict includes:
        av_type, audio_type, audio_configs (list of sample_rate/bit_depth/
        channel_count/codec dicts).

    Args:
        sdr_bytes_hex: hex string of the serialised ServiceDiscoveryResponse.
        channel_id:    the integer channel ID to look up.

    Returns:
        Full channel dict, or None if not found.
    """
    for ch in channels_from_sdr_bytes(sdr_bytes_hex):
        if ch.get("channel_id") == channel_id:
            return ch
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


def _read_varint(buf: bytes, pos: int) -> tuple[int | None, int]:
    """Read a protobuf varint from *buf* at *pos*.

    Returns (value, new_pos) on success, or (None, pos) on truncated input.
    Used internally by parse_media_with_timestamp and any manual proto parsers.
    """
    result = 0
    shift  = 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            return None, pos
    return None, pos


def _skip_field(buf: bytes, pos: int, wire_type: int) -> int | None:
    """Advance *pos* past an unknown field of *wire_type*.  Returns new pos or None."""
    if wire_type == 0:    # varint
        _, pos = _read_varint(buf, pos)
        return pos
    if wire_type == 1:    # 64-bit
        return pos + 8 if pos + 8 <= len(buf) else None
    if wire_type == 2:    # length-delimited
        length, pos = _read_varint(buf, pos)
        if length is None:
            return None
        return pos + length if pos + length <= len(buf) else None
    if wire_type == 5:    # 32-bit
        return pos + 4 if pos + 4 <= len(buf) else None
    return None           # wire types 3/4 are deprecated; treat as unrecoverable


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
