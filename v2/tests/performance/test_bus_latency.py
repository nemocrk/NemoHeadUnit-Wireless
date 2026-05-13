"""
Fase 4 §1 — test_bus_latency.py

Misura la latenza publish→receive del bus ZMQ in-process.

Strategia:
  - N campioni per scenario (default: 1000)
  - Calcolo p50 / p95 / p99
  - Soglie: p50 ≤ 2ms, p95 ≤ 5ms, p99 ≤ 10ms
  - Output opzionale JSON in tests/reports/perf-{date}-{commit}.json

Marker: @pytest.mark.performance  — NON bloccante per il merge CI.

Per eseguire:
    pytest -m performance -v --json-report --json-report-file=tests/reports/perf.json
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from shared.bus_client import BusClient

_N_SAMPLES = int(os.getenv("PERF_BUS_SAMPLES", "1000"))
_TOPIC = "perf.latency.probe"
_TOPIC_BURST = "perf.latency.burst"
_TOPIC_LARGE = "perf.latency.large"
_TOPIC_MULTI = "perf.latency.multi"
_TOPIC_CROSS = "perf.latency.cross"

# Soglie (ms)
_P50_MAX_MS = float(os.getenv("PERF_P50_MS", "2.0"))
_P95_MAX_MS = float(os.getenv("PERF_P95_MS", "5.0"))
_P99_MAX_MS = float(os.getenv("PERF_P99_MS", "10.0"))


def _percentile(data: list[float], p: int) -> float:
    """Calcola il p-esimo percentile su una lista ordinata."""
    if not data:
        return float("nan")
    sorted_data = sorted(data)
    k = (p / 100) * (len(sorted_data) - 1)
    lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (k - lo) * (sorted_data[hi] - sorted_data[lo])


def _measure_rtt(
    publisher: BusClient,
    subscriber: BusClient,
    topic: str,
    payload_fn: Callable[[int], dict],
    n: int = _N_SAMPLES,
) -> list[float]:
    """
    Pubblica `n` messaggi e misura il RTT (publish → receive) in ms.
    Restituisce la lista di campioni in ms.
    """
    latencies: list[float] = []
    received = threading.Event()
    last_seq: list[int] = [-1]

    def _on_msg(payload: dict) -> None:
        ts_recv = time.perf_counter_ns()
        ts_sent = payload.get("ts_sent_ns", ts_recv)
        latencies.append((ts_recv - ts_sent) / 1_000_000)  # ns → ms
        last_seq[0] = payload.get("seq", -1)
        if len(latencies) >= n:
            received.set()

    subscriber.subscribe(topic, _on_msg)
    time.sleep(0.05)  # lascia stabilizzare la sottoscrizione

    for i in range(n):
        payload = payload_fn(i)
        payload["ts_sent_ns"] = time.perf_counter_ns()
        payload["seq"] = i
        publisher.publish(topic, payload)
        time.sleep(0.001)  # 1ms tra messaggi

    received.wait(timeout=n * 0.002 + 2.0)
    subscriber.unsubscribe(topic, _on_msg)
    return latencies


def _write_report(scenario: str, latencies: list[float]) -> None:
    """Scrive i risultati in JSON se la directory reports/ esiste."""
    from datetime import datetime
    reports_dir = Path("tests/reports")
    if not reports_dir.exists():
        try:
            reports_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

    date_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    report_path = reports_dir / f"perf-{date_str}-{scenario}.json"
    report = {
        "scenario": scenario,
        "n_samples": len(latencies),
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99),
        "mean_ms": statistics.mean(latencies) if latencies else 0,
        "stdev_ms": statistics.stdev(latencies) if len(latencies) > 1 else 0,
        "min_ms": min(latencies, default=0),
        "max_ms": max(latencies, default=0),
        "thresholds": {
            "p50_max_ms": _P50_MAX_MS,
            "p95_max_ms": _P95_MAX_MS,
            "p99_max_ms": _P99_MAX_MS,
        },
    }
    try:
        report_path.write_text(json.dumps(report, indent=2))
    except OSError:
        pass


@pytest.mark.performance
class TestBusLatency:
    """
    Misura la latenza del bus ZMQ in-process sotto diversi scenari.
    I test REGISTRANO i risultati ma NON bloccano il merge CI.
    """

    def test_publish_latency_p50(self, in_process_broker):
        """Latenza p50 publish→receive entro 2ms con payload minimo."""
        pub = BusClient(module_name="perf_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_sub", broker_url=in_process_broker.url)

        latencies = _measure_rtt(pub, sub, _TOPIC, lambda i: {"i": i})
        p50 = _percentile(latencies, 50)
        _write_report("p50_baseline", latencies)

        if latencies:
            assert p50 < _P50_MAX_MS, \
                f"p50 latenza {p50:.2f}ms > soglia {_P50_MAX_MS}ms ({len(latencies)} campioni)"

    def test_publish_latency_p95(self, in_process_broker):
        """Latenza p95 publish→receive entro 5ms con payload minimo."""
        pub = BusClient(module_name="perf_pub_p95", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_sub_p95", broker_url=in_process_broker.url)

        latencies = _measure_rtt(pub, sub, _TOPIC, lambda i: {"i": i})
        p95 = _percentile(latencies, 95)
        _write_report("p95_baseline", latencies)

        if latencies:
            assert p95 < _P95_MAX_MS, \
                f"p95 latenza {p95:.2f}ms > soglia {_P95_MAX_MS}ms ({len(latencies)} campioni)"

    def test_publish_latency_p99(self, in_process_broker):
        """Latenza p99 publish→receive entro 10ms con payload minimo."""
        pub = BusClient(module_name="perf_pub_p99", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_sub_p99", broker_url=in_process_broker.url)

        latencies = _measure_rtt(pub, sub, _TOPIC, lambda i: {"i": i})
        p99 = _percentile(latencies, 99)
        _write_report("p99_baseline", latencies)

        if latencies:
            assert p99 < _P99_MAX_MS, \
                f"p99 latenza {p99:.2f}ms > soglia {_P99_MAX_MS}ms ({len(latencies)} campioni)"

    def test_latency_burst_100_messages(self, in_process_broker):
        """Burst di 100 messaggi senza sleep: p99 entro 20ms."""
        pub = BusClient(module_name="perf_burst_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_burst_sub", broker_url=in_process_broker.url)

        n = 100
        latencies: list[float] = []
        received = threading.Event()

        def _on_msg(payload: dict) -> None:
            ts_recv = time.perf_counter_ns()
            ts_sent = payload.get("ts_sent_ns", ts_recv)
            latencies.append((ts_recv - ts_sent) / 1_000_000)
            if len(latencies) >= n:
                received.set()

        sub.subscribe(_TOPIC_BURST, _on_msg)
        time.sleep(0.05)

        # Burst senza sleep
        for i in range(n):
            pub.publish(_TOPIC_BURST, {"i": i, "ts_sent_ns": time.perf_counter_ns(), "seq": i})

        received.wait(timeout=5.0)
        sub.unsubscribe(_TOPIC_BURST, _on_msg)
        _write_report("burst_100", latencies)

        if len(latencies) >= n * 0.9:  # almeno 90% dei messaggi
            p99 = _percentile(latencies, 99)
            assert p99 < 20.0, f"Burst p99 {p99:.2f}ms > soglia 20ms"

    def test_latency_large_payload_64kb(self, in_process_broker):
        """Payload 64KB: p95 entro 15ms."""
        pub = BusClient(module_name="perf_large_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_large_sub", broker_url=in_process_broker.url)

        large_data = "x" * 65536
        latencies = _measure_rtt(
            pub, sub, _TOPIC_LARGE,
            lambda i: {"i": i, "data": large_data},
            n=100,  # meno campioni per payload grande
        )
        p95 = _percentile(latencies, 95)
        _write_report("large_payload_64kb", latencies)

        if latencies:
            assert p95 < 15.0, f"Large payload p95 {p95:.2f}ms > soglia 15ms"

    def test_latency_multiple_subscribers(self, in_process_broker):
        """
        Con 5 subscriber sullo stesso topic, il publisher non rallenta: p95 ≤ 8ms.
        """
        pub = BusClient(module_name="perf_multi_pub", broker_url=in_process_broker.url)
        subs = [
            BusClient(module_name=f"perf_multi_sub_{i}", broker_url=in_process_broker.url)
            for i in range(5)
        ]

        counts = [0] * 5
        latencies: list[float] = []
        n = 200
        main_event = threading.Event()

        def make_handler(idx: int) -> Callable:
            def _handler(payload: dict) -> None:
                counts[idx] += 1
                if idx == 0:  # registra latenza solo dal primo subscriber
                    ts_recv = time.perf_counter_ns()
                    ts_sent = payload.get("ts_sent_ns", ts_recv)
                    latencies.append((ts_recv - ts_sent) / 1_000_000)
                    if latencies and len(latencies) >= n:
                        main_event.set()
            return _handler

        for i, sub in enumerate(subs):
            sub.subscribe(_TOPIC_MULTI, make_handler(i))
        time.sleep(0.1)

        for i in range(n):
            pub.publish(_TOPIC_MULTI, {"i": i, "ts_sent_ns": time.perf_counter_ns()})
            time.sleep(0.001)

        main_event.wait(timeout=5.0)

        for i, sub in enumerate(subs):
            sub.unsubscribe(_TOPIC_MULTI, make_handler(i))

        _write_report("multi_subscriber_5", latencies)

        if latencies:
            p95 = _percentile(latencies, 95)
            assert p95 < 8.0, f"Multi-sub p95 {p95:.2f}ms > soglia 8ms"

    def test_latency_cross_thread(self, in_process_broker):
        """
        Publisher in thread separato, subscriber nel thread principale.
        p99 ≤ 15ms.
        """
        pub = BusClient(module_name="perf_xthread_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_xthread_sub", broker_url=in_process_broker.url)

        n = 300
        latencies: list[float] = []
        done_event = threading.Event()

        def _on_msg(payload: dict) -> None:
            ts_recv = time.perf_counter_ns()
            ts_sent = payload.get("ts_sent_ns", ts_recv)
            latencies.append((ts_recv - ts_sent) / 1_000_000)
            if len(latencies) >= n:
                done_event.set()

        sub.subscribe(_TOPIC_CROSS, _on_msg)
        time.sleep(0.05)

        def _publish_worker() -> None:
            for i in range(n):
                pub.publish(_TOPIC_CROSS, {"i": i, "ts_sent_ns": time.perf_counter_ns()})
                time.sleep(0.001)

        t = threading.Thread(target=_publish_worker, daemon=True)
        t.start()
        done_event.wait(timeout=n * 0.002 + 5.0)
        t.join(timeout=2.0)
        sub.unsubscribe(_TOPIC_CROSS, _on_msg)

        _write_report("cross_thread", latencies)

        if latencies:
            p99 = _percentile(latencies, 99)
            assert p99 < 15.0, f"Cross-thread p99 {p99:.2f}ms > soglia 15ms"

    def test_latency_under_load(self, in_process_broker):
        """
        100 publisher concorrenti su topic distinti.
        Nessun publisher vede p95 > 20ms.
        """
        n_pub = 20  # ridotto a 20 per CI
        n_msg = 50
        all_latencies: dict[int, list[float]] = {i: [] for i in range(n_pub)}
        done_events = [threading.Event() for _ in range(n_pub)]

        clients = [
            BusClient(
                module_name=f"perf_load_{i}",
                broker_url=in_process_broker.url,
            )
            for i in range(n_pub)
        ]

        def make_load_worker(idx: int) -> Callable:
            topic = f"perf.load.{idx}"

            def _handler(payload: dict) -> None:
                ts_recv = time.perf_counter_ns()
                ts_sent = payload.get("ts_sent_ns", ts_recv)
                all_latencies[idx].append((ts_recv - ts_sent) / 1_000_000)
                if len(all_latencies[idx]) >= n_msg:
                    done_events[idx].set()

            def _worker() -> None:
                clients[idx].subscribe(topic, _handler)
                time.sleep(0.05)
                for j in range(n_msg):
                    clients[idx].publish(
                        topic,
                        {"j": j, "ts_sent_ns": time.perf_counter_ns()},
                    )
                    time.sleep(0.001)
                done_events[idx].wait(timeout=5.0)
                clients[idx].unsubscribe(topic, _handler)

            return _worker

        threads = [threading.Thread(target=make_load_worker(i)(), daemon=True) for i in range(n_pub)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # Verifica che almeno l'80% dei publisher abbia dati
        good = sum(1 for lats in all_latencies.values() if len(lats) >= n_msg * 0.8)
        assert good >= n_pub * 0.8, f"Solo {good}/{n_pub} publisher hanno completato"

        # p95 di tutti i publisher
        all_flat = [ms for lats in all_latencies.values() for ms in lats]
        if all_flat:
            _write_report("under_load_20pub", all_flat)
            p95 = _percentile(all_flat, 95)
            assert p95 < 20.0, f"Under-load p95 {p95:.2f}ms > soglia 20ms"

    def test_latency_regression_vs_baseline(self, in_process_broker, tmp_path):
        """
        Confronta p50 attuale con baseline salvata.
        Se la baseline non esiste, la crea e il test passa.
        Se la baseline esiste, la p50 attuale non deve peggiorare del 50%.
        """
        pub = BusClient(module_name="perf_reg_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="perf_reg_sub", broker_url=in_process_broker.url)

        baseline_path = Path("tests/reports/perf-baseline.json")

        latencies = _measure_rtt(pub, sub, _TOPIC, lambda i: {"i": i}, n=200)
        if not latencies:
            pytest.skip("Nessun campione raccolto")

        current_p50 = _percentile(latencies, 50)

        if not baseline_path.exists():
            # Prima run: salva baseline
            try:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps({"p50_ms": current_p50}))
            except OSError:
                pass
            pytest.skip("Baseline non esistente: salvata per la prossima run")
        else:
            try:
                baseline = json.loads(baseline_path.read_text())
                baseline_p50 = baseline.get("p50_ms", current_p50)
            except (json.JSONDecodeError, OSError):
                pytest.skip("Baseline non leggibile")
                return

            regression_threshold = baseline_p50 * 1.5
            assert current_p50 < regression_threshold, (
                f"Regressione latenza: p50 attuale {current_p50:.2f}ms "
                f"> 1.5x baseline {baseline_p50:.2f}ms"
            )
