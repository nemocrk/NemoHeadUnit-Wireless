# Roadmap Test Suite — NemoHeadUnit-Wireless v2

> Documento di pianificazione per la costruzione completa della test suite di `v2/`.
> Basato su `docs/TEST_SUITE_ARCHITECTURE.md` v2.0 e `docs/project-vision.md` v3.3.
> Data di creazione: 2026-05-13 — **Ultimo aggiornamento: 2026-05-13 (v3.4)**

---

## Executive Summary

Nelle sessioni del 2026-05-13 sono stati prodotti **53 file di test + 3 file infrastruttura + 3 helper E2E** per un totale di **~3084 test**. Fase 0–4 completamente chiuse.

L'obiettivo è raggiungere **≥ 80% di coverage globale** su `v2/` — soglia che blocca il merge in CI.

> **Nota architetturale**: i commit `ddd7142`, `1b70b0a`, `859d70e` hanno introdotto `BusClient` refactor con
> iniezione `_trace` nel payload wire, `BusTracer` (shared) e modulo `zmq_trace`.

---

## Fase 0 — Infrastruttura ✅ COMPLETATA

| File | Commit | Stato |
|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | ✅ |
| `v2/tests/pytest.ini` | `0ff487e` | ✅ |
| `v2/tests/requirements-test.txt` | `0ff487e` | ✅ |

---

## Fase 1 — Unit Test ✅ COMPLETATA (~2264 test, 29 file)

### 1.1 Shared
| File | Test | Stato |
|---|---|---|
| `unit/shared/test_proto_utils.py` | 47 | ✅ |
| `unit/shared/test_bus_client.py` | ~75 | ✅ `db85dc8` |
| `unit/shared/test_config_client.py` | 38 | ✅ |
| `unit/shared/test_logger.py` | 88 | ✅ `38a2885` |
| `unit/shared/test_bus_trace.py` | 72 | ✅ `be3068e` |

### 1.2–1.3 Channel Modules
| File | Test | Stato |
|---|---|---|
| `channel_modules/test_base_channel_module.py` | 44 | ✅ |
| `channel_modules/audio/test_audio_module.py` | 42 | ✅ |
| `channel_modules/video/test_video_module.py` | 38 | ✅ |
| `channel_modules/av_input/test_av_input.py` | 96 | ✅ `0328b84` |
| `channel_modules/bluetooth/test_bluetooth_channel.py` | 72 | ✅ `7791347` |
| `channel_modules/input/test_input.py` | 88 | ✅ `5aaa396` |
| `channel_modules/sensor/test_sensor.py` | 84 | ✅ `d6f8a78` |
| `channel_modules/wifi/test_wifi.py` | 72 | ✅ `a1fa970` |

### 1.4 Moduli Standalone
| File | Test | Stato |
|---|---|---|
| `oaa_control_channel/test_oaa_control_channel_main.py` | 54 | ✅ |
| `oaa_control_channel/test_handshake.py` | 62 | ✅ |
| `oaa_control_channel/test_serializer.py` | 68 | ✅ `ffe6314` |
| `oaa_control_channel/test_service_discovery.py` | 72 | ✅ `4e7a28d` |
| `modules/channel_manager/test_channel_manager.py` | 78 | ✅ `4f90f8a` |
| `modules/tcp_server/test_tcp_server.py` | 84 | ✅ `412541a` |
| `modules/audio_manager/test_audio_manager.py` | 88 | ✅ `acb6dce` |
| `modules/video_ui/test_video_ui.py` | 92 | ✅ `83973cb` |
| `modules/bluetooth/test_bluetooth_main.py` | 96 | ✅ `b024893` |
| `modules/bluetooth/test_bluez_adapter.py` | 88 | ✅ `17c7c4d` |
| `modules/bluetooth/test_discovery.py` | 72 | ✅ `a1b5156` |
| `modules/bluetooth/test_pairing.py` | 84 | ✅ `d4973f8` |
| `modules/bluetooth/test_paired_devices.py` | 68 | ✅ `6a83677` |
| `modules/config_manager/test_config_manager.py` | 96 | ✅ `e1c0847` |
| `modules/zmq_trace/test_zmq_trace.py` | 68 | ✅ `cdc9e6f` |
| `test_rfcomm_and_channel_manager.py` | ~51 | ✅ `9ab6c2e` |

---

## Fase 2 — Integration Test ✅ COMPLETATA (~454 test, 7 file)

| File | Test | Commit | Stato |
|---|---|---|---|
| `integration/test_bus_broker.py` | 84 | `bd326e5` | ✅ |
| `integration/test_channel_lifecycle.py` | 88 | `1734764` | ✅ |
| `integration/test_audio_manager.py` | ~47 | `7e1d9be` | ✅ |
| `integration/test_config_manager.py` | ~50 | `9f08aa6` | ✅ |
| `integration/test_video_pipeline.py` | ~60 | `2d8d861` | ✅ |
| `integration/test_bluetooth_flow.py` | ~60 | `dbbc2b4` | ✅ |
| `integration/test_boot_shutdown.py` | ~65 | `2fe07ef` | ✅ |

---

## Fase 3 — E2E Test ✅ COMPLETATA

### Prerequisito: `e2e/helpers/` ✅
| Helper | Commit | Stato |
|---|---|---|
| `e2e/helpers/phone_mock.py` | `5c74859` | ✅ |
| `e2e/helpers/frame_sequences.py` | `bab116c` | ✅ |
| `e2e/helpers/stack_launcher.py` | `637631d` | ✅ |

### Smoke (`@pytest.mark.e2e_smoke`) ✅
| File | Test | Stato |
|---|---|---|
| `e2e/smoke/test_bt_connect_to_handshake.py` | 10 | ✅ |
| `e2e/smoke/test_channel_manager_boot.py` | 9 | ✅ `f45cf77` |
| `e2e/smoke/test_audio_path_smoke.py` | 8 | ✅ `e734333` |

### Full Session (`@pytest.mark.e2e_full`) ✅
| File | Test | Stato |
|---|---|---|
| `e2e/full_session/test_full_aa_session.py` | ~12 | ✅ `1cfa379` |
| `e2e/full_session/test_session_recovery.py` | ~8 | ✅ `aa2c995` |

**Totale Fase 3: ~47 test in 5 file + 3 helper.**

---

## Fase 4 — Performance Benchmark ✅ COMPLETATA

Marker `@pytest.mark.performance`. **Non bloccano il merge.**

| File | Metrica | Test | Stato |
|---|---|---|---|
| `performance/test_bus_latency.py` | publish→receive p50/p95/p99 ms | ~9 | ✅ `1e461f9` |
| `performance/test_bus_throughput.py` | msg/s, MB/s | ~8 | ✅ `9f75a88` |
| `performance/test_audio_latency.py` | ≤ 10ms p50 | ~8 | ✅ `4927f1c` |
| `performance/test_memory_rss.py` | RSS baseline/+session/reconnect | ~6 | ✅ `777af59` |
| `performance/test_video_frame_rate.py` | ≥ 30fps decode pipeline | ~7 | ✅ **NUOVO** |
| `performance/test_aa_frame_decode.py` | encode+decode RTT µs | ~6 | ✅ **NUOVO** |

**Totale Fase 4: 6/6 file, ~44 test. ✅ COMPLETA.**

---

## Fase 5 — Fuzz Test 🟡 IN CORSO

Marker `@pytest.mark.fuzz`. Motore: `hypothesis`. **Non bloccano il merge.**

| File | Scenario | Stato |
|---|---|---|
| `fuzz/test_aa_wire_format.py` | Frame AA malformati / troncati / overflow | ✅ **NUOVO** |
| `fuzz/test_proto_utils_roundtrip.py` | Roundtrip proto encode→decode con input arbitrary | ❌ |
| `fuzz/test_bus_payload_malformed.py` | Payload JSON malformati / tipi errati sul bus | ❌ |

**Totale Fase 5 completato: 1/3 file.**

---

## Riepilogo Generale

| Fase | File prodotti / totali | Test scritti | Blocca CI | Stato |
|---|---|---|---|---|
| 0 — Infrastruttura | 3 / 3 | — | ✅ Sì | ✅ Completa |
| 1 — Unit Test | 29 / 29 | ~2264 | ✅ Sì (≥80%) | ✅ **Completa** |
| 2 — Integration | 7 / 7 | ~454 | ✅ Sì | ✅ **Completa** |
| 3 — E2E helpers | 3 / 3 | — | — | ✅ **Completo** |
| 3 — E2E Smoke | 3 / 3 | ~27 | ✅ Sì | ✅ **Completa** |
| 3 — E2E Full | 2 / 2 | ~20 | ❌ No | ✅ **Completa** |
| 4 — Performance | 6 / 6 | ~44 | ❌ No | ✅ **Completa** |
| 5 — Fuzz | 1 / 3 | ~12 | ❌ No | 🟡 **In corso** |
| **Totale** | **54 / ~57** | **~3084** | — | 🟡 |

---

## Ordine di Esecuzione Raccomandato (aggiornato)

1. ~~**Fase 0**~~ ✅
2. ~~**Fase 1**~~ ✅ ~2264 test
3. ~~**Fase 2**~~ ✅ ~454 test
4. ~~**Prerequisito Fase 3**: helpers~~ ✅
5. ~~**Fase 3 Smoke**~~ ✅ ~27 test
6. ~~**Fase 3 Full**~~ ✅ ~20 test
7. ~~**Fase 4 §1–§6**~~ ✅ ~44 test
8. **PROSSIMO → Fase 5 §2**: `fuzz/test_proto_utils_roundtrip.py`
9. **Fase 5 §3**: `fuzz/test_bus_payload_malformed.py` (chiude Fase 5)

---

*Roadmap Version: 3.4*
*Aggiornato: 2026-05-13*
*Basata su: `docs/TEST_SUITE_ARCHITECTURE.md` v2.0, `docs/project-vision.md` v3.3*
*Vedi anche: `docs/session_handoff.md` per dettagli tecnici della sessione corrente*
