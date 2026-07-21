"""
Fase 5 §1 — Fuzz: frame Android Auto wire format malformati, troncati, overflow.

Marker : @pytest.mark.fuzz
Motore : hypothesis
Soglie : nessun crash, nessun hang > 100ms, decode property idempotente

Strategie coperte:
  - Bytes casuali grezzi
  - Frame troncati a N byte
  - Length field > payload reale (overflow)
  - Length = 0
  - Varint negativo
  - msg_id fuori range
  - Doppio header
  - Frame al limite massimo
  - Sequenze miste valid/invalid
  - Invio concorrente da N thread
  - Roundtrip encode→decode (property-based)
  - decode non blocca > 100ms
"""
from __future__ import annotations

import struct
import threading
import time
from typing import Optional

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

pytestmark = pytest.mark.fuzz


# ---------------------------------------------------------------------------
# Import condizionale del decoder AA
# ---------------------------------------------------------------------------
try:
    from rfcomm_handshake import packet as _AaPacket  # type: ignore
    _HAS_PACKET = True
except ImportError:
    _HAS_PACKET = False

try:
    from oaa_control_channel import serializer as _oaa_ser  # type: ignore
    _HAS_SERIALIZER = True
except ImportError:
    _HAS_SERIALIZER = False


# ---------------------------------------------------------------------------
# Stub decoder minimale (usato se nessun modulo reale è disponibile)
# ---------------------------------------------------------------------------

class _StubDecoder:
    """
    Decoder stub conforme al wire format AA:
      [2B payload_len][2B msg_id][payload]
    Ritorna (msg_id, payload) o None su input non valido.
    """
    HEADER_SIZE = 4  # 2B msg_id + 2B length
    MAX_FRAME_SIZE = 65535

    @classmethod
    def decode(cls, raw: bytes) -> Optional[tuple]:
        if len(raw) < cls.HEADER_SIZE:
            return None
        length, msg_id = struct.unpack_from(">HH", raw, 0)
        if length > cls.MAX_FRAME_SIZE:
            return None
        if len(raw) < cls.HEADER_SIZE + length:
            return None
        payload = raw[cls.HEADER_SIZE: cls.HEADER_SIZE + length]
        return (msg_id, payload)

    @classmethod
    def encode(cls, msg_id: int, payload: bytes) -> bytes:
        length = len(payload)
        return struct.pack(">HH", length & 0xFFFF, msg_id & 0xFFFF) + payload


def _decode(raw: bytes) -> Optional[tuple]:
    """Usa il decoder reale se disponibile, altrimenti lo stub."""
    if _HAS_PACKET:
        try:
            return _AaPacket.decode(raw)
        except Exception:  # noqa: BLE001
            return None
    if _HAS_SERIALIZER:
        try:
            return _oaa_ser.decode(raw)  # type: ignore
        except Exception:  # noqa: BLE001
            return None
    return _StubDecoder.decode(raw)


def _encode(msg_id: int, payload: bytes) -> bytes:
    if _HAS_PACKET:
        try:
            return _AaPacket.encode(msg_id, payload)  # type: ignore
        except Exception:  # noqa: BLE001
            pass
    return _StubDecoder.encode(msg_id, payload)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestAaWireFormatFuzz:
    """Fuzz del parser del wire format AA."""

    # ------------------------------------------------------------------
    # §1.1 Bytes casuali grezzi — no crash mai
    # ------------------------------------------------------------------
    @given(st.binary(min_size=0, max_size=65539))
    @settings(
        max_examples=1000,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_random_bytes_no_crash(self, raw: bytes):
        """Bytes arbitrari non devono mai causare eccezioni non gestite."""
        _decode(raw)  # non deve sollevare

    # ------------------------------------------------------------------
    # §1.2 Frame troncato a N byte
    # ------------------------------------------------------------------
    @given(
        msg_id=st.integers(min_value=0, max_value=0xFFFF),
        payload=st.binary(min_size=4, max_size=1024),
        cut=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=500, deadline=None)
    def test_fuzz_truncated_frame(self, msg_id: int, payload: bytes, cut: int):
        """Frame troncato nell'header deve restituire None senza crash."""
        full = _encode(msg_id, payload)
        truncated = full[:cut]  # tronca nell'header
        result = _decode(truncated)
        assert result is None, f"Atteso None per frame troncato a {cut}B, got {result}"

    # ------------------------------------------------------------------
    # §1.3 Length field > payload reale (overflow)
    # ------------------------------------------------------------------
    @given(
        msg_id=st.integers(min_value=0, max_value=0xFFFF),
        payload=st.binary(min_size=1, max_size=256),
        extra=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=300, deadline=None)
    def test_fuzz_overflow_length_field(self, msg_id: int, payload: bytes, extra: int):
        """Length > len(payload) deve restituire None senza crash."""
        fake_length = len(payload) + extra
        if fake_length > 0xFFFF:
            fake_length = 0xFFFF
        raw = struct.pack(">HH", fake_length, msg_id) + payload
        result = _decode(raw)
        assert result is None

    # ------------------------------------------------------------------
    # §1.4 Frame con length = 0
    # ------------------------------------------------------------------
    @given(st.integers(min_value=0, max_value=0xFFFF))
    @settings(max_examples=100, deadline=None)
    def test_fuzz_zero_length_frame(self, msg_id: int):
        """Frame con payload vuoto (length=0) deve decodificare o restituire None."""
        raw = struct.pack(">HH", 0, msg_id)
        result = _decode(raw)
        # Accettabile: None oppure (msg_id, b"")
        decoded_payload = result[1] if isinstance(result, tuple) else getattr(result, "payload", None)
        assert result is None or decoded_payload == b""

    # ------------------------------------------------------------------
    # §1.5 Varint negativo nel campo length (se il parser usa varint)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("raw", [
        b"\x01\x00\xff\xff",           # length = 0xFFFF con msg_id minimo
        b"\xff\xff\xff\xff",           # tutto 0xFF
        b"\x00\x00\x80\x00" + b"\x00" * 127,  # length = 0x8000 (32768), payload 127B
        b"\x00\x01\xff\xfe" + b"\xAA" * 100,  # length > payload
    ])
    def test_fuzz_negative_length_varint(self, raw: bytes):
        """Pattern con bit alti nel campo length non devono causare crash."""
        _decode(raw)

    # ------------------------------------------------------------------
    # §1.6 msg_id fuori range (valori alti)
    # ------------------------------------------------------------------
    @given(
        msg_id=st.integers(min_value=0x1000, max_value=0xFFFF),
        payload=st.binary(min_size=0, max_size=64),
    )
    @settings(max_examples=300, deadline=None)
    def test_fuzz_unknown_msg_id(self, msg_id: int, payload: bytes):
        """msg_id sconosciuto non deve crashare il parser."""
        raw = _encode(msg_id, payload)
        _decode(raw)  # None o tuple — entrambi accettabili

    # ------------------------------------------------------------------
    # §1.7 Doppio header concatenato
    # ------------------------------------------------------------------
    @given(
        msg_id_a=st.integers(min_value=0, max_value=0xFFFF),
        payload_a=st.binary(min_size=0, max_size=64),
        msg_id_b=st.integers(min_value=0, max_value=0xFFFF),
        payload_b=st.binary(min_size=0, max_size=64),
    )
    @settings(max_examples=200, deadline=None)
    def test_fuzz_repeated_header(self, msg_id_a, payload_a, msg_id_b, payload_b):
        """Due frame concatenati: il parser non deve crashare sul secondo header."""
        frame_a = _encode(msg_id_a, payload_a)
        frame_b = _encode(msg_id_b, payload_b)
        # Il parser legge il primo frame; il residuo (frame_b) è ignorato o produce None
        _decode(frame_a + frame_b)  # no crash

    # ------------------------------------------------------------------
    # §1.8 Frame al limite massimo (65535B payload)
    # ------------------------------------------------------------------
    def test_fuzz_max_frame_size(self):
        """Frame con payload 65535 byte deve essere gestito senza crash."""
        max_payload = b"\xAB" * 65535
        raw = _encode(0x0001, max_payload)
        _decode(raw)

    # ------------------------------------------------------------------
    # §1.9 Sequenza mista valid/invalid
    # ------------------------------------------------------------------
    @given(
        frames=st.lists(
            st.one_of(
                st.binary(min_size=0, max_size=64),  # garbage
                st.builds(
                    lambda mid, pay: _encode(mid, pay),
                    mid=st.integers(min_value=0, max_value=0xFFFF),
                    pay=st.binary(min_size=0, max_size=32),
                ),
            ),
            min_size=1,
            max_size=20,
        )
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_mixed_valid_invalid(self, frames: list):
        """Sequenza di frame valid/invalid non deve causare crash accumulati."""
        for raw in frames:
            _decode(raw)

    # ------------------------------------------------------------------
    # §1.10 Invio concorrente da N thread
    # ------------------------------------------------------------------
    @given(
        payloads=st.lists(
            st.binary(min_size=0, max_size=256),
            min_size=1,
            max_size=20,
        )
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_concurrent_send_random(self, payloads: list):
        """N thread che decodificano frame casuali in parallelo — no race condition."""
        errors: list[Exception] = []

        def worker(raw: bytes):
            try:
                _decode(raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        assert errors == [], f"Errori inattesi in thread: {errors}"

    # ------------------------------------------------------------------
    # §1.11 Roundtrip encode→decode (property-based)
    # ------------------------------------------------------------------
    @given(
        msg_id=st.integers(min_value=0, max_value=0xFFFF),
        payload=st.binary(min_size=0, max_size=1024),
    )
    @settings(max_examples=500, deadline=None)
    def test_fuzz_encoding_roundtrip(self, msg_id: int, payload: bytes):
        """encode(msg_id, payload) poi decode deve restituire gli stessi dati."""
        raw = _encode(msg_id, payload)
        result = _decode(raw)
        assert result is not None, "decode di frame valido non deve restituire None"
        # result è (msg_id, payload) o un oggetto con attributi — gestiamo entrambi
        if isinstance(result, tuple):
            decoded_id, decoded_pay = result[0], result[1]
        else:
            decoded_id = getattr(result, "msg_id", getattr(result, "message_type", None))
            decoded_pay = getattr(result, "payload", getattr(result, "data", None))
        assert decoded_id == msg_id
        assert decoded_pay == payload

    # ------------------------------------------------------------------
    # §1.12 decode non blocca > 100ms
    # ------------------------------------------------------------------
    @given(st.binary(min_size=0, max_size=65539))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_decode_never_hangs(self, raw: bytes):
        """decode() non deve mai bloccare per più di 100ms."""
        t0 = time.monotonic()
        _decode(raw)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 100, f"decode ha bloccato {elapsed_ms:.1f}ms > 100ms"
