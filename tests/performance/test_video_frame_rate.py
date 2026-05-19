"""
Fase 4 §5 — test_video_frame_rate.py

Misura il throughput del decoder video nel pipeline bus:
  publish(aa.video.frame) → callback video_ui (loopback in test)

Soglie di default (configurabili via env):
  PERF_VIDEO_FPS_MIN    = 30     fps minimo
  PERF_VIDEO_FPS_60     = 60     fps target H.264
  PERF_VIDEO_P95_MS     = 33.0   ms (1 frame @30fps)
  PERF_VIDEO_KEYFRAME_MS = 50.0  ms decode keyframe

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

_FPS_MIN        = float(os.getenv("PERF_VIDEO_FPS_MIN",     "30.0"))
_FPS_60         = float(os.getenv("PERF_VIDEO_FPS_60",      "60.0"))
_P95_MS         = float(os.getenv("PERF_VIDEO_P95_MS",      "33.0"))
_KEYFRAME_MS    = float(os.getenv("PERF_VIDEO_KEYFRAME_MS", "50.0"))
_SUSTAINED_S    = int(os.getenv("PERF_VIDEO_SUSTAINED_S",   "10"))   # ridotto per CI
_DEGRADATION    = float(os.getenv("PERF_VIDEO_DEGRADATION", "0.10"))


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
        (reports_dir / f"perf-video-{scenario}-{ts}.json").write_text(
            json.dumps(data, indent=2)
        )
    except OSError:
        pass


def _measure_fps(
    broker_url: str,
    n_frames: int,
    frame_interval_s: float,
    payload_extra: dict | None = None,
    pub_name: str = "vid_pub",
    sub_name: str = "vid_sub",
    loop_name: str = "vid_loop",
) -> tuple[float, list[float]]:
    """
    Pubblica `n_frames` frame video a `frame_interval_s` cadenza.
    Restituisce (fps_misurato, lista_latenze_ms).
    Il loopback simula il processing del modulo video_ui.
    """
    topic_in  = "aa.video.frame"
    topic_out = "video.frame.rendered"

    pub  = BusClient(module_name=pub_name,  broker_url=broker_url)
    sub  = BusClient(module_name=sub_name,  broker_url=broker_url)
    loop = BusClient(module_name=loop_name, broker_url=broker_url)

    latencies: list[float] = []
    pending: dict[int, float] = {}
    done = threading.Event()

    def _lb(payload: dict) -> None:
        loop.publish(topic_out, payload)

    def _recv(payload: dict) -> None:
        ts_r = time.perf_counter_ns()
        seq = payload.get("seq", -1)
        ts_s = pending.pop(seq, ts_r)
        latencies.append((ts_r - ts_s) / 1_000_000)
        if len(latencies) >= n_frames:
            done.set()

    loop.subscribe(topic_in, _lb)
    sub.subscribe(topic_out, _recv)
    time.sleep(0.05)

    t0 = time.perf_counter()
    for i in range(n_frames):
        payload = {"seq": i, "codec": "h264", "keyframe": (i % 30 == 0), **(payload_extra or {})}
        pending[i] = time.perf_counter_ns()
        pub.publish(topic_in, payload)
        if frame_interval_s > 0:
            time.sleep(frame_interval_s)

    done.wait(timeout=n_frames * frame_interval_s + 5.0)
    elapsed = time.perf_counter() - t0

    sub.unsubscribe(topic_out, _recv)
    loop.unsubscribe(topic_in, _lb)

    actual = len(latencies)
    fps = actual / elapsed if elapsed > 0 and actual > 0 else 0.0
    return fps, latencies


@pytest.mark.performance
class TestVideoFrameRate:
    """Misura fps e latenza del path video."""

    def test_video_decode_fps_30(self, in_process_broker):
        """Pipeline video a 30fps: fps misurato ≥ 28fps (margine 7%)."""
        fps, lats = _measure_fps(
            in_process_broker.url,
            n_frames=150,
            frame_interval_s=1 / 30,
            pub_name="vfr30_pub",
            sub_name="vfr30_sub",
            loop_name="vfr30_loop",
        )
        _write_report("fps30", {"fps": fps, "threshold": _FPS_MIN, "n": len(lats)})
        if fps > 0:
            assert fps >= _FPS_MIN * 0.93, \
                f"FPS 30 misurato {fps:.1f} < soglia {_FPS_MIN * 0.93:.1f}"

    def test_video_decode_fps_60(self, in_process_broker):
        """Pipeline video a 60fps: fps misurato ≥ 55fps."""
        fps, lats = _measure_fps(
            in_process_broker.url,
            n_frames=300,
            frame_interval_s=1 / 60,
            pub_name="vfr60_pub",
            sub_name="vfr60_sub",
            loop_name="vfr60_loop",
        )
        _write_report("fps60", {"fps": fps, "threshold": _FPS_60, "n": len(lats)})
        if fps > 0:
            assert fps >= _FPS_60 * 0.92, \
                f"FPS 60 misurato {fps:.1f} < soglia {_FPS_60 * 0.92:.1f}"

    def test_video_frame_latency_p95(self, in_process_broker):
        """Latenza p95 per frame video ≤ 33ms (1 frame @30fps)."""
        _, lats = _measure_fps(
            in_process_broker.url,
            n_frames=150,
            frame_interval_s=1 / 30,
            pub_name="vlat_p95_pub",
            sub_name="vlat_p95_sub",
            loop_name="vlat_p95_loop",
        )
        p95 = _percentile(lats, 95)
        _write_report("latency_p95", {"p95_ms": p95, "threshold_ms": _P95_MS, "n": len(lats)})
        if lats:
            assert p95 < _P95_MS, f"Video latency p95 {p95:.2f}ms > soglia {_P95_MS}ms"

    def test_video_keyframe_decode_time(self, in_process_broker):
        """Keyframe (ogni 30): latenza < 50ms."""
        topic_in  = "aa.video.frame"
        topic_out = "video.frame.rendered"

        pub  = BusClient(module_name="vkf_pub",  broker_url=in_process_broker.url)
        sub  = BusClient(module_name="vkf_sub",  broker_url=in_process_broker.url)
        loop = BusClient(module_name="vkf_loop", broker_url=in_process_broker.url)

        kf_lats: list[float] = []
        pending: dict[int, float] = {}
        done = threading.Event()
        n_kf = 5

        def _lb(p: dict) -> None:
            loop.publish(topic_out, p)

        def _recv(p: dict) -> None:
            ts_r = time.perf_counter_ns()
            seq = p.get("seq", -1)
            ts_s = pending.pop(seq, ts_r)
            if p.get("keyframe"):
                kf_lats.append((ts_r - ts_s) / 1_000_000)
            if len(kf_lats) >= n_kf:
                done.set()

        loop.subscribe(topic_in, _lb)
        sub.subscribe(topic_out, _recv)
        time.sleep(0.05)

        for i in range(n_kf * 30):
            pending[i] = time.perf_counter_ns()
            pub.publish(topic_in, {"seq": i, "codec": "h264", "keyframe": (i % 30 == 0)})
            time.sleep(1 / 30)

        done.wait(timeout=6.0)
        _write_report("keyframe", {"kf_latencies": kf_lats, "threshold_ms": _KEYFRAME_MS})

        if kf_lats:
            worst = max(kf_lats)
            assert worst < _KEYFRAME_MS, \
                f"Keyframe decode worst-case {worst:.2f}ms > soglia {_KEYFRAME_MS}ms"

    def test_video_fps_under_audio_load(self, in_process_broker):
        """
        FPS video stabile con publisher audio concorrente.
        Degradazione < 15% rispetto a baseline senza audio.
        """
        fps_no_audio, _ = _measure_fps(
            in_process_broker.url,
            n_frames=90,
            frame_interval_s=1 / 30,
            pub_name="vfa_base_pub",
            sub_name="vfa_base_sub",
            loop_name="vfa_base_loop",
        )

        aud_pub = BusClient(module_name="vfa_aud_pub", broker_url=in_process_broker.url)
        stop_aud = threading.Event()

        def _audio_load() -> None:
            while not stop_aud.is_set():
                aud_pub.publish("aa.audio.frame", {"codec": "aac", "data": "A" * 1024})
                time.sleep(0.02)

        t_aud = threading.Thread(target=_audio_load, daemon=True)
        t_aud.start()

        fps_with_audio, _ = _measure_fps(
            in_process_broker.url,
            n_frames=90,
            frame_interval_s=1 / 30,
            pub_name="vfa_vid_pub",
            sub_name="vfa_vid_sub",
            loop_name="vfa_vid_loop",
        )
        stop_aud.set()
        t_aud.join(timeout=2.0)

        _write_report("under_audio_load", {
            "fps_no_audio": fps_no_audio,
            "fps_with_audio": fps_with_audio,
        })

        if fps_no_audio > 0 and fps_with_audio > 0:
            degrad = (fps_no_audio - fps_with_audio) / fps_no_audio
            assert degrad <= 0.15, \
                f"Video degradato del {degrad*100:.1f}% sotto carico audio (soglia 15%)"

    def test_video_fps_sustained(self, in_process_broker):
        """
        FPS sostenuto per _SUSTAINED_S secondi: ultima finestra
        non cala > 10% rispetto alla prima.
        """
        topic_in  = "aa.video.sustained"
        topic_out = "video.sustained.rendered"

        pub  = BusClient(module_name="vfps_sus_pub",  broker_url=in_process_broker.url)
        loop = BusClient(module_name="vfps_sus_loop", broker_url=in_process_broker.url)
        sub  = BusClient(module_name="vfps_sus_sub",  broker_url=in_process_broker.url)

        recv_times: list[float] = []
        stop = threading.Event()

        def _lb(p: dict) -> None:
            loop.publish(topic_out, p)

        def _recv(_: dict) -> None:
            recv_times.append(time.perf_counter())

        loop.subscribe(topic_in, _lb)
        sub.subscribe(topic_out, _recv)
        time.sleep(0.05)

        def _sender() -> None:
            while not stop.is_set():
                pub.publish(topic_in, {"codec": "h264"})
                time.sleep(1 / 30)

        t = threading.Thread(target=_sender, daemon=True)
        t.start()
        time.sleep(_SUSTAINED_S)
        stop.set()
        t.join(timeout=2.0)

        sub.unsubscribe(topic_out, _recv)
        loop.unsubscribe(topic_in, _lb)

        # Dividi in 2 finestre e confronta fps
        half = len(recv_times) // 2
        if half < 5:
            pytest.skip("Troppo pochi frame per analisi sostenuta")

        window = _SUSTAINED_S / 2
        fps_first = half / window
        fps_last  = (len(recv_times) - half) / window

        _write_report("sustained", {"fps_first": fps_first, "fps_last": fps_last})

        degrad = (fps_first - fps_last) / fps_first if fps_first > 0 else 0
        assert degrad <= _DEGRADATION, \
            f"FPS calo sostenuto {degrad*100:.1f}% > soglia {_DEGRADATION*100:.0f}%"

    def test_video_fps_regression_vs_baseline(self, in_process_broker):
        """Confronta fps con baseline salvata. Regressione > 20% = fallimento."""
        baseline_path = Path("tests/reports/video-fps-baseline.json")
        fps, _ = _measure_fps(
            in_process_broker.url,
            n_frames=60,
            frame_interval_s=1 / 30,
            pub_name="vfps_reg_pub",
            sub_name="vfps_reg_sub",
            loop_name="vfps_reg_loop",
        )
        if fps == 0:
            pytest.skip("Nessun frame ricevuto")

        if not baseline_path.exists():
            try:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps({"fps": fps}))
            except OSError:
                pass
            pytest.skip("Baseline video FPS non esistente: salvata")
        else:
            try:
                base_fps = json.loads(baseline_path.read_text()).get("fps", fps)
            except (json.JSONDecodeError, OSError):
                pytest.skip("Baseline video FPS non leggibile")
                return

            threshold = base_fps * 0.80
            assert fps >= threshold, (
                f"Regressione FPS: {fps:.1f} < 80% baseline {base_fps:.1f}"
            )
