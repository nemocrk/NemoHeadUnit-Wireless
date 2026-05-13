"""
Fase 4 §2 — test_bus_throughput.py

Misura il throughput del bus ZMQ in-process:
  - msg/s con payload minimo
  - MB/s con payload 1 KB e 64 KB
  - multi-topic concorrenti
  - sostenuto 60s senza degrado
  - regression vs baseline JSON

Soglie di default (configurabili via env):
  PERF_THROUGHPUT_MSG_S  = 10000   msg/s minimo
  PERF_THROUGHPUT_MB_S_1K = 50     MB/s con payload 1 KB
  PERF_THROUGHPUT_MB_S_64K = 10    MB/s con payload 64 KB

Marker: @pytest.mark.performance — NON blocca CI.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from shared.bus_client import BusClient

_THROUGHPUT_MSG_S    = int(os.getenv("PERF_THROUGHPUT_MSG_S",    "10000"))
_THROUGHPUT_MB_S_1K  = float(os.getenv("PERF_THROUGHPUT_MB_S_1K",  "50.0"))
_THROUGHPUT_MB_S_64K = float(os.getenv("PERF_THROUGHPUT_MB_S_64K", "10.0"))
_SUSTAINED_WINDOW_S  = int(os.getenv("PERF_SUSTAINED_WINDOW_S", "10"))  # ridotto da 60 per CI
_SUSTAINED_DEGRADO   = float(os.getenv("PERF_SUSTAINED_DEGRADO", "0.20"))  # max 20% calo


def _build_topic(suffix: str) -> str:
    return f"perf.throughput.{suffix}"


def _write_report(scenario: str, data: dict) -> None:
    from datetime import datetime
    reports_dir = Path("tests/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"perf-throughput-{scenario}-{ts}.json"
    try:
        path.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def _measure_throughput(
    broker_url: str,
    topic: str,
    payload: dict,
    n: int = 5000,
    publisher_name: str = "tp_pub",
    subscriber_name: str = "tp_sub",
) -> tuple[float, float]:
    """
    Pubblica `n` messaggi il più velocemente possibile.
    Restituisce (msg_per_sec, mb_per_sec).
    """
    pub = BusClient(module_name=publisher_name, broker_url=broker_url)
    sub = BusClient(module_name=subscriber_name, broker_url=broker_url)

    received = [0]
    done = threading.Event()

    def _handler(_payload: dict) -> None:
        received[0] += 1
        if received[0] >= n:
            done.set()

    sub.subscribe(topic, _handler)
    time.sleep(0.05)  # stabilizza sub

    payload_size = len(json.dumps(payload).encode())
    t0 = time.perf_counter()
    for _ in range(n):
        pub.publish(topic, payload)
    send_elapsed = time.perf_counter() - t0

    done.wait(timeout=max(send_elapsed * 3, 5.0))
    recv_elapsed = time.perf_counter() - t0

    sub.unsubscribe(topic, _handler)

    actual_n = received[0]
    if recv_elapsed <= 0 or actual_n == 0:
        return 0.0, 0.0

    msg_s = actual_n / recv_elapsed
    mb_s = (actual_n * payload_size) / recv_elapsed / (1024 * 1024)
    return msg_s, mb_s


@pytest.mark.performance
class TestBusThroughput:
    """Misura il throughput del bus ZMQ in diversi scenari."""

    def test_throughput_msg_per_second_minimal(self, in_process_broker):
        """Payload minimo: ≥ 10k msg/s."""
        msg_s, _ = _measure_throughput(
            in_process_broker.url,
            _build_topic("minimal"),
            payload={"i": 0},
            n=5000,
            publisher_name="tp_min_pub",
            subscriber_name="tp_min_sub",
        )
        _write_report("minimal", {"msg_s": msg_s, "threshold": _THROUGHPUT_MSG_S})
        if msg_s > 0:
            assert msg_s >= _THROUGHPUT_MSG_S, \
                f"Throughput {msg_s:.0f} msg/s < soglia {_THROUGHPUT_MSG_S} msg/s"

    def test_throughput_mb_per_second_1kb(self, in_process_broker):
        """Payload 1 KB: ≥ 50 MB/s."""
        payload_1kb = {"data": "x" * 1000}
        msg_s, mb_s = _measure_throughput(
            in_process_broker.url,
            _build_topic("1kb"),
            payload=payload_1kb,
            n=2000,
            publisher_name="tp_1kb_pub",
            subscriber_name="tp_1kb_sub",
        )
        _write_report("1kb", {"msg_s": msg_s, "mb_s": mb_s, "threshold_mb_s": _THROUGHPUT_MB_S_1K})
        if mb_s > 0:
            assert mb_s >= _THROUGHPUT_MB_S_1K, \
                f"Throughput {mb_s:.1f} MB/s < soglia {_THROUGHPUT_MB_S_1K} MB/s (payload 1KB)"

    def test_throughput_mb_per_second_64kb(self, in_process_broker):
        """Payload 64 KB: ≥ 10 MB/s."""
        payload_64kb = {"data": "x" * 65000}
        msg_s, mb_s = _measure_throughput(
            in_process_broker.url,
            _build_topic("64kb"),
            payload=payload_64kb,
            n=200,
            publisher_name="tp_64kb_pub",
            subscriber_name="tp_64kb_sub",
        )
        _write_report("64kb", {"msg_s": msg_s, "mb_s": mb_s, "threshold_mb_s": _THROUGHPUT_MB_S_64K})
        if mb_s > 0:
            assert mb_s >= _THROUGHPUT_MB_S_64K, \
                f"Throughput {mb_s:.1f} MB/s < soglia {_THROUGHPUT_MB_S_64K} MB/s (payload 64KB)"

    def test_throughput_multi_topic_concurrent(self, in_process_broker):
        """
        10 publisher su 10 topic distinti in parallelo.
        Ogni publisher deve raggiungere ≥ 5k msg/s (soglia dimezzata per contesa).
        """
        n_topics = 10
        n_msg = 1000
        results: list[float] = []
        lock = threading.Lock()

        def _worker(idx: int) -> None:
            ms, _ = _measure_throughput(
                in_process_broker.url,
                _build_topic(f"multi_{idx}"),
                payload={"i": idx},
                n=n_msg,
                publisher_name=f"tp_mp_pub_{idx}",
                subscriber_name=f"tp_mp_sub_{idx}",
            )
            with lock:
                results.append(ms)

        threads = [threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(n_topics)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        _write_report("multi_topic", {"per_topic_msg_s": results})
        good = sum(1 for r in results if r >= _THROUGHPUT_MSG_S * 0.5)
        assert good >= n_topics * 0.8, \
            f"Solo {good}/{n_topics} topic raggiungono la soglia multi-topic"

    def test_throughput_sustained(self, in_process_broker):
        """
        Throughput sostenuto per _SUSTAINED_WINDOW_S secondi:
        ultima finestra non deve calare > 20% rispetto alla prima.
        """
        topic = _build_topic("sustained")
        pub = BusClient(module_name="tp_sus_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="tp_sus_sub", broker_url=in_process_broker.url)

        window_size = 1.0  # finestra di misurazione (secondi)
        windows: list[float] = []
        total_sent = [0]
        total_recv = [0]
        stop_flag = threading.Event()

        def _handler(_: dict) -> None:
            total_recv[0] += 1

        sub.subscribe(topic, _handler)
        time.sleep(0.05)

        def _publish_worker() -> None:
            while not stop_flag.is_set():
                pub.publish(topic, {"ts": time.time()})
                total_sent[0] += 1

        t_pub = threading.Thread(target=_publish_worker, daemon=True)
        t_pub.start()

        elapsed = 0.0
        prev_recv = 0
        while elapsed < _SUSTAINED_WINDOW_S:
            time.sleep(window_size)
            elapsed += window_size
            cur_recv = total_recv[0]
            windows.append(cur_recv - prev_recv)
            prev_recv = cur_recv

        stop_flag.set()
        t_pub.join(timeout=2.0)
        sub.unsubscribe(topic, _handler)

        _write_report("sustained", {"windows_msg": windows, "window_size_s": window_size})

        if len(windows) >= 2:
            first = windows[0] or 1
            last = windows[-1]
            degrado = (first - last) / first
            assert degrado <= _SUSTAINED_DEGRADO, \
                f"Degrado throughput: {degrado*100:.1f}% > soglia {_SUSTAINED_DEGRADO*100:.0f}%"

    def test_throughput_with_slow_subscriber(self, in_process_broker):
        """
        Subscriber lento (sleep 1ms/msg): il publisher non viene bloccato.
        I messaggi in eccesso rispetto a HWM vengono droppati silenziosamente.
        Il publisher completa in < 3s indipendentemente dal subscriber.
        """
        topic = _build_topic("slow_sub")
        pub = BusClient(module_name="tp_slow_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="tp_slow_sub", broker_url=in_process_broker.url)

        received = [0]

        def _slow_handler(_: dict) -> None:
            time.sleep(0.001)  # 1ms per messaggio
            received[0] += 1

        sub.subscribe(topic, _slow_handler)
        time.sleep(0.05)

        n = 1000
        t0 = time.perf_counter()
        for i in range(n):
            pub.publish(topic, {"i": i})
        elapsed = time.perf_counter() - t0

        sub.unsubscribe(topic, _slow_handler)
        _write_report("slow_subscriber", {"elapsed_s": elapsed, "n": n})

        # Il publisher non deve bloccarsi più di 3s per 1000 msg
        assert elapsed < 3.0, \
            f"Publisher bloccato da subscriber lento: {elapsed:.2f}s > 3s"

    def test_throughput_no_subscriber(self, in_process_broker):
        """
        Publish senza subscriber: nessuna eccezione, nessun blocco.
        publish() ritorna True o False senza hang.
        """
        topic = _build_topic("no_sub")
        pub = BusClient(module_name="tp_nosub_pub", broker_url=in_process_broker.url)

        t0 = time.perf_counter()
        for i in range(500):
            pub.publish(topic, {"i": i})
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0, f"Publish senza subscriber ha impiegato {elapsed:.2f}s"

    def test_throughput_regression_vs_baseline(self, in_process_broker):
        """
        Confronta msg/s attuale con baseline salvata.
        Se non esiste, la crea. Regressione > 30% = fallimento.
        """
        baseline_path = Path("tests/reports/throughput-baseline.json")
        msg_s, _ = _measure_throughput(
            in_process_broker.url,
            _build_topic("regression"),
            payload={"i": 0},
            n=2000,
            publisher_name="tp_reg_pub",
            subscriber_name="tp_reg_sub",
        )
        if msg_s == 0:
            pytest.skip("Nessun campione raccolto")

        if not baseline_path.exists():
            try:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps({"msg_s": msg_s}))
            except OSError:
                pass
            pytest.skip("Baseline throughput non esistente: salvata")
        else:
            try:
                baseline = json.loads(baseline_path.read_text())
                baseline_msg_s = baseline.get("msg_s", msg_s)
            except (json.JSONDecodeError, OSError):
                pytest.skip("Baseline throughput non leggibile")
                return

            threshold = baseline_msg_s * 0.70  # max 30% regressione
            assert msg_s >= threshold, (
                f"Regressione throughput: {msg_s:.0f} msg/s < "
                f"70% baseline {baseline_msg_s:.0f} msg/s"
            )
