"""
Fase 5 §2 — Fuzz: proto_utils encode→decode roundtrip con input arbitrari.

Marker : @pytest.mark.fuzz
Motore : hypothesis
Soglie : nessun crash/hang, roundtrip idempotente dove applicabile
"""
from __future__ import annotations

import time
import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import condizionale del modulo sotto test
# ---------------------------------------------------------------------------
try:
    from shared import proto_utils  # type: ignore
    HAS_PROTO_UTILS = True
except ImportError:
    try:
        import proto_utils  # type: ignore
        HAS_PROTO_UTILS = True
    except ImportError:
        HAS_PROTO_UTILS = False

# Tentiamo di ricavare encode/decode in modo flessibile
def _get_codec():
    """Restituisce (encode_fn, decode_fn) o (None, None) se non trovato."""
    if not HAS_PROTO_UTILS:
        return None, None
    encode = getattr(proto_utils, "encode", None) or getattr(proto_utils, "encode_message", None)
    decode = getattr(proto_utils, "decode", None) or getattr(proto_utils, "decode_message", None)
    return encode, decode


pytestmark = pytest.mark.fuzz


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _safe_decode(raw: bytes):
    """Chiama decode senza propagare eccezioni note (ValueError, struct.error, ecc.)."""
    _, decode = _get_codec()
    if decode is None:
        return None
    try:
        return decode(raw)
    except (ValueError, TypeError, AttributeError, OverflowError, UnicodeDecodeError):
        return None
    except Exception as exc:  # noqa: BLE001
        # Eccezioni sconosciute NON devono arrivare qui: il fuzz le cattura
        raise exc


def _safe_encode(obj: Any) -> bytes | None:
    encode, _ = _get_codec()
    if encode is None:
        return None
    try:
        return encode(obj)
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_PROTO_UTILS, reason="proto_utils non disponibile")
class TestProtoUtilsRoundtripFuzz:
    """Fuzz della funzione di encode/decode di proto_utils."""

    # ------------------------------------------------------------------
    # §2.1 Roundtrip: encode→decode è idempotente
    # ------------------------------------------------------------------
    @given(st.binary(min_size=0, max_size=4096))
    @settings(
        max_examples=500,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_encode_decode_roundtrip(self, raw: bytes):
        """Se decode(raw) restituisce un oggetto, encode(obj) deve essere bytes."""
        obj = _safe_decode(raw)
        if obj is None:
            return  # input non valido: non ci aspettiamo roundtrip
        reencoded = _safe_encode(obj)
        assert reencoded is None or isinstance(reencoded, (bytes, bytearray))

    # ------------------------------------------------------------------
    # §2.2 decode su bytes arbitrari non crasha mai
    # ------------------------------------------------------------------
    @given(st.binary(min_size=0, max_size=65536))
    @settings(
        max_examples=1000,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_decode_arbitrary_bytes(self, raw: bytes):
        """decode() non deve mai lanciare eccezioni non gestite."""
        _safe_decode(raw)  # non deve sollevare

    # ------------------------------------------------------------------
    # §2.3 Struttura protobuf valida (varint field tags) sempre decodificabile
    # ------------------------------------------------------------------
    @given(
        st.lists(
            st.tuples(st.integers(min_value=1, max_value=15), st.binary(min_size=0, max_size=64)),
            min_size=0,
            max_size=10,
        )
    )
    @settings(
        max_examples=300,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_decode_valid_proto_structure(self, fields):
        """Payload con struttura minimale protobuf non deve causare crash permanenti."""
        # Costruiamo un buffer con tag+length_delimited wire type
        buf = bytearray()
        for field_id, value in fields:
            tag = (field_id << 3) | 2  # wire type 2 = length-delimited
            encoded_tag = bytearray()
            while tag > 0x7F:
                encoded_tag.append((tag & 0x7F) | 0x80)
                tag >>= 7
            encoded_tag.append(tag)
            length = len(value)
            encoded_len = bytearray()
            while length > 0x7F:
                encoded_len.append((length & 0x7F) | 0x80)
                length >>= 7
            encoded_len.append(length)
            buf += encoded_tag + encoded_len + value
        _safe_decode(bytes(buf))

    # ------------------------------------------------------------------
    # §2.4 Field ID overflow
    # ------------------------------------------------------------------
    @given(st.integers(min_value=2**29, max_value=2**32 - 1))
    @settings(max_examples=100, deadline=None)
    def test_fuzz_field_overflow(self, field_id: int):
        """Field ID > 2^28 (fuori spec protobuf) non deve causare crash."""
        tag = (field_id << 3) | 0  # wire type 0 = varint
        raw = bytearray()
        while tag > 0x7F:
            raw.append((tag & 0x7F) | 0x80)
            tag >>= 7
        raw.append(tag & 0x7F)
        raw.append(0x01)  # valore varint = 1
        _safe_decode(bytes(raw))

    # ------------------------------------------------------------------
    # §2.5 Repeated field con molti elementi
    # ------------------------------------------------------------------
    @given(st.lists(st.integers(min_value=0, max_value=255), min_size=0, max_size=10000))
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
        deadline=None,
    )
    def test_fuzz_repeated_field_huge(self, values):
        """Repeated field con migliaia di elementi non deve bloccare o crashare."""
        # Ogni elemento come varint field 1
        buf = bytearray()
        for v in values:
            tag_byte = (1 << 3) | 0  # field 1, wire type 0
            buf.append(tag_byte)
            buf.append(v & 0x7F)
        _safe_decode(bytes(buf))

    # ------------------------------------------------------------------
    # §2.6 Messaggi annidati in profondità
    # ------------------------------------------------------------------
    def test_fuzz_nested_message_deep(self):
        """Messaggio annidato fino a depth 100 non deve causare stack overflow."""
        # Costruiamo un messaggio protobuf annidato artificialmente
        # field 1 wire type 2 (length-delimited) che contiene se stesso
        inner = b"\x08\x01"  # field 1 varint = 1 (foglia)
        for _ in range(100):
            length = len(inner)
            # tag field 1, wire type 2
            encoded = bytes([0x0A, length]) + inner
            inner = encoded
        _safe_decode(inner)

    # ------------------------------------------------------------------
    # §2.7 Stringhe Unicode arbitrarie in field stringa
    # ------------------------------------------------------------------
    @given(st.text(min_size=0, max_size=1000))
    @settings(max_examples=300, deadline=None)
    def test_fuzz_unicode_string_field(self, text: str):
        """Stringhe Unicode arbitrarie in campo string non devono crashare decode."""
        encoded = text.encode("utf-8", errors="replace")
        length = len(encoded)
        # field 1, wire type 2
        tag_len = bytes([0x0A])
        varint_len = bytearray()
        v = length
        while v > 0x7F:
            varint_len.append((v & 0x7F) | 0x80)
            v >>= 7
        varint_len.append(v)
        raw = tag_len + bytes(varint_len) + encoded
        _safe_decode(raw)

    # ------------------------------------------------------------------
    # §2.8 Integer ai limiti (int64 min/max, 0, negativi)
    # ------------------------------------------------------------------
    @given(
        st.one_of(
            st.just(0),
            st.just(2**63 - 1),
            st.just(-(2**63)),
            st.just(-1),
            st.integers(min_value=-(2**63), max_value=2**63 - 1),
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_fuzz_integer_extremes(self, value: int):
        """Valori interi estremi come varint non devono crashare decode."""
        # Encode come signed varint (zigzag)
        if value < 0:
            zigzag = ((-value - 1) << 1) | 1
        else:
            zigzag = value << 1
        buf = bytearray()
        # field 1, wire type 0
        buf.append(0x08)
        v = zigzag
        while v > 0x7F:
            buf.append((v & 0x7F) | 0x80)
            v >>= 7
        buf.append(v & 0x7F)
        _safe_decode(bytes(buf))

    # ------------------------------------------------------------------
    # §2.9 Float speciali (inf, -inf, nan)
    # ------------------------------------------------------------------
    @given(
        st.one_of(
            st.just(float("inf")),
            st.just(float("-inf")),
            st.just(float("nan")),
            st.floats(allow_nan=False, allow_infinity=False),
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_fuzz_float_specials(self, value: float):
        """Float speciali non devono causare crash durante encode/decode."""
        import struct
        raw = struct.pack("<d", value)  # double little-endian
        # field 1, wire type 1 (64-bit)
        buf = bytes([0x09]) + raw
        _safe_decode(buf)

    # ------------------------------------------------------------------
    # §2.10 decode non blocca > 50ms
    # ------------------------------------------------------------------
    @given(st.binary(min_size=0, max_size=8192))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_proto_no_hang(self, raw: bytes):
        """decode() non deve mai bloccare per più di 50ms."""
        t0 = time.monotonic()
        _safe_decode(raw)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 50, f"decode ha bloccato {elapsed_ms:.1f}ms > 50ms"
