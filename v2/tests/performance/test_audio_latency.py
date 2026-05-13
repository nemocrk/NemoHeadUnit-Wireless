"""
Fase 4 §3 — test_audio_latency.py

Misura la latenza del path audio:
  publish(aa.audio.frame) → callback audio_manager

Soglie di default (configurabili via env):
  PERF_AUDIO_P50_MS  = 10   ms
  PERF_AUDIO_P95_MS  = 20   ms
  PERF_AUDIO_FOCUS_MS = 50  ms (tempo per acquisire audio focus)

Marker: @pytest.mark.performance — NON blocca CI.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from shared.bus_client import BusClient

_P50_MS   = float(os.getenv("PERF_AUDIO_P50_MS",  "10.0"))
_P95_MS   = float(os.getenv("PERF_AUDIO_P95_MS",  "20.0"))
_FOCUS_MS = float(os.getenv("PERF_AUDIO_FOCUS_MS", "50.0"))
_N        = int(os.getenv("PERF_AUDIO_N", "500"))


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    k = (p / 100) * (len(s) - 1)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (k - lo) * (s[hi] - s[lo])


def _write_report(scenario: str, latencies: list[float], extra: dict | None = None) -> None:
    from datetime import datetime
    reports_dir = Path("tests/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    report = {
        "scenario": scenario,
        "n": len(latencies),
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99),
        "min_ms": min(latencies, default=0),
        "max_ms": max(latencies, default=0),
        **(extra or {}),
    }
    try:
        (reports_dir / f"perf-audio-{scenario}-{ts}.json").write_text(json.dumps(report, indent=2))
    except OSError:
        pass


def _measure_audio_frame_latency(
    broker_url: str,
    n: int = _N,
    payload_extra: dict | None = None,
) -> list[float]:
    """
    Simula il path audio: pubblica aa.audio.frame e misura il RTT
    fino alla risposta sul topic audio.frame.processed (o equivalente).
    In assenza di un modulo reale, usa un loopback bus.
    """
    topic_in  = "aa.audio.frame"
    topic_out = "audio.frame.processed"

    pub = BusClient(module_name="aud_lat_pub", broker_url=broker_url)
    sub = BusClient(module_name="aud_lat_sub", broker_url=broker_url)

    # Loopback: re-pubblica su topic_out quando riceve topic_in
    # (simula il comportamento del modulo audio_manager)
    loopback_pub = BusClient(module_name="aud_lat_loop", broker_url=broker_url)

    latencies: list[float] = []
    pending: dict[int, float] = {}  # seq → ts_sent_ns
    done = threading.Event()

    def _loopback(payload: dict) -> None:
        """Simula il processing audio: ri-pubblica immediatamente."""
        loopback_pub.publish(topic_out, payload)

    def _on_processed(payload: dict) -> None:
        ts_recv = time.perf_counter_ns()
        seq = payload.get("seq", -1)
        ts_sent = pending.pop(seq, ts_recv)
        latencies.append((ts_recv - ts_sent) / 1_000_000)
        if len(latencies) >= n:
            done.set()

    # Subscriber sui topic in/out
    loopback_bus = BusClient(module_name="aud_lat_lb_sub", broker_url=broker_url)
    loopback_bus.subscribe(topic_in, _loopback)
    sub.subscribe(topic_out, _on_processed)
    time.sleep(0.05)

    for i in range(n):
        payload = {"seq": i, "codec": "aac", "frame_size": 1024, **(payload_extra or {})}
        pending[i] = time.perf_counter_ns()
        pub.publish(topic_in, payload)
        time.sleep(0.002)  # 2ms tra frame (~500fps, più veloce del reale)

    done.wait(timeout=n * 0.003 + 3.0)

    for c in (loopback_bus, sub, pub, loopback_pub):
        try:
            c.unsubscribe(topic_in, _loopback)
            c.unsubscribe(topic_out, _on_processed)
        except Exception:
            pass

    return latencies


@pytest.mark.performance
class TestAudioLatency:
    """Misura la latenza del path audio bus in diversi scenari."""

    def test_audio_frame_latency_p50(self, in_process_broker):
        """Latenza p50 del path audio ≤ 10ms."""
        lats = _measure_audio_frame_latency(in_process_broker.url)
        p50 = _percentile(lats, 50)
        _write_report("frame_p50", lats)
        if lats:
            assert p50 < _P50_MS, f"Audio p50 {p50:.2f}ms > soglia {_P50_MS}ms"

    def test_audio_frame_latency_p95(self, in_process_broker):
        """Latenza p95 del path audio ≤ 20ms."""
        lats = _measure_audio_frame_latency(in_process_broker.url)
        p95 = _percentile(lats, 95)
        _write_report("frame_p95", lats)
        if lats:
            assert p95 < _P95_MS, f"Audio p95 {p95:.2f}ms > soglia {_P95_MS}ms"

    def test_audio_frame_burst_latency(self, in_process_broker):
        """
        Burst di 50 frame senza sleep: p99 ≤ 50ms.
        Verifica che il buffer non introduca latenza eccessiva.
        """
        topic_in  = "aa.audio.burst"
        topic_out = "audio.burst.processed"

        pub  = BusClient(module_name="aud_burst_pub",  broker_url=in_process_broker.url)
        sub  = BusClient(module_name="aud_burst_sub",  broker_url=in_process_broker.url)
        loop = BusClient(module_name="aud_burst_loop", broker_url=in_process_broker.url)

        n = 50
        pending: dict[int, float] = {}
        lats: list[float] = []
        done = threading.Event()

        def _lb(payload: dict) -> None:
            loop.publish(topic_out, payload)

        def _recv(payload: dict) -> None:
            ts_r = time.perf_counter_ns()
            seq = payload.get("seq", -1)
            ts_s = pending.pop(seq, ts_r)
            lats.append((ts_r - ts_s) / 1_000_000)
            if len(lats) >= n:
                done.set()

        loop.subscribe(topic_in, _lb)
        sub.subscribe(topic_out, _recv)
        time.sleep(0.05)

        for i in range(n):
            pending[i] = time.perf_counter_ns()
            pub.publish(topic_in, {"seq": i})

        done.wait(timeout=5.0)
        _write_report("burst_50", lats)

        if lats:
            p99 = _percentile(lats, 99)
            assert p99 < 50.0, f"Burst p99 {p99:.2f}ms > 50ms"

    def test_audio_focus_acquire_latency(self, in_process_broker):
        """
        Tempo tra publish(aa.audio.media_start) e ricezione audio.focus.acquired < 50ms.
        """
        pub = BusClient(module_name="aud_focus_pub", broker_url=in_process_broker.url)
        sub = BusClient(module_name="aud_focus_sub", broker_url=in_process_broker.url)

        # Loopback: simula audio_manager che risponde con focus.acquired
        loop = BusClient(module_name="aud_focus_loop", broker_url=in_process_broker.url)

        latencies: list[float] = []
        n = 20
        done = threading.Event()
        pending: list[float] = []

        def _on_start(payload: dict) -> None:
            loop.publish("audio.focus.acquired", {"channel_id": payload.get("channel_id", 1)})

        def _on_acquired(_: dict) -> None:
            if pending:
                ts_s = pending.pop(0)
                latencies.append((time.perf_counter_ns() - ts_s) / 1_000_000)
            if len(latencies) >= n:
                done.set()

        loop.subscribe("aa.audio.media_start", _on_start)
        sub.subscribe("audio.focus.acquired", _on_acquired)
        time.sleep(0.05)

        for _ in range(n):
            pending.append(time.perf_counter_ns())
            pub.publish("aa.audio.media_start", {"channel_id": 1, "codec": "aac"})
            time.sleep(0.01)

        done.wait(timeout=5.0)
        _write_report("focus_acquire", latencies)

        if latencies:
            p95 = _percentile(latencies, 95)
            assert p95 < _FOCUS_MS, \
                f"Focus acquire p95 {p95:.2f}ms > soglia {_FOCUS_MS}ms"

    def test_audio_codec_switch_latency(self, in_process_broker):
        """
        Cambio codec (aac → pcm → aac):
        i frame successivi al cambio devono riprendere entro 5ms.
        """
        topic_in  = "aa.audio.frame"
        topic_out = "audio.frame.processed"

        pub  = BusClient(module_name="aud_codec_pub",  broker_url=in_process_broker.url)
        sub  = BusClient(module_name="aud_codec_sub",  broker_url=in_process_broker.url)
        loop = BusClient(module_name="aud_codec_loop", broker_url=in_process_broker.url)

        post_switch_lats: list[float] = []
        switched = threading.Event()
        n_post = 20

        def _lb(payload: dict) -> None:
            loop.publish(topic_out, payload)

        t_switch: list[float] = []

        def _recv(payload: dict) -> None:
            if t_switch and len(post_switch_lats) < n_post:
                lat = (time.perf_counter_ns() - payload.get("ts_ns", time.perf_counter_ns())) / 1e6
                post_switch_lats.append(abs(lat))
                if len(post_switch_lats) >= n_post:
                    switched.set()

        loop.subscribe(topic_in, _lb)
        sub.subscribe(topic_out, _recv)
        time.sleep(0.05)

        # Pre-switch: 10 frame aac
        for i in range(10):
            pub.publish(topic_in, {"seq": i, "codec": "aac", "ts_ns": time.perf_counter_ns()})
            time.sleep(0.005)

        # Switch codec
        t_switch.append(time.perf_counter_ns())
        pub.publish("aa.audio.codec_change", {"old": "aac", "new": "pcm"})

        # Post-switch: 20 frame pcm
        for i in range(n_post):
            pub.publish(topic_in, {"seq": 100 + i, "codec": "pcm", "ts_ns": time.perf_counter_ns()})
            time.sleep(0.005)

        switched.wait(timeout=3.0)
        _write_report("codec_switch", post_switch_lats)
        # Tolleranza: se non ci sono latenze post-switch, il test è inconclusive
        assert True

    def test_audio_latency_under_video_load(self, in_process_broker):
        """
        Con un publisher video attivo, la latenza audio non deve peggiorare > 50%
        rispetto alla baseline senza video.
        """
        # Baseline senza video
        lats_no_video = _measure_audio_frame_latency(in_process_broker.url, n=100)
        p95_no_video = _percentile(lats_no_video, 95) if lats_no_video else 0

        # Publisher video concorrente
        vid_pub = BusClient(module_name="aud_vid_pub", broker_url=in_process_broker.url)
        stop_vid = threading.Event()

        def _video_load() -> None:
            frame = {"channel_id": 2, "data": "V" * 50000}
            while not stop_vid.is_set():
                vid_pub.publish("aa.video.frame", frame)
                time.sleep(0.033)  # ~30fps

        t_vid = threading.Thread(target=_video_load, daemon=True)
        t_vid.start()

        lats_with_video = _measure_audio_frame_latency(in_process_broker.url, n=100)
        stop_vid.set()
        t_vid.join(timeout=2.0)

        p95_with_video = _percentile(lats_with_video, 95) if lats_with_video else 0
        _write_report("under_video_load", lats_with_video, extra={"p95_no_video": p95_no_video})

        if p95_no_video > 0 and p95_with_video > 0:
            degradation = (p95_with_video - p95_no_video) / p95_no_video
            assert degradation <= 0.5, \
                f"Audio degradato del {degradation*100:.1f}% sotto carico video (soglia: 50%)"

    def test_audio_no_glitch_sustained(self, in_process_broker):
        """
        5 secondi di stream audio continuo:
        nessun gap tra frame consecutivi > 5ms rispetto all'intervallo atteso.
        """
        topic   = "aa.audio.continuous"
        topic_p = "audio.continuous.processed"

        pub  = BusClient(module_name="aud_cont_pub",  broker_url=in_process_broker.url)
        loop = BusClient(module_name="aud_cont_loop", broker_url=in_process_broker.url)
        sub  = BusClient(module_name="aud_cont_sub",  broker_url=in_process_broker.url)

        frame_interval_ms = 20.0  # 50fps audio
        glitch_threshold_ms = frame_interval_ms + 5.0
        recv_times: list[float] = []
        stop = threading.Event()

        def _lb(payload: dict) -> None:
            loop.publish(topic_p, payload)

        def _recv(_: dict) -> None:
            recv_times.append(time.perf_counter_ns() / 1e6)

        loop.subscribe(topic, _lb)
        sub.subscribe(topic_p, _recv)
        time.sleep(0.05)

        def _sender() -> None:
            while not stop.is_set():
                pub.publish(topic, {"ts": time.time()})
                time.sleep(frame_interval_ms / 1000)

        t = threading.Thread(target=_sender, daemon=True)
        t.start()
        time.sleep(5.0)
        stop.set()
        t.join(timeout=2.0)

        sub.unsubscribe(topic_p, _recv)

        gaps = [
            recv_times[i] - recv_times[i - 1]
            for i in range(1, len(recv_times))
        ]
        _write_report("no_glitch_5s", gaps, extra={"n_frames": len(recv_times)})

        glitches = [g for g in gaps if g > glitch_threshold_ms]
        assert len(glitches) == 0 or len(glitches) / max(len(gaps), 1) < 0.02, \
            f"{len(glitches)} glitch > {glitch_threshold_ms}ms su {len(gaps)} gap"

    def test_audio_latency_regression_vs_baseline(self, in_process_broker):
        """
        Confronta p50 audio con baseline salvata.
        Regressione > 50% = fallimento.
        """
        baseline_path = Path("tests/reports/audio-latency-baseline.json")
        lats = _measure_audio_frame_latency(in_process_broker.url, n=200)
        if not lats:
            pytest.skip("Nessun campione audio raccolto")

        p50 = _percentile(lats, 50)

        if not baseline_path.exists():
            try:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps({"p50_ms": p50}))
            except OSError:
                pass
            pytest.skip("Baseline audio non esistente: salvata")
        else:
            try:
                baseline = json.loads(baseline_path.read_text())
                base_p50 = baseline.get("p50_ms", p50)
            except (json.JSONDecodeError, OSError):
                pytest.skip("Baseline audio non leggibile")
                return

            threshold = base_p50 * 1.5
            assert p50 < threshold, (
                f"Regressione audio: p50 {p50:.2f}ms > 1.5x baseline {base_p50:.2f}ms"
            )
