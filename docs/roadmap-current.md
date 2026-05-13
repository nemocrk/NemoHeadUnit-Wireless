# Roadmap Test Suite — NemoHeadUnit-Wireless v2

> Documento di pianificazione per la costruzione completa della test suite di `v2/`.
> Basato su `docs/TEST_SUITE_ARCHITECTURE.md` v2.0 e `docs/project-vision.md` v3.3.
> Data di creazione: 2026-05-13 — **Ultimo aggiornamento: 2026-05-13**

---

## Executive Summary

Nelle sessioni del 2026-05-13 sono stati prodotti **35 file di test + 3 file infrastruttura** per un totale di **~2370 test**. La Fase 0 e la Fase 1 sono completamente chiuse. La Fase 2 conta 5 file completati su 7.

L'obiettivo è raggiungere **≥ 80% di coverage globale** su `v2/` — soglia che blocca il merge in CI — seguendo l'architettura stratificata definita in `TEST_SUITE_ARCHITECTURE.md`.

> **Nota architetturale**: i commit `ddd7142`, `1b70b0a`, `859d70e` hanno introdotto `BusClient` refactor con
> iniezione `_trace` nel payload wire, `BusTracer` (shared) e modulo `zmq_trace`. I relativi test
> sono stati prodotti nella stessa sessione e integrati nella Fase 1.

---

## Fase 0 — Infrastruttura ✅ COMPLETATA

| File | Commit | Stato |
|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | ✅ |
| `v2/tests/pytest.ini` | `0ff487e` | ✅ |
| `v2/tests/requirements-test.txt` | `0ff487e` | ✅ |

### Fixture disponibili in `conftest.py`

- **`in_process_broker`** — broker ZMQ XPUB/XSUB su socket IPC univoci (`ipc:///tmp/nemotest-{uuid}.{pub|sub}`), teardown con TERMINATE + join 2s
- **`bus_client`** — `BusClient` con monkey-patch degli indirizzi + BusTracer mockato; espone `publish`, `subscribe`, `wait_for`, `received`
- **`mock_bus`** — mock senza socket, registra chiamate; espone `trigger`, `published_topics`, `last_payload`, `reset`
- **`aa_frame_factory`** — factory frame AA con metodi: `setup_request`, `channel_open_request`, `av_media_with_timestamp`, `av_media_indication`, `h264_idr_frame`, `h264_p_frame`, `malformed` (5 strategie)
- **`mock_config`** — YAML minimale in `tmp_path`
- **`qt_app`** — `QApplication` offscreen, scope session
- **`dbus_session`** — D-Bus session dedicata per test bluetooth
- **`audio_source`** — parametrizzata mock/hardware con `skipif` automatico
- **`hardware_available(device)`** — utility runtime per audio, bluetooth, gst_sw, gst_vaapi, dbus

---

## Fase 1 — Unit Test ✅ COMPLETATA

Target: **< 1s per test**, coverage ≥ 80%, marker `@pytest.mark.unit`.

### 1.1 Shared

| File target | Test | Stato |
|---|---|---|
| `unit/shared/test_proto_utils.py` | 47 | ✅ |
| `unit/shared/test_bus_client.py` | ~75 | ✅ aggiornato `db85dc8` |
| `unit/shared/test_config_client.py` | 38 | ✅ |
| `unit/shared/test_logger.py` | 88 | ✅ `38a2885` |
| `unit/shared/test_bus_trace.py` | 72 | ✅ `be3068e` |

### 1.2 Channel Modules — Base

| File target | Test | Stato |
|---|---|---|
| `unit/modules/channel_modules/test_base_channel_module.py` | 44 | ✅ |

### 1.3 Channel Modules — Specifici

| File target | Test | Stato |
|---|---|---|
| `unit/modules/channel_modules/audio/test_audio_module.py` | 42 | ✅ |
| `unit/modules/channel_modules/video/test_video_module.py` | 38 | ✅ |
| `unit/modules/channel_modules/av_input/test_av_input.py` | 96 | ✅ `0328b84` |
| `unit/modules/channel_modules/bluetooth/test_bluetooth_channel.py` | 72 | ✅ `7791347` |
| `unit/modules/channel_modules/input/test_input.py` | 88 | ✅ `5aaa396` |
| `unit/modules/channel_modules/sensor/test_sensor.py` | 84 | ✅ `d6f8a78` |
| `unit/modules/channel_modules/wifi/test_wifi.py` | 72 | ✅ `a1fa970` |

### 1.4 Moduli Standalone

| File target | Test | Stato |
|---|---|---|
| `unit/oaa_control_channel/test_oaa_control_channel_main.py` | 54 | ✅ |
| `unit/oaa_control_channel/test_handshake.py` | 62 | ✅ |
| `unit/oaa_control_channel/test_serializer.py` | 68 | ✅ `ffe6314` |
| `unit/oaa_control_channel/test_service_discovery.py` | 72 | ✅ `4e7a28d` |
| `unit/modules/channel_manager/test_channel_manager.py` | 78 | ✅ `4f90f8a` |
| `unit/modules/tcp_server/test_tcp_server.py` | 84 | ✅ `412541a` |
| `unit/modules/audio_manager/test_audio_manager.py` | 88 | ✅ `acb6dce` |
| `unit/modules/video_ui/test_video_ui.py` | 92 | ✅ `83973cb` |
| `unit/modules/bluetooth/test_bluetooth_main.py` | 96 | ✅ `b024893` |
| `unit/modules/bluetooth/test_bluez_adapter.py` | 88 | ✅ `17c7c4d` |
| `unit/modules/bluetooth/test_discovery.py` | 72 | ✅ `a1b5156` |
| `unit/modules/bluetooth/test_pairing.py` | 84 | ✅ `d4973f8` |
| `unit/modules/bluetooth/test_paired_devices.py` | 68 | ✅ `6a83677` |
| `unit/modules/config_manager/test_config_manager.py` | 96 | ✅ `e1c0847` |
| `unit/modules/zmq_trace/test_zmq_trace.py` | 68 | ✅ `cdc9e6f` |

**Totale Fase 1: 2213 test in 28 file unit.**

---

## Fase 2 — Integration Test 🟡 IN CORSO

Target: **< 10s per test**, marker `@pytest.mark.integration`. Bus ZMQ reale in-process condiviso tra moduli avviati come thread.

> **Nota**: tutti i `_make_integration_client()` nei test di Fase 2 devono mockare `BusTracer`
> per evitare thread drain spurii e socket ZMQ extra. Pattern: `patch("shared.bus_client.BusTracer", return_value=MagicMock())`

| File target | Test | Commit | Stato |
|---|---|---|---|
| `integration/test_bus_broker.py` | 84 | `bd326e5` | ✅ |
| `integration/test_channel_lifecycle.py` | 88 | `1734764` | ✅ |
| `integration/test_audio_manager.py` | ~47 | `7e1d9be` | ✅ |
| `integration/test_config_manager.py` | ~50 | `9f08aa6` | ✅ |
| `integration/test_video_pipeline.py` | ~60 | `2d8d861` | ✅ **COMPLETATO** |
| `integration/test_bluetooth_flow.py` | — | — | ❌ **PROSSIMO** |
| `integration/test_boot_shutdown.py` | — | — | ❌ da fare |

**Totale Fase 2 finora: ~329 test in 5 file.**

### Pattern integration (recap)

- `_load_cm` / `_load_am` / `_load_video_ui` / `_make_video_module` per reload modulo + patch indirizzi
- `_make_client` + `_start_client` + `_wait` — helper condivisi
- Handler `on_*` / `_handle_*` chiamati direttamente; spy BusClient riceve topic pubblicati
- `importlib.reload()` per ogni test — bus ZMQ fresco e stato modulo pulito
- PyQt6/GStreamer/gi stubbed in `sys.modules` per video_ui senza display

---

## Fase 3 — E2E Test

### Smoke (`@pytest.mark.e2e_smoke`) — run in CI, < 30s

| File target | Stato |
|---|---|
| `e2e/smoke/test_bt_connect_to_handshake.py` | ❌ da fare |
| `e2e/smoke/test_channel_manager_boot.py` | ❌ da fare |
| `e2e/smoke/test_audio_path_smoke.py` | ❌ da fare |

Helper richiesti in `e2e/helpers/`: `phone_mock.py`, `frame_sequences.py`, `stack_launcher.py`.

### Full Session (`@pytest.mark.e2e_full`) — nightly/on-demand

| File target | Stato |
|---|---|
| `e2e/full_session/test_full_aa_session.py` | ❌ da fare |
| `e2e/full_session/test_session_recovery.py` | ❌ da fare |

---

## Fase 4 — Performance Benchmark

Marker `@pytest.mark.performance`. **Non bloccano il merge.** Output: `tests/reports/perf-{YYYYMMDD}-{commit}.json`.

| File target | Metrica | Stato |
|---|---|---|
| `performance/test_bus_latency.py` | publish→receive p50/p95/p99 ms | ❌ |
| `performance/test_bus_throughput.py` | msg/s, MB/s | ❌ |
| `performance/test_audio_latency.py` | ≤ 10ms p50 | ❌ |
| `performance/test_video_frame_rate.py` | ≥ 30fps | ❌ |
| `performance/test_memory_rss.py` | RSS baseline/+5min/+30min | ❌ |
| `performance/test_aa_frame_decode.py` | encode+decode round-trip µs | ❌ |

---

## Fase 5 — Fuzz Test

Marker `@pytest.mark.fuzz`. Motore: `hypothesis`. Profili: `ci` (100), `local` (1000), `nightly` (10000).

| File target | Stato |
|---|---|
| `fuzz/test_aa_wire_format.py` | ❌ |
| `fuzz/test_proto_utils_roundtrip.py` | ❌ |
| `fuzz/test_bus_payload_malformed.py` | ❌ |

---

## Riepilogo Generale

| Fase | File prodotti / totali | Test scritti | Blocca CI | Stato |
|---|---|---|---|---|
| 0 — Infrastruttura | 3 / 3 | — | ✅ Sì | ✅ Completa |
| 1 — Unit Test | 28 / 28 | 2213 | ✅ Sì (≥80%) | ✅ **Completa** |
| 2 — Integration | 5 / 7 | ~329 | ✅ Sì | 🟡 In corso |
| 3 — E2E Smoke | 0 / 3 | 0 | ✅ Sì | ❌ Todo |
| 3 — E2E Full | 0 / 2 | 0 | ❌ No | ❌ Todo |
| 4 — Performance | 0 / 6 | 0 | ❌ No | ❌ Todo |
| 5 — Fuzz | 0 / 3 | 0 | ❌ No | ❌ Todo |
| **Totale** | **36 / ~54** | **~2542** | — | 🟡 |

---

## Ordine di Esecuzione Raccomandato (aggiornato)

1. ⁠~~**Fase 0**~~ ✅ Completata
2. ⁠~~**Fase 1 §1.1–1.4 completo**~~ ✅ 2213 test
3. ⁠~~**Fase 2 §1**: `test_bus_broker.py`~~ ✅
4. ⁠~~**Fase 2 §2**: `test_channel_lifecycle.py`~~ ✅
5. ⁠~~**Fase 2 §3**: `test_audio_manager.py`~~ ✅
6. ⁠~~**Fase 2 §4**: `test_config_manager.py`~~ ✅
7. ⁠~~**Fase 2 §5**: `test_video_pipeline.py`~~ ✅
8. **PROSSIMO → Fase 2 §6**: `test_bluetooth_flow.py` — bluetooth pairing + A2DP flow
9. Fase 2 §7: `test_boot_shutdown.py` — full system boot sequence
10. **Fase 3 Smoke** — E2E smoke dopo integration
11. **Fase 4 + 5** — Performance e fuzz in parallelo, non bloccanti

---

*Roadmap Version: 2.6*  
*Aggiornato: 2026-05-13*  
*Basata su: `docs/TEST_SUITE_ARCHITECTURE.md` v2.0, `docs/project-vision.md` v3.3*  
*Vedi anche: `docs/session_handoff.md` per dettagli tecnici della sessione corrente*
