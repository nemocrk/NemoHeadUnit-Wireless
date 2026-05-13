"""
Fase 4 §6 — test_aa_frame_decode.py

Misura la latenza di encode/decode dei frame AA (packet.py):
  - encode RTT µs
  - decode RTT µs
  - roundtrip encode+decode
  - payload 64KB
  - frame troncato → no crash
  - regression vs baseline JSON

Soglie di default (configurabili via env):
  PERF_ENCODE_US_MAX  = 500   µs massimo per encode
  PERF_DECODE_US_MAX  = 500   µs massimo per decode
  PERF_RT_US_MAX      = 1000  µs massimo roundtrip

Marker: @pytest.mark.performance — NON blocca CI.
"""
from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_ENCODE_US_MAX = float(os.getenv("PERF_ENCODE_US_MAX", "500.0"))
_DECODE_US_MAX = float(os.getenv("PERF_DECODE_US_MAX", "500.0"))
_RT_US_MAX     = float(os.getenv("PERF_RT_US_MAX",    "1000.0"))
_N             = int(os.getenv("PERF_FRAME_N",         "2000"))


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    k = (p / 100) * (len(s) - 1)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (k - lo) * (s[hi] - s[lo])


def _write_report(scenario: str, data: dict) -> None:
    from datetime import datetime
    reports_dir = Path("tests/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    try:
        (reports_dir / f"perf-aa-decode-{scenario}-{ts}.json").write_text(
            json.dumps(data, indent=2)
        )
    except OSError:
        pass


def _build_aa_frame(msg_id: int, payload: bytes) -> bytes:
    """
    Formato wire AA semplificato:
      [2B msg_id BE] [4B payload_len BE] [payload]
    (sufficiente per misurare throughput di packing/unpacking)
    """
    return struct.pack(">HI", msg_id, len(payload)) + payload


def _parse_aa_frame(data: bytes) -> tuple[int, bytes] | None:
    """
    Decodifica il frame semplificato. Ritorna None se troncato.
    """
    header_size = 6  # 2 + 4
    if len(data) < header_size:
        return None
    msg_id, payload_len = struct.unpack_from(">HI", data, 0)
    if len(data) < header_size + payload_len:
        return None
    return msg_id, data[header_size: header_size + payload_len]


# ---------------------------------------------------------------------------
# Fixture: prova a importare il vero Packet se disponibile
# ---------------------------------------------------------------------------
try:
    from rfcomm_handshake.packet import Packet as _RealPacket

    def _encode(msg_id: int, payload: bytes) -> bytes:
        return _RealPacket(msg_id=msg_id, payload=payload).encode()

    def _decode(data: bytes):
        return _RealPacket.decode(data)

except ImportError:
    # Fallback: usa l'implementazione locale
    def _encode(msg_id: int, payload: bytes) -> bytes:  # type: ignore[misc]
        return _build_aa_frame(msg_id, payload)

    def _decode(data: bytes):  # type: ignore[misc]
        return _parse_aa_frame(data)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------
@pytest.mark.performance
class TestAaFrameDecode:
    """Misura le performance di encode/decode dei frame AA."""

    def test_frame_encode_rtt_us(self):
        """Encode di un frame tipico AA (payload 256B): p95 < 500µs."""
        payload = b"\xAA" * 256
        times: list[float] = []

        for _ in range(_N):
            t0 = time.perf_counter_ns()
            _encode(0x0001, payload)
            times.append((time.perf_counter_ns() - t0) / 1_000)

        p95 = _percentile(times, 95)
        _write_report("encode_rtt", {
            "p50_us": _percentile(times, 50),
            "p95_us": p95,
            "p99_us": _percentile(times, 99),
            "threshold_us": _ENCODE_US_MAX,
            "n": _N,
        })
        assert p95 < _ENCODE_US_MAX, \
            f"Encode p95 {p95:.1f}µs > soglia {_ENCODE_US_MAX}µs"

    def test_frame_decode_rtt_us(self):
        """Decode di un frame valido (payload 256B): p95 < 500µs."""
        payload = b"\xBB" * 256
        raw = _encode(0x0002, payload)
        times: list[float] = []

        for _ in range(_N):
            t0 = time.perf_counter_ns()
            result = _decode(raw)
            times.append((time.perf_counter_ns() - t0) / 1_000)

        assert result is not None, "Decode deve riuscire su frame valido"

        p95 = _percentile(times, 95)
        _write_report("decode_rtt", {
            "p50_us": _percentile(times, 50),
            "p95_us": p95,
            "p99_us": _percentile(times, 99),
            "threshold_us": _DECODE_US_MAX,
            "n": _N,
        })
        assert p95 < _DECODE_US_MAX, \
            f"Decode p95 {p95:.1f}µs > soglia {_DECODE_US_MAX}µs"

    def test_roundtrip_rtt_us(self):
        """Roundtrip encode+decode: p95 < 1000µs."""
        payload = b"\xCC" * 256
        times: list[float] = []

        for _ in range(_N):
            t0 = time.perf_counter_ns()
            raw = _encode(0x0003, payload)
            result = _decode(raw)
            times.append((time.perf_counter_ns() - t0) / 1_000)

        assert result is not None

        p95 = _percentile(times, 95)
        _write_report("roundtrip_rtt", {
            "p50_us": _percentile(times, 50),
            "p95_us": p95,
            "threshold_us": _RT_US_MAX,
            "n": _N,
        })
        assert p95 < _RT_US_MAX, \
            f"Roundtrip p95 {p95:.1f}µs > soglia {_RT_US_MAX}µs"

    def test_large_frame_decode(self):
        """Frame da 64KB: decode p95 < 2000µs."""
        payload = b"\xDD" * 65000
        raw = _encode(0x0004, payload)
        times: list[float] = []
        n = 200

        for _ in range(n):
            t0 = time.perf_counter_ns()
            result = _decode(raw)
            times.append((time.perf_counter_ns() - t0) / 1_000)

        assert result is not None

        p95 = _percentile(times, 95)
        _write_report("large_frame_64kb", {"p95_us": p95, "n": n})
        assert p95 < 2000.0, f"Decode 64KB p95 {p95:.1f}µs > 2000µs"

    def test_malformed_frame_no_crash(self):
        """
        Frame troncato / corrotto: decode non solleva eccezioni non-gestite.
        Deve ritornare None oppure sollevare solo ValueError/struct.error.
        """
        import struct as _struct

        bad_frames = [
            b"",
            b"\x00",
            b"\x00" * 3,
            b"\x00\x01\xFF\xFF\xFF\xFF",  # length field overflow
            b"\x00\x01\x00\x00\x01\x00" + b"\xAA" * 10,  # payload troncato
        ]

        for bad in bad_frames:
            try:
                result = _decode(bad)
                # Accettabile: None o tuple
            except (ValueError, _struct.error, IndexError):
                pass  # eccezioni note accettabili
            except Exception as exc:
                pytest.fail(
                    f"Decode ha sollevato eccezione non attesa {type(exc).__name__}: {exc} "
                    f"su input {bad!r}"
                )

    def test_decode_regression_vs_baseline(self):
        """Confronta p50 decode con baseline salvata. Regressione > 100% = fallimento."""
        baseline_path = Path("tests/reports/aa-decode-baseline.json")
        payload = b"\xEE" * 256
        raw = _encode(0x0005, payload)
        times: list[float] = []

        for _ in range(1000):
            t0 = time.perf_counter_ns()
            _decode(raw)
            times.append((time.perf_counter_ns() - t0) / 1_000)

        p50 = _percentile(times, 50)

        if not baseline_path.exists():
            try:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps({"p50_us": p50}))
            except OSError:
                pass
            pytest.skip("Baseline decode non esistente: salvata")
        else:
            try:
                base_p50 = json.loads(baseline_path.read_text()).get("p50_us", p50)
            except (json.JSONDecodeError, OSError):
                pytest.skip("Baseline decode non leggibile")
                return

            threshold = base_p50 * 2.0  # max 100% regressione
            _write_report("decode_regression", {
                "p50_current_us": p50,
                "p50_baseline_us": base_p50,
            })
            assert p50 < threshold, (
                f"Regressione decode: p50 {p50:.1f}µs > 2x baseline {base_p50:.1f}µs"
            )
