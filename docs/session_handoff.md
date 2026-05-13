# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `bd326e5`

---

## Stato Corrente in Una Frase

**27 file di test, 1907 test** su `main`. Fase 1 Unit Test completata. Fase 2 Integration Test avviata con `test_bus_broker.py`.

---

## File Prodotti

| File | Commit | Test | Note |
|---|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | — | Fixture globali |
| `v2/tests/pytest.ini` | `0ff487e` | — | Marker e config pytest |
| `v2/tests/requirements-test.txt` | `0ff487e` | — | Dipendenze test |
| `v2/tests/unit/shared/test_proto_utils.py` | precedente | 47 | |
| `v2/tests/unit/shared/test_bus_client.py` | precedente | 52 | |
| `v2/tests/unit/shared/test_config_client.py` | precedente | 38 | |
| `v2/tests/unit/shared/test_logger.py` | `38a2885` | 88 | |
| `v2/tests/unit/modules/channel_modules/audio/test_audio_module.py` | precedente | 42 | |
| `v2/tests/unit/modules/channel_modules/video/test_video_module.py` | precedente | 38 | |
| `v2/tests/unit/modules/channel_modules/test_base_channel_module.py` | precedente | 44 | |
| `v2/tests/unit/modules/channel_modules/input/test_input_module.py` | `5aaa396` | 88 | keycodes, binding, touch/key dispatch, proto helpers |
| `v2/tests/unit/modules/channel_modules/sensor/test_sensor_module.py` | `d6f8a78` | 84 | driving_status, night_mode, GPS, sensor start request |
| `v2/tests/unit/modules/channel_modules/bluetooth/test_bluetooth_channel_module.py` | `7791347` | 72 | NON-AV: decode_aa_frame dispatch, pairing, auth |
| `v2/tests/unit/modules/channel_modules/wifi/test_wifi_channel_module.py` | `a1fa970` | 72 | NON-AV: credentials, hostapd config, on_hostapd_ready |
| `v2/tests/unit/modules/channel_modules/av_input/test_av_input_module.py` | `0328b84` | 96 | pacat spawn, _start/_stop_stream, drain queue, threading |
| `v2/tests/unit/oaa_control_channel/test_oaa_control_channel_main.py` | `c3d7a4a` | 54 | |
| `v2/tests/unit/oaa_control_channel/test_handshake.py` | `6c99b41` | 62 | |
| `v2/tests/unit/oaa_control_channel/test_serializer.py` | `ffe6314` | 68 | |
| `v2/tests/unit/oaa_control_channel/test_service_discovery.py` | `4e7a28d` | 72 | |
| `v2/tests/unit/modules/channel_manager/test_channel_manager.py` | `4f90f8a` | 78 | |
| `v2/tests/unit/modules/tcp_server/test_tcp_server.py` | `412541a` | 84 | |
| `v2/tests/unit/modules/audio_manager/test_audio_manager.py` | `acb6dce` | 88 | |
| `v2/tests/unit/modules/video_ui/test_video_ui.py` | `83973cb` | 92 | |
| `v2/tests/unit/modules/bluetooth/test_bluetooth_main.py` | `b024893` | 96 | |
| `v2/tests/unit/modules/bluetooth/test_bluez_adapter.py` | `17c7c4d` | 88 | |
| `v2/tests/unit/modules/bluetooth/test_discovery.py` | `a1b5156` | 72 | |
| `v2/tests/unit/modules/bluetooth/test_pairing.py` | `d4973f8` | 84 | |
| `v2/tests/unit/modules/bluetooth/test_paired_devices.py` | `6a83677` | 68 | |
| `v2/tests/unit/modules/config_manager/test_config_manager.py` | `e1c0847` | 96 | |
| `v2/tests/integration/test_bus_broker.py` | `bd326e5` | 84 | **NUOVO** — Fase 2 §1 |

**Totale: 1907 test in 27 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti

### Canali NON-AV (bluetooth, wifi channel modules)
Patern stabilito:
1. `decode_aa_frame()` patchato via `patch.object(_module, "decode_aa_frame")` nei test di dispatch
2. `return_value=None` testa il path malformed-frame
3. `return_value=(msg_id, body)` testa dispatch per specifico messaggio
4. Proto MagicMock iniettato in sys.modules pre-import

### AVInput (threading + subprocess)
1. `subprocess.Popen` patchato per isolare spawn
2. `threading.Thread` patchato per evitare thread reali nei test
3. `_start_stream` / `_stop_stream` patchati con `patch.object` nei test che non li testano direttamente
4. `_send_queue` (SimpleQueue) manipolato direttamente nelle fixture per i test di `_drain_send_queue`

### Integration Tests — Pattern BusClient in-process
1. `_make_client(in_process_broker, name)` — helper locale che monkey-patcha BROKER_PUB_ADDR/BROKER_SUB_ADDR
2. `_start_client(client)` — avvia receive loop non-blocking + sleep 0.05s
3. `_wait_received(list, count, timeout)` — polling con deadline per asserzioni asincrone
4. Tutti i client devono chiamare `.stop()` nel teardown del test
5. `time.sleep(0.1-0.2)` dopo subscribe per attendere propagazione subscription ZMQ

### Logger
Vedi handoff v3.1.

### Helper riutilizzabili (unit test)
```python
def _published_topics(mock_bus) -> list[str]:
    return [c.args[0] for c in mock_bus.publish.call_args_list]

def _published_payload(mock_bus, topic: str) -> dict:
    for c in mock_bus.publish.call_args_list:
        if c.args[0] == topic:
            return c.args[1]
    return {}
```

---

## Stato Roadmap per Fase

| Fase | Stato | Note |
|---|---|---|
| **0 — Infrastruttura** | ✅ Completa | |
| **1 — Unit Test §1.1 Shared** | ✅ Completa | proto_utils, bus_client, config_client, logger ✅ |
| **1 — Unit Test §1.2 Base** | ✅ Completa | base_channel_module ✅ |
| **1 — Unit Test §1.3 Channel Modules** | ✅ Completa | audio, video, base, input, sensor, bluetooth, wifi, av_input ✅ |
| **1 — Unit Test §1.4 Standalone** | ✅ Completa | oaa_cc, tcp, audio_mgr, video_ui, bluetooth, config_manager ✅ |
| **2 — Integration Tests** | 🟡 In corso | `test_bus_broker.py` ✅ (84 test) |
| **3–5** | ❌ Non iniziata | |

---

## Prossimo Passo Immediato

**Fase 2 — `test_channel_lifecycle.py`**

Scope: `channel_manager` + `channel_modules` avviati come thread reali con bus ZMQ in-process condiviso.
Cosa testare:
- `channel_manager` riceve `module_ready_to_start` e avvia i canali nell'ordine corretto
- Canali pubblicano `channel.ready` dopo setup
- `channel_manager` gestisce `channel.error` e risponde con shutdown
- Sequenza `system.start` → canali `READY` entro 5s

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| Stub loguru+zmq pre-import | side-effect all'import: non si può importare senza mock |
| `patch("atexit.register")` durante reload | evita doppio registrazione handler |
| decode_aa_frame patchato in NON-AV dispatch | NON-AV usa raw bytes + decode, non message_id già estratto |
| subprocess.Popen patchato in _start_stream | evita spawn reale di pacat nei test |
| threading.Thread patchato in _start_stream | evita thread reali nei test unit |
| _send_queue manipolato direttamente | SimpleQueue è accessibile senza mock |
| Integration: BusClient monkey-patch indirizzi | no modifica al codice sorgente — test isola tramite fixture |
| Integration: sleep(0.1) dopo subscribe | ZMQ subscription propagation non è sincrona |
| Integration: tolleranza 45/50 su burst test | CI lenta può droppar pochi msg senza che sia un errore |

---

## 2026-05-13 — Fase 2 §1: test_bus_broker.py

**What changed:**
Creato `v2/tests/integration/test_bus_broker.py` — primo file della Fase 2 Integration Tests.

**Why:**
Il broker ZMQ è il prerequisito di tutti gli altri test di integrazione. Va testato per primo e in isolamento prima di coinvolgere i moduli applicativi.

**Status:** Completato — commit `bd326e5`

**Next 1-3 steps:**
1. `test_channel_lifecycle.py` — channel_manager + channel_modules con bus reale
2. `test_audio_pipeline.py` — audio_manager + av_input
3. `test_boot_shutdown.py` — sequenza boot completa

---

*Handoff Version: 4.0*  
*Aggiornato: 2026-05-13*  
*Commit head: `bd326e5`*  
*Test totali scritti: 1907*
