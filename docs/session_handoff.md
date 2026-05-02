# Session Handoff Documentation

## 2026-04-21 - Multi-threaded Message Bus Architecture

**What changed:**
- Enhanced MessageBus with thread affinity registry
- Updated ComponentRegistry with thread declaration support
- Updated main.py to declare threads for all components

**Why:**
- Enable multi-threaded execution where each component runs in its own thread
- Components declare which thread they need during registration
- MessageBus routes messages to the appropriate thread

**Status:**
Completed

**Next 1-3 steps:**
1. Review component implementations for thread-safety
2. Add tests for cross-thread message handling
3. Verify thread lifecycle management

**Verification commands/results:**
```bash
python -c "from app.main import Application; print('Python build OK')"
python -m pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80 -v
```

**Run Application Command**
```bash
python app/main.py
```

---

## 2026-04-21 - v2 Wireless Modules — Initial Implementation

**What changed:**

Creati 4 moduli standalone sotto `v2/modules/` seguendo l'architettura v2
(processo OS indipendente per modulo, comunicazione esclusiva via ZeroMQ bus):

| Modulo | File | Descrizione |
|---|---|---|
| `bluetooth` | `bluez_adapter.py`, `discovery.py`, `pairing.py`, `rfcomm.py`, `main.py` | D-Bus BlueZ, discovery timed, pairing Agent2, RFCOMM ch.8 |
| `hostapd_helper` | `ap_manager.py`, `ap_monitor.py`, `main.py` | Crea AP WiFi on-the-fly via hostapd+dnsmasq, polling conferma attivazione |
| `rfcomm_handshake` | `packet.py`, `handshake.py`, `main.py` | 5-stage AA wireless handshake, encode/decode pacchetti |
| `tcp_server` | `server.py`, `frame_relay.py`, `main.py` | TCP listen :5288, SSL interno, relay frame AA sul bus |

**Status:** Completed

**Next 1-3 steps:**
1. Aggiungere test unitari per ogni modulo
2. Implementare protobuf reali da `mrmees/open-android-auto`
3. Testare flusso end-to-end su hardware

**Verification commands:**
```bash
python v2/bus_broker.py
python v2/main.py
python v2/modules/bluetooth/main.py
```

---

## 2026-04-21 - v2 config_manager Module

**What changed:**
Creato `v2/modules/config_manager/main.py` — servizio centralizzato di configurazione
con persistenza YAML per modulo.

**Contract:**

| Direzione | Topic | Payload |
|---|---|---|
| Subscribe | `config.get` | `{"module": "<name>"}` |
| Subscribe | `config.set` | `{"module": "<name>", "key": "<k>", "value": <v>}` |
| Publish | `config.response` | `{"module": "<name>", "config": {...}}` |
| Publish | `config.changed` | `{"module": "<name>", "key": "<k>", "value": <v>}` |

**YAML layout:** `v2/config/<module_name>.yaml` — un file per modulo.

**Status:** Completed

---

## 2026-04-21 - config_manager full integration

**What changed:**

1. **`tests/v2/test_config_manager.py`** — 12 test unitari con mock del bus ZMQ:
   - `TestConfigGet`: missing field, unknown module, existing config, YAML corrotto
   - `TestConfigSet`: missing fields, persist YAML, publish config.changed, accumulo chiavi, overwrite, roundtrip
   - `TestLifecycle`: system.start crea config dir, system.stop chiama bus.stop

2. **`v2/shared/config_client.py`** — helper riutilizzabile per qualsiasi modulo:
   - `cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)`
   - `cfg.register()` → subscribe a `config.response` + `config.changed`
   - `cfg.get()` → pubblica `config.get`, risposta via `on_config_loaded`
   - `cfg.set(key, value)` → pubblica `config.set`
   - Filtra automaticamente per `module_name` — sicuro con più moduli attivi

3. **`v2/modules/bluetooth/`** — integrazione ConfigClient:
   - `bluez_adapter.py`: aggiunto `set_name(name)` per impostare l'alias BT
   - `main.py`: carica config su `system.start`, applica su `config.changed`
   - Chiavi: `discoverable`, `discoverable_timeout`, `discovery_duration_sec`, `adapter_name`

4. **`v2/modules/hostapd_helper/`** — integrazione ConfigClient:
   - `ap_manager.py`: tutti i parametri di rete spostati in `APConfig` (no più costanti globali)
   - `main.py`: carica config, costruisce `APConfig` dinamicamente da `_config`
   - Chiavi: `interface`, `ssid`, `channel`, `ap_password`, `subnet`, `gateway_ip`, `dhcp_range_start`, `dhcp_range_end`, `monitor_timeout`

5. **`environment.yml`** — aggiunto `pyyaml>=6.0` (mancava, causava `ModuleNotFoundError`)

6. **`v2/modules/_template/main.py`** — riscritto con:
   - `ConfigClient` integrato e commentato step-by-step
   - Pattern `_DEFAULTS` + `_config` + `_on_config_loaded` + `_on_config_changed`
   - Istruzioni numerate STEP 1–5 inline nel codice
   - Sezione bus.publish con esempio

**Why:**
- I moduli devono poter leggere/scrivere config persistente senza accoppiamento diretto
- `ConfigClient` elimina boilerplate ripetuto in ogni modulo
- Il template aggiornato rende il pattern immediatamente chiaro ai nuovi sviluppatori

**Status:** Completed

**Next 1-3 steps:**
1. Aggiornare l'ambiente conda: `conda env update -f environment.yml --prune`
2. Aggiungere test unitari per `bluetooth` e `hostapd_helper` con mock ConfigClient
3. Integrare `ConfigClient` anche in `rfcomm_handshake` e `tcp_server`

**Verification commands:**
```bash
# Fix dipendenza mancante
conda env update -f environment.yml --prune

# Test config_manager
python -m pytest tests/v2/test_config_manager.py -v

# Avvio stack completo
python v2/bus_broker.py &
python v2/modules/config_manager/main.py &
python v2/modules/bluetooth/main.py
```

---

## 2026-04-21 - bluetooth_ui, config_ui e system.get_modules

**What changed:**

### 1. `v2/main.py` — aggiunto responder `system.get_modules`
- Nuovo import `threading` e costante `BROKER_SUB_ADDR`
- Funzione `_module_status(proc)` → restituisce `"active"` o `"exited (<code>)"`
- Funzione `_start_get_modules_responder(processes, stop_event)` → thread daemon che
  ascolta `system.get_modules` e risponde con `system.modules_response`
- Payload risposta: `{modules: [{name, pid, status}, ...]}`
- `_stop_responder` event segnalato su Ctrl+C per terminazione pulita del thread

### 2. `v2/modules/bluetooth_ui/main.py` — nuovo modulo
Finestra PyQt6 standalone per avviare e monitorare il pairing Bluetooth.

| Direzione | Topic | Payload |
|---|---|---|
| Subscribe | `bluetooth.device.found` | `{address, name, rssi}` |
| Subscribe | `bluetooth.discovery.completed` | `{devices: [...]}` |
| Subscribe | `bluetooth.pairing.pin` | `{device_address, pin}` |
| Subscribe | `bluetooth.pairing.completed` | `{device_address}` |
| Subscribe | `bluetooth.pairing.failed` | `{device_address, error}` |
| Publish | `bluetooth.discover` | `{duration_sec: 10}` |
| Publish | `bluetooth.pair` | `{device_address}` |
| Publish | `bluetooth.confirm_pairing` | `{device_address, pin}` |

UI: bottone scan, lista dispositivi con RSSI, bottone pair, dialog PIN, status bar.
Thread safety: ZMQ gira in daemon thread, tutti gli update Qt via `QMetaObject.invokeMethod`.

### 3. `v2/modules/bluetooth_ui/tests/test_bluetooth_ui.py` — test unitari
21 test suddivisi in 6 classi: `TestInitialState`, `TestScanAction`, `TestDeviceFound`,
`TestPairAction`, `TestBusHandlers`, `TestSystemStop`.

### 4. `v2/modules/config_ui/main.py` — nuovo modulo
Finestra PyQt6 standalone per navigare e modificare la configurazione di ogni modulo.

| Direzione | Topic | Payload |
|---|---|---|
| Subscribe | `system.modules_response` | `{modules: [{name, pid, status}]}` |
| Subscribe | `config.response` | `{module, config: {key: value}}` |
| Publish | `system.get_modules` | `{}` |
| Publish | `config.get` | `{module}` |
| Publish | `config.set` | `{module, key, value}` |

UI: tab per modulo (autodiscovery via bus), form key/value editabile, bottone Salva
(pubblica solo le chiavi cambiate), bottone Ricarica per-tab e globale.

### 5. `v2/modules/config_ui/tests/test_config_ui.py` — test unitari
20 test suddivisi in 7 classi: `TestInitialState`, `TestSystemStart`, `TestModulesResponse`,
`TestConfigResponse`, `TestSaveAction`, `TestRefreshActions`, `TestSystemStop`.

**Why:**
- Validare l'infrastruttura v2 end-to-end con UI tangibili
- `bluetooth_ui` permette di verificare che il modulo `bluetooth` e il bus funzionino
- `config_ui` permette di ispezionare e modificare la config di qualsiasi modulo a runtime
- `system.get_modules` rende l'orchestratore interrogabile senza conoscere i moduli a priori

**Status:** Completed

**Next 1-3 steps:**
1. Testare `bluetooth_ui` su hardware reale con modulo `bluetooth` attivo
2. Verificare che `config_ui` mostri correttamente i tab per tutti i moduli v2
3. Aggiungere test per `v2/main.py` responder (`system.get_modules`)

**Verification commands:**
```bash
# Stack completo (tutti i moduli autodiscoverati)
python v2/main.py

# Test nuovi moduli
python -m pytest v2/modules/bluetooth_ui/tests/ v2/modules/config_ui/tests/ -v

# Standalone bluetooth_ui (richiede broker attivo)
python v2/bus_broker.py &
python v2/modules/bluetooth_ui/main.py

# Standalone config_ui (richiede broker + config_manager)
python v2/bus_broker.py &
python v2/modules/config_manager/main.py &
python v2/modules/config_ui/main.py
```

---

## 2026-05-02 - log_viewer module + bus log forwarding

**What changed:**

### 1. `v2/modules/log_viewer/main.py` — nuovo modulo UI
Finestra PyQt6 standalone che mostra in realtime tutti i log pubblicati sul bus.

| Direzione | Topic | Payload |
|---|---|---|
| Subscribe | `log.entry` | `{module, level, message, ts}` |
| Subscribe | `system.start` / `system.stop` / `system.readytostart` | standard |
| Publish | `system.module_ready` / `system.ready` | standard |

Funzionalità UI:
- `QTextEdit` read-only monospace su sfondo scuro (stile terminale)
- Colori per livello: DEBUG grigio, INFO bianco, WARNING giallo, ERROR/CRITICAL rosso
- Filtro dropdown per livello (ALL / DEBUG / INFO / WARNING / ERROR / CRITICAL)
- Auto-scroll al bottom su ogni nuovo messaggio
- Pulsante Clear
- Limite righe configurabile via `config_manager` (chiave `max_lines`, default 500)
- Priority: 2 (UI), autodiscovery automatico

### 2. `v2/shared/logger.py` — aggiunto `BusLogHandler` e `attach_bus()`
- `BusLogHandler(bus)` — `logging.Handler` che pubblica ogni record su `log.entry`
  con payload `{module, level, message, ts}`. Errori di publish silenziati.
- `attach_bus(bus)` — funzione pubblica da chiamare una volta dopo la connessione
  al broker. Aggiunge il handler a tutti i logger già creati e a quelli futuri.
- **Zero modifiche ai moduli esistenti** — il forwarding è completamente trasparente.

### 3. `v2/main.py` — step 4 aggiunto
- Import aggiornato: `from shared.logger import get_logger, attach_bus`
- Nuovo step 4 nel `run()`: crea `BusClient(module_name="main_logger")`, lo avvia,
  chiama `attach_bus()`. Da quel momento tutti i `log.*` dell'orchestratore e dei
  moduli fluiscono verso `log_viewer`.

**Why:**
- Visibilità realtime dei log di tutti i moduli senza dover leggere stdout/stderr
- Nessun accoppiamento aggiuntivo tra moduli: il logger pubblica, il viewer ascolta
- Pattern estendibile: chiunque può subscribere `log.entry` (es. log su file, alerting)

**Status:** Completed

**Next 1-3 steps:**
1. Scrivere test unitari per `BusLogHandler` e `log_viewer`
2. Aggiungere filtro per modulo (oltre al filtro per livello già presente)
3. Valutare persistenza log su file tramite un subscriber dedicato

**Verification commands:**
```bash
# Stack completo con log_viewer attivo
python v2/main.py

# Standalone log_viewer (richiede broker attivo)
python v2/bus_broker.py &
python v2/modules/log_viewer/main.py

# Verifica forwarding da qualsiasi modulo
python v2/bus_broker.py &
python v2/modules/log_viewer/main.py &
python v2/modules/bluetooth/main.py
```
