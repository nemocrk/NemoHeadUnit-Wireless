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

**YAML layout:** `v2/config/<module_name>.yaml` — un file per modulo.

**Status:** Completed

---

## 2026-04-21 - config_manager full integration

**Status:** Completed

**Verification commands:**
```bash
conda env update -f environment.yml --prune
python -m pytest tests/v2/test_config_manager.py -v
```

---

## 2026-04-21 - bluetooth_ui, config_ui e system.get_modules

**Status:** Completed

**Verification commands:**
```bash
python v2/main.py
python -m pytest v2/modules/bluetooth_ui/tests/ v2/modules/config_ui/tests/ -v
```

---

## 2026-05-02 - log_viewer module + bus log forwarding

**Status:** Completed

**Verification commands:**
```bash
python v2/main.py
python v2/bus_broker.py &
python v2/modules/log_viewer/main.py
```

---

## 2026-05-02 - Fix: attach_bus in tutti i moduli subprocess

**Status:** Completed

---

## 2026-05-02 - Fix: ImportError attach_bus nei test v2

**Status:** Completed

**Verification commands:**
```bash
python -m pytest tests/v2/ -v
# Atteso: 128 test raccolti, 0 errori di collection
```

---

## 2026-05-02 - Pulsante spegnimento in config_ui + nmcli reconnect + system.shutdown handler

**Status:** Completed

| File modificato | Commit |
|---|---|
| `v2/modules/config_ui/main.py` | ce147bb |
| `v2/modules/hostapd_helper/ap_manager.py` | 6f6645f |
| `v2/main.py` | 4065b8e |

---

## 2026-05-03 - OAA control channel handshake v2

**Status:** In Progress

**Next 1-3 steps:**
1. Aggiungere `v2/modules/oaa_control_channel/__init__.py` e verificare autodiscovery
2. Aggiungere test dedicati per `proto_utils`, `frame_codec`, `ControlChannelHandshake`
3. Validare il wire format reale contro una cattura telefono

---

## 2026-05-04 - config_ui modularization

**What changed:**

`v2/modules/config_ui/main.py` decomposto in 4 file specializzati.

| File | Responsabilità |
|---|---|
| `field_widgets.py` | `_FieldWidget`, `_ScalarListEditor` |
| `form_builder.py` | `build_form_for_schema()`, `_FormWidget` |
| `list_editor.py` | `_ListEditor`, `_AccordionItem` v2 |
| `module_tab.py` | `ModuleConfigTab` |
| `main.py` | `ConfigWindow`, bus handlers, lifecycle |

**Status:** Completed

---

## 2026-05-04 - config_ui: nested schema UX, _OneofWidget, _OptionalMessageWidget, validazione cascata

**What changed:**

### 1. `field_widgets.py` — nuovi widget per tipi strutturati

- **`_OneofWidget`**: `QComboBox` per selezionare il branch attivo + body collassabile
  ricostruito ad ogni cambio branch. `get_value()` restituisce il valore del branch
  direttamente (non wrappato in `{branch: value}`). Placeholder "— seleziona tipo —"
  quando nessun branch è pre-selezionabile.
- **`_OptionalMessageWidget`**: checkbox "attivo" che mostra/nasconde il sotto-form.
  - Unchecked → `get_value()` restituisce `None` (campo omesso dal payload)
  - Checked → `get_value()` restituisce il dict del sotto-form
- **`validate()`** aggiunto a `_OptionalMessageWidget`:
  - Unchecked → `[]` (nessuna validazione, il messaggio è assente)
  - Checked → delega a `self._body.validate()` se disponibile

### 2. `form_builder.py` — routing verso i nuovi widget

- Branch `ConfigFieldOneof` → istanzia `_OneofWidget`
- Branch `ConfigFieldMessage(optional=True)` → istanzia `_OptionalMessageWidget`
- Branch `ConfigFieldMessage(optional=False)` → `_FormWidget` inline (invariato)
- `build_default_value(schema)` esportata — usata da `list_editor._default_item()`

### 3. `list_editor.py` — default item ricorsivo

- `_default_item()` delegato a `build_default_value()` per correttezza ricorsiva
  su strutture con `oneof` annidati
- `_btn_del` salvato come attributo diretto sull'`_AccordionItem` (elimina fragile
  accesso via `layout().itemAt(n)`)

### 4. `module_tab.py` — refactor + validazione inline

- `_list_editors` rinominato in `_struct_editors` (contiene qualsiasi editor
  strutturato: lista, message, oneof)
- `_validate()` esteso con cascata su `_struct_editors`:
  ```python
  for key, editor in self._struct_editors.items():
      if hasattr(editor, "validate"):
          for e in editor.validate():
              errors.append(f"'{key}' → {e}")
  ```
- Error banner rosso visibile sotto il pulsante Salva quando la validazione fallisce
- Rilevamento chiavi rimosse (optional fields unchecked → `changed[key] = None`)

### 5. `main.py` — pulizia legacy

- Rimossi `_AccordionItem`, `_ListFieldInlineEditor`, `_build_message_form`
- Import snelliti (rimossi ~10 import non più necessari)

**Why:**
- Un messaggio opzionale annidato deve mostrare i suoi campi solo se esplicitamente
  attivato dall'utente tramite checkbox — UX richiesta esplicitamente nella sessione
- La validazione dei campi required dentro un optional message non deve scattare
  quando il messaggio è disattivato (checkbox unchecked)
- Il codice legacy in `main.py` era ridondante dopo la modularizzazione

**Status:** Completed

**Next 1-3 steps:**
1. Aggiungere `validate()` anche a `_OneofWidget` e `_ListEditor` per completare
   la copertura della validazione cascata su tutti i tipi strutturati
2. Aggiungere test unitari per `_OptionalMessageWidget.validate()` e
   `ModuleConfigTab._validate()` con struct editors
3. Aggiornare `tests/v2/test_config_ui.py` per coprire i nuovi widget

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `field_widgets.py` | [fe8f2f6](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/fe8f2f6a6f0c0d83138a0650470485519a2bd957) | `_OneofWidget`, `_OptionalMessageWidget` + `validate()` |
| `form_builder.py` | [ac67c39](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/ac67c39682949c0b6bf12308cc04eb89ef34988e) | routing oneof/optional, `build_default_value()` |
| `list_editor.py` | [7e9d510](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/7e9d51077b67c736b4b03b9593bde800f1c90ee2) | `_default_item()` ricorsivo, `_btn_del` come attributo |
| `module_tab.py` | [4d9ff58](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/4d9ff58cc4c2c53a51140754e12fa139fa8b394b) | `_struct_editors`, cascade `validate()`, error banner |
| `main.py` | [5c758bb](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/5c758bbf0f583f088bb42abc447bd36f735d294e) | Rimosso tutto il codice legacy |

**Verification commands:**
```bash
python -c "from v2.modules.config_ui.field_widgets import _OptionalMessageWidget; print('OK')"
python -c "from v2.modules.config_ui.form_builder import build_form_for_schema, build_default_value; print('OK')"
python -c "from v2.modules.config_ui.module_tab import ModuleConfigTab; print('OK')"
python -m pytest v2/modules/config_ui/tests/ -v
python v2/bus_broker.py &
python v2/modules/config_manager/main.py &
python v2/modules/config_ui/main.py
```

---

## 2026-05-04 - video module — AA video channel handler

**What changed:**

Creato `v2/modules/video/main.py` — modulo che gestisce il canale video Android Auto
con scoperta dinamica del channel id e flow control via MediaAck.

**Status:** Completed

**Commit:**

| File | Commit |
|---|---|
| `v2/modules/video/main.py` | [285a76a](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/285a76a2ebefddc093d9f3cfc892503cf832a1ac) |

---

## 2026-05-04 - input module — AA input channel handler

**What changed:**

Creato `v2/modules/channel_modules/input/main.py` — modulo che gestisce il canale
Input Android Auto (touch + tasti fisici).

**Status:** Completed

---

## 2026-05-04 - sensor module — AA sensor channel handler

**What changed:**

Creato `v2/modules/channel_modules/sensor/main.py` — modulo che gestisce il canale
Sensor Android Auto (DrivingStatus, NightMode, GPS).

**Status:** Completed

**Commit:**

| File | Commit |
|---|---|
| `v2/modules/channel_modules/sensor/main.py` | [9ed34d0](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ed34d08891e13f3070ff1de808bff4f842ee824) |

---

## 2026-05-04 - channel_manager registry rewrite + service_discovery fix

**What changed:**

Riscritti `registry.py` e `main.py` di `channel_manager`, e fix chirurgico a
`service_discovery.py` di `oaa_control_channel`.

**Status:** Completed

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `v2/modules/oaa_control_channel/service_discovery.py` | [81cc925](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/81cc9256f2ccfefa8f2bf62e8193255458e3def6) | Espone `audio_type` in `channels_from_sdr_bytes` |
| `v2/modules/channel_manager/registry.py` | [bbfee3a](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/bbfee3aea79792f152c0c56d513d39b67616475b) | Riscrittura completa: descriptor-key routing, costanti corrette, `SkipChannel` |
| `v2/modules/channel_manager/main.py` | [b062ee4](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b062ee41f767ea76ca1eb5bdc3451fe2f51e59fc) | Cattura `SkipChannel` con warning invece di abort |

---

## 2026-05-05 - channel_modules refactor: discovery dinamica → CLI (--channel-id)

**What changed:**

Rimossa la discovery dinamica del `channel_id` (pattern `config.get` /
`config.response` / `_resolve_*_channel`) da tutti i moduli channel che la
usavano ancora. Ora tutti i moduli ricevono `channel_id` e SDR bytes
esclusivamente via argomenti CLI (`--channel-id`, `--sdr-bytes-hex`),
coerentemente con quanto già implementato in `audio/main.py` e con la logica
di `BaseChannelModule`.

**Moduli aggiornati:**

| Modulo | Prima | Dopo | Commit |
|---|---|---|---|
| `input/main.py` | discovery dinamica via `config.response` | `CHANNEL_ID = -1`, `_init()` solo loga | [182047e](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/182047e99727418cbaaaaae90aaec2bc43b010f6) |
| `sensor/main.py` | discovery dinamica via `config.response` | `CHANNEL_ID = -1`, `_init()` solo loga | [9aad7dc](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9aad7dcc8ba92ce6fc5fbf6068c25579c50dee5d) |
| `video/main.py` | già allineato | invariato | — |
| `audio/main.py` | già allineato | invariato | — |

**Dettaglio rimozioni per modulo:**
- `on_config_response()` → rimosso
- `_request_oaa_config()` → rimosso
- `_resolve_*_channel()` → rimosso
- `_channel_resolved` flag → rimosso
- `_*_CHANNEL_FALLBACK` costante → rimossa
- Subscription a `config.response` in `run()` → rimossa
- `_init()` ora logga solo `channel_id` e `channel_config` per debug

**Why:**
- `system.ready` deve essere pubblicato da `BaseChannelModule` in modo uniforme,
  gated su `channel_config is not None` (già implementato in base).
- La discovery tramite `config.get`/`config.response` era un pattern parallelo
  e ridondante rispetto all'approccio CLI già adottato da `audio`.
- Eliminare la dipendenza da `oaa_control_channel` nei moduli channel semplifica
  il grafo delle dipendenze e rende ogni modulo testabile in isolamento.
- La responsabilità di sapere quale `channel_id` assegnare a quale modulo
  appartiene a `channel_manager`, non ai moduli stessi.

**Status:** Completed

**Next 1-3 steps:**
1. Verificare che `BaseChannelModule` pubblichi correttamente `system.ready`
   quando `channel_config` è valorizzato dopo il parsing CLI
2. Aggiungere test unitari per `InputModule` e `SensorModule` con mock
   `--channel-id` / `--sdr-bytes-hex`
3. Aggiornare `channel_manager/main.py` per passare `--channel-id` e
   `--sdr-bytes-hex` correttamente allo spawn di `input` e `sensor`

**Verification commands:**
```bash
python -c "from v2.modules.channel_modules.input.main import InputModule; print('OK')"
python -c "from v2.modules.channel_modules.sensor.main import SensorModule; print('OK')"
```
