# Test Suite Architecture: NemoHeadUnit-Wireless v2

> **Versione**: 1.0  
> **Data**: 2026-05-13  
> **Autore**: Nemo Development Team  
> **Riferimento**: [`docs/project-vision.md`](project-vision.md) — §7.1 Quality Metrics, §15.x v2 Guidelines

---

## Panoramica

Questo documento definisce l'architettura completa, il flusso di lavoro, i componenti e i criteri di completamento della test suite di NemoHeadUnit-Wireless v2. La suite è strutturata in quattro livelli gerarchici — **unit → integration → e2e → performance** — ciascuno con scope, fixture, marcatori e criteri di uscita distinti.

La suite è progettata per:
- Girare in CI senza hardware fisico (usando mock e parametrizzazione)
- Girare su hardware reale quando disponibile (stesso test case, sorgente diversa)
- Coprire robustezza, correttezza funzionale, flussi end-to-end e prestazioni
- Rispettare il contratto architetturale v2 (nessun import diretto tra moduli, tutto via bus ZMQ)

---

## 1. Struttura delle Directory

La suite vive dentro `v2/` per rispecchiare la struttura della codebase target. Quando la codebase migrerà alla root, i test migrano con essa senza modifiche ai path relativi.

```
v2/
├── tests/
│   ├── conftest.py                        ← fixture globali condivise
│   ├── pytest.ini                         ← configurazione runner, marker registrati
│   ├── requirements-test.txt              ← dipendenze test (pytest, hypothesis, ecc.)
│   │
│   ├── unit/                              ← Livello 1: test isolati, zero I/O reale
│   │   ├── conftest.py                    ← fixture specifiche unit (mock bus leggero)
│   │   ├── shared/
│   │   │   ├── test_proto_utils.py
│   │   │   ├── test_logger.py
│   │   │   └── test_bus_client.py
│   │   └── modules/
│   │       ├── channel_modules/
│   │       │   ├── test_base_channel_module.py
│   │       │   ├── test_audio.py
│   │       │   ├── test_av_input.py
│   │       │   └── test_video.py
│   │       ├── oaa_control_channel/
│   │       │   └── test_handshake.py
│   │       ├── bluetooth/
│   │       │   ├── test_paired_devices.py
│   │       │   ├── test_pairing.py
│   │       │   └── test_autoconnect.py
│   │       ├── audio_manager/
│   │       │   └── test_audio_manager.py
│   │       ├── video_ui/
│   │       │   └── test_video_ui.py
│   │       ├── bluetooth_ui/
│   │       │   └── test_bluetooth_ui.py
│   │       └── channel_manager/
│   │           └── test_channel_manager.py
│   │
│   ├── integration/                       ← Livello 2: più moduli reali, bus in-process
│   │   ├── conftest.py                    ← fixture bus reale, broker in-process
│   │   ├── test_bus_broker.py
│   │   ├── test_channel_manager_boot.py
│   │   ├── test_audio_pipeline.py
│   │   ├── test_av_input_pipeline.py
│   │   ├── test_video_pipeline.py
│   │   ├── test_bluetooth_flow.py
│   │   └── test_shutdown_sequence.py
│   │
│   ├── e2e/                               ← Livello 3: sessione AA simulata
│   │   ├── conftest.py                    ← fixture stack completo, phone mock
│   │   ├── smoke/                         ← e2e veloci (< 30s totali)
│   │   │   ├── test_bt_connect_smoke.py
│   │   │   ├── test_handshake_smoke.py
│   │   │   └── test_channel_open_smoke.py
│   │   └── full/                          ← e2e completi (possono richiedere minuti)
│   │       ├── test_full_aa_session.py
│   │       ├── test_audio_session.py
│   │       └── test_video_session.py
│   │
│   └── performance/                       ← Livello 4: benchmark, nessun assert hard
│       ├── conftest.py                    ← fixture benchmark, reporter JSON
│       ├── test_bus_throughput.py
│       ├── test_bus_latency.py
│       ├── test_audio_latency.py
│       ├── test_video_frame_rate.py
│       ├── test_memory_rss.py
│       └── test_aa_frame_decode.py
│
└── ... (codebase v2 esistente)
```

---

## 2. Configurazione Runner

### `v2/tests/pytest.ini`

```ini
[pytest]
testpaths = v2/tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

markers =
    unit: test isolato, nessun I/O reale (deselect con -m "not unit")
    integration: più moduli reali con bus in-process
    e2e: flusso end-to-end con stack completo o phone mock
    e2e_smoke: subset e2e veloci (< 30s totali)
    e2e_full: sessione AA completa, può richiedere minuti
    performance: benchmark informativi, non falliscono per soglie
    hardware: richiede hardware fisico (audio, BT, display)
    qt: richiede QApplication offscreen
    dbus: richiede sessione D-Bus di test
    slow: atteso > 10s
    fuzz: property-based con Hypothesis

filterwarnings =
    ignore::DeprecationWarning:zmq.*
    ignore::ResourceWarning

addopts =
    --strict-markers
    --tb=short
    -q
```

### Comandi di esecuzione per livello

```bash
# Solo unit test (CI fast path, < 60s attesi)
pytest -m unit

# Unit + integration (CI standard)
pytest -m "unit or integration"

# E2E smoke (CI pre-merge)
pytest -m e2e_smoke

# E2E full (nightly / hardware disponibile)
pytest -m e2e_full

# Performance benchmark (genera report JSON)
pytest -m performance --benchmark-json=reports/perf-$(date +%Y%m%d).json

# Tutto tranne hardware (CI senza device fisici)
pytest -m "not hardware"

# Solo hardware (esecuzione manuale su target)
pytest -m hardware

# Run completo con coverage
pytest --cov=v2 --cov-report=html --cov-fail-under=80
```

---

## 3. Fixture Globali — `conftest.py` Root

Il `conftest.py` radice (`v2/tests/conftest.py`) espone le fixture condivise da tutti i livelli.

### 3.1 Bus Fixtures

```python
# Descrizione funzionale — il codice implementativo va in conftest.py

@pytest.fixture(scope="session")
def zmq_context():
    """Contesto ZMQ condiviso per l'intera sessione di test.
    Scope session: un solo contesto per tutti i test, evita overhead di creazione."""

@pytest.fixture(scope="function")
def in_process_broker(zmq_context):
    """Broker XPUB/XSUB reale in-process su socket ipc:// temporanee univoche.
    Ogni test riceve broker su path unico (tmpdir) → zero interferenza tra test paralleli.
    Teardown: invia poison pill al broker thread, attende join con timeout 2s."""

@pytest.fixture(scope="function")
def bus_client(in_process_broker):
    """BusClient connesso al broker in-process.
    Espone: publish(topic, payload), subscribe(topic, callback), wait_for(topic, timeout)."""

@pytest.fixture(scope="function")
def mock_bus():
    """Mock leggero del bus per unit test che non richiedono ZMQ reale.
    Registra tutte le chiamate publish/subscribe per asserzioni.
    Non avvia socket, non crea thread."""
```

### 3.2 Config Fixtures

```python
@pytest.fixture
def mock_config(tmp_path):
    """Config YAML minimale in tmp_path per ogni modulo.
    Parametrizzabile: mock_config({'audio': {'sample_rate': 48000}})."""

@pytest.fixture
def config_loader(mock_config):
    """Istanza del config loader v2 puntata al tmp_path."""
```

### 3.3 AA Frame Fixtures

```python
@pytest.fixture
def aa_frame_factory():
    """Factory per costruire frame AA wire-format validi.
    
    Metodi esposti:
    - setup_request(channel_id, config) → bytes
    - channel_open_request(channel_id) → bytes
    - av_media_with_timestamp(ts_us, pcm_bytes) → bytes
    - av_media_indication(asc_bytes) → bytes  (codec_data AAC)
    - h264_idr_frame(width, height) → bytes
    - h264_p_frame() → bytes
    - malformed(strategy) → bytes  (per test robustezza)
    
    strategy per malformed:
    - 'truncated_header': header 4-byte troncato a 2
    - 'zero_payload': payload vuoto
    - 'overflow_channel': channel_id > 255
    - 'wrong_msg_type': msg_type non riconosciuto
    - 'random_bytes': N byte casuali
    """

@pytest.fixture
def aa_session_factory(aa_frame_factory):
    """Sequenza completa di frame per simulare una sessione AA dall'handshake."""
```

### 3.4 Hardware Detection Fixtures

```python
def pytest_configure(config):
    """Registra marker hardware e rileva disponibilità device all'avvio."""

@pytest.fixture(params=["mock", "hardware"])
def audio_source(request, mock_bus):
    """Parametrizzazione mock/hardware per test audio.
    
    - "mock": usa sounddevice mock, nessun device fisico richiesto
    - "hardware": usa sounddevice reale; se device non disponibile → pytest.skip con messaggio
    
    Rilevamento: tenta `sounddevice.query_devices()` prima di parametrizzare.
    Se nessun device audio → parametro "hardware" skippato automaticamente.
    """

@pytest.fixture(params=["mock", "hardware"])
def bluetooth_source(request):
    """Parametrizzazione mock/hardware per test bluetooth.
    Mock: dbus.SessionBus con bus di test dbus-launch.
    Hardware: dbus.SystemBus reale con BlueZ attivo.
    Rilevamento: `systemctl is-active bluetooth` prima di parametrizzare."""

@pytest.fixture(params=["mock", "hardware"])
def gstreamer_source(request):
    """Parametrizzazione mock/hardware per test GStreamer.
    Mock: pipeline GStreamer con fakesrc/fakesink.
    Hardware: pipeline reale con vaapih264dec o avdec_h264.
    Rilevamento: `Gst.ElementFactory.find('avdec_h264')` prima di parametrizzare."""
```

### 3.5 Qt Fixtures

```python
@pytest.fixture(scope="session")
def qt_app():
    """QApplication con offscreen platform.
    Impostato prima dell'import di qualsiasi modulo Qt:
      os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    Scope session: una sola QApplication per sessione (requisito Qt).
    """

@pytest.fixture
def qt_widget_factory(qt_app, bus_client):
    """Factory per istanziare widget Qt connessi al bus in-process."""
```

### 3.6 D-Bus Fixtures

```python
@pytest.fixture(scope="session")
def dbus_session():
    """Sessione D-Bus dedicata ai test, lanciata con dbus-launch.
    Evita interferenza con il system bus di produzione.
    Teardown: kill del processo dbus-daemon."""

@pytest.fixture
def mock_bluez(dbus_session):
    """Oggetto BlueZ mock registrato sulla sessione D-Bus di test.
    Espone: Adapter1, Device1, AgentManager1 con comportamento configurabile."""
```

---

## 4. Livello 1 — Unit Test

### Scope e Principi

I test unitari testano **un singolo modulo o funzione in isolamento totale**. Nessun socket ZMQ reale, nessun thread, nessun I/O di sistema. Il bus è sempre `mock_bus`. Le dipendenze esterne (sounddevice, GStreamer, D-Bus, Qt) sono sempre mockate.

**Regola d'oro**: un test unit non dovrebbe mai impiegare più di 500ms. Se supera questa soglia, probabilmente non è un unit test.

### Pattern Ricorrente

Ogni test unit di un modulo v2 segue questo pattern:

```
1. Istanziare il modulo con mock_bus e mock_config
2. Chiamare on_system_start() o il metodo specifico
3. Pubblicare un messaggio sul mock_bus (simulando input dal bus)
4. Verificare che mock_bus.published contanga i topic/payload attesi
5. Verificare lo stato interno del modulo se necessario
```

### Copertura per Modulo

#### `shared/proto_utils.py`
- `test_encode_decode_roundtrip`: encode → decode restituisce i valori originali per tutti i tipi di frame
- `test_build_parse_media_with_timestamp`: round-trip ts_us + pcm_bytes
- `test_encode_aa_frame_boundary_values`: channel=0, channel=255, flags=0x00, flags=0xFF
- `test_decode_aa_frame_truncated`: dati troncati sollevano eccezione specifica (non crash generico)
- `test_decode_aa_frame_empty`: payload vuoto gestito correttamente
- **Fuzz** (`@pytest.mark.fuzz`): `test_fuzz_decode_aa_frame` con Hypothesis — genera bytes arbitrari, verifica che `decode_aa_frame` non sollevi mai eccezioni non documentate

#### `shared/logger.py`
- `test_get_logger_returns_loguru_instance`
- `test_attach_bus_creates_dedicated_socket`: verifica che il drain thread sia separato dal thread del modulo
- `test_bus_sink_no_race_condition`: due thread pubblicano log concorrentemente, verifica che topic e payload non siano scambiati (il bug storico)
- `test_enqueue_true_nonblocking`: `log.info()` ritorna in < 1ms anche con bus lento

#### `shared/bus_client.py`
- `test_publish_subscribe_roundtrip` (con broker in-process)
- `test_subscribe_multiple_topics`
- `test_publish_before_subscribe`: messaggio non perso se subscriber non ancora connesso (ZMQ late-join semantics)
- `test_disconnect_graceful`

#### `modules/channel_modules/base_channel_module.py`
- `test_setup_request_triggers_open_request`
- `test_channel_open_request_transitions_state`
- `test_module_start_filter_by_priority`: verifica che `module_start` filtri per `priority`, non per `name`
- `test_module_stopped_ack_published`: verifica che `channel_manager.module_stopped {name}` sia pubblicato
- `test_send_frame_before_ready_raises`
- `test_is_ready_default_false`

#### `modules/channel_modules/audio/main.py`
- `test_codec_data_capture`: frame da 2 byte (ASC) viene salvato in `_aac_codec_data` e non passato al decoder
- `test_decode_aac_prepends_codec_data`: mock pyav, verifica che il feed sia `asc + frame`
- `test_decode_aac_without_codec_data_raises_or_skips`: comportamento documentato quando manca l'ASC
- `test_prebuffer_flushes_at_threshold`: accumula N frame, verifica write singola a soglia
- `test_prebuffer_reset_on_stop`: `_handle_stop_indication` azzera prebuffer e codec_data
- `test_pacat_client_name`: comando pacat include `--client-name=NemoHU`
- `test_codec_data_update_on_restart`: se AA rimanda l'ASC, `_aac_codec_data` viene aggiornato

#### `modules/channel_modules/av_input/main.py`
- `test_handle_setup_request`: payload `AVChannelSetupResponse` corretto
- `test_handle_input_open_request_start`: mock sounddevice, stream aperto
- `test_handle_input_open_request_stop`: stream chiuso correttamente
- `test_mic_callback_sends_frame`: verifica che il callback sounddevice chiami `send_frame` con payload corretto
- `test_mic_callback_uses_monotonic_ns`: timestamp in µs calcolato correttamente
- `test_state_topic_published_on_start_stop`: `av_input.state`, `av_input.mic_started`, `av_input.mic_stopped`

#### `modules/oaa_control_channel/handshake.py`
- `test_audio_focus_request_reply`: risposta `GAIN + granted=True`
- `test_audio_focus_request_transitions_to_active`: se stato `CHANNELS_OPENING` → `ACTIVE`
- `test_navigation_focus_request_reply`: risposta `NAV_FOCUS_PROJECTED`
- `test_voice_session_request_no_reply`: solo log, nessuna risposta
- `test_battery_status_notification_logged`: log level, time_remaining_s, critical_battery
- `test_ping_request_no_double_callback`: `on_active` chiamata una sola volta per sessione

#### `modules/bluetooth/paired_devices.py`
- `test_list_paired`: mock `GetManagedObjects`, verifica filtro `Paired=True OR Trusted=True`
- `test_get_info`: dict con tutti i campi attesi
- `test_connect_already_connected`: `AlreadyConnected` → chiama `on_connected`
- `test_connect_watchdog_fires`: mock `Device1.Connect` che non risponde → `on_failed` dopo timeout
- `test_disconnect_not_connected`: `NotConnected` → chiama `on_disconnected`
- `test_remove_device`: mock `Adapter1.RemoveDevice`, verifica return `True`

#### `modules/bluetooth/pairing.py`
- `test_handle_confirm_request_returns_immediately`: GLib thread non bloccato
- `test_confirm_worker_auto_accepts_after_timeout`: mock input utente, verifica auto-accept dopo `AUTO_ACCEPT_TIMEOUT_S`
- `test_confirm_worker_accepts_on_user_input`: input utente prima del timeout

#### `modules/bluetooth/main.py` (autoconnect)
- `test_autoconnect_starts_on_config_loaded`
- `test_autoconnect_stops_on_rfcomm_connected`
- `test_autoconnect_ignores_duplicate_start`
- `test_autoconnect_skips_connected_devices`
- `test_autoconnect_backoff_doubles`: verifica che backoff raddoppi ad ogni giro
- `test_autoconnect_backoff_capped`: backoff non supera `autoconnect_backoff_cap_s`

#### `modules/audio_manager/main.py`
- `test_sink_selected_published_on_start`: mock wpctl, verifica `audio.sink.selected`
- `test_source_selected_published_on_start`: mock wpctl, verifica `audio.source.selected`
- `test_device_discovery_wpctl`: mock output wpctl, verifica parsing corretto
- `test_hotplug_republishes_topic`: mock evento hotplug, verifica ripubblicazione

#### `modules/video_ui/main.py`
- `test_conn_state_waiting_bt_on_init`
- `test_conn_state_transitions_bt_pairing`: `bluetooth.pairing.completed` → `HANDSHAKE`
- `test_conn_state_transitions_video_playing`: `video.state=PLAYING` → `STREAMING`
- `test_conn_state_transitions_shutdown`: `aa.session.shutdown` → `WAITING_BT`
- `test_build_pipeline_selects_decoder`: mock Gst, verifica priorità decoder (vaapih264dec > vah264dec > openh264dec > avdec_h264)
- `test_scan_path_vaapi_system`: mock `Gst.Registry.scan_path`, verifica caricamento da sistema
- `test_push_frame_no_gst`: senza GStreamer, `push_frame` non solleva eccezioni
- `test_h264parse_config_interval`: `config-interval=-1` presente nella pipeline string
- **Fuzz** (`@pytest.mark.fuzz`): `test_fuzz_push_frame` con Hypothesis — bytes arbitrari come frame, verifica no crash

#### `modules/bluetooth_ui/main.py`
- `test_refresh_paired_list_populates_widget`
- `test_buttons_enabled_state_connected`: Disconnetti abilitato, Connetti disabilitato
- `test_buttons_enabled_state_disconnected`: Connetti abilitato, Disconnetti disabilitato
- `test_remove_confirmation_publishes_topic`: mock `QMessageBox.question` → Yes
- `test_on_paired_removed_deletes_item`
- `test_auto_populate_on_system_start`

#### `modules/channel_manager/main.py`
- `test_children_ready_timeout`: se non tutti i child rispondono entro `CHILDREN_READYTOSTART_WINDOW`, boot procede comunque
- `test_module_start_sent_after_module_readytostart`
- `test_shutdown_publishes_stopped`: verifica `channel_manager.stopped` pubblicato
- `test_shutdown_sleep_duration`: sleep 0.5s presente prima di stopped

---

## 5. Livello 2 — Integration Test

### Scope e Principi

I test di integrazione testano **più moduli reali che comunicano attraverso un broker ZMQ in-process**. I moduli vengono istanziati come oggetti Python (non come subprocess separati) e connessi allo stesso broker temporaneo. Le dipendenze di sistema (sounddevice, GStreamer, D-Bus) rimangono mockate salvo dove indicato con `@pytest.mark.hardware`.

**Regola d'oro**: un test di integrazione non dovrebbe mai impiegare più di 10s. Se supera questa soglia, è marcato `@pytest.mark.slow`.

### Test Inclusi

#### `test_bus_broker.py`
- `test_broker_routes_message`: modulo A pubblica, modulo B riceve
- `test_broker_multicast`: un publisher, tre subscriber, tutti ricevono
- `test_broker_topic_filtering`: subscriber su `audio.*` non riceve `video.*`
- `test_broker_reconnect`: client si disconnette e riconnette, messaggi successivi arrivano
- `test_broker_large_payload`: frame H.264 IDR (~400KB) transitato senza corruzione (verifica SHA)
- `test_broker_concurrent_publishers`: 10 thread pubblicano contemporaneamente, nessun messaggio perso o corrotto (il bug storico della race condition ZMQ)
- `test_broker_graceful_shutdown`: broker riceve stop signal, tutti i client ricevono disconnect event

#### `test_channel_manager_boot.py`
- `test_boot_sequence_completes`: `channel_manager` avvia 3 channel module mock, tutti raggiungono `module_ready`
- `test_boot_sequence_timeout_partial`: un module non risponde, gli altri bootano comunque
- `test_wait_channel_manager_stopped`: main riceve `channel_manager.stopped` prima di `_terminate_all`
- `test_priority_ordering`: moduli bootano nell'ordine di priorità corretto

#### `test_audio_pipeline.py`
- `test_audio_module_receives_frame_and_decodes`: `audio` module riceve frame AAC sul bus, chiama decode (mock pyav)
- `test_audio_module_subscribes_sink_selected`: dopo `audio.sink.selected`, pacat usa il device corretto
- `test_audio_manager_publishes_on_start`: `audio_manager` pubblica `audio.sink.selected` entro 2s da `system.start`
- `test_audio_prebuffer_integration`: N frame pubblicati sul bus, verifica write singola su pacat.stdin al raggiungimento della soglia

#### `test_av_input_pipeline.py`
- `test_av_input_subscribes_source_selected`
- `test_av_input_publishes_mic_started`
- `test_av_input_sends_frame_on_bus`: mock sounddevice callback, verifica frame `AV_MEDIA_WITH_TIMESTAMP` su bus

#### `test_video_pipeline.py`
- `test_video_module_publishes_frames`: `video` module con `publish_frames=True` pubblica su `video.frame`
- `test_video_ui_receives_frames`: `video_ui` subscrive `video.frame` e chiama `push_frame`
- `test_video_ui_state_machine_integration`: sequenza `bluetooth.pairing.completed` → `video.state=PLAYING` → `aa.session.shutdown` transita gli stati correttamente

#### `test_bluetooth_flow.py`
- `test_bluetooth_pairing_publishes_completed`: mock D-Bus, agente accetta pairing, `bluetooth.pairing.completed` pubblicato
- `test_bluetooth_autoconnect_stops_on_rfcomm`: `bluetooth.try_autoconnect` avvia loop, `bluetooth.rfcomm.connected` lo ferma
- `test_bluetooth_paired_list_response`: `bluetooth.paired.list` → `bluetooth.paired.devices` con dati corretti

#### `test_shutdown_sequence.py`
- `test_graceful_shutdown_order`: `system.stop` → tutti i moduli pubblicano ACK → `channel_manager.stopped` → `_terminate_all`
- `test_shutdown_timeout_fallthrough`: channel_manager non risponde entro 5s → `_terminate_all` eseguito comunque
- `test_no_double_cleanup`: `aa.session.shutdown` e `system.stop` non triggerano doppio cleanup

---

## 6. Livello 3 — End-to-End Test

### Scope e Principi

I test e2e testano **flussi completi dall'input esterno all'output osservabile**, avviando l'intera stack v2 come subprocess reali. Il "telefono Android" è simulato da un `PhoneMock` — un processo Python che parla il wire protocol AA (frame ZMQ o TCP secondo il modulo di connessione) e può inviare sequenze di frame predefinite.

I test e2e sono divisi in due sotto-livelli:
- **smoke** (`@pytest.mark.e2e_smoke`): veloci (< 30s totali), verificano che il sistema si avvii e le connessioni fondamentali funzionino
- **full** (`@pytest.mark.e2e_full`): completi, simulano sessioni AA realistiche, possono richiedere minuti

### `PhoneMock` — Componente Fondamentale

`PhoneMock` è un'utility di test (non un modulo v2) che:
- Parla il wire protocol AA (RFCOMM handshake + canali TCP/UDP)
- Può inviare sequenze di frame predefinite (handshake, video IDR+P, audio AAC)
- Espone un'API sincrona per i test: `connect()`, `send_frame()`, `disconnect()`
- Registra tutti i frame ricevuti dall'HU per asserzioni

```
v2/tests/e2e/
└── helpers/
    ├── phone_mock.py          ← implementazione PhoneMock
    ├── frame_sequences.py     ← sequenze predefinite di frame AA
    └── stack_launcher.py      ← avvia/stoppa l'intera stack v2
```

### E2E Smoke Tests

#### `test_bt_connect_smoke.py`
- `test_stack_starts_without_error`: `python v2/main.py` avvia senza eccezioni entro 5s
- `test_all_modules_ready`: tutti i moduli pubblicano `system.module_ready` entro 10s
- `test_bus_broker_reachable`: BusClient esterno si connette al broker entro 2s

#### `test_handshake_smoke.py`
- `test_rfcomm_handshake_completes`: PhoneMock si connette via RFCOMM, handshake AA completa senza timeout
- `test_version_negotiation`: versione AA negoziata correttamente
- `test_channel_setup_ack`: `CHANNEL_SETUP_REQUEST` riceve risposta entro 2s

#### `test_channel_open_smoke.py`
- `test_audio_channel_opens`: canale audio (ch.1) raggiunge stato `OPEN`
- `test_av_input_channel_opens`: canale av_input (ch.7) raggiunge stato `OPEN`
- `test_video_channel_opens`: canale video (ch.2) raggiunge stato `OPEN`

### E2E Full Tests

#### `test_full_aa_session.py`
- `test_complete_session_lifecycle`: BT connect → handshake → tutti i canali open → streaming → disconnect → cleanup
- `test_session_restart`: sessione completa, disconnect, nuova connessione → secondo handshake completato correttamente
- `test_session_crash_recovery`: processo channel_module killato durante sessione → `system.shutdown` pubblicato, stack si ferma ordinatamente

#### `test_audio_session.py`
- `test_audio_stream_decoded`: PhoneMock invia 100 frame AAC, verifica che pacat.stdin riceva PCM non nullo
- `test_audio_codec_data_propagation`: primo frame ASC riconosciuto, frame successivi decodificati
- `test_audio_focus_grant`: `AUDIO_FOCUS_REQUEST` ricevuto, risposta `GAIN` inviata entro 500ms
- `test_mic_stream_on_request`: `INPUT_OPEN_REQUEST(open=True)` → microfono avviato, frame `AV_MEDIA_WITH_TIMESTAMP` inviati al telefono

#### `test_video_session.py`
- `test_video_stream_displayed`: PhoneMock invia IDR + 30 P-frame H.264, `video_ui` riceve e processa senza errori
- `test_video_decoder_selected_logged`: log del decoder scelto (HW o SW) presente entro 5s dall'inizio dello stream
- `test_video_no_artifacts_after_drop`: frame droppato intenzionalmente, IDR successivo ripristina decodifica pulita (verifica via `config-interval=-1`)

---

## 7. Livello 4 — Performance Test

### Scope e Principi

I test di performance sono **benchmark informativi**: misurano e registrano metriche, ma **non falliscono per soglie hardcodate**. L'output è un report JSON che può essere confrontato tra commit per rilevare regressioni.

Ogni test di performance:
- È marcato `@pytest.mark.performance`
- Salva il risultato in `reports/perf-YYYYMMDD.json`
- Loga un riassunto human-readable al termine
- Rispetta le performance requirements di `project-vision.md` §4.5 come valori di riferimento (non assertion)

### Metriche Coperte

| Metrica | File | Valore di Riferimento (§4.5) |
|---|---|---|
| Latenza bus publish→receive (p50, p95, p99) | `test_bus_latency.py` | < broker overhead teorico |
| Throughput bus (msg/s, MB/s) | `test_bus_throughput.py` | — |
| Latenza audio (frame AAC → PCM pronto) | `test_audio_latency.py` | ≤ 10ms |
| Frame rate video (frame/s processati) | `test_video_frame_rate.py` | ≥ 30 fps |
| Memoria RSS processo principale (baseline, after 5min, after 30min) | `test_memory_rss.py` | — |
| Tempo decode singolo frame AA (p50, p95, p99) | `test_aa_frame_decode.py` | — |
| CPU % durante stream audio+video simultaneo | `test_memory_rss.py` | ottimizzato per Atom |
| Tempo boot completo (system.start → tutti module_ready) | (in `test_memory_rss.py`) | — |
| Latenza handshake AA (RFCOMM connect → sessione ACTIVE) | (in e2e smoke + timer) | — |
| Jitter frame video (deviazione standard inter-frame time) | `test_video_frame_rate.py` | — |

### `test_bus_latency.py`

```
Scenario:
  - 10.000 messaggi inviati sequenzialmente
  - Timestamp publisher (ns) incluso nel payload
  - Subscriber calcola latenza = now() - ts_publisher
  - Report: min, max, p50, p95, p99, mean, stddev

Varianti:
  - Payload piccolo (< 100 B): tipico topic di controllo
  - Payload medio (1-10 KB): tipico frame audio AAC
  - Payload grande (100KB-400KB): tipico frame video H.264 IDR
```

### `test_bus_throughput.py`

```
Scenario:
  - Publisher a rate massimo per 5 secondi
  - Conta messaggi ricevuti dal subscriber
  - Report: msg/s, MB/s, messaggi persi (se ZMQ_SNDHWM raggiunto)

Varianti:
  - 1 publisher / 1 subscriber
  - 1 publisher / 3 subscriber (multicast)
  - 3 publisher / 1 subscriber (fan-in)
```

### `test_audio_latency.py`

```
Scenario (mock sounddevice):
  - Simula callback audio a 48kHz/mono/16-bit
  - Misura: tempo da callback → frame sul bus (AV_MEDIA_WITH_TIMESTAMP)
  - 1000 campioni, report: p50, p95, p99
  - Target di riferimento: ≤ 10ms (project-vision.md §4.5)
```

### `test_video_frame_rate.py`

```
Scenario:
  - PhoneMock invia 300 frame H.264 al rate nominale (30fps)
  - `video_ui` conta frame processati in finestra di 10s
  - Report: fps effettivi, frame droppati, jitter (stddev inter-frame time ms)
  - Target di riferimento: ≥ 30fps, jitter < 5ms
```

### `test_memory_rss.py`

```
Scenario:
  - Avvia stack completo v2
  - Misura RSS al boot (baseline)
  - Avvia stream audio+video per 5 minuti
  - Misura RSS ogni 30s
  - Report: baseline MB, max MB, delta MB/min (leak indicator)
  - CPU %: campionato ogni 5s, report: mean, max, p95
```

### `test_aa_frame_decode.py`

```
Scenario:
  - 10.000 frame AA sintetici (via aa_frame_factory)
  - Misura: tempo encode + decode round-trip
  - Report: p50, p95, p99 µs
  
Varianti (Hypothesis property-based):
  - Frame validi di dimensioni variabili (1B - 400KB payload)
  - Frame con campi boundary (channel=0, channel=255, flags=0xFF)
```

---

## 8. Fuzzing con Hypothesis

I test property-based usano la libreria `hypothesis`. Sono marcati `@pytest.mark.fuzz` e inclusi nella suite unit ma con profilo Hypothesis separato per CI (esempio ridotto) vs. run locale (esempio esteso).

### Configurazione Hypothesis

```python
# v2/tests/conftest.py
from hypothesis import settings, HealthCheck

settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("local", max_examples=1000)
settings.register_profile("nightly", max_examples=10000)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))
```

### Target di Fuzzing

| Target | Strategia | Invariante Verificata |
|---|---|---|
| `decode_aa_frame(bytes)` | `st.binary()` arbitrario | Non solleva mai eccezioni non documentate |
| `parse_media_with_timestamp(bytes)` | `st.binary(min_size=0, max_size=1000)` | Nessun crash; dati < 8B → eccezione documentata |
| `AVInputModule._mic_callback(bytes, ...)` | PCM bytes arbitrari | `send_frame` chiamata con payload non vuoto |
| `VideoUI.push_frame(bytes)` | `st.binary()` | Nessun crash, nessuna eccezione non gestita |
| `encode_aa_frame` round-trip | `st.integers` + `st.binary` per tutti i campi | `decode(encode(x)) == x` per tutti gli input validi |
| Handshake handler routing | `st.integers(min_value=0, max_value=65535)` come msg_id | Nessun KeyError/AttributeError per msg_id sconosciuto |

---

## 9. Parametrizzazione Mock/Hardware

Il principio fondamentale è che **ogni test che tocca un device fisico esiste in due varianti**: una con mock (sempre eseguita) e una con hardware reale (eseguita solo se il device è disponibile).

### Meccanismo

```python
# In conftest.py — rilevamento automatico
def detect_audio_hardware():
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        return any(d['max_input_channels'] > 0 for d in devices)
    except Exception:
        return False

AUDIO_HARDWARE_AVAILABLE = detect_audio_hardware()

# Fixture parametrizzata
@pytest.fixture(params=["mock", pytest.param("hardware", marks=pytest.mark.skipif(
    not AUDIO_HARDWARE_AVAILABLE, reason="Nessun device audio disponibile"
))])
def audio_source(request):
    if request.param == "mock":
        return MockAudioSource()
    else:
        return RealAudioSource()
```

### Device Detection Matrix

| Device | Rilevamento | Fallback |
|---|---|---|
| Audio input | `sounddevice.query_devices()` | skip parametro "hardware" |
| Audio output | `sounddevice.query_devices()` | skip parametro "hardware" |
| Bluetooth | `systemctl is-active bluetooth` | skip parametro "hardware" |
| GStreamer decode | `Gst.ElementFactory.find('avdec_h264')` | skip parametro "hardware" |
| VA-API HW decode | `Gst.ElementFactory.find('vaapih264dec')` | degradazione a SW |
| Display (Qt) | `QT_QPA_PLATFORM=offscreen` sempre | nessun skip, sempre mock |
| D-Bus system | `dbus.SystemBus()` senza eccezioni | skip parametro "hardware" |

---

## 10. Copertura e Reporting

### Soglia Coverage

- **Global**: 80% (fail CI se sotto soglia)
- **Granularità**: per-file nel report HTML, nessuna soglia differenziata per modulo
- **Esclusioni**: `v2/modules/_template/`, `v2/tests/` stessi, `v2/tests/e2e/helpers/`

### Comandi Coverage

```bash
# Coverage completo con report HTML
pytest --cov=v2 \
       --cov-report=html:reports/coverage \
       --cov-report=term-missing \
       --cov-fail-under=80 \
       -m "not performance"

# Coverage solo unit (più veloce)
pytest --cov=v2/shared --cov=v2/modules \
       --cov-report=term-missing \
       -m unit
```

### Report Performance

I benchmark performance salvano risultati in `reports/perf-YYYYMMDD.json`:

```json
{
  "timestamp": "2026-05-13T10:00:00Z",
  "commit": "abc1234",
  "metrics": {
    "bus_latency_p50_ms": 0.42,
    "bus_latency_p95_ms": 1.1,
    "bus_latency_p99_ms": 2.3,
    "bus_throughput_msg_s": 45000,
    "audio_latency_p50_ms": 3.2,
    "video_fps": 29.8,
    "video_jitter_ms": 1.4,
    "memory_baseline_mb": 85,
    "memory_after_5min_mb": 92,
    "cpu_mean_pct": 12.4,
    "boot_time_s": 1.8,
    "handshake_latency_ms": 420
  }
}
```

---

## 11. Dipendenze Test

### `v2/tests/requirements-test.txt`

```
pytest>=8.0
pytest-cov>=5.0
pytest-asyncio>=0.23
pytest-timeout>=2.3
hypothesis>=6.100
dbus-python>=1.3
```

### Dipendenze Condizionali

Già presenti in `environment.yml` (non duplicate):
- `pyzmq` — bus reale in-process
- `PyQt6` — fixture Qt offscreen
- `sounddevice` — fixture audio (mock + hardware)
- `loguru` — fixture logger
- `pyav` — fixture decode AAC

---

## 12. Flusso di Lavoro CI

### Pipeline Raccomandata

```
┌─────────────────┐
│  git push/PR    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  pytest -m unit │  < 60s attesi — blocca merge se fallisce
│  coverage ≥ 80% │
└────────┬────────┘
         │ pass
         ▼
┌──────────────────────┐
│ pytest -m integration│  < 5min attesi — blocca merge se fallisce
└────────┬─────────────┘
         │ pass
         ▼
┌──────────────────────┐
│ pytest -m e2e_smoke  │  < 2min attesi — blocca merge se fallisce
└────────┬─────────────┘
         │ pass
         ▼
┌──────────────────────┐   (nightly / manuale)
│ pytest -m e2e_full   │  può richiedere minuti
│ pytest -m performance│  genera report JSON
└──────────────────────┘
```

### Regole di Merge

| Check | Condizione di blocco |
|---|---|
| Unit test | Qualsiasi FAIL o XFAIL inatteso |
| Integration test | Qualsiasi FAIL |
| E2E smoke | Qualsiasi FAIL |
| Coverage | < 80% globale |
| Performance | Mai bloccante (solo informativi) |
| Hardware test | Mai bloccanti in CI (skippa se no device) |

---

## 13. Aggiunta di Nuovi Test

Quando si aggiunge un nuovo modulo v2, seguire questa checklist:

```
[ ] File unit test in v2/tests/unit/modules/<nome_modulo>/test_<nome_modulo>.py
[ ] Tutti i path pubblici del modulo coperti a livello unit
[ ] Test di robustezza: input malformati, eccezioni, edge case
[ ] Test fuzz se il modulo processa dati binari esterni (wire protocol, audio, video)
[ ] Integration test in v2/tests/integration/ se il modulo interagisce con altri moduli
[ ] Fixture parametrizzata mock/hardware se il modulo usa device fisici
[ ] E2E smoke test se il modulo fa parte del path di connessione critico
[ ] Nessun test hardcoda path, porte o socket: tutto via fixture
[ ] `pytest -m unit` passa localmente prima del push
[ ] Coverage del nuovo modulo ≥ 80% verificata localmente
```

---

## 14. Test Pendenti (Backlog Iniziale)

I test seguenti sono stati identificati nel `session_handoff.md` come pendenti prima della creazione di questa suite formalizzata. Vanno implementati come prima priorità:

| Modulo | File Target | Test Prioritari |
|---|---|---|
| `av_input` | `unit/modules/channel_modules/test_av_input.py` | round-trip proto_utils, handle_setup, mic callback |
| `handshake` | `unit/modules/oaa_control_channel/test_handshake.py` | 4 nuovi handler ch0 |
| `audio` | `unit/modules/channel_modules/test_audio.py` | codec_data, prebuffer, decode |
| `video_ui` | `unit/modules/video_ui/test_video_ui.py` | pipeline build, decoder selection, state machine |
| `paired_devices` | `unit/modules/bluetooth/test_paired_devices.py` | list, connect, watchdog, remove |
| `bluetooth autoconnect` | `unit/modules/bluetooth/test_autoconnect.py` | stop on rfcomm, no duplicate start, skip connected |
| `bluetooth_ui` | `unit/modules/bluetooth_ui/test_bluetooth_ui.py` | list populate, button states, remove confirm |
| `pairing` | `unit/modules/bluetooth/test_pairing.py` | non-blocking GLib, auto-accept timeout |

---

*Document Version: 1.0*  
*Scope: NemoHeadUnit-Wireless v2 Test Suite*  
*Livelli: unit → integration → e2e → performance*
