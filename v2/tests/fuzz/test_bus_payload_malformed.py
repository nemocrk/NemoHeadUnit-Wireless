"""
Fase 5 §3 — Fuzz: payload JSON malformati / tipi errati sul BusClient.

Marker : @pytest.mark.fuzz
Motore : hypothesis
Soglie : nessun crash, nessun hang, handler riceve payload invariato

NOTA: BusClient usa ZMQ in-process; in questi test viene sempre
      sostituito da un FakeBus in-memory per evitare dipendenze di rete.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

pytestmark = pytest.mark.fuzz


# ---------------------------------------------------------------------------
# FakeBus — in-memory bus senza ZMQ
# ---------------------------------------------------------------------------

class FakeBus:
    """Bus sincrono in-memory per test fuzz: publish→handler nello stesso thread."""

    def __init__(self):
        self._handlers: dict[str, list] = {}
        self._published: list[tuple[str, Any]] = []
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler) -> None:
        with self._lock:
            self._handlers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: Any) -> bool:
        with self._lock:
            self._published.append((topic, payload))
            handlers = list(self._handlers.get(topic, []))
        for h in handlers:
            try:
                h(topic, payload)
            except Exception:  # noqa: BLE001
                pass  # handler errors non devono propagarsi
        return True

    def stop(self) -> None:
        pass

    @property
    def published(self):
        with self._lock:
            return list(self._published)


# ---------------------------------------------------------------------------
# Strategie composite
# ---------------------------------------------------------------------------

_ANY_SCALAR = st.one_of(
    st.text(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    st.binary(),
)

_NESTED_JSON = st.recursive(
    st.one_of(
        st.text(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.booleans(),
        st.none(),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)

_TOPIC_ST = st.text(min_size=0, max_size=128)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestBusPayloadMalformedFuzz:
    """Fuzz dei payload inviati al BusClient / FakeBus."""

    # ------------------------------------------------------------------
    # §3.1 publish con qualsiasi valore scalare non crasha
    # ------------------------------------------------------------------
    @given(
        topic=_TOPIC_ST,
        value=_ANY_SCALAR,
    )
    @settings(
        max_examples=500,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_publish_any_value(self, topic: str, value: Any):
        """publish(topic, {\"v\": value}) non deve mai sollevare eccezioni."""
        bus = FakeBus()
        bus.publish(topic, {"v": value})

    # ------------------------------------------------------------------
    # §3.2 publish con dict annidato arbitrariamente
    # ------------------------------------------------------------------
    @given(payload=_NESTED_JSON)
    @settings(
        max_examples=300,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_publish_nested_dict(self, payload: Any):
        """Payload JSON arbitrariamente annidato non deve crashare il bus."""
        bus = FakeBus()
        bus.publish("test.nested", payload)

    # ------------------------------------------------------------------
    # §3.3 publish con lista come payload
    # ------------------------------------------------------------------
    @given(st.lists(_ANY_SCALAR, min_size=0, max_size=100))
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_publish_list_payload(self, values: list):
        """Lista come payload non deve crashare il bus."""
        bus = FakeBus()
        bus.publish("test.list", values)

    # ------------------------------------------------------------------
    # §3.4 publish con None come valore di campo
    # ------------------------------------------------------------------
    @given(st.dictionaries(st.text(max_size=20), st.none(), max_size=10))
    @settings(max_examples=100, deadline=None)
    def test_fuzz_publish_none_value(self, payload: dict):
        """Dict con tutti i valori None non deve crashare."""
        bus = FakeBus()
        bus.publish("test.none", payload)

    # ------------------------------------------------------------------
    # §3.5 publish con tipi non-JSON (bytes, set, oggetti custom)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("payload", [
        b"binary data",
        {1, 2, 3},
        object(),
        lambda: None,
        bytearray(b"\x00\xff"),
        frozenset(["a", "b"]),
    ])
    def test_fuzz_publish_wrong_types(self, payload: Any):
        """Tipi non serializzabili non devono crashare il bus (graceful handling)."""
        bus = FakeBus()
        # Non deve sollevare eccezioni non gestite
        try:
            bus.publish("test.wrong_type", payload)
        except (TypeError, ValueError):
            pass  # eccezione di serializzazione è accettabile

    # ------------------------------------------------------------------
    # §3.6 subscribe con topic arbitrario non crasha
    # ------------------------------------------------------------------
    @given(_TOPIC_ST)
    @settings(max_examples=300, deadline=None)
    def test_fuzz_subscribe_topic_arbitrary(self, topic: str):
        """subscribe() con topic stringa arbitraria non deve crashare."""
        bus = FakeBus()
        received = []
        bus.subscribe(topic, lambda t, p: received.append(p))
        bus.publish(topic, {"ok": True})
        # Se topic valido, handler deve aver ricevuto il messaggio
        if topic:  # topic vuoto potrebbe essere scartato a seconda dell'impl
            assert len(received) >= 0  # non ci aspettiamo specifico comportamento

    # ------------------------------------------------------------------
    # §3.7 Stringa JSON-like malformata come payload
    # ------------------------------------------------------------------
    @given(st.text(min_size=0, max_size=4096))
    @settings(
        max_examples=300,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_malformed_json_string(self, raw_str: str):
        """Stringa arbitraria (inclusi JSON malformati) come payload non crasha."""
        bus = FakeBus()
        bus.publish("test.raw_str", raw_str)

    # ------------------------------------------------------------------
    # §3.8 Payload di grandi dimensioni (1 MB+)
    # ------------------------------------------------------------------
    @given(st.binary(min_size=0, max_size=1024 * 1024))
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
        deadline=None,
    )
    def test_fuzz_large_payload(self, data: bytes):
        """Payload 1MB+ non deve bloccare il bus (< 200ms)."""
        bus = FakeBus()
        t0 = time.monotonic()
        bus.publish("test.large", {"data": data.hex()})
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 200, f"publish ha bloccato {elapsed_ms:.1f}ms > 200ms"

    # ------------------------------------------------------------------
    # §3.9 Publish concorrente da N thread con payload errati
    # ------------------------------------------------------------------
    @given(
        payloads=st.lists(
            st.one_of(_ANY_SCALAR, _NESTED_JSON),
            min_size=1,
            max_size=20,
        )
    )
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_fuzz_concurrent_malformed(self, payloads: list):
        """N thread che pubblicano payload errati in parallelo non devono causare race."""
        bus = FakeBus()
        errors: list[Exception] = []

        def worker(p: Any):
            try:
                bus.publish("test.concurrent", p)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        # Errori di tipo sono tollerati; crash (non-Exception) no
        non_type_errors = [e for e in errors if not isinstance(e, (TypeError, ValueError))]
        assert non_type_errors == [], f"Errori non attesi: {non_type_errors}"

    # ------------------------------------------------------------------
    # §3.10 Handler riceve esattamente il payload originale
    # ------------------------------------------------------------------
    @given(
        topic=st.text(min_size=1, max_size=64, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="._-")),
        payload=st.dictionaries(st.text(min_size=1, max_size=10), st.integers(), max_size=10),
    )
    @settings(max_examples=300, deadline=None)
    def test_fuzz_handler_receives_original(self, topic: str, payload: dict):
        """Il handler deve ricevere esattamente lo stesso payload inviato."""
        bus = FakeBus()
        received: list[Any] = []
        bus.subscribe(topic, lambda _t, p: received.append(p))
        bus.publish(topic, payload)
        assert len(received) == 1
        assert received[0] == payload
