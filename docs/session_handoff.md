# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-13 — **TEST SUITE COMPLETA** (Fase 0–5 chiuse, 57 file, ~3120 test)

---

## Stato Corrente in Una Frase

**57 file di test, ~3120 test + 3 helper E2E** su `main`. Tutte le fasi completate. **Prossimo: coverage report + top-up moduli sotto 80%.**

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
| `e2e/smoke/test_bt_connect_to_handshake.py` | — | 10 | |
| `e2e/smoke/test_channel_manager_boot.py` | `f45cf77` | 9 | |
| `e2e/smoke/test_audio_path_smoke.py` | `e734333` | 8 | |
| `e2e/full_session/test_full_aa_session.py` | `1cfa379` | ~12 | |
| `e2e/full_session/test_session_recovery.py` | `aa2c995` | ~8 | |
| `performance/test_bus_latency.py` | `1e461f9` | ~9 | |
| `performance/test_bus_throughput.py` | `9f75a88` | ~8 | |
| `performance/test_audio_latency.py` | `4927f1c` | ~8 | |
| `performance/test_memory_rss.py` | `777af59` | ~6 | |
| `performance/test_video_frame_rate.py` | `14df4e6` | ~7 | |
| `performance/test_aa_frame_decode.py` | `1b22850` | ~6 | |
| `fuzz/test_aa_wire_format.py` | — | ~12 | Fase 5 §1 |
| `fuzz/test_proto_utils_roundtrip.py` | — | ~10 | **Fase 5 §2** |
| `fuzz/test_bus_payload_malformed.py` | — | ~10 | **Fase 5 §3** |

**Totale: ~3120 test in 57 file + 3 helper + 3 infra.**

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
    # Mai assert su valori specifici: assert su proprietà (no crash, no hang)
    # Ogni test deve completare in < 30s

    # Proto roundtrip property:
    # @given(st.binary())
    # def test_roundtrip(raw): decoded = decode(raw); assert encode(decoded) == raw  # o None-safe

    # Bus payload property:
    # @given(st.one_of(st.text(), st.integers(), st.binary(), st.floats(allow_nan=False)))
    # def test_publish_no_crash(val): bus.publish(topic, {"v": val})  # no exception
```

---

## 2026-05-13 — Fase 5 §2/§3 (chiude test suite)

**Cosa cambiato:**

- **`fuzz/test_proto_utils_roundtrip.py`** (~10 test `@pytest.mark.fuzz`)
  - `test_fuzz_encode_decode_roundtrip` — `@given(st.binary())`: encode→decode→encode idempotente
  - `test_fuzz_decode_arbitrary_bytes` — nessun crash su bytes arbitrari
  - `test_fuzz_decode_valid_proto_structure` — struttura protobuf valida sempre decodificabile
  - `test_fuzz_field_overflow` — field ID > INT32_MAX
  - `test_fuzz_repeated_field_huge` — campo repeated con 10k elementi
  - `test_fuzz_nested_message_deep` — messaggi annidati fino a depth 100
  - `test_fuzz_unicode_string_field` — stringhe Unicode arbitrarie
  - `test_fuzz_integer_extremes` — int64 min/max, 0, negativi
  - `test_fuzz_float_specials` — inf, -inf, nan (gestiti gracefully)
  - `test_fuzz_proto_no_hang` — decode non blocca > 50ms

- **`fuzz/test_bus_payload_malformed.py`** (~10 test `@pytest.mark.fuzz`)
  - `test_fuzz_publish_any_value` — `@given(st.one_of(...))`: publish non crasha
  - `test_fuzz_publish_nested_dict` — dict arbitrariamente annidato
  - `test_fuzz_publish_list_payload` — lista come payload value
  - `test_fuzz_publish_none_value` — None come value di campo
  - `test_fuzz_publish_wrong_types` — tipi errati (bytes, set, object)
  - `test_fuzz_subscribe_topic_arbitrary` — topic string arbitraria → no crash
  - `test_fuzz_malformed_json_string` — stringa JSON-like malformata
  - `test_fuzz_large_payload` — payload 1MB+ non blocca il bus
  - `test_fuzz_concurrent_malformed` — N thread pubblicano payload errati in parallelo
  - `test_fuzz_handler_receives_original` — handler sempre riceve il payload originale invariato

**Perché:** Completare la Fase 5 e chiudere la test suite.

**Status:** Completato ✅ — **TEST SUITE COMPLETA**

**Prossimi 3 passi:**
1. **IMMEDIATO** — Eseguire `pytest --cov=v2 --cov-report=html` e identificare moduli sotto 80%
2. Top-up test unit mirati sui moduli sotto soglia
3. Verifica CI: `pytest -m "unit or integration" --cov-fail-under=80` in green

---

## Handoff precedenti (sommario)

| Data | Cosa | Status |
|---|---|---|
| 2026-05-13 | Fase 4 §5/§6 + Fase 5 §1 | ✅ |
| 2026-05-13 | Fase 4 §2/§3/§4 | ✅ |
| 2026-05-13 | Fase 3 Full + Fase 4 §1 | ✅ |
| 2026-05-13 | Fase 3 Smoke §2/§3 | ✅ |
| 2026-05-13 | Unit rfcomm_handshake + channel_manager | ✅ |
| 2026-05-13 | E2E Helpers | ✅ |

---

## Comandi Utili

```bash
# Coverage report
pytest --cov=v2 --cov-report=html --cov-report=term-missing

# Fuzz (tutti)
pytest -m fuzz -v

# Performance
pytest -m performance -v

# Smoke CI
pytest -m e2e_smoke -v

# Unit + integration (blocca merge)
pytest -m "unit or integration" --cov=v2 --cov-fail-under=80

# Tutto
pytest -v --cov=v2
```
