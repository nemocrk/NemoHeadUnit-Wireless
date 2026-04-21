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
