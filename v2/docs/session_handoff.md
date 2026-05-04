# Session Handoff Log

Registro delle sessioni di sviluppo, modifiche apportate e prossimi step.

---

## 2026-05-04 — config_schema: _SCHEMA per bluetooth e hostapd_helper + piano oaa_control_channel

**What changed:**
- `modules/bluetooth/main.py` — aggiunto import `field_bool`, `field_int`, `field_string` da `shared.config_schema`; aggiunto `_SCHEMA` con tutti e 4 i campi tipizzati (`discoverable: field_bool`, `discoverable_timeout: field_int(min=0)`, `discovery_duration_sec: field_int(min=1, max=120)`, `adapter_name: field_string`); aggiornata chiamata `cfg.get(defaults=_DEFAULTS, schema=_SCHEMA)`
- `modules/hostapd_helper/main.py` — aggiunto import `field_enum`, `field_int`, `field_string` da `shared.config_schema`; aggiunto `_SCHEMA` con tutti gli 11 campi tipizzati (`hw_mode: field_enum(choices=["a","g"])`, `channel: field_int(min=1, max=196)`, `monitor_timeout: field_int(min=5, max=120)`, le restanti 8 chiavi come `field_string`); aggiornata chiamata `cfg.get(defaults=_DEFAULTS, schema=_SCHEMA)`

**Why:**
I due moduli usavano `ConfigClient` ma non passavano lo schema a `cfg.get()`, quindi `config_manager` non poteva validare i valori in ingresso e `config_ui` non poteva mostrare widget tipizzati. Con l'aggiunta di `_SCHEMA` entrambi i moduli sono ora fully typed.

**Schema aggiunto — bluetooth:**

| Chiave | Tipo | Vincoli |
|---|---|---|
| `discoverable` | `field_bool` | default `True` |
| `discoverable_timeout` | `field_int` | `min=0` |
| `discovery_duration_sec` | `field_int` | `min=1, max=120` |
| `adapter_name` | `field_string` | default `NemoHeadUnit` |

**Schema aggiunto — hostapd_helper:**

| Chiave | Tipo | Vincoli |
|---|---|---|
| `interface` | `field_string` | default `wlan0` |
| `ssid` | `field_string` | default `AndroidAutoAP` |
| `hw_mode` | `field_enum` | choices `["a", "g"]` |
| `channel` | `field_int` | `min=1, max=196` |
| `ap_password` | `field_string` | default `""` |
| `subnet` | `field_string` | — |
| `gateway_ip` | `field_string` | — |
| `dhcp_range_start` | `field_string` | — |
| `dhcp_range_end` | `field_string` | — |
| `country_code` | `field_string` | default `IT` |
| `monitor_timeout` | `field_int` | `min=5, max=120` |

**Status:** Completed

---

## PIANO — oaa_control_channel: typed schema + migrazione a ConfigClient

**Problema identificato:**
`oaa_control_channel` bypassa `ConfigClient` e gestisce manualmente il ciclo `config.get` → `config.response` tramite subscriber raw. Questo è un residuo storico che impedisce la validazione dei valori e i widget tipizzati in `config_ui`. Il modulo non passa alcun schema, tutte le 16 chiavi sono non tipizzate.

**Step A — Aggiungere `_SCHEMA` in `service_discovery.py`:**

| Chiave | Tipo suggerito | Vincoli |
|---|---|---|
| `hu.name`, `hu.make`, `hu.model`, `hu.sw_version` | `field_string` | nessuno |
| `video.resolution` | `field_enum` | choices `["VIDEO_1280x720", "VIDEO_800x480", ...]` |
| `video.fps` | `field_enum` | choices `["_30", "_60"]` |
| `video.dpi` | `field_int` | `min=72, max=320` |
| `touch.width`, `touch.height` | `field_int` | `min=480` |
| `audio.media.sample_rate`, `audio.speech.sample_rate` | `field_int` | choices fisse → valutare `field_enum` |
| `audio.system.sample_rate` | `field_int` | choices fisse (16000) → `field_enum` |
| `audio.media.channel_count` | `field_int` | `min=1, max=2` |
| `nav.min_interval_ms` | `field_int` | `min=100, max=5000` |
| `nav.image.width`, `nav.image.height` | `field_int` | `min=32, max=256` |

**Step B — Migrare `main.py` a `ConfigClient`:**
- Aggiungere `cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)`
- Rimuovere il publish manuale di `config.get` da `on_system_start` e il subscriber `on_config_response`
- Spostare la logica attuale di `on_config_response` in `_on_config_loaded(config: dict)`
- Rinominare `on_config_changed` → mantenuto ma collegato a `cfg.on_config_changed`
- Chiamare `cfg.get(defaults=DEFAULTS, schema=_SCHEMA)` in `on_system_start`
- Chiamare `cfg.register()` in `run()` prima delle subscribe

**Step C — Verificare che `system.ready` sia pubblicato solo dopo `_on_config_loaded`:**
Attualmente il modulo pubblica `system.ready` all'interno di `on_config_response`. Con `ConfigClient`, lo stesso comportamento deve essere garantito nel callback `_on_config_loaded`.

**Rischio:** il modulo usa la config *prima* di pubblicare `system.ready` (necessario per costruire la `ServiceDiscoveryResponse`). La migrazione a `ConfigClient` non deve rompere questa garanzia.

**Status:** Da fare (prossima sessione)

**Next 1-3 steps:**
1. Aggiungere `_SCHEMA` in `service_discovery.py` (Step A)
2. Migrare `oaa_control_channel/main.py` a `ConfigClient` (Step B + C)
3. Aggiungere test unitari per `_on_config_loaded` e `_on_config_changed` in `oaa_control_channel`

---

## 2026-05-04 — config_schema: tipo `bool` + QCheckBox in config_ui

**What changed:**
- `shared/config_schema.py` — aggiunto `"bool"` al `Literal` dei tipi; aggiunte costanti `_BOOL_TRUE`/`_BOOL_FALSE` per coercion flessibile (accetta `True/False`, `1/0`, `"true"/"false"`, `"yes"/"no"`, `"on"/"off"`, case-insensitive); aggiunta factory `field_bool(default: bool = False)`; aggiunto branch `bool` in `validate_value()` con passthrough nativo Python `bool`/`int` e coercion da stringa
- `modules/config_ui/main.py` — aggiunto `QCheckBox` agli import PyQt6; aggiunta costante locale `_BOOL_TRUE`; aggiunto metodo `_build_bool()` in `_FieldWidget` (crea `QCheckBox` checked/unchecked in base al valore raw); aggiunto branch `elif field_schema.type == "bool"` in `__init__`; aggiunto branch `"checkbox"` in `get_value()` (ritorna `bool` nativo); confronto bool-aware in `_on_save()` per evitare falsi positivi `True vs "True"`; docstring widget selection aggiornato con riga `bool → QCheckBox`
- `modules/_template/main.py` — aggiunto `field_bool` alla riga di import da `config_schema`; aggiornati commenti di `_DEFAULTS` e `_SCHEMA` con esempio `"enabled": field_bool(default=True)`

**Why:**
I moduli che necessitano di toggle semplici (abilitare/disabilitare una feature) non avevano un tipo dedicato: erano costretti a usare `field_enum(choices=["off", "on"])` o `field_string`. Il nuovo tipo `bool` copre questo caso con un widget nativo (`QCheckBox`) e una validazione robusta che gestisce tutte le forme comuni del valore booleano.

**Coercion `bool` accettata:**

| Input | Risultato |
|---|---|
| `True` / `False` | passthrough |
| `1` / `0` | `True` / `False` |
| `"true"`, `"yes"`, `"on"`, `"1"` | `True` |
| `"false"`, `"no"`, `"off"`, `"0"` | `False` |
| qualsiasi altro | `ValueError` |

**Status:** Completed

**Next 1-3 steps:**
1. Scrivere test unitari per `field_bool()` e `validate_value()` (tutti i casi coercion + `ValueError`)
2. Aggiornare un modulo reale (es. `bluetooth`) con `field_bool` per almeno una chiave (es. `auto_connect`)
3. Aggiungere test per `config_manager.on_config_set` con tipo `bool` (verifica che la validazione rifiuti input non validi)

---

## 2026-05-04 — config_schema: typed widgets e validazione lato config_manager

**What changed:**
- `shared/config_schema.py` — creato: `ConfigFieldSchema` dataclass con campi `type`, `default`, `min`, `max`, `choices`; factory helpers `field_string`, `field_int`, `field_float`, `field_enum`; `schema_to_dict` / `schema_from_dict` per serializzazione bus-safe; `validate_value()` con coercion e range check
- `shared/config_client.py` — `cfg.get()` ora accetta `schema=_SCHEMA`; lo schema viene serializzato con `schema_to_dict` e incluso nel payload di `config.get`
- `modules/config_manager/main.py` — aggiunto `_schemas: dict` (RAM); `on_config_get` salva lo schema se presente nel payload; `on_config_set` valida il valore con `validate_value()` prima di persistere, pubblica `config.error {module, key, value, reason}` in caso di errore
- `modules/config_ui/main.py` — `_FieldWidget` con widget tipizzati: `QLineEdit` (string), `QSlider`/`QLineEdit±` (int/float), `QComboBox` (enum); badge `[TYPE]` accanto al label; `mark_error()` + `set_error()` per errori inline; `on_config_error` subscriber
- `modules/_template/main.py` — aggiunto pattern `_SCHEMA` con import e commenti per tutti i tipi disponibili

**Why:**
Prima tutta la configurazione era priva di tipo: ogni chiave era una stringa libera, senza validazione né widget dedicato. Ora il contratto tra modulo e config_manager è esplicito, la UI mostra il widget corretto per ogni tipo e i valori non validi vengono rifiutati con feedback inline.

**Status:** Completed

---

## 2026-05-04 — Graceful session restart: flusso completo SHUTDOWN → VERSION_REQUEST

**What changed:**
- `oaa_control_channel/service_discovery.py` — fix: tutti i builder privati ora restituiscono `ChannelDescriptor` (non `bytes`); rimosso `encode_proto` da ogni builder; `build_service_discovery_response` usa `resp.channels.add().MergeFrom(desc)` invece di `resp.channels.append(bytes)`
- `tcp_server/main.py` — aggiunto `on_aa_session_restart`: invia `SHUTDOWN_REQUEST` (ch0 msgId 0x000D) al phone, attende `SHUTDOWN_RESPONSE` via `_shutdown_ack_event` (timeout 3s), chiama `cryptor.deinit()`, pubblica `aa.session.restarting`; aggiunto `on_ch0_frame`: subscriber su `aa.frame.ch0`, segnala `_shutdown_ack_event` quando riceve msgId 0x000E solo se `_restart_pending=True`; aggiunto flag `_restart_pending` per distinguere restart da disconnessione accidentale in `_on_session_closed`; aggiunte subscribe per `aa.session.restart` e `aa.frame.ch0`
- `oaa_control_channel/main.py` — aggiunto `on_aa_session_restarting`: riceve `aa.session.restarting` da tcp_server, crea un nuovo `ControlChannelHandshake` con il `_cfg` già aggiornato, pubblica `aa.handshake.state=IDLE`, chiama `send_version_request()`; aggiunta subscribe per `aa.session.restarting`

**Why:**
Prima il restart di sessione (causato da un cambio di config) non aveva un flusso definito: `oaa_control_channel` pubblicava `aa.session.restart` ma nessuno gestiva la chiusura ordinata verso il phone né il reset del cryptor TLS. Ora il flusso è completo e deterministico: shutdown protocol → reset SSL → nuovo handshake sulla stessa connessione TCP.

**Flusso restart completo:**

```
config.changed
    │ oaa_control_channel: _cfg[key]=value, _handshake=None
    ▼
aa.session.restart
    │ tcp_server: SHUTDOWN_REQUEST (ch0 0x000D) → phone
    │ tcp_server: attende SHUTDOWN_RESPONSE (max 3s)
    │ tcp_server: cryptor.deinit()
    ▼
aa.session.restarting
    │ oaa_control_channel: _make_handshake() (cfg aggiornato)
    ▼
VERSION_REQUEST → phone  (handshake riparte sulla stessa TCP conn)
```

**Nuovi messaggi bus:**

| Messaggio | Da | A | Payload |
|---|---|---|---|
| `aa.session.restart` | `oaa_control_channel` | `tcp_server` | `{}` |
| `aa.session.restarting` | `tcp_server` | `oaa_control_channel` | `{}` |

**Status:** Completed

**Next 1-3 steps:**
1. Aggiungere test unitari per `on_aa_session_restart` in `tcp_server` (ack ricevuto, timeout, cryptor reset)
2. Aggiungere test unitari per `on_aa_session_restarting` in `oaa_control_channel` (nuovo handshake + version request)
3. Aggiungere test unitari per `on_config_response` e `on_config_changed` in `oaa_control_channel/main.py`

---

## 2026-05-04 — Config integration: service_discovery ora legge da config_manager

**What changed:**
- `oaa_control_channel/service_discovery.py` — rimossi `HU_NAME`, `HU_MAKE`, `HU_MODEL`, `HU_SW_VERSION` come costanti globali; aggiunto `DEFAULTS: dict` con tutte le chiavi namespace (es. `hu.name`, `video.dpi`, `nav.min_interval_ms`); `build_service_discovery_response()` ora accetta `cfg: dict` al posto delle costanti; tutti i builder che usano valori configurabili accettano `cfg: dict`; i valori di protocollo (channel_id, keycodes, bit_depth, ecc.) restano hardcoded con commento `# protocol constant`
- `oaa_control_channel/handshake.py` — aggiunto `cfg: dict | None = None` a `ControlChannelHandshake.__init__`; `_cfg` salvato come `self._cfg`; passato `cfg=self._cfg` a `build_service_discovery_response` in `_on_service_discovery_request`
- `oaa_control_channel/main.py` — aggiunto `_cfg: dict` (inizializzato con `DEFAULTS`) e `_cfg_loaded: bool`; `on_system_start()` pubblica `config.get` con defaults (first-boot seeding); `on_config_response()` aggiorna `_cfg` e pubblica `system.ready` solo dopo la risposta; `on_config_changed()` aggiorna `_cfg`, azzera `_handshake`, pubblica `aa.session.shutdown` + `aa.session.restart`; aggiunte subscribe per `config.response` e `config.changed`; `_make_handshake()` passa `cfg=_cfg`

**Why:**
Tutte le impostazioni utente erano hardcoded in `service_discovery.py`. Ora sono persistite in `config/oaa_control_channel.yaml` tramite `config_manager`. Al boot il modulo chiede la config prima di pubblicare `system.ready`. Se un valore cambia a runtime, la sessione attiva viene chiusa e `aa.session.restart` segnala a `tcp_server` di gestire lo shutdown ordinato.

**Nuovi messaggi bus:**

| Messaggio | Da | A | Payload |
|---|---|---|---|
| `config.get` | `oaa_control_channel` | `config_manager` | `{module, requester, defaults}` |
| `config.response` | `config_manager` | `oaa_control_channel` | `{module, config, requester}` |
| `config.changed` | `config_manager` | `oaa_control_channel` | `{module, key, value}` |

**Chiavi configurabili (oaa_control_channel.yaml):**

| Chiave | Default | Descrizione |
|---|---|---|
| `hu.name` | `NemoHeadUnit` | Nome HU visibile sul telefono |
| `hu.make` | `Nemo` | Produttore |
| `hu.model` | `NemoHeadUnit-Wireless` | Modello |
| `hu.sw_version` | `2.0` | Versione SW |
| `video.resolution` | `VIDEO_1280x720` | Risoluzione video (enum name) |
| `video.fps` | `_30` | FPS (enum name) |
| `video.dpi` | `140` | DPI schermo |
| `touch.width` | `1280` | Larghezza touch (px) |
| `touch.height` | `720` | Altezza touch (px) |
| `audio.media.sample_rate` | `48000` | Sample rate media audio (Hz) |
| `audio.media.channel_count` | `2` | Canali media audio |
| `audio.speech.sample_rate` | `48000` | Sample rate speech (Hz) |
| `audio.system.sample_rate` | `16000` | Sample rate system audio (Hz) |
| `nav.min_interval_ms` | `500` | Intervallo minimo navigazione (ms) |
| `nav.image.width` | `64` | Larghezza immagine nav (px) |
| `nav.image.height` | `64` | Altezza immagine nav (px) |

**Status:** Completed

---

## 2026-05-04 — Refactor TLS: AACryptor ownership spostata in tcp_server

**What changed:**
- `tcp_server/aa_cryptor.py` — creato (spostato da `oaa_control_channel/`), logger aggiornato a `tcp_server.aa_cryptor`
- `tcp_server/aa_certs.py` — eliminato (i certificati erano già embedded in `aa_cryptor.py`)
- `tcp_server/main.py` — aggiunto ownership di `AACryptor`: stato `_cryptor`, handler `on_handshake_start_tls` e `on_handshake_feed_input`, decrypt automatico dei frame cifrati in `_on_frame`, reset del cryptor in `_teardown`
- `oaa_control_channel/handshake.py` — rimosso `AACryptor` e tutta la logica TLS locale; aggiunto `publish_fn` come parametro costruttore; aggiunti `on_tls_handshake_blob()` e `on_tls_complete()` per ricevere il risultato dal bus
- `oaa_control_channel/main.py` — passato `publish_fn=bus.publish` a `ControlChannelHandshake`; aggiunti handler `on_tls_handshake` e `on_tls_handshake_completed`; aggiunte due subscribe
- `oaa_control_channel/aa_cryptor.py` — eliminato

**Why:**
In precedenza `AACryptor` viveva in `oaa_control_channel`, che gestiva autonomamente sia la negoziazione TLS che il decrypt dei frame post-handshake. Questo impediva a `tcp_server` di decriptare i frame cifrati su canali diversi da ch0. Ora `tcp_server` possiede il cryptor, decripta i frame in ingresso prima di pubblicarli sul bus (payload sempre in chiaro per i subscriber), e gestisce il loop TLS tramite messaggi bus con `oaa_control_channel`.

**Nuovi messaggi bus introdotti:**

| Messaggio | Da | A | Payload |
|---|---|---|---|
| `aa.handshake.start_tls` | `oaa_control_channel` | `tcp_server` | `{}` |
| `aa.handshake.feed_input` | `oaa_control_channel` | `tcp_server` | `{payload_hex}` |
| `tcp.server.tls_handshake` | `tcp_server` | `oaa_control_channel` | `{outgoing_hex}` |
| `tcp.server.tls_handshake_completed` | `tcp_server` | `oaa_control_channel` | `{}` |

**Status:** Completed

---

## 2026-05-04 — Schema strutturato: config_manager + oaa_control_channel + config_ui

**What changed:**
- `modules/config_manager/main.py` — import esteso con `ConfigFieldSchema`, `schema_to_dict`; `_schema_dict_for_response()` ora usa `schema_to_dict(schema)` invece del loop manuale `{k: v.to_dict()}` che rompeva su nodi strutturati; in `on_config_set()` aggiunto guard `isinstance(field_schema, ConfigFieldSchema)` prima di chiamare `validate_value()` — i nodi strutturati (Message/List/Oneof) vengono persistiti as-is con `log.debug`
- `modules/oaa_control_channel/main.py` — import esteso con `_SCHEMA` da `service_discovery`; `on_system_start()` ora chiama `cfg.get(defaults=_CFG_DEFAULTS, schema=_SCHEMA)` invece di `cfg.get(defaults=_CFG_DEFAULTS)`; il docstring Config flow aggiornato
- `modules/config_ui/main.py` — import esteso con `ConfigFieldSchema`; estratta nuova funzione `_schema_type_badge(field_schema)` che legge `.type` solo su `ConfigFieldSchema`, per i nodi strutturati usa `type().__name__.replace("ConfigField","").upper()` → badge `[MESSAGE]`/`[LIST]`/`[ONEOF]`; in `ModuleConfigTab.populate()` aggiunto guard `isinstance(ConfigFieldSchema)` — i nodi strutturati passano `scalar_schema=None` a `_FieldWidget` (QLineEdit fallback)

**Why:**
`config_schema` supporta ora nodi strutturati (`ConfigFieldMessage`, `ConfigFieldList`, `ConfigFieldOneof`) oltre ai tipi scalari. I tre moduli che interagiscono con lo schema avevano codice che assumeva solo `ConfigFieldSchema` scalare: `config_manager` rompeva la serializzazione della risposta, `oaa_control_channel` non passava lo schema strutturato al boot, `config_ui` avrebbe sollevato `AttributeError` sul badge `.type`.

**Flusso completo ora funzionante:**
```
boot
 └─ oaa_control_channel.on_system_start()
     └─ cfg.get(defaults=_CFG_DEFAULTS, schema=_SCHEMA)
         └─ bus.publish("config.get", {schema: schema_to_dict(_SCHEMA)})
             └─ config_manager.on_config_get()
                 ├─ schema_from_dict(raw_schema) → _schemas["oaa_control_channel"]
                 └─ bus.publish("config.response", {schema: schema_to_dict(...)})
                     └─ config_ui riceve schema proto-derivato, renderizza badge corretti
```

**Commit:**

| SHA | File | Descrizione |
|---|---|---|
| `2515d08` | `config_manager/main.py` | `schema_to_dict()` + guard scalare in `on_config_set` |
| `0e85e78` | `oaa_control_channel/main.py` | Passa `schema=_SCHEMA` a `cfg.get()` |
| `a18570f` | `config_ui/main.py` | Badge + populate() sicuri su nodi strutturati |

**Status:** Completed

**Next 1-3 steps:**
1. Scrivere test unitari per `config_manager`: `_schema_dict_for_response()` con nodi strutturati, guard scalare in `on_config_set`
2. Scrivere test unitari per `config_ui`: `_schema_type_badge()` per tutti i tipi, `populate()` con schema misto scalare+strutturato
3. Verificare che `ConfigClient.get(schema=_SCHEMA)` serializzi correttamente `_SCHEMA` con nodi strutturati (test su `shared/config_client.py`)
