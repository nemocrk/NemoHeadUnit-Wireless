# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — Fase 3 Full completata + Fase 4 §1 (`test_bus_latency.py`)

---

## Stato Corrente in Una Frase

**48 file di test, ~2918 test + 3 helper E2E** su `main`. Fase 0–3 completamente chiuse. Fase 4 avviata con `test_bus_latency.py`. **Prossimo: Fase 4 §2 — `test_bus_throughput.py`**.

---

## File Prodotti

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
| `e2e/full_session/test_full_aa_session.py` | — | ~12 | **Fase 3 Full §1** |
| `e2e/full_session/test_session_recovery.py` | — | ~8 | **Fase 3 Full §2** |
| `performance/test_bus_latency.py` | — | ~18 | **Fase 4 §1** |

**Totale: ~2918 test in 48 file di test + 3 helper E2E + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti

### BusClient (commit ddd7142)
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### E2E — pattern consolidato
```python
@pytest.mark.e2e_smoke   # oppure e2e_full
class TestXxx:
    def test_yyy(self, in_process_broker):
        with e2e_stack(in_process_broker, modules=[...]) as stack:
            mock = PhoneMock(phone_sock).start()
            stack.publish("bluetooth.rfcomm.connected", {"fd": ..., "address": ...})
            assert stack.wait_topic("rfcomm.handshake.completed", timeout=5)
```

### Performance — pattern (Fase 4)
```python
@pytest.mark.performance
class TestBusLatency:
    """Misura latenza publish→receive con bus ZMQ reale in-process."""
    PERCENTILES = [50, 95, 99]
    THRESHOLDS_MS = {50: 2.0, 95: 5.0, 99: 10.0}

    def _measure_latency(self, bus_client, n=1000) -> list[float]:
        # pubblica N messaggi, registra RTT, restituisce lista in ms
        ...

    def test_publish_latency_p50(self, in_process_broker): ...
    def test_publish_latency_p95(self, in_process_broker): ...
    def test_publish_latency_p99(self, in_process_broker): ...
```

### Full Session E2E — struttura
```python
@pytest.mark.e2e_full
class TestFullAaSession:
    # timeout più lunghi: 30s per session completa
    # usa FullHandshakeSequence.as_bus_payloads() per injectare frame senza socket reali
    # verifica ogni fase: boot → version → auth → service_disc → channels → media → shutdown
```

---

## 2026-05-13 — Fase 3 Full + Fase 4 §1

**Cosa cambiato:**

- **`test_full_aa_session.py`** (~12 test `@pytest.mark.e2e_full`)
  - Happy path completo: boot → RFCOMM → TCP AA → version exchange → auth → service discovery → tutti canali aperti → media → ping → shutdown da telefono
  - `test_session_with_audio_focus` — media + focus AA
  - `test_session_with_video_frame` — H.264 IDR frame passante
  - `test_session_with_sensor_events` — sensor channel attivo
  - `test_session_shutdown_from_phone` + `test_session_shutdown_from_hu`
  - `test_session_ping_pong` — latenza ping < 100ms
  - `test_session_reconnect_after_unexpected_disconnect`

- **`test_session_recovery.py`** (~8 test `@pytest.mark.e2e_full`)
  - `test_recovery_after_phone_disconnect_rfcomm` — re-handshake entro 10s
  - `test_recovery_after_tcp_drop` — TCP drop + reconnect
  - `test_recovery_after_channel_crash` — crash canale singolo
  - `test_recovery_multiple_reconnects` — 3 cicli connessione/disconnessione
  - `test_state_clean_after_recovery` — nessun residuo di stato dalla sessione precedente
  - `test_audio_restored_after_recovery`

- **`test_bus_latency.py`** (~18 test `@pytest.mark.performance`)
  - Misura RTT publish→receive con 1000 campioni per scenario
  - Soglie: p50 ≤ 2ms, p95 ≤ 5ms, p99 ≤ 10ms
  - Scenari: burst 100msg, payload grande (64KB), multi-subscriber (x5), cross-thread
  - Output JSON in `tests/reports/perf-{date}-{commit}.json`
  - `test_latency_under_load` — 100 publisher concorrenti
  - `test_latency_regression` — confronto con baseline salvata

**Perché:**
Chiudere la Fase 3 Full come da roadmap e avviare la Fase 4 con la metrica più critica (latenza bus).

**Status:** Completato ✅

**Prossimi 3 passi:**
1. **IMMEDIATO** — `v2/tests/performance/test_bus_throughput.py` (Fase 4 §2)
2. `v2/tests/performance/test_audio_latency.py` (Fase 4 §3)
3. `v2/tests/performance/test_memory_rss.py` (Fase 4 §4)

---

## 2026-05-13 — Fase 3 Smoke Completata

**Status:** Completato ✅ (27 test in 3 file, commit `f45cf77` / `e734333`)

---

## 2026-05-13 — Unit Tests rfcomm_handshake + channel_manager

**Status:** Completato ✅ (commit `9ab6c2e`)

---

## 2026-05-13 — E2E Helpers Completati

**Status:** Completato ✅ (commit `5c74859`, `bab116c`, `637631d`)

---

## Comandi Utili

```bash
# Smoke (CI veloci)
pytest -m e2e_smoke -v

# Full session (nightly)
pytest -m e2e_full -v --timeout=60

# Performance
pytest -m performance -v --json-report --json-report-file=tests/reports/perf.json

# Unit + integration (CI blocca merge)
pytest -m "unit or integration" --cov=v2 --cov-fail-under=80

# Tutto
pytest -v --cov=v2
```
