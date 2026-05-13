"""
Fase 4 §4 — test_memory_rss.py

Misura l'RSS del processo durante diversi scenari operativi:
  - RSS a riposo < 150 MB
  - Delta RSS dopo sessione simulata < 20 MB
  - Nessun leak evidente dopo 3 cicli connect/disconnect
  - Buffer audio rilasciato dopo stop
  - Payload 64KB garbage-collected
  - Regression vs baseline JSON

Dipendenza: psutil (già in requirements-test.txt).
Marker: @pytest.mark.performance — NON blocca CI.
"""
from __future__ import annotations

import gc
import json
import os
import threading
import time
from pathlib import Path

import pytest

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from shared.bus_client import BusClient

_MAX_RSS_IDLE_MB   = float(os.getenv("PERF_MAX_RSS_IDLE_MB",    "150.0"))
_MAX_DELTA_SESSION = float(os.getenv("PERF_MAX_DELTA_SESSION_MB", "20.0"))
_MAX_DELTA_RECONNECT = float(os.getenv("PERF_MAX_DELTA_RECONNECT_MB", "5.0"))
_MAX_DELTA_AUDIO_MB  = float(os.getenv("PERF_MAX_DELTA_AUDIO_MB",     "5.0"))


def _rss_mb() -> float:
    """Ritorna RSS corrente in MB. Richiede psutil."""
    proc = psutil.Process(os.getpid())
    return proc.memory_info().rss / (1024 * 1024)


def _write_report(scenario: str, data: dict) -> None:
    from datetime import datetime
    reports_dir = Path("tests/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    try:
        (reports_dir / f"perf-rss-{scenario}-{ts}.json").write_text(json.dumps(data, indent=2))
    except OSError:
        pass


@pytest.mark.performance
@pytest.mark.skipif(not HAS_PSUTIL, reason="psutil non installato")
class TestMemoryRSS:
    """Misura consumo di memoria RSS in diversi scenari."""

    def test_rss_baseline_idle(self, in_process_broker):
        """RSS a riposo (solo broker + BusClient inattivo) < 150 MB."""
        gc.collect()
        time.sleep(0.1)
        rss = _rss_mb()
        _write_report("baseline_idle", {"rss_mb": rss, "threshold_mb": _MAX_RSS_IDLE_MB})
        assert rss < _MAX_RSS_IDLE_MB, \
            f"RSS idle {rss:.1f} MB > soglia {_MAX_RSS_IDLE_MB} MB"

    def test_rss_after_simulated_session(self, in_process_broker):
        """
        Delta RSS dopo una sessione simulata (1000 publish + subscribe) < 20 MB.
        Verifica che gli oggetti creati durante la sessione vengano liberati.
        """
        gc.collect()
        rss_before = _rss_mb()

        pub = BusClient(module_name="rss_sess_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="rss_sess_sub", broker_url=in_process_broker.url)
        received = [0]
        done = threading.Event()

        def _handler(_: dict) -> None:
            received[0] += 1
            if received[0] >= 500:
                done.set()

        sub.subscribe("rss.session.test", _handler)
        time.sleep(0.03)

        for i in range(500):
            pub.publish("rss.session.test", {"i": i, "data": "x" * 200})

        done.wait(timeout=5.0)
        sub.unsubscribe("rss.session.test", _handler)

        gc.collect()
        time.sleep(0.1)
        rss_after = _rss_mb()
        delta = rss_after - rss_before

        _write_report("simulated_session", {
            "rss_before": rss_before,
            "rss_after": rss_after,
            "delta_mb": delta,
            "threshold_mb": _MAX_DELTA_SESSION,
        })
        assert delta < _MAX_DELTA_SESSION, \
            f"Delta RSS post-sessione {delta:.1f} MB > soglia {_MAX_DELTA_SESSION} MB"

    def test_rss_no_leak_on_reconnect(self, in_process_broker):
        """
        3 cicli di connect/publish/disconnect.
        Delta RSS complessivo < 5 MB: nessun leak evidente.
        """
        gc.collect()
        rss_before = _rss_mb()

        for cycle in range(3):
            pub = BusClient(module_name=f"rss_rc_pub_{cycle}", broker_url=in_process_broker.url)
            sub = BusClient(module_name=f"rss_rc_sub_{cycle}", broker_url=in_process_broker.url)
            done = threading.Event()
            recv = [0]

            def _h(_: dict, _recv=recv, _done=done) -> None:
                _recv[0] += 1
                if _recv[0] >= 50:
                    _done.set()

            sub.subscribe(f"rss.reconnect.{cycle}", _h)
            time.sleep(0.02)

            for i in range(50):
                pub.publish(f"rss.reconnect.{cycle}", {"i": i})

            done.wait(timeout=3.0)
            sub.unsubscribe(f"rss.reconnect.{cycle}", _h)
            del pub, sub

        gc.collect()
        time.sleep(0.1)
        rss_after = _rss_mb()
        delta = rss_after - rss_before

        _write_report("reconnect_3_cycles", {
            "rss_before": rss_before,
            "rss_after": rss_after,
            "delta_mb": delta,
            "threshold_mb": _MAX_DELTA_RECONNECT,
        })
        assert delta < _MAX_DELTA_RECONNECT, \
            f"Possibile leak: delta RSS {delta:.1f} MB > soglia {_MAX_DELTA_RECONNECT} MB"

    def test_rss_audio_buffer_released(self, in_process_broker):
        """
        Dopo stop audio, i buffer allocati devono essere rilasciati.
        Delta RSS < 5 MB dopo GC.
        """
        gc.collect()
        rss_before = _rss_mb()

        pub = BusClient(module_name="rss_aud_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="rss_aud_sub", broker_url=in_process_broker.url)

        buffers_recv = [0]
        done = threading.Event()

        def _handler(payload: dict) -> None:
            _ = payload.get("frame", b"")
            buffers_recv[0] += 1
            if buffers_recv[0] >= 100:
                done.set()

        sub.subscribe("aa.audio.frame", _handler)
        time.sleep(0.03)

        # Pubblica 100 frame audio da 4KB ciascuno
        frame_payload = "A" * 4096
        for i in range(100):
            pub.publish("aa.audio.frame", {"seq": i, "frame": frame_payload})

        done.wait(timeout=5.0)

        # Stop audio: de-registra handler e forza GC
        sub.unsubscribe("aa.audio.frame", _handler)
        del frame_payload, pub, sub
        gc.collect()
        time.sleep(0.2)

        rss_after = _rss_mb()
        delta = rss_after - rss_before

        _write_report("audio_buffer_released", {
            "rss_before": rss_before,
            "rss_after": rss_after,
            "delta_mb": delta,
            "threshold_mb": _MAX_DELTA_AUDIO_MB,
        })
        assert delta < _MAX_DELTA_AUDIO_MB, \
            f"Buffer audio non rilasciati: delta {delta:.1f} MB > soglia {_MAX_DELTA_AUDIO_MB} MB"

    def test_rss_large_payload_gc(self, in_process_broker):
        """
        Pubblica 50 payload da 64 KB: dopo GC, RSS non deve crescere > 10 MB.
        """
        gc.collect()
        rss_before = _rss_mb()

        pub = BusClient(module_name="rss_big_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="rss_big_sub", broker_url=in_process_broker.url)

        recv = [0]
        done = threading.Event()

        def _h(_: dict) -> None:
            recv[0] += 1
            if recv[0] >= 50:
                done.set()

        sub.subscribe("rss.big.payload", _h)
        time.sleep(0.03)

        for i in range(50):
            pub.publish("rss.big.payload", {"i": i, "data": "B" * 65000})

        done.wait(timeout=10.0)
        sub.unsubscribe("rss.big.payload", _h)
        del pub, sub

        gc.collect()
        time.sleep(0.2)
        rss_after = _rss_mb()
        delta = rss_after - rss_before

        _write_report("large_payload_gc", {
            "rss_before": rss_before,
            "rss_after": rss_after,
            "delta_mb": delta,
        })
        assert delta < 10.0, f"Payload 64KB non GC: delta {delta:.1f} MB > 10 MB"

    def test_rss_regression_vs_baseline(self, in_process_broker):
        """
        Confronta RSS idle attuale con baseline salvata.
        Crescita > 20 MB rispetto alla baseline = fallimento.
        """
        baseline_path = Path("tests/reports/rss-baseline.json")

        gc.collect()
        time.sleep(0.1)
        rss = _rss_mb()

        if not baseline_path.exists():
            try:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps({"rss_idle_mb": rss}))
            except OSError:
                pass
            pytest.skip("Baseline RSS non esistente: salvata")
        else:
            try:
                baseline = json.loads(baseline_path.read_text())
                base_rss = baseline.get("rss_idle_mb", rss)
            except (json.JSONDecodeError, OSError):
                pytest.skip("Baseline RSS non leggibile")
                return

            delta = rss - base_rss
            _write_report("regression", {
                "rss_current_mb": rss,
                "rss_baseline_mb": base_rss,
                "delta_mb": delta,
            })
            assert delta < 20.0, (
                f"Regressione RSS: {rss:.1f} MB vs baseline {base_rss:.1f} MB (+{delta:.1f} MB)"
            )
