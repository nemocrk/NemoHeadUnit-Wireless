# Roadmap Test Suite — NemoHeadUnit-Wireless v2

> Documento di pianificazione per la costruzione completa della test suite di `v2/`.
> Basato su `docs/TEST_SUITE_ARCHITECTURE.md` v2.0 e `docs/project-vision.md` v3.3.
> Data di creazione: 2026-05-13

---

## Executive Summary

La test suite di `v2/` è attualmente quasi assente: esistono 3 file di test nella root di `v2/tests/` che coprono parzialmente `oaa_control_channel`, `config_manager` e `tcp_server`, ma mancano completamente l'infrastruttura condivisa (`conftest.py`, `pytest.ini`), la struttura a livelli (`unit/`, `integration/`, `e2e/`, `performance/`, `fuzz/`) e i test per tutti gli altri moduli.

L'obiettivo è raggiungere **≥ 80% di coverage globale** su `v2/` — soglia che blocca il merge in CI — seguendo l'architettura stratificata definita in `TEST_SUITE_ARCHITECTURE.md`.

---

## Fase 0 — Infrastruttura (prerequisito bloccante)

Nessun test può girare correttamente senza queste fondamenta. Va completata prima di qualsiasi altro lavoro.

| File | Descrizione | Priorità |
|---|---|---|
| `v2/tests/conftest.py` | Fixture globali: `in_process_broker`, `bus_client`, `mock_bus`, `aa_frame_factory`, `qt_app`, `dbus_session`, `hardware_available()` | 🔴 Bloccante |
| `v2/tests/pytest.ini` | Marker registrati, `testpaths`, `addopts` con `--strict-markers` e `-q` | 🔴 Bloccante |
| `v2/tests/requirements-test.txt` | `pytest>=8.0`, `pytest-cov>=5.0`, `pytest-asyncio>=0.23`, `pytest-timeout>=2.3`, `hypothesis>=6.100`, `dbus-python>=1.3` | 🔴 Bloccante |

### Dettaglio fixture `conftest.py`

- **`in_process_broker`** — broker ZMQ XPUB/XSUB reale su socket IPC univoci (`ipc:///tmp/nemotest-{uuid}.{pub|sub}`), teardown con poison pill + join thread timeout 2s
- **`bus_client`** — `BusClient` connesso al broker in-process; espone `publish`, `subscribe`, `wait_for`
- **`mock_bus`** — mock leggero senza socket ZMQ, registra tutte le chiamate per asserzioni
- **`aa_frame_factory`** — factory per frame AA wire-format validi e invalidi (setup_request, channel_open_request, av_media_with_timestamp, h264_idr_frame, malformed)
- **`qt_app`** — `QApplication` con `QT_QPA_PLATFORM=offscreen`, scope session
- **`dbus_session`** — D-Bus session dedicata per test bluetooth

---

## Fase 1 — Unit Test (backlog prioritario)

Target: **< 1s per test**, coverage ≥ 80%, marker `@pytest.mark.unit`.

### 1.1 Shared

| File target | Modulo sotto test | Test prioritari |
|---|---|---|
| `unit/shared/test_bus_client.py` | `v2/shared/bus_client.py` | connect/disconnect, publish, subscribe, wait_for timeout, reconnect |
| `unit/shared/test_proto_utils.py` | `v2/shared/proto_utils.py` | encode/decode round-trip, malformed input |
| `unit/shared/test_logger.py` | logger shared | verbosity levels, output format |

### 1.2 Channel Modules — Base

| File target | Modulo sotto test | Test prioritari |
|---|---|---|
| `unit/modules/channel_modules/test_base_channel_module.py` | `base_channel_module.py` (17 KB) | state machine transitions, setup_request handling, open/close lifecycle, error paths |

> ⚠️ **Priorità alta**: `base_channel_module.py` è ereditato da tutti i canali — ogni test qui copre logica comune a `audio`, `av_input`, `video`, `bluetooth`, `input`, `sensor`, `wifi`.

### 1.3 Channel Modules — Specifici

| File target | Modulo | Dimensione | Test prioritari |
|---|---|---|---|
| `unit/modules/channel_modules/av_input/test_av_input.py` | `av_input/main.py` (17 KB) | round-trip proto_utils, `handle_setup_request`, mic callback, codec negotiation |
| `unit/modules/channel_modules/audio/test_audio.py` | `audio/main.py` (27 KB) | codec_data capture, decode prepend, prebuffer flush, sink selection |
| `unit/modules/channel_modules/video/test_video.py` | `video/main.py` | frame decode, state machine, GStreamer mock |
| `unit/modules/channel_modules/bluetooth/test_bluetooth_channel.py` | `channel_modules/bluetooth/main.py` | setup, open, teardown |
| `unit/modules/channel_modules/input/test_input.py` | `channel_modules/input/main.py` | touch event routing |
| `unit/modules/channel_modules/sensor/test_sensor.py` | `channel_modules/sensor/main.py` | sensor data publish |
| `unit/modules/channel_modules/wifi/test_wifi.py` | `channel_modules/wifi/main.py` | setup, credential exchange |

### 1.4 Moduli Standalone

| File target | Modulo | Test prioritari |
|---|---|---|
| `unit/modules/oaa_control_channel/test_handshake.py` | `oaa_control_channel/` | 4 handler ch0: `audio_focus`, `nav_focus`, `voice_session`, `battery` |
| `unit/modules/channel_manager/test_channel_manager.py` | `channel_manager/` | autodiscovery canali, sequenza start, `module_ready_to_start`, stop ordinato |
| `unit/modules/audio_manager/test_audio_manager.py` | `audio_manager/` | `audio.sink.selected`, routing mic, fallback sink |
| `unit/modules/video_ui/test_video_ui.py` | `video_ui/` | pipeline build, decoder selection (GStreamer/ffplay), state machine transitions |
| `unit/modules/bluetooth/test_paired_devices.py` | `bluetooth/` | list devices, connect watchdog, `AlreadyConnected`, remove |
| `unit/modules/bluetooth/test_pairing.py` | `bluetooth/` | GLib non-blocking, auto-accept timeout |
| `unit/modules/bluetooth/test_autoconnect.py` | `bluetooth/` | stop on rfcomm, no duplicate start, skip connected |
| `unit/modules/bluetooth_ui/test_bluetooth_ui.py` | `bluetooth_ui/` | list populate, button states, remove confirm |
| `unit/modules/config_manager/test_config_manager.py` | `config_manager/` | load YAML, validate, defaults, update |
| `unit/modules/tcp_server/test_tcp_server.py` | `tcp_server/` | session lifecycle, restart, timeout |
| `unit/modules/rfcomm_handshake/test_rfcomm_handshake.py` | `rfcomm_handshake/` | handshake sequence, error handling |
| `unit/modules/hostapd_helper/test_hostapd_helper.py` | `hostapd_helper/` | start/stop, config generation |
| `unit/modules/config_ui/test_config_ui.py` | `config_ui/` | form binding, save/cancel |
| `unit/modules/log_viewer/test_log_viewer.py` | `log_viewer/` | log ingestion, filter, scroll |

---

## Fase 2 — Integration Test

Target: **< 10s per test**, marker `@pytest.mark.integration`. Bus ZMQ reale in-process condiviso tra moduli avviati come thread.

| File target | Scope | Cosa verifica |
|---|---|---|
| `integration/test_bus_broker.py` | broker + multi-client | pub/sub multi-client, latenza, reconnect |
| `integration/test_channel_lifecycle.py` | `channel_manager` + `channel_modules` | `module_ready_to_start` → canali avviati nell'ordine corretto |
| `integration/test_audio_pipeline.py` | `audio_manager` + `audio` + `av_input` | `audio.sink.selected` → `AudioModule` configurato |
| `integration/test_video_pipeline.py` | `video` + `video_ui` | frame H.264 → render corretto |
| `integration/test_bluetooth_flow.py` | `bluetooth` + `bluetooth_ui` | pairing → UI aggiornata |
| `integration/test_boot_shutdown.py` | `main.py` completo | `system.start` → moduli attivi → `system.stop` → `channel_manager.stopped` → terminate |

---

## Fase 3 — E2E Test

### Smoke (`@pytest.mark.e2e_smoke`) — run in CI, < 30s

| File target | Flusso coperto |
|---|---|
| `e2e/smoke/test_bt_connect_to_handshake.py` | `bluetooth.rfcomm.connected` → `channel_manager` boot → `ACTIVE` |
| `e2e/smoke/test_channel_manager_boot.py` | `system.start` → tutti i canali `READY` entro 5s |
| `e2e/smoke/test_audio_path_smoke.py` | `SETUP_REQUEST` → `OPEN` → primo frame PCM ricevuto da pacat mock |

Richiede i seguenti helper in `e2e/helpers/`:
- `phone_mock.py` — simulatore telefono AA wire protocol
- `frame_sequences.py` — sequenze frame predefinite
- `stack_launcher.py` — avvia/stoppa stack v2 come subprocess

### Full Session (`@pytest.mark.e2e_full`) — nightly/on-demand, > 60s

| File target | Flusso coperto |
|---|---|
| `e2e/full_session/test_full_aa_session.py` | BT connect → handshake → video H.264 + audio AAC 30s → disconnect |
| `e2e/full_session/test_session_recovery.py` | Crash channel_module durante stream → restart → seconda sessione OK |

---

## Fase 4 — Performance Benchmark

Marker `@pytest.mark.performance`. **Non bloccano il merge** — solo informativi. Output in `tests/reports/perf-{YYYYMMDD}-{commit}.json`.

| File target | Metrica | Riferimento vision |
|---|---|---|
| `performance/test_bus_latency.py` | publish→receive p50/p95/p99 ms | broker overhead: zero Python per hop |
| `performance/test_bus_throughput.py` | msg/s, MB/s (1→1, 1→3, 3→1) | — |
| `performance/test_audio_latency.py` | frame AAC → PCM pronto p50/p95/p99 | ≤ 10ms |
| `performance/test_video_frame_rate.py` | fps effettivi, frame droppati, jitter ms | ≥ 30fps |
| `performance/test_memory_rss.py` | RSS baseline/+5min/+30min, CPU p95 durante stream | ottimizzato Atom |
| `performance/test_aa_frame_decode.py` | encode+decode round-trip p50/p95/p99 µs | — |

---

## Fase 5 — Fuzz Test

Marker `@pytest.mark.fuzz`. Motore: `hypothesis`. Profili: `ci` (100 esempi), `local` (1000), `nightly` (10000).

| File target | Target | Invariante |
|---|---|---|
| `fuzz/test_aa_wire_format.py` | `decode_aa_frame(bytes arbitrari)` | Nessuna eccezione non documentata |
| `fuzz/test_proto_utils_roundtrip.py` | encode/decode + build/parse media_with_timestamp | `decode(encode(x)) == x` |
| `fuzz/test_bus_payload_malformed.py` | payload JSON malformato, topic vuoto, frame incompleto | Nessun crash, nessun warning "invalid JSON" |

---

## Riepilogo Generale

| Fase | File da creare | Blocca CI | Stato |
|---|---|---|---|
| 0 — Infrastruttura | 3 | ✅ Sì | ❌ Todo |
| 1 — Unit Test | ~25 | ✅ Sì (coverage ≥ 80%) | ❌ Todo |
| 2 — Integration | 6 | ✅ Sì | ❌ Todo |
| 3 — E2E Smoke | 3 + 3 helper | ✅ Sì | ❌ Todo |
| 3 — E2E Full | 2 | ❌ No (nightly) | ❌ Todo |
| 4 — Performance | 6 | ❌ No (informativi) | ❌ Todo |
| 5 — Fuzz | 3 | ❌ No | ❌ Todo |
| **Totale** | **~51 file** | — | — |

## Ordine di Esecuzione Raccomandato

1. **Fase 0** — `conftest.py` + `pytest.ini` + `requirements-test.txt`
2. **Fase 1 §1.2** — `test_base_channel_module.py` (massimo ROI: copre logica comune a tutti i canali)
3. **Fase 1 §1.3** — `av_input` → `audio` → `video` (backlog prioritario)
4. **Fase 1 §1.4** — `oaa_control_channel` → `channel_manager` → `bluetooth` → `audio_manager` → `video_ui`
5. **Fase 1 §1.1** — `shared/` (bus_client, proto_utils, logger)
6. **Fase 1 §1.4 secondario** — `config_manager`, `tcp_server`, `rfcomm_handshake`, `hostapd_helper`, `config_ui`, `log_viewer`
7. **Fase 2** — Integration test una volta che i unit test passano
8. **Fase 3 Smoke** — E2E smoke dopo integration
9. **Fase 4 + 5** — Performance e fuzz in parallelo, non bloccanti

---

*Roadmap Version: 1.0*
*Creata: 2026-05-13*
*Basata su: `docs/TEST_SUITE_ARCHITECTURE.md` v2.0, `docs/project-vision.md` v3.3*
