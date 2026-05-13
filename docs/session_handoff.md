# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.
> **Aggiornato**: 2026-05-13 — Fase 4 completa (6/6), Fase 5 §1 completato (`test_aa_wire_format.py`)

---

## Stato Corrente in Una Frase

**54 file di test, ~3084 test + 3 helper E2E** su `main`. Fase 0–4 chiuse. Fase 5: 1/3 completati. **Prossimo: Fase 5 §2 — `fuzz/test_proto_utils_roundtrip.py`**.

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
| `performance/test_bus_throughput.py` | `9f75a88` | ~8 | Fase 4 §2 |
| `performance/test_audio_latency.py` | `4927f1c` | ~8 | Fase 4 §3 |
| `performance/test_memory_rss.py` | `777af59` | ~6 | Fase 4 §4 |
| `performance/test_video_frame_rate.py` | — | ~7 | **Fase 4 §5** |
| `performance/test_aa_frame_decode.py` | — | ~6 | **Fase 4 §6** |
| `fuzz/test_aa_wire_format.py` | — | ~12 | **Fase 5 §1** |

**Totale: ~3084 test in 54 file + 3 helper + 3 infra.**

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
```

### Fuzz — pattern consolidato (Fase 5)
```python
@pytest.mark.fuzz
class TestXxxFuzz:
    # Motore: hypothesis
    # @given(st.binary() | st.text() | st.integers() | ...)
    # @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    # Mai assert su valori specifici: assert su proprietà (no crash, no hang, no exception non-gestita)
    # Ogni test deve completare in < 30s
```

---

## 2026-05-13 — Fase 4 §5/§6 + Fase 5 §1

**Cosa cambiato:**

- **`test_video_frame_rate.py`** (~7 test `@pytest.mark.performance`)
  - `test_video_decode_fps_30` — decoder pipeline ≥ 30fps
  - `test_video_decode_fps_60` — decoder pipeline ≥ 60fps (H.264)
  - `test_video_frame_latency_p95` — p95 < 33ms (1 frame @30fps)
  - `test_video_keyframe_decode_time` — keyframe < 50ms
  - `test_video_fps_under_audio_load` — fps stabile con audio attivo
  - `test_video_fps_sustained_60s` — degradazione < 10% su finestra 10s
  - `test_video_fps_regression` — vs baseline JSON

- **`test_aa_frame_decode.py`** (~6 test `@pytest.mark.performance`)
  - `test_frame_encode_rtt_us` — encode RTT µs con payload tipico AA
  - `test_frame_decode_rtt_us` — decode RTT µs
  - `test_roundtrip_rtt_us` — encode+decode roundtrip
  - `test_large_frame_decode` — payload 64KB
  - `test_malformed_frame_no_crash` — frame troncato non crasha il decoder
  - `test_decode_regression` — vs baseline JSON

- **`fuzz/test_aa_wire_format.py`** (~12 test `@pytest.mark.fuzz`)
  - `test_fuzz_random_bytes_no_crash` — bytes arbitrari → no exception non-gestita
  - `test_fuzz_truncated_frame` — frame troncato a N byte
  - `test_fuzz_overflow_length_field` — length field > payload reale
  - `test_fuzz_zero_length_frame` — length = 0
  - `test_fuzz_negative_length_varint` — varint negativo
  - `test_fuzz_unknown_msg_id` — msg_id fuori range
  - `test_fuzz_repeated_header` — doppio header
  - `test_fuzz_max_frame_size` — frame al limite massimo
  - `test_fuzz_mixed_valid_invalid` — sequenza mista valid/invalid
  - `test_fuzz_concurrent_send_random` — N thread inviano frame casuali
  - `test_fuzz_encoding_roundtrip` — `@given`: encode(decode(x)) == x
  - `test_fuzz_decode_never_hangs` — decode non blocca > 100ms

**Perché:** Completare Fase 4 e avviare Fase 5 fuzz test.

**Status:** Completato ✅

**Prossimi 3 passi:**
1. **IMMEDIATO** — `v2/tests/fuzz/test_proto_utils_roundtrip.py` (Fase 5 §2)
2. `v2/tests/fuzz/test_bus_payload_malformed.py` (Fase 5 §3 — chiude Fase 5)
3. Review coverage report + eventuale top-up sui moduli sotto soglia 80%

---

## Handoff precedenti (sommario)

| Data | Cosa | Status |
|---|---|---|
| 2026-05-13 | Fase 4 §2/§3/§4 (`throughput`, `audio_latency`, `memory_rss`) | ✅ |
| 2026-05-13 | Fase 3 Full + Fase 4 §1 (`bus_latency`) | ✅ |
| 2026-05-13 | Fase 3 Smoke §2/§3 | ✅ |
| 2026-05-13 | Unit rfcomm_handshake + channel_manager | ✅ |
| 2026-05-13 | E2E Helpers | ✅ |

---

## Comandi Utili

```bash
# Fuzz (tutti)
pytest -m fuzz -v

# Fuzz singolo file
pytest v2/tests/fuzz/test_aa_wire_format.py -v

# Performance
pytest -m performance -v --json-report

# Smoke CI
pytest -m e2e_smoke -v

# Unit + integration (blocca merge)
pytest -m "unit or integration" --cov=v2 --cov-fail-under=80

# Tutto
pytest -v --cov=v2
```
