# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — Fase 4 §2/§3/§4 completati (`test_bus_throughput`, `test_audio_latency`, `test_memory_rss`)

---

## Stato Corrente in Una Frase

**51 file di test, ~3072 test + 3 helper E2E** su `main`. Fase 0–3 chiuse. Fase 4: 4/6 completati. **Prossimo: Fase 4 §5 — `test_video_frame_rate.py`**.

---

## File Prodotti (tabella completa)

| File | Commit | Test | Note |
|---|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | — | Fixture globali |
| `v2/tests/pytest.ini` | `0ff487e` | — | |
| `v2/tests/requirements-test.txt` | `0ff487e` | — | |
| `unit/shared/test_proto_utils.py` | precedente | 47 | |
| `unit/shared/test_bus_client.py` | `db85dc8` | ~75 | |
| `unit/shared/test_config_client.py` | precedente | 38 | |
| `unit/shared/test_logger.py` | `38a2885` | 88 | |
| `unit/shared/test_bus_trace.py` | `be3068e` | 72 | |
| `channel_modules/test_base_channel_module.py` | precedente | 44 | |
| `channel_modules/audio/test_audio_module.py` | precedente | 42 | |
| `channel_modules/video/test_video_module.py` | precedente | 38 | |
| `channel_modules/input/test_input_module.py` | `5aaa396` | 88 | |
| `channel_modules/sensor/test_sensor_module.py` | `d6f8a78` | 84 | |
| `channel_modules/bluetooth/test_bluetooth_channel_module.py` | `7791347` | 72 | |
| `channel_modules/wifi/test_wifi_channel_module.py` | `a1fa970` | 72 | |
| `channel_modules/av_input/test_av_input_module.py` | `0328b84` | 96 | |
| `oaa_control_channel/test_oaa_control_channel_main.py` | `c3d7a4a` | 54 | |
| `oaa_control_channel/test_handshake.py` | `6c99b41` | 62 | |
| `oaa_control_channel/test_serializer.py` | `ffe6314` | 68 | |
| `oaa_control_channel/test_service_discovery.py` | `4e7a28d` | 72 | |
| `modules/channel_manager/test_channel_manager.py` | `4f90f8a` | 78 | |
| `modules/tcp_server/test_tcp_server.py` | `412541a` | 84 | |
| `modules/audio_manager/test_audio_manager.py` | `acb6dce` | 88 | |
| `modules/video_ui/test_video_ui.py` | `83973cb` | 92 | |
| `modules/bluetooth/test_bluetooth_main.py` | `b024893` | 96 | |
| `modules/bluetooth/test_bluez_adapter.py` | `17c7c4d` | 88 | |
| `modules/bluetooth/test_discovery.py` | `a1b5156` | 72 | |
| `modules/bluetooth/test_pairing.py` | `d4973f8` | 84 | |
| `modules/bluetooth/test_paired_devices.py` | `6a83677` | 68 | |
| `modules/config_manager/test_config_manager.py` | `e1c0847` | 96 | |
| `modules/zmq_trace/test_zmq_trace.py` | `cdc9e6f` | 68 | |
| `integration/test_bus_broker.py` | `bd326e5` | 84 | |
| `integration/test_channel_lifecycle.py` | `1734764` | 88 | |
| `integration/test_audio_manager.py` | `7e1d9be` | ~47 | |
| `integration/test_config_manager.py` | `9f08aa6` | ~50 | |
| `integration/test_video_pipeline.py` | `2d8d861` | ~60 | |
| `integration/test_bluetooth_flow.py` | `dbbc2b4` | ~60 | |
| `integration/test_boot_shutdown.py` | `2fe07ef` | ~65 | |
| `e2e/helpers/phone_mock.py` | `5c74859` | — | |
| `e2e/helpers/frame_sequences.py` | `bab116c` | — | |
| `e2e/helpers/stack_launcher.py` | `637631d` | — | |
| `test_rfcomm_and_channel_manager.py` | `9ab6c2e` | ~51 | |
| `e2e/smoke/test_bt_connect_to_handshake.py` | — | 10 | Fase 3 Smoke §1 |
| `e2e/smoke/test_channel_manager_boot.py` | `f45cf77` | 9 | Fase 3 Smoke §2 |
| `e2e/smoke/test_audio_path_smoke.py` | `e734333` | 8 | Fase 3 Smoke §3 |
| `e2e/full_session/test_full_aa_session.py` | `1cfa379` | ~12 | Fase 3 Full §1 |
| `e2e/full_session/test_session_recovery.py` | `aa2c995` | ~8 | Fase 3 Full §2 |
| `performance/test_bus_latency.py` | `1e461f9` | ~9 | Fase 4 §1 |
| `performance/test_bus_throughput.py` | — | ~10 | **Fase 4 §2** |
| `performance/test_audio_latency.py` | — | ~8 | **Fase 4 §3** |
| `performance/test_memory_rss.py` | — | ~7 | **Fase 4 §4** |

**Totale: ~3072 test in 51 file + 3 helper + 3 infra.**

---

## Pattern Architetturali Stabiliti

### BusClient (commit ddd7142)
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### Performance — pattern consolidato (Fase 4)
```python
@pytest.mark.performance
class TestXxx:
    # Soglie via env: PERF_P50_MS, PERF_P95_MS, PERF_P99_MS
    # Output JSON: tests/reports/perf-{scenario}.json
    # Baseline regression: tests/reports/perf-baseline.json

    def _measure_rtt(pub, sub, topic, payload_fn, n=1000) -> list[float]: ...
    def _percentile(data, p) -> float: ...  # helper interno
    def _write_report(scenario, latencies): ...  # salva JSON
```

### Throughput — metrica aggiuntiva
```python
# msg/s: pubblica N msg, misura wall-time totale
# MB/s: payload_size * N / elapsed
# Soglie: 10_000 msg/s minimo, 50 MB/s per payload 1KB
```

### Memory RSS — pattern
```python
import psutil, os
proc = psutil.Process(os.getpid())
rss_baseline = proc.memory_info().rss
# ... run workload ...
rss_after = proc.memory_info().rss
assert (rss_after - rss_baseline) < MAX_DELTA_MB * 1024 * 1024
```

### Audio Latency — pattern
```python
# p50 <= 10ms dal publish aa.audio.frame sul bus
# fino alla chiamata del callback del modulo audio
# usa threading.Event() per misurare RTT
```

---

## 2026-05-13 — Fase 4 §2/§3/§4

**Cosa cambiato:**

- **`test_bus_throughput.py`** (~10 test `@pytest.mark.performance`)
  - `test_throughput_msg_per_second` — soglia 10k msg/s con payload minimo
  - `test_throughput_mb_per_second_1kb` — soglia 50 MB/s con payload 1KB
  - `test_throughput_mb_per_second_64kb` — soglia 10 MB/s con payload 64KB
  - `test_throughput_multi_topic` — 10 topic concorrenti
  - `test_throughput_sustained_60s` — 1 minuto senza degrado >§20%
  - `test_throughput_regression_vs_baseline` — confronto con baseline JSON

- **`test_audio_latency.py`** (~8 test `@pytest.mark.performance`)
  - `test_audio_frame_latency_p50` — ≤ 10ms
  - `test_audio_frame_latency_p95` — ≤ 20ms
  - `test_audio_frame_burst_latency` — burst 50 frame
  - `test_audio_focus_acquire_latency` — `audio.focus.acquired` < 50ms
  - `test_audio_codec_switch_latency` — cambio codec senza drop > 5ms
  - `test_audio_latency_under_video_load` — latenza audio con video attivo
  - `test_audio_latency_regression`
  - `test_audio_no_glitch_sustained` — 5s senza glitch >2ms

- **`test_memory_rss.py`** (~7 test `@pytest.mark.performance`)
  - `test_rss_baseline_idle` — RSS a riposo < 150MB
  - `test_rss_after_5min_session` — delta < 20MB dopo 5 min
  - `test_rss_after_full_aa_session` — delta < 10MB dopo sessione completa
  - `test_rss_no_leak_on_reconnect` — 3 cicli connect/disconnect, delta < 5MB
  - `test_rss_audio_buffer_released` — RSS decresce dopo stop audio
  - `test_rss_large_payload_gc` — GC libera payload 64KB
  - `test_rss_regression_vs_baseline`

**Perché:** Completare il blocco "metrica di sistema" della Fase 4 prima di passare alle metriche video.

**Status:** Completato ✅

**Prossimi 3 passi:**
1. **IMMEDIATO** — `v2/tests/performance/test_video_frame_rate.py` (Fase 4 §5)
2. `v2/tests/performance/test_aa_frame_decode.py` (Fase 4 §6 — chiude la Fase 4)
3. `v2/tests/fuzz/test_aa_wire_format.py` (Fase 5 §1 — avvia i fuzz test con `hypothesis`)

---

## 2026-05-13 — Fase 3 Full + Fase 4 §1

**Status:** Completato ✅ (commit `1cfa379`, `aa2c995`, `1e461f9`)

---

## 2026-05-13 — Fase 3 Smoke Completata

**Status:** Completato ✅ (commit `f45cf77` / `e734333`)

---

## 2026-05-13 — Unit Tests rfcomm_handshake + channel_manager

**Status:** Completato ✅ (commit `9ab6c2e`)

---

## 2026-05-13 — E2E Helpers Completati

**Status:** Completato ✅ (commit `5c74859`, `bab116c`, `637631d`)

---

## Comandi Utili

```bash
# Performance (tutti)
pytest -m performance -v --json-report

# Performance singolo scenario
pytest v2/tests/performance/test_bus_throughput.py -v

# Smoke CI
pytest -m e2e_smoke -v

# Unit + integration (blocca merge)
pytest -m "unit or integration" --cov=v2 --cov-fail-under=80

# Tutto
pytest -v --cov=v2
```
