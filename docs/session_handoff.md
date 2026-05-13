# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `2fe07ef`

---

## Stato Corrente in Una Frase

**37 file di test, ~2495 test** su `main`. Fase 0, 1 e **2 completamente chiuse**. Prossimo: Fase 3 E2E Smoke.

---

## File Prodotti

| File | Commit | Test | Note |
|---|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | — | Fixture globali |
| `v2/tests/pytest.ini` | `0ff487e` | — | Marker e config pytest |
| `v2/tests/requirements-test.txt` | `0ff487e` | — | Dipendenze test |
| `v2/tests/unit/shared/test_proto_utils.py` | precedente | 47 | |
| `v2/tests/unit/shared/test_bus_client.py` | `db85dc8` | ~75 | AGGIORNATO — fix _trace, BusTracer mock, nuovi test |
| `v2/tests/unit/shared/test_config_client.py` | precedente | 38 | |
| `v2/tests/unit/shared/test_logger.py` | `38a2885` | 88 | |
| `v2/tests/unit/shared/test_bus_trace.py` | `be3068e` | 72 | BusTracer |
| `v2/tests/unit/modules/channel_modules/audio/test_audio_module.py` | precedente | 42 | |
| `v2/tests/unit/modules/channel_modules/video/test_video_module.py` | precedente | 38 | |
| `v2/tests/unit/modules/channel_modules/test_base_channel_module.py` | precedente | 44 | |
| `v2/tests/unit/modules/channel_modules/input/test_input_module.py` | `5aaa396` | 88 | |
| `v2/tests/unit/modules/channel_modules/sensor/test_sensor_module.py` | `d6f8a78` | 84 | |
| `v2/tests/unit/modules/channel_modules/bluetooth/test_bluetooth_channel_module.py` | `7791347` | 72 | |
| `v2/tests/unit/modules/channel_modules/wifi/test_wifi_channel_module.py` | `a1fa970` | 72 | |
| `v2/tests/unit/modules/channel_modules/av_input/test_av_input_module.py` | `0328b84` | 96 | |
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
| `v2/tests/unit/modules/zmq_trace/test_zmq_trace.py` | `cdc9e6f` | 68 | zmq_trace module |
| `v2/tests/integration/test_bus_broker.py` | `bd326e5` | 84 | Fase 2 §1 |
| `v2/tests/integration/test_channel_lifecycle.py` | `1734764` | 88 | Fase 2 §2 |
| `v2/tests/integration/test_audio_manager.py` | `7e1d9be` | ~47 | Fase 2 §3 |
| `v2/tests/integration/test_config_manager.py` | `9f08aa6` | ~50 | Fase 2 §4 |
| `v2/tests/integration/test_video_pipeline.py` | `2d8d861` | ~60 | Fase 2 §5 |
| `v2/tests/integration/test_bluetooth_flow.py` | `dbbc2b4` | ~60 | Fase 2 §6 |
| `v2/tests/integration/test_boot_shutdown.py` | `2fe07ef` | ~65 | **NUOVO** — Fase 2 §7 |

**Totale: ~2495 test in 37 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti

### BusClient — importante cambio (commit ddd7142)
- `publish()` ora inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `_handle_received_message()` rimuove `_trace` prima di consegnare all'handler
- `publish()` ritorna `bool`: `True` = ok, `False` = dropped
- `BUS_HWM` alzato a 5000
- `BusTracer` istanziato in `__init__` — **va sempre mockato** nei test unit con `patch("shared.bus_client.BusTracer", return_value=MagicMock())`

### Pattern mock BusTracer nei test unit
```python
mock_tracer = MagicMock()
with patch("shared.bus_client.zmq.Context", return_value=ctx):
    with patch("shared.bus_client.BusTracer", return_value=mock_tracer):
        client = BusClient(module_name="test_unit")
```

### _FakeSocket — aggiunto getsockopt()
I test unit di BusClient richiedono che `_FakeSocket` implementi `getsockopt()` (usato da BusTracer emit calls).

### BusTracer test pattern
```python
def _make_tracer(enabled=True, module_name="test"):
    from shared.bus_trace import BusTracer
    with patch("shared.bus_trace.TRACE_ENABLED", enabled):
        with patch.object(BusTracer, "_drain_loop", return_value=None):
            return BusTracer(module_name=module_name)
```

### zmq_trace test pattern
```python
def _load_module():
    # Stub shared.bus_client, config_client, logger, config_schema
    # Reload zmq_trace.main per test isolation
    # Attach mock_bus as mod._mock_bus for assertions
```

### Canali NON-AV (bluetooth, wifi channel modules)
1. `decode_aa_frame()` patchato via `patch.object(_module, "decode_aa_frame")` nei test di dispatch
2. `return_value=None` testa il path malformed-frame
3. `return_value=(msg_id, body)` testa dispatch per specifico messaggio
4. Proto MagicMock iniettato in sys.modules pre-import

### AVInput (threading + subprocess)
1. `subprocess.Popen` patchato per isolare spawn
2. `threading.Thread` patchato per evitare thread reali nei test
3. `_start_stream` / `_stop_stream` patchati con `patch.object`
4. `_send_queue` manipolato direttamente

### Integration Tests — Pattern BusClient in-process
1. `_make_client(in_process_broker, name)` — monkey-patcha BROKER_PUB_ADDR/BROKER_SUB_ADDR **+ BusTracer mock**
2. `_start_client(client)` — avvia receive loop non-blocking + sleep 0.05s
3. `_wait(list, count, timeout)` — polling con deadline
4. Tutti i client chiamano `.stop()` nel teardown
5. `time.sleep(0.1-0.2)` dopo subscribe per propagazione ZMQ

### Integration Tests — Pattern ChannelManagerSession
1. `importlib.reload(cm_main)` ad ogni test per garantire bus fresco con indirizzi in-process
2. Launcher sempre MagicMock — nessun subprocess reale nei test di integrazione (layer 2)
3. `_make_session()` incapsula reload + ChannelManagerSession()
4. Handler on_* testati via chiamata diretta + spy sul bus per verificare i topic pubblicati
5. `cm_main.CHILDREN_READY_TIMEOUT` patchato a 0.3s per test di timeout

### Integration Tests — Pattern audio_manager (Fase 2 §3)
1. `subprocess.run` patchato con `_fake_subprocess_run` che simula output wpctl/pactl
2. `importlib.reload(am_main)` per ogni test — bus fresco e stato modulo pulito
3. `am.cfg = MagicMock()` per isolare ConfigClient da config_manager reale
4. Handler on_* chiamati direttamente; spy BusClient per verificare topic pubblicati
5. Test negativi verificano assenza di pubblicazioni con `time.sleep(0.2)` + assert `len == 0`

### Integration Tests — Pattern config_manager (Fase 2 §4)
1. `_load_cm(broker, config_dir)` — reload `config_manager.main`, patcha bus + BusTracer, sovrascrive `CONFIG_DIR` con `tmp_path`
2. `tmp_path` (pytest fixture) — ogni test ha directory YAML isolata; nessun side-effect tra test
3. Handler `on_config_get` / `on_config_set` chiamati direttamente — bus spy riceve i topic pubblicati
4. Schema registrato tramite `on_config_get` con `schema=` payload, poi verificato nell'echo della response
5. Validazione schema testata end-to-end: valore invalido → `config.error` pubblicato, YAML non scritto
6. Campi strutturati (ConfigFieldList, ConfigFieldMessage) testati: nessuna validazione scalare, stored as-is

### Integration Tests — Pattern video_pipeline (Fase 2 §5)
1. PyQt6 / GStreamer / gi stubbed in `sys.modules` **pre-import** con MagicMock — nessuna finestra Qt in CI
2. `_load_video_ui(broker)` — reload `video_ui.main` + BusTracer mock + `_window=None`
3. `_make_video_module(broker)` — reload `channel_modules/video/main.py` + `VideoModule()` istanziata
4. `_handle_*` chiamati direttamente su VideoModule; spy BusClient verifica topic pubblicati
5. `_conn_state` di video_ui testato come state machine (WAITING_BT → HANDSHAKE → STREAMING → INTERRUPTED)
6. `publish_frames=False` testato esplicitamente per verificare soppressione video.frame
7. Wire format `_media_with_ts_bytes()` helper per costruire payload MediaWithTimestamp senza dipendenze proto

### Integration Tests — Pattern bluetooth_flow (Fase 2 §6)
1. D-Bus / BlueZ / GLib / `gi` stubbed in `sys.modules` **pre-import** — nessun hardware BT in CI
2. `_load_bt(broker)` — reload `modules/bluetooth/main.py` + patch `sys.modules` per submoduli BT
3. `BluezAdapter`, `DiscoverySession`, `PairingAgent`, `paired_devices` — tutti MagicMock istanziati in `_load_bt`
4. `bt._adapter` pre-iniettato con mock_adapter per evitare "Adapter not ready" nei test di layer 2
5. Callback interni (`_on_device_found`, `_on_pairing_completed`, etc.) chiamati direttamente — spy verifica topic bus
6. Autoconnect testato: `_autoconnect_stop` verificato direttamente, loop inline con `_autoconnect_stop.set()`
7. Config callbacks (`_on_config_loaded` / `_on_config_changed`) chiamati direttamente con `_apply_config` patchato

### Integration Tests — Pattern boot_shutdown (Fase 2 §7)
1. `FakeModule(broker, name, priority)` — mini-classe che implementa il boot protocol sul bus ZMQ reale
2. `BootOrchestrator(broker)` — raccoglie `system.module_ready`, invia `system.start`, raccoglie `system.ready`
3. Handler `_on_readytostart` / `_on_system_start` / `_on_system_stop` implementati in FakeModule
4. `_load_cm(broker)` — reload channel_manager.main con indirizzi in-process per test E2E con modulo reale
5. Test di timing: late subscriber non riceve readytostart (ZMQ no replay)
6. Moduli con stessa priority rispondono entrambi a `system.start {priority}`
7. Boot sequenziale verificato: start p1 → wait ready p1 → start p2 → wait ready p2 (in ordine)

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
| **1 — Unit Test §1.1 Shared** | ✅ Completa | Include test_bus_trace.py |
| **1 — Unit Test §1.2 Base** | ✅ Completa | |
| **1 — Unit Test §1.3 Channel Modules** | ✅ Completa | |
| **1 — Unit Test §1.4 Standalone** | ✅ Completa | Include test_zmq_trace.py |
| **2 — Integration Tests** | ✅ **COMPLETATA** (7/7) | broker ✅ + lifecycle ✅ + audio ✅ + config ✅ + video ✅ + bluetooth ✅ + boot_shutdown ✅ |
| **3 — E2E Smoke** | ❌ **PROSSIMO** | `test_bt_connect_to_handshake.py` |
| **4–5** | ❌ Non iniziata | |

---

## Prossimo Passo Immediato

**Fase 3 — E2E Smoke: `test_bt_connect_to_handshake.py`**

Scope: test E2E smoke che simula il flow completo BT connect → RFCOMM handshake → AA session su bus in-process.
Marker `@pytest.mark.e2e_smoke`, target < 30s.

Cosa testare:
- BT device connesso → `bt.device_connected` pubblicato
- rfcomm_handshake riceve evento e avvia handshake RFCOMM
- Handshake OK → `rfcomm.handshake_complete` → oaa_control_channel si avvia
- AA session attiva → `aa.session.active` pubblicato

**File da leggere prima**: `v2/modules/rfcomm_handshake/main.py`, `v2/modules/oaa_control_channel/main.py`

**Prerequisito**: helper `e2e/helpers/` (phone_mock, frame_sequences, stack_launcher) da creare prima.

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| Stub loguru+zmq pre-import | side-effect all'import |
| `patch("atexit.register")` durante reload | evita doppio registrazione handler |
| decode_aa_frame patchato in NON-AV dispatch | NON-AV usa raw bytes + decode |
| subprocess.Popen patchato in _start_stream | evita spawn reale di pacat |
| threading.Thread patchato in _start_stream | evita thread reali nei test unit |
| _send_queue manipolato direttamente | SimpleQueue accessibile senza mock |
| Integration: BusClient monkey-patch indirizzi + BusTracer mock | no modifica al codice sorgente, no thread trace spurii |
| Integration: sleep(0.1) dopo subscribe | ZMQ subscription propagation non sincrona |
| Integration: tolleranza 45/50 su burst test | CI lenta può droppar pochi msg |
| Integration: importlib.reload per ogni test | garantisce bus ZMQ fresco con socket in-process |
| Integration: Launcher mockato in Fase 2 | nessun subprocess reale — test layer 2 puro |
| BusTracer mock in TUTTI i test unit | BusTracer lancia thread drain; mock evita thread spurii e socket ZMQ nei test unit |
| Integration audio_manager: subprocess.run patchato | wpctl/pactl non disponibili in CI |
| Integration audio_manager: cfg=MagicMock() | isola ConfigClient; config_manager non avviato in layer 2 |
| Integration config_manager: CONFIG_DIR=tmp_path | ogni test ha filesystem isolato; no side-effect tra test |
| Integration config_manager: campi strutturati stored as-is | ConfigFieldList/Message saltano validate_value(); testato esplicitamente |
| Integration video_pipeline: PyQt6/GStreamer/gi stubbed in sys.modules | nessun display/hardware in CI |
| Integration video_pipeline: _conn_state testato come state machine | verifica coerenza transizioni senza finestra Qt |
| Integration video_pipeline: _media_with_ts_bytes() helper | costruisce wire format senza importare protobuf reale |
| Integration bluetooth_flow: D-Bus/BlueZ/GLib/gi stubbed in sys.modules | nessun hardware BT in CI |
| Integration bluetooth_flow: bt._adapter pre-iniettato | evita "Adapter not ready" nei test handler layer 2 |
| Integration bluetooth_flow: autoconnect loop inline con stop event set | testa logica loop senza thread reali |
| Integration bluetooth_flow: callback _on_* chiamati direttamente | verifica pubblicazione topic senza GLib event loop |
| Integration boot_shutdown: FakeModule + BootOrchestrator come actor sul bus reale | nessun mock del bus — verifica comportamento wire reale |
| Integration boot_shutdown: test late subscriber | verifica che ZMQ non faccia replay dei messaggi |
| Integration boot_shutdown: stessa priority = entrambi rispondono | comportamento atteso da moduli UI paralleli |

---

## 2026-05-13 — Fase 2 §7: test_boot_shutdown integration

**What changed:**
`v2/tests/integration/test_boot_shutdown.py` creato con ~65 test in 6 gruppi:
1. Single module boot protocol (readytostart, system.start correct/wrong priority, system.stop, no early module_ready)
2. Multi-module boot sequential priorities (all respond to readytostart, sequential start per priority, all receive stop, 5 modules, same-priority both respond)
3. channel_manager boot reale (announce priority 2, system.ready on start, ignore wrong priority, stop cleans session, stop no session no crash)
4. Full boot sequence (2 moduli sequential, boot+stop, ordered by priority, readytostart before modules=no reply, late subscriber misses)
5. Shutdown protocol (stop before start, stop after partial boot, double stop no crash, stop without readytostart, stopped flag)
6. Boot E2E con channel_manager reale + FakeModules (cm partecipa al boot, stop cleanup session, priority 2 announce, full e2e boot+stop)

**Why:**
Fase 2 Integration Test §7 — ultimo file integration. Verifica il boot protocol dell'intero sistema:
orchestrazione multi-modulo, sequenza per priority level, shutdown ordinato, edge cases timing ZMQ,
con bus ZMQ reale in-process e channel_manager reale come actor principale.

**Status:** Completato — commit `2fe07ef` — **FASE 2 COMPLETATA** ✅

**Next 1-3 steps:**
1. Creare `e2e/helpers/` (phone_mock, frame_sequences, stack_launcher)
2. `test_bt_connect_to_handshake.py` — Fase 3 E2E Smoke §1
3. Coverage report — verificare soglia 80% su tutti i moduli prima di Fase 3

---

## 2026-05-13 — Fase 2 §6: test_bluetooth_flow integration

**What changed:**
`v2/tests/integration/test_bluetooth_flow.py` creato con ~60 test in 8 gruppi.

**Status:** Completato — commit `dbbc2b4`

---

## 2026-05-13 — Fase 2 §5: test_video_pipeline integration

**What changed:**
`v2/tests/integration/test_video_pipeline.py` creato con ~60 test in 10 gruppi.

**Status:** Completato — commit `2d8d861`

---

## 2026-05-13 — Fase 2 §4: test_config_manager integration

**What changed:**
`v2/tests/integration/test_config_manager.py` creato con ~50 test in 8 gruppi.

**Status:** Completato — commit `9f08aa6`

---

## 2026-05-13 — Fase 2 §3: test_audio_manager integration

**What changed:**
`v2/tests/integration/test_audio_manager.py` creato con ~47 test in 6 gruppi.

**Status:** Completato — commit `7e1d9be`

---

## 2026-05-13 — Fix test_bus_client + Nuovi test trace

**What changed:**
`test_bus_client.py` aggiornato, `test_bus_trace.py` e `test_zmq_trace.py` creati.

**Status:** Completato — commit `cdc9e6f`

---

*Handoff Version: 4.7*  
*Aggiornato: 2026-05-13*  
*Commit head: `2fe07ef`*  
*Test totali scritti: ~2495*
