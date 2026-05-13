# Roadmap Test Suite — NemoHeadUnit-Wireless v2

> Documento di pianificazione per la costruzione completa della test suite di `v2/`.
> Basato su `docs/TEST_SUITE_ARCHITECTURE.md` v2.0 e `docs/project-vision.md` v3.3.
> Data di creazione: 2026-05-13 — **Ultimo aggiornamento: 2026-05-13**

---

## Executive Summary

Nelle sessioni del 2026-05-13 sono stati prodotti **41 file di test + 3 file infrastruttura + 3 helper E2E** per un totale di **~2777 test**. La Fase 0, 1, 2 e **3 Smoke sono completamente chiuse**. **Il prossimo obiettivo è la Fase 3 Full Session.**

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
| `test_rfcomm_and_channel_manager.py` | ~51 | ✅ `9ab6c2e` |

**Totale Fase 1: ~2264 test in 29 file unit.**

---

## Fase 2 — Integration Test ✅ COMPLETATA

Target: **< 10s per test**, marker `@pytest.mark.integration`. Bus ZMQ reale in-process condiviso tra moduli avviati come thread.

| File target | Test | Commit | Stato |
|---|---|---|---|
| `integration/test_bus_broker.py` | 84 | `bd326e5` | ✅ |
| `integration/test_channel_lifecycle.py` | 88 | `1734764` | ✅ |
| `integration/test_audio_manager.py` | ~47 | `7e1d9be` | ✅ |
| `integration/test_config_manager.py` | ~50 | `9f08aa6` | ✅ |
| `integration/test_video_pipeline.py` | ~60 | `2d8d861` | ✅ |
| `integration/test_bluetooth_flow.py` | ~60 | `dbbc2b4` | ✅ |
| `integration/test_boot_shutdown.py` | ~65 | `2fe07ef` | ✅ |

**Totale Fase 2: ~454 test in 7 file.**

### Pattern integration (recap)

- `_load_*` / `_make_*` per reload modulo + patch indirizzi + BusTracer mock
- `_make_client` + `_start_client` + `_wait` — helper condivisi
- `FakeModule` + `BootOrchestrator` come actor attivi sul bus reale (boot_shutdown)
- `importlib.reload()` per ogni test — bus ZMQ fresco e stato modulo pulito
- Hardware stub: D-Bus/BlueZ/GLib/gi + PyQt6/GStreamer in `sys.modules`

---

## Fase 3 — E2E Test ✅ SMOKE COMPLETATA

### Prerequisito: `e2e/helpers/` ✅ COMPLETATO

| Helper | Scopo | Commit | Stato |
|---|---|---|---|
| `e2e/helpers/phone_mock.py` | `PhoneMock` (RFCOMM responder) + `TcpPhoneClient` (AA TCP client) | `5c74859` | ✅ |
| `e2e/helpers/frame_sequences.py` | `VersionSequence`, `AuthSequence`, `ServiceDiscoverySeq`, `ChannelOpenSeq`, `PingSequence`, `MediaSequence`, `ShutdownSequence`, `FullHandshakeSequence` | `bab116c` | ✅ |
| `e2e/helpers/stack_launcher.py` | `StackLauncher`, `_ModuleThread`, context manager `e2e_stack()` | `637631d` | ✅ |

### Smoke (`@pytest.mark.e2e_smoke`) — run in CI, < 30s ✅ COMPLETATA

| File target | Test | Stato |
|---|---|---|
| `e2e/smoke/test_bt_connect_to_handshake.py` | 10 | ✅ |
| `e2e/smoke/test_channel_manager_boot.py` | 9 | ✅ |
| `e2e/smoke/test_audio_path_smoke.py` | 8 | ✅ |

**Totale Fase 3 Smoke: ~27 test in 3 file.**

### Full Session (`@pytest.mark.e2e_full`) — nightly/on-demand

| File target | Stato |
|---|---|
| `e2e/full_session/test_full_aa_session.py` | ❌ **PROSSIMO** |
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
| 1 — Unit Test | 29 / 29 | ~2264 | ✅ Sì (≥80%) | ✅ **Completa** |
| 2 — Integration | 7 / 7 | ~454 | ✅ Sì | ✅ **Completa** |
| 3 — E2E helpers | 3 / 3 | — | — | ✅ **Completo** |
| 3 — E2E Smoke | 3 / 3 | ~27 | ✅ Sì | ✅ **Completa** |
| 3 — E2E Full | 0 / 2 | 0 | ❌ No | ❌ **PROSSIMA** |
| 4 — Performance | 0 / 6 | 0 | ❌ No | ❌ Todo |
| 5 — Fuzz | 0 / 3 | 0 | ❌ No | ❌ Todo |
| **Totale** | **45 / ~57** | **~2777** | — | 🟡 |

---

## Ordine di Esecuzione Raccomandato (aggiornato)

1. ~~**Fase 0**~~ ✅
2. ~~**Fase 1 §1.1–1.4**~~ ✅ ~2264 test
3. ~~**Fase 2 §1–§7**~~ ✅ 454 test
4. ~~**Prerequisito Fase 3**: `e2e/helpers/`~~ ✅
5. ~~**Fase 3 Smoke §1–§3**~~ ✅ ~27 test
6. **PROSSIMO → Fase 3 Full §1**: `test_full_aa_session.py`
7. **Fase 3 Full §2**: `test_session_recovery.py`
8. **Fase 4 + 5** — Performance e fuzz in parallelo, non bloccanti

---

*Roadmap Version: 3.1*
*Aggiornato: 2026-05-13*
*Basata su: `docs/TEST_SUITE_ARCHITECTURE.md` v2.0, `docs/project-vision.md` v3.3*
*Vedi anche: `docs/session_handoff.md` per dettagli tecnici della sessione corrente*
