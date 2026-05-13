# Roadmap Test Suite — NemoHeadUnit-Wireless v2

> Documento di pianificazione per la costruzione completa della test suite di `v2/`.
> Basato su `docs/TEST_SUITE_ARCHITECTURE.md` v2.0 e `docs/project-vision.md` v3.3.
> Data di creazione: 2026-05-13 — **Ultimo aggiornamento: 2026-05-13**

---

## Executive Summary

La test suite di `v2/` era quasi assente al kick-off. Nelle sessioni del 2026-05-13 sono stati prodotti **10 file di test + 3 file infrastruttura** per un totale di **517 test** coperti da marker `@pytest.mark.unit`. La Fase 0 è completamente chiusa. La Fase 1 è in corso.

L'obiettivo è raggiungere **≥ 80% di coverage globale** su `v2/` — soglia che blocca il merge in CI — seguendo l'architettura stratificata definita in `TEST_SUITE_ARCHITECTURE.md`.

---

## Fase 0 — Infrastruttura ✅ COMPLETATA

| File | Commit | Stato |
|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | ✅ |
| `v2/tests/pytest.ini` | `0ff487e` | ✅ |
| `v2/tests/requirements-test.txt` | `0ff487e` | ✅ |

### Fixture disponibili in `conftest.py`

- **`in_process_broker`** — broker ZMQ XPUB/XSUB su socket IPC univoci (`ipc:///tmp/nemotest-{uuid}.{pub|sub}`), teardown con TERMINATE + join 2s
- **`bus_client`** — `BusClient` con monkey-patch degli indirizzi; espone `publish`, `subscribe`, `wait_for`, `received`
- **`mock_bus`** — mock senza socket, registra chiamate; espone `trigger`, `published_topics`, `last_payload`, `reset`
- **`aa_frame_factory`** — factory frame AA con metodi: `setup_request`, `channel_open_request`, `av_media_with_timestamp`, `av_media_indication`, `h264_idr_frame`, `h264_p_frame`, `malformed` (5 strategie)
- **`mock_config`** — YAML minimale in `tmp_path`
- **`qt_app`** — `QApplication` offscreen, scope session
- **`dbus_session`** — D-Bus session dedicata per test bluetooth
- **`audio_source`** — parametrizzata mock/hardware con `skipif` automatico
- **`hardware_available(device)`** — utility runtime per audio, bluetooth, gst_sw, gst_vaapi, dbus

---

## Fase 1 — Unit Test

Target: **< 1s per test**, coverage ≥ 80%, marker `@pytest.mark.unit`.

### 1.1 Shared

| File target | Test | Stato |
|---|---|---|
| `unit/shared/test_proto_utils.py` | 47 | ✅ |
| `unit/shared/test_bus_client.py` | 52 | ✅ |
| `unit/shared/test_config_client.py` | 38 | ✅ |
| `unit/shared/test_logger.py` | — | ❌ da fare |

### 1.2 Channel Modules — Base

| File target | Test | Stato |
|---|---|---|
| `unit/modules/channel_modules/test_base_channel_module.py` | 44 | ✅ |

> ⚠️ **Priorità alta**: `base_channel_module.py` è ereditato da tutti i canali — ogni test qui copre logica comune a `audio`, `av_input`, `video`, `bluetooth`, `input`, `sensor`, `wifi`.

### 1.3 Channel Modules — Specifici

| File target | Test | Stato |
|---|---|---|
| `unit/modules/channel_modules/audio/test_audio_module.py` | 42 | ✅ |
| `unit/modules/channel_modules/video/test_video_module.py` | 38 | ✅ |
| `unit/modules/channel_modules/av_input/test_av_input.py` | — | ❌ da fare |
| `unit/modules/channel_modules/bluetooth/test_bluetooth_channel.py` | — | ❌ da fare |
| `unit/modules/channel_modules/input/test_input.py` | — | ❌ da fare |
| `unit/modules/channel_modules/sensor/test_sensor.py` | — | ❌ da fare |
| `unit/modules/channel_modules/wifi/test_wifi.py` | — | ❌ da fare |

### 1.4 Moduli Standalone

| File target | Test | Stato |
|---|---|---|
| `unit/oaa_control_channel/test_oaa_control_channel_main.py` | 54 | ✅ |
| `unit/oaa_control_channel/test_handshake.py` | 62 | ✅ |
| `unit/oaa_control_channel/test_serializer.py` | 68 | ✅ commit `ffe6314` |
| `unit/oaa_control_channel/test_service_discovery.py` | 72 | ✅ commit `4e7a28d` |
| `unit/modules/channel_manager/test_channel_manager.py` | — | ❌ **PROSSIMO** |
| `unit/modules/audio_manager/test_audio_manager.py` | — | ❌ da fare |
| `unit/modules/video_ui/test_video_ui.py` | — | ❌ da fare |
| `unit/modules/bluetooth/test_paired_devices.py` | — | ❌ da fare |
| `unit/modules/bluetooth/test_pairing.py` | — | ❌ da fare |
| `unit/modules/bluetooth/test_autoconnect.py` | — | ❌ da fare |
| `unit/modules/bluetooth_ui/test_bluetooth_ui.py` | — | ❌ da fare |
| `unit/modules/config_manager/test_config_manager.py` | — | ❌ da fare |
| `unit/modules/tcp_server/test_tcp_server.py` | — | ❌ da fare |
| `unit/modules/rfcomm_handshake/test_rfcomm_handshake.py` | — | ❌ da fare |
| `unit/modules/hostapd_helper/test_hostapd_helper.py` | — | ❌ da fare |
| `unit/modules/config_ui/test_config_ui.py` | — | ❌ da fare |
| `unit/modules/log_viewer/test_log_viewer.py` | — | ❌ da fare |

**Totale Fase 1 prodotto finora: 517 test in 10 file.**

---

## Fase 2 — Integration Test

Target: **< 10s per test**, marker `@pytest.mark.integration`. Bus ZMQ reale in-process condiviso tra moduli avviati come thread.

| File target | Stato |
|---|---|
| `integration/test_bus_broker.py` | ❌ da fare |
| `integration/test_channel_lifecycle.py` | ❌ da fare |
| `integration/test_audio_pipeline.py` | ❌ da fare |
| `integration/test_video_pipeline.py` | ❌ da fare |
| `integration/test_bluetooth_flow.py` | ❌ da fare |
| `integration/test_boot_shutdown.py` | ❌ da fare |

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
| 1 — Unit Test | 10 / ~25 | 517 | ✅ Sì (≥80%) | 🟡 In corso |
| 2 — Integration | 0 / 6 | 0 | ✅ Sì | ❌ Todo |
| 3 — E2E Smoke | 0 / 6 | 0 | ✅ Sì | ❌ Todo |
| 3 — E2E Full | 0 / 2 | 0 | ❌ No | ❌ Todo |
| 4 — Performance | 0 / 6 | 0 | ❌ No | ❌ Todo |
| 5 — Fuzz | 0 / 3 | 0 | ❌ No | ❌ Todo |
| **Totale** | **13 / ~51** | **517** | — | 🟡 |

## Ordine di Esecuzione Raccomandato (aggiornato)

1. ~~**Fase 0**~~ ✅ Completata
2. ~~**Fase 1 §1.2**~~ ✅ `test_base_channel_module.py`
3. ~~**Fase 1 §1.1 parziale**~~ ✅ `proto_utils`, `bus_client`, `config_client`
4. ~~**Fase 1 §1.3 parziale**~~ ✅ `audio`, `video`
5. ~~**Fase 1 §1.4 parziale**~~ ✅ `oaa_control_channel_main`, `handshake`
6. ~~**Fase 1 §1.4**~~ ✅ `test_serializer.py` — 68 test
7. ~~**Fase 1 §1.4**~~ ✅ `test_service_discovery.py` — 72 test
8. **PROSSIMO → Fase 1 §1.4**: `test_channel_manager.py`
9. Fase 1 §1.3 resto: `av_input`, `bluetooth`, `input`, `sensor`, `wifi`
10. Fase 1 §1.4 secondario: `tcp_server`, `audio_manager`, `video_ui`, `bluetooth/*`, `config_manager`, altri
11. Fase 1 §1.1: `test_logger.py`
12. **Fase 2** — Integration dopo che unit test passano
13. **Fase 3 Smoke** — E2E smoke dopo integration
14. **Fase 4 + 5** — Performance e fuzz in parallelo, non bloccanti

---

*Roadmap Version: 2.2*  
*Aggiornato: 2026-05-13*  
*Basata su: `docs/TEST_SUITE_ARCHITECTURE.md` v2.0, `docs/project-vision.md` v3.3*  
*Vedi anche: `docs/session_handoff.md` per dettagli tecnici della sessione corrente*
