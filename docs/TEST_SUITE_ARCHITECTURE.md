# Test Suite Architecture — NemoHeadUnit-Wireless v2

> Documento di riferimento per la progettazione, struttura e workflow della test suite completa di `v2/`.  
> Referenziato da [`docs/project-vision.md`](project-vision.md) §7.1 Quality Metrics.

---

## 1. Principi Guida

- **Stessa architettura del codice**: la struttura dei test specchia `v2/modules/`, con target di migrazione a root quando la codebase v2 diventerà root.
- **Nessun modulo accoppiato nei test**: i test unitari non importano mai moduli fratelli — solo il modulo sotto test e le fixture condivise.
- **Parametrizzazione hardware/mock**: ogni test case che tocca hardware esterno gira due volte — una con mock, una con hardware reale se disponibile. La disponibilità è rilevata a runtime.
- **Bus reale in-process**: tutti i livelli usano un broker ZMQ reale avviato in-process sulla fixture `fake_bus`. Nessun mock del protocollo ZMQ stesso.
- **Property-based fuzzing**: `hypothesis` genera automaticamente frame AA wire format con valori boundary, campi mancanti e overflow.
- **Performance benchmark informativi**: misurano e loggano tutte le metriche chiave, non falliscono la build.

---

## 2. Struttura Directory

```
v2/
└── tests/
    ├── conftest.py                          ← fixture globali condivise
    ├── pytest.ini                           ← marker registrati + profili
    │
    ├── unit/                                ← @pytest.mark.unit
    │   ├── shared/
    │   │   ├── test_proto_utils.py
    │   │   ├── test_logger.py
    │   │   └── test_bus_client.py
    │   └── modules/
    │       ├── channel_modules/
    │       │   ├── test_base_channel_module.py
    │       │   ├── audio/
    │       │   │   └── test_audio.py
    │       │   ├── av_input/
    │       │   │   └── test_av_input.py
    │       │   └── video/
    │       │       └── test_video.py
    │       ├── oaa_control_channel/
    │       │   └── test_handshake.py
    │       ├── channel_manager/
    │       │   └── test_channel_manager.py
    │       ├── bluetooth/
    │       │   ├── test_paired_devices.py
    │       │   ├── test_pairing.py
    │       │   └── test_autoconnect.py
    │       ├── audio_manager/
    │       │   └── test_audio_manager.py
    │       ├── video_ui/
    │       │   └── test_video_ui.py          ← QApplication offscreen
    │       └── bluetooth_ui/
    │           └── test_bluetooth_ui.py      ← QApplication offscreen
    │
    ├── integration/                          ← @pytest.mark.integration
    │   ├── test_bus_broker.py               ← broker ZMQ + multi-client
    │   ├── test_channel_lifecycle.py        ← channel_manager + channel_modules
    │   ├── test_audio_pipeline.py           ← audio_manager + audio + av_input
    │   ├── test_video_pipeline.py           ← video + video_ui
    │   ├── test_bluetooth_flow.py           ← bluetooth + bluetooth_ui
    │   └── test_boot_shutdown.py            ← main.py boot/shutdown completo
    │
    ├── e2e/                                 ← @pytest.mark.e2e
    │   ├── helpers/
    │   │   ├── phone_mock.py                ← simulatore telefono AA wire protocol
    │   │   ├── frame_sequences.py           ← sequenze frame predefinite
    │   │   └── stack_launcher.py            ← avvia/stoppa stack v2 come subprocess
    │   ├── smoke/                           ← @pytest.mark.e2e_smoke (veloci, <30s)
    │   │   ├── test_bt_connect_to_handshake.py
    │   │   ├── test_channel_manager_boot.py
    │   │   └── test_audio_path_smoke.py
    │   └── full_session/                    ← @pytest.mark.e2e_full (lenti, >60s)
    │       ├── test_full_aa_session.py
    │       └── test_session_recovery.py
    │
    ├── performance/                         ← @pytest.mark.performance
    │   ├── test_bus_throughput.py
    │   ├── test_bus_latency.py
    │   ├── test_audio_latency.py
    │   ├── test_video_frame_rate.py
    │   ├── test_memory_rss.py
    │   └── test_aa_frame_decode.py
    │
    └── fuzz/                                ← @pytest.mark.fuzz (hypothesis)
        ├── test_aa_wire_format.py
        ├── test_proto_utils_roundtrip.py
        └── test_bus_payload_malformed.py
```

---

## 3. Livelli di Test

### 3.1 Unit (`@pytest.mark.unit`)

**Scope**: singolo modulo/classe/funzione in isolamento completo.  
**Dipendenze esterne**: sempre mockate (sounddevice, pacat, wpctl, D-Bus, GStreamer).  
**Bus**: fixture `fake_bus` — broker ZMQ reale in-process su socket `ipc:///tmp/nemotest-{uuid}.pub`.  
**Velocità target**: < 1s per test.  
**Coverage target**: 80% globale.

Ogni test unitario che tocca hardware esterno è parametrizzato:

```python
@pytest.mark.unit
@pytest.mark.parametrize("source_mode", ["mock", "hardware"], indirect=True)
def test_handle_setup_request(source_mode, aa_frame_factory): ...
```

Quando `"hardware"` è richiesto ma il device non è disponibile, il test viene saltato con `pytest.skip` e un messaggio esplicito.

### 3.2 Integration (`@pytest.mark.integration`)

**Scope**: due o più moduli reali che comunicano attraverso il bus.  
**Bus**: broker ZMQ reale in-process condiviso tra i moduli avviati come thread.  
**Velocità target**: < 10s per test.

Test chiave:
- `test_channel_lifecycle`: `channel_manager` riceve `module_ready_to_start` e avvia i canali nell'ordine corretto.
- `test_boot_shutdown`: sequenza completa `system.start` → moduli attivi → `system.stop` → `channel_manager.stopped` → terminate.
- `test_audio_pipeline`: `audio_manager` pubblica `audio.sink.selected`; `AudioModule` si configura correttamente.

### 3.3 E2E (`@pytest.mark.e2e`)

**Scope**: stack v2 completo con un `PhoneMock` che simula il telefono AA a livello wire protocol.

**Smoke** (`@pytest.mark.e2e_smoke`): flussi critici singoli, < 30s, run in CI standard.

| Test | Flusso coperto |
|---|---|
| `test_bt_connect_to_handshake` | `bluetooth.rfcomm.connected` → `channel_manager` boot → `ACTIVE` |
| `test_channel_manager_boot` | `system.start` → tutti i canali `READY` entro 5s |
| `test_audio_path_smoke` | `SETUP_REQUEST` → `OPEN` → primo frame PCM ricevuto da pacat mock |

**Full session** (`@pytest.mark.e2e_full`): sessione AA completa, > 60s, run separato (nightly o on-demand).

| Test | Flusso coperto |
|---|---|
| `test_full_aa_session` | BT connect → handshake → video H.264 + audio AAC 30s → disconnect pulito |
| `test_session_recovery` | Crash channel_module durante stream → `system.shutdown` → restart → seconda sessione OK |

### 3.4 Performance (`@pytest.mark.performance`)

Tutti i benchmark sono **informativi**: misurano, loggano in `tests/reports/`, non falliscono la build.

| File | Metrica | Riferimento (`project-vision.md` §4.5) |
|---|---|---|
| `test_bus_latency.py` | publish→receive p50/p95/p99 ms | broker overhead: zero Python per hop |
| `test_bus_throughput.py` | msg/s, MB/s (1→1, 1→3, 3→1) | — |
| `test_audio_latency.py` | frame AAC → PCM pronto p50/p95/p99 | ≤ 10ms |
| `test_video_frame_rate.py` | fps effettivi, frame droppati, jitter ms | ≥ 30fps |
| `test_memory_rss.py` | RSS baseline/+5min/+30min, CPU p95 durante stream | ottimizzato Atom |
| `test_aa_frame_decode.py` | encode+decode round-trip p50/p95/p99 µs | — |

Formato output: `reports/perf-{YYYYMMDD}-{commit}.json`.

### 3.5 Fuzz (`@pytest.mark.fuzz`)

Motore: `hypothesis`. Profili: `ci` (100 esempi), `local` (1000), `nightly` (10000).

| File | Target | Invariante |
|---|---|---|
| `test_aa_wire_format.py` | `decode_aa_frame(bytes arbitrari)` | Nessuna eccezione non documentata |
| `test_proto_utils_roundtrip.py` | encode/decode + build/parse media_with_timestamp | `decode(encode(x)) == x` |
| `test_bus_payload_malformed.py` | payload JSON malformato, topic vuoto, frame incompleto | Nessun crash, nessun warning "invalid JSON" |

---

## 4. Componenti Condivise (`conftest.py`)

### 4.1 `fake_bus` / `in_process_broker`

```python
@pytest.fixture(scope="function")
def in_process_broker():
    """Broker ZMQ XPUB/XSUB reale su socket IPC univoche per test.
    Path: ipc:///tmp/nemotest-{uuid4().hex}.{pub|sub}
    Teardown: poison pill → join thread con timeout 2s."""

@pytest.fixture(scope="function")
def bus_client(in_process_broker):
    """BusClient connesso al broker in-process.
    Espone: publish(topic, payload), subscribe(topic, callback), wait_for(topic, timeout)."""

@pytest.fixture(scope="function")
def mock_bus():
    """Mock leggero del bus per unit test senza socket ZMQ.
    Registra tutte le chiamate publish/subscribe per asserzioni."""
```

### 4.2 `mock_config`

Config YAML minimale in `tmp_path`. Parametrizzabile per test-case. Evita dipendenze da file YAML su disco.

### 4.3 `aa_frame_factory`

```python
@pytest.fixture
def aa_frame_factory():
    """Factory per frame AA wire-format validi e invalidi.

    Metodi:
    - setup_request(channel_id, config)
    - channel_open_request(channel_id)
    - av_media_with_timestamp(ts_us, pcm_bytes)
    - av_media_indication(asc_bytes)       # codec_data AAC
    - h264_idr_frame(width, height)
    - h264_p_frame()
    - malformed(strategy)
      strategy: 'truncated_header' | 'zero_payload' | 'overflow_channel'
                | 'wrong_msg_type' | 'random_bytes'
    """
```

### 4.4 `phone_mock` (E2E)

Simulatore del telefono AA per i test e2e. Implementa il wire protocol AA: handshake SETUP → OPEN → frame media → disconnect. Configurabile per crash, timeout, riconnessione.

### 4.5 `hardware_available(device)` — Device Detection Matrix

Utility per parametrizzazione hardware/mock. Rilevamento a runtime prima di parametrizzare:

| Device | Detection |
|---|---|
| Audio input/output | `sounddevice.query_devices()` senza eccezioni |
| Bluetooth | `systemctl is-active bluetooth` exit 0 |
| GStreamer SW decode | `Gst.ElementFactory.find('avdec_h264')` non None |
| VA-API HW decode | `Gst.ElementFactory.find('vaapih264dec')` non None |
| D-Bus system | `dbus.SystemBus()` senza eccezioni |
| Qt display | `QT_QPA_PLATFORM=offscreen` — sempre disponibile |

### 4.6 `qt_app`

```python
@pytest.fixture(scope="session")
def qt_app():
    """QApplication con offscreen platform.
    os.environ['QT_QPA_PLATFORM'] = 'offscreen' impostato prima dell'import Qt.
    Scope session: una sola QApplication per sessione (requisito Qt)."""
```

### 4.7 `dbus_session`

Sessione D-Bus dedicata per test bluetooth. Avvia `dbus-daemon --session` come subprocess, setta `DBUS_SESSION_BUS_ADDRESS`. Teardown garantito.

---

## 5. Configurazione Runner

### `v2/tests/pytest.ini`

```ini
[pytest]
testpaths = v2/tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

markers =
    unit: test isolato, nessun I/O reale
    integration: più moduli reali con bus in-process
    e2e: flusso end-to-end con stack completo o phone mock
    e2e_smoke: subset e2e veloci (< 30s totali)
    e2e_full: sessione AA completa, > 60s
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

### `Makefile` — Profili di Esecuzione

```makefile
test-unit:
    pytest -m unit --cov=v2 --cov-report=term-missing --cov-fail-under=80

test-integration:
    pytest -m integration

test-e2e-smoke:
    pytest -m e2e_smoke

test-e2e-full:
    pytest -m e2e_full

test-performance:
    pytest -m performance

test-fuzz:
    HYPOTHESIS_PROFILE=local pytest -m fuzz

test-ci:
    pytest -m "unit or integration or e2e_smoke" --cov=v2 --cov-fail-under=80

test-all:
    pytest -m "unit or integration or e2e_smoke or fuzz"

test-hardware:
    pytest -m hardware
```

---

## 6. Parametrizzazione Hardware/Mock

Pattern standard per ogni test che tocca device fisici:

```python
# In conftest.py
AUDIO_AVAILABLE = detect_audio_hardware()  # chiamato una volta all'avvio

@pytest.fixture(params=["mock", pytest.param("hardware", marks=pytest.mark.skipif(
    not AUDIO_AVAILABLE, reason="Nessun device audio rilevato"
))])
def audio_source(request):
    return MockAudioSource() if request.param == "mock" else RealAudioSource()
```

Il test gira con entrambi i parametri se il device è disponibile, solo `mock` altrimenti. Nessun fail inatteso in CI senza hardware.

---

## 7. Coverage

- **Strumento**: `pytest-cov` con `--cov=v2`
- **Soglia**: 80% globale flat (nessuna differenziazione per modulo)
- **Esclusi**: `v2/tests/`, `v2/modules/_template/`, file `__init__.py` vuoti
- **Report**: `term-missing` in stdout + `htmlcov/` locale
- **CI gate**: `--cov-fail-under=80` blocca il merge

---

## 8. Flusso CI

```
PR aperta
    │
    ▼
pytest -m unit  →  coverage ≥ 80%       blocca merge se FAIL
    │ pass
    ▼
pytest -m integration                   blocca merge se FAIL
    │ pass
    ▼
pytest -m e2e_smoke                     blocca merge se FAIL
    │ pass
    ▼
Merge in main
    │
    ▼
Nightly:
  pytest -m e2e_full                    informativo
  pytest -m performance                 → reports/perf-{date}-{commit}.json
```

| Check | Blocca merge |
|---|---|
| Unit test | Sì — qualsiasi FAIL |
| Integration test | Sì — qualsiasi FAIL |
| E2E smoke | Sì — qualsiasi FAIL |
| Coverage < 80% | Sì |
| Performance | No — solo informativi |
| Hardware test | No — skip automatico in CI |

---

## 9. Dipendenze Test

### `v2/tests/requirements-test.txt`

```
pytest>=8.0
pytest-cov>=5.0
pytest-asyncio>=0.23
pytest-timeout>=2.3
hypothesis>=6.100
dbus-python>=1.3
```

Dipendenze già in `environment.yml` (non duplicate): `pyzmq`, `PyQt6`, `sounddevice`, `loguru`, `pyav`, `gst-libav`.

---

## 10. Backlog Test Pendenti

Identificati nel `session_handoff.md` come priorità immediata:

| Modulo | File Target | Test Prioritari |
|---|---|---|
| `av_input` | `unit/modules/channel_modules/av_input/test_av_input.py` | round-trip proto_utils, handle_setup, mic callback |
| `handshake` | `unit/modules/oaa_control_channel/test_handshake.py` | 4 handler ch0 (audio_focus, nav_focus, voice_session, battery) |
| `audio` | `unit/modules/channel_modules/audio/test_audio.py` | codec_data capture, decode prepend, prebuffer flush |
| `video_ui` | `unit/modules/video_ui/test_video_ui.py` | pipeline build, decoder selection, state machine transitions |
| `paired_devices` | `unit/modules/bluetooth/test_paired_devices.py` | list, connect watchdog, AlreadyConnected, remove |
| `bluetooth autoconnect` | `unit/modules/bluetooth/test_autoconnect.py` | stop on rfcomm, no duplicate start, skip connected |
| `bluetooth_ui` | `unit/modules/bluetooth_ui/test_bluetooth_ui.py` | list populate, button states, remove confirm |
| `pairing` | `unit/modules/bluetooth/test_pairing.py` | GLib non-blocking, auto-accept timeout |

---

## 11. Checklist Nuovo Modulo

Quando si aggiunge un nuovo modulo v2, prima del merge:

```
[ ] v2/tests/unit/modules/<nome>/test_<nome>.py — tutti i path pubblici coperti
[ ] Test robustezza: input malformati, eccezioni, edge case
[ ] Fuzz se il modulo processa dati binari esterni (wire protocol, audio, video)
[ ] Integration test se il modulo interagisce con altri moduli via bus
[ ] Fixture parametrizzata mock/hardware se usa device fisici
[ ] E2E smoke test se fa parte del path di connessione critico
[ ] Nessun path/porta/socket hardcodato nei test — tutto via fixture
[ ] pytest -m unit passa localmente prima del push
[ ] Coverage del nuovo modulo ≥ 80% verificata localmente
```

---

*Document Version: 2.0*  
*Sostituisce versione 1.0 del 2026-05-13*  
*Livelli: unit → integration → e2e → performance*
