# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-19 — join-network mode in `ap_manager_service`

---

## Stato Corrente in Una Frase

**Test suite completa (57 file, ~3120 test) + tutti i bug di produzione fixati + `ap_manager_service` esteso con join-network mode (26 nuovi test).** Prossimo: coverage report + validazione su device reale.

---

## 2026-05-19 — ap_manager_service: join-network mode

**Cosa cambiato:**

- **`services/ap_manager_service/ap_manager_service.py`** — commit [`23ee37d`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/23ee37d7074e11d16bdbd5005455b5908f65e89a)
  - Aggiunti 3 helper puri: `_detect_existing_wifi()`, `_get_iface_ip()`, `_get_wifi_psk()`
  - `_APRunner` esteso con campo `_mode` (`"ap"` | `"join"` | `None`)
  - `start()` ora sceglie automaticamente il percorso:
    - **join-network**: HU già connessa con PSK disponibile → nessun daemon, nessun cambio interfaccia
    - **ap-mode**: comportamento precedente invariato
  - `stop()` in modalità `"join"`: solo reset stato, zero teardown (no `ip flush`, no NM restart)
  - `is_running()` in modalità `"join"`: `True` finché `_cfg is not None`
  - `get_mode()` esposto per log e test
  - Fallback trasparente ad AP mode se: nessuna rete WiFi attiva, PSK non recuperabile, IP non assegnato, rete enterprise (802.1X)

- **`services/ap_manager_service/tests/test_ap_manager_service.py`** — commit [`c7ac71f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c7ac71f11bc742d4c156476dc31b2defbc6418ba)
  - 26 test case, copertura ≥ 80% del codice nuovo
  - Import del modulo senza D-Bus reale (stub `dbus`, `gi`, `GLib` via `sys.modules`)
  - Classi di test: `TestDetectExistingWifi` (10), `TestGetIfaceIp` (4), `TestGetWifiPsk` (5), `TestAPRunnerJoinNetworkMode` (8), `TestAPRunnerFallbackToAP` (3), `TestAPRunnerAPModeStop` (3), `TestAPRunnerIsRunning` (5)

**Perché:** Evitare la creazione di un AP quando la HU è già connessa ad una rete WiFi con PSK — il telefono si connette alla stessa rete e raggiunge la HU tramite il suo IP locale.

**Status:** Completato ✅

**Prossimi 3 passi:**
1. Aggiornare `docs/session_handoff.md` ✅ (questa entry)
2. Validare su device reale: testare fallback con rete enterprise e rete senza PSK in NM
3. Valutare segnale D-Bus dedicato `APMode(mode: s)` per permettere a `hostapd_helper` di distinguere i due percorsi senza polling

---

## 2026-05-15 — Fix tutti i bug di produzione

**Cosa cambiato:**

- **Bug #1 — `AudioModule._prebuffer_bytes`** non resettato dopo flush
  - Commit: `dea274a` (fatto da utente prima di questa sessione)
  - File: `v2/modules/channel_modules/audio/main.py`

- **Bug #2 — `ServiceDiscovery` `audio_type` perso per ch 5/6**
  - Commit: [`987ffb3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/987ffb3cf61281ebb559e25564f1f9490fd106a6)
  - File: `v2/modules/oaa_control_channel/service_discovery.py`
  - Fix: `channels_from_sdr_bytes()` ora imposta `audio_type` anche quando
    `stream_type == AVStreamType.AUDIO` (fallback per ch 5/6 senza codec).
    Estratto `_AUDIO_CODEC_VALUES` frozenset.

- **Bug #3 — `Logger.Popen` fuori dal `try`**
  - Verificato: già corretto nel codice attuale, nessuna modifica necessaria.

- **Bug #4 — `Logger.exception()` con `sys.exc_info()`**
  - Commit: `1f4f227` (fatto da utente prima di questa sessione)
  - File: `v2/shared/logger.py`

- **Bug #5 — `ChannelManager` sessione vuota in timeout**
  - Commit: `fb5d2a3` (fatto da utente prima di questa sessione)
  - File: `v2/modules/channel_manager/main.py`

- **`docs/KNOWN_PRODUCTION_BUGS.md`** aggiornato con tutti i fix — commit [`9ab40b7`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ab40b7778864bb4f7d87bfee51fe24fc7045262)

**Perchè:** Chiudere tutti i bug noti prima di procedere con il coverage report.

**Status:** Completato ✅

**Prossimi 3 passi:**
1. **Eseguire coverage report**: `pytest --cov=v2 --cov-report=html --cov-report=term-missing`
2. **Top-up test mirati** sui moduli sotto soglia 80%
3. **Verifica CI**: `pytest -m "unit or integration" --cov-fail-under=80` in green

---

## Handoff precedenti (sommario)

| Data | Cosa | Status |
|---|---|---|
| 2026-05-19 | `ap_manager_service` join-network mode + 26 test | ✅ |
| 2026-05-15 | Fix 5 bug produzione da KNOWN_PRODUCTION_BUGS.md | ✅ |
| 2026-05-13 | Fase 5 §2/§3 (chiude test suite) | ✅ |
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

# Test ap_manager_service (no D-Bus richiesto)
python -m pytest services/ap_manager_service/tests/test_ap_manager_service.py -v
```

---

## File Prodotti (tabella completa)

| File | Commit | Test | Note |
|---|---|---|---|
| `services/ap_manager_service/ap_manager_service.py` | [`23ee37d`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/23ee37d7074e11d16bdbd5005455b5908f65e89a) | — | join-network mode |
| `services/ap_manager_service/tests/test_ap_manager_service.py` | [`c7ac71f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c7ac71f11bc742d4c156476dc31b2defbc6418ba) | 26 | stub dbus, no D-Bus richiesto |
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
| `fuzz/test_proto_utils_roundtrip.py` | — | ~10 | Fase 5 §2 |
| `fuzz/test_bus_payload_malformed.py` | — | ~10 | Fase 5 §3 |

**Totale: ~3146 test in 59 file + 3 helper + 3 infra.**

---

## Pattern Architetturali Stabiliti

### BusClient (commit ddd7142)
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### ap_manager_service — join-network mode (2026-05-19)
- `_detect_existing_wifi()` usa `nmcli -t -f active,ssid,bssid,device,security,type device wifi`
- Fallback automatico ad AP mode se: nessuna rete, enterprise (802.1X), no IP, no PSK in NM
- In join mode: **zero teardown** su `stop()` — non toccare l'interfaccia né NM
- Test: stub `dbus`/`gi`/`GLib` via `sys.modules` per eseguire senza D-Bus di sistema

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
```
