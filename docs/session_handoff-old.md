# Session Handoff Documentation

## 2026-04-21 - Multi-threaded Message Bus Architecture

**What changed:**
- Enhanced MessageBus with thread affinity pattern for ZMQ sockets
- Added BusClient helper for modules to publish/subscribe
- Implemented topic-based routing in broker

**Why:**
ZMQ sockets are not thread-safe. The previous implementation shared sockets
across threads causing intermittent crashes under load.

**Status:** Completed

**Next 1-3 steps:**
1. Add integration tests for bus broker
2. Document bus topic naming conventions
3. Add bus health monitoring

---

## 2026-04-24 - BaseChannelModule + channel_manager scaffold

**What changed:**

`v2/modules/channel_modules/` — struttura base per i moduli canale AA.

| File | Descrizione |
|---|---|
| `base.py` | `BaseChannelModule` ABC: `setup()`, `handle_message()`, `send_frame()`, `_is_ready()` |
| `channel_manager.py` | Registry + dispatch: mappa `channel_id → BaseChannelModule`, routing messaggi in ingresso |
| `_template/main.py` | Reference implementation commentata per nuovi moduli |

**Why:**
Ogni canale AA (video, audio, sensor, av_input, nav…) ha lo stesso lifecycle:
handshake SETUP → OPEN → messaggi. `BaseChannelModule` codifica questo pattern
una volta sola; i moduli concreti implementano solo `_handle_setup_request` e
`_handle_channel_specific`.

**Status:** Completed

**Next 1-3 steps:**
1. Implementare `VideoModule(BaseChannelModule)` in `channel_modules/video/main.py`
2. Implementare `AudioModule(BaseChannelModule)` in `channel_modules/audio/main.py`
3. Collegare `channel_manager` al loop principale in `oaa_control_channel/main.py`

---

## 2026-04-28 - proto_utils: encode/decode AA frame + build_media_with_timestamp

**What changed:**

`v2/shared/proto_utils.py` — funzioni wire-format condivise tra tutti i moduli canale.

| Funzione | Descrizione |
|---|---|
| `encode_aa_frame(channel, flags, msg_type, payload)` | Impacchetta header 4-byte + payload in bytes |
| `decode_aa_frame(data)` | Spacchetta bytes → `(channel, flags, msg_type, payload)` |
| `build_media_with_timestamp(ts_us, pcm_bytes)` | Costruisce payload `AV_MEDIA_WITH_TIMESTAMP` (8-byte header ts + PCM) |
| `parse_media_with_timestamp(payload)` | Inverso: estrae `(ts_us, pcm_bytes)` |

**Why:**
Prima ogni modulo reimplementava encode/decode AA frame in modo leggermente
diverso. Centralizzare in `proto_utils` garantisce compatibilità wire format
consistente con `openauto-prodigy AVInputChannelHandler::sendMicData`.
Public API docstring aggiornato.

### 2. `v2/modules/channel_modules/av_input/__init__.py` — scaffold

File minimo creato per rendere la directory un package Python.

### 3. `v2/modules/channel_modules/av_input/main.py` — implementazione completa

`AVInputModule(BaseChannelModule)` — canale AVInput (ch.7 default).

| Aspetto | Dettaglio |
|---|---|
| Cattura | `sounddevice.RawInputStream` 48kHz/mono/16-bit |
| Lifecycle | Controllato dal telefono via `INPUT_OPEN_REQUEST(open=True/False)` |
| Handshake | `SETUP_REQUEST` → `CHANNEL_OPEN_REQUEST` → `INPUT_OPEN_REQUEST` |
| Invio frame | `build_media_with_timestamp(ts_us, pcm)` + `send_frame(AV_MEDIA_WITH_TIMESTAMP, ...)` |
| Config keys | `mic_device` (enum, default=`"default"`), `max_unacked` (int, default=1) |
| Bus topics | `av_input.state`, `av_input.mic_started`, `av_input.mic_stopped` |
| Timestamp | `time.monotonic_ns() // 1000` in µs nel callback sounddevice |
| `_is_ready()` | Sempre `True` — lo stream è aperto solo on-demand |

**Why:**
- Il canale AVInput è la controparte del canale audio: anziché ricevere media
  dal telefono e riprodurlo localmente, acquisisce dal microfono HU e invia
  PCM grezzo al telefono per il riconoscimento vocale AA.
- `build_media_with_timestamp` appartiene a `proto_utils` perché è wire format,
  non business logic — riutilizzabile da qualsiasi modulo che invii media con timestamp.

**Status:** Completed

**Commit map:**

| File | Commit |
|---|---|
| `v2/shared/proto_utils.py` | [88e5aab](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/88e5aab8515ab2eb40f03102ed758f9b4e4417bc) |
| `v2/modules/channel_modules/av_input/__init__.py` | [92104c6](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/92104c65c33bde08ae37f0e28d83404cb959e9a0) |
| `v2/modules/channel_modules/av_input/main.py` | [2ff45cc](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/2ff45ccd3398008f76c19974560f0615fee08622) |

**Next 1-3 steps:**
1. ⏳ Aggiungere test unitari in `tests/v2/test_av_input.py`:
   - `test_build_media_with_timestamp` (round-trip con `parse_media_with_timestamp`)
   - `test_handle_setup_request` (verifica payload `AVChannelSetupResponse`)
   - `test_handle_input_open_request_start` / `_stop` (mock sounddevice)
   - `test_mic_callback` (verifica che `send_frame` venga chiamato con payload corretto)
2. ✅ Compatibilità wire format con `openauto-prodigy` verificata su hardware
3. ✅ `av_input` registrato nel registry del `channel_manager`

**Verification commands:**
```bash
python -c "from shared.proto_utils import build_media_with_timestamp; print('OK')"
python -c "from v2.modules.channel_modules.av_input.main import AVInputModule; print('OK')"
python -m pytest tests/v2/ -v
```
## 2026-05-06 - ch0 focus handlers: AudioFocus, NavigationFocus, VoiceSession, BatteryStatus

**What changed:**

`v2/modules/oaa_control_channel/handshake.py` — aggiunti 4 handler per messaggi ch0
precedentemente non gestiti (causavano blocco della sessione wireless AA).

| msg_id | Nome | Handler | Comportamento |
|---|---|---|---|
| 18 (0x0012) | `AUDIO_FOCUS_REQUEST` | `_on_audio_focus_request` | Risponde `GAIN + granted=True`; se stato `CHANNELS_OPENING` → `ACTIVE` (secondo trigger wireless) |
| 13 (0x000D) | `NAVIGATION_FOCUS_REQUEST` | `_on_navigation_focus_request` | Risponde sempre `NAV_FOCUS_PROJECTED` |
| 17 (0x0011) | `VOICE_SESSION_REQUEST` | `_on_voice_session_request` | Solo log START/STOP, nessuna risposta |
| 23 (0x0017) | `BATTERY_STATUS_NOTIFICATION` | `_on_battery_status_notification` | Log level, time_remaining_s, critical_battery |

**Why:**
Su Android Auto wireless il telefono manda `AUDIO_FOCUS_REQUEST (0x0012)` prima
(o al posto) del `PING_REQUEST`. Senza handler la sessione non diventava mai ACTIVE.

**Status:** Completed

**Commit:** [a505b88](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a505b887a5b8f05df864fce06e95fa343bb7174a)

**Next 1-3 steps:**
1. ⏳ Test unitari per i 4 nuovi handler in `tests/v2/test_handshake.py`
2. ✅ Test end-to-end su hardware wireless — OK
3. ✅ `PING_REQUEST` non causa doppia callback `on_active` — verificato

---
# Session Handoff Log

Registro delle sessioni di sviluppo, modifiche apportate e prossimi step.

---

## 2026-05-08 — AA wireless video/audio debug: SPS/PPS, audio mute, shutdown hang

**Current user report:**
- Video: non mostra piu' warning dopo la correzione SPS/PPS; potenzialmente OK.
- Audio: ancora muto, audio mai riprodotto finora nell'app WIP.
- Shutdown/freezing: dopo un run video/audio, `Ctrl+C received` appare alle `16:24:40`, ma `bus_broker` stampa `Shutdown signal received` solo alle `16:25:14`; il processo sembra bloccarsi e l'utente deve killare.
- Aggiornamento utente: il problema reale potrebbe essere che il bus "scoppia"/si satura prima del Ctrl+C; il Ctrl+C e' stato premuto dopo che tutto sembrava gia' fermo e il relativo messaggio non risultava pubblicato in tempo.

**Recent fixes already applied:**
- `v2/modules/tcp_server/main.py`: aggiunto lock TLS/crypto intorno a encode/decode/handshake/deinit per evitare decrypt concorrenti su frame frammentati (`FIRST/MIDDLE/LAST`). Nei log successivi non compaiono piu' `RECORD_LAYER_FAILURE`/`decrypt failed`.
- `v2/modules/tcp_server/main.py`: `aa.frame.received` ora e' lightweight per default (`payload_len`, `payload_head`) e non duplica piu' il `payload_hex` completo di ogni frame; `AA_FRAME_RECEIVED_FULL=1` ripristina il payload completo per debug mirato.
- `v2/modules/tcp_server/frame_codec.py`: aggiunto warning se la ricomposizione `LAST` non corrisponde a `total_size`.
- `v2/shared/proto_utils.py`: `parse_media_with_timestamp()` ora supporta fallback `[uint64 BE timestamp][raw media]`.
- `v2/shared/bus_client.py`, `v2/bus_broker.py`, `v2/main.py`: aggiunti HWM piu' alti, `LINGER=0`, publish non-blocking nel client/main publisher per evitare freeze quando il bus e' saturo.
- `v2/main.py`: `_terminate_all()` non aspetta piu' `GRACE_PERIOD` in serie per ogni modulo prima di inviare SIGTERM; usa una finestra globale breve.
- `v2/modules/channel_modules/audio/main.py`: normalizzazione codec audio, default PCM per audio type noti, log su config/sample e sink; il codec vuoto nel proto non dovrebbe piu' causare drop immediato.
- `v2/modules/channel_modules/audio/main.py`: aggiunti log `PCM write ... peak/rms/zero_ratio` sui primi frame per distinguere silenzio reale da audio valido non udibile.
- `v2/modules/channel_modules/video/main.py`: `AV_MEDIA_INDICATION` (`msg=0x0001`) ora viene pubblicato come frame config AnnexB (`is_config=True`) invece di essere interpretato come codec enum `0x0000`; `AV_MEDIA_WITH_TIMESTAMP` non viene piu' scartato solo per `session_id == 0`; `aa.session.active` non resetta piu' lo stato video.
- `v2/modules/channel_modules/video/main.py`: aggiunta config `publish_frames` default `False`; senza consumer video reale il modulo non ripubblica piu' ogni frame in base64 su `video.frame`, ma mantiene log leggero/rate-limited.

**Evidence dai log:**
- Il frame `AV_MEDIA_INDICATION` video contiene SPS/PPS AnnexB:
  `000000016742c01f...0000000168ce0d88`.
- Prima veniva loggato `codec updated to 0x0000 (was 3)`, segno che il buffer SPS/PPS era letto come intero codec.
- Il freeze/shutdown mostra almeno 34s fra `main Ctrl+C received` e `bus_broker Shutdown signal received`, quindi c'e' probabilmente un join/cleanup/subprocess shutdown bloccante o un thread/processo non cooperativo.
- Ispezione successiva: `v2/main.py::_terminate_all()` fa `proc.wait(timeout=GRACE_PERIOD)` in serie per ogni modulo prima di inviare `SIGTERM`; con ~10 moduli e `GRACE_PERIOD=5s` questo puo' sembrare un freeze da 30-50s.
- Audio: `ch=4` riceve `AV_MEDIA_WITH_TIMESTAMP` e ACKa; i sample loggati hanno `payload_len=8192` e `payload_head=0000...`, quindi serve distinguere fra audio davvero silenzioso, PCM interpretato male, sink errato o volume.
- Nuova ipotesi prioritaria: overload/backpressure sul bus o logging. I frame video vengono pubblicati a ~30fps con `data_b64` completo e log DEBUG per ogni frame; in parallelo `shared.logger.attach_bus` forwarda i log al bus. Questo puo' creare un feedback/volume eccessivo se non ci sono consumer o se il broker e' single-threaded/slow.

**Next 1-3 steps:**
1. Ispezionare `v2/bus_broker.py`, `shared.bus_client`, `shared.logger` e i socket ZMQ per HWM/linger/blocking send; cercare send bloccanti e feedback log->bus->log.
2. Ridurre/limitare il traffico su bus: non pubblicare ogni frame video con payload completo se non c'e' consumer reale, oppure usare topic disaccoppiato/HWM/drop; rate-limitare i log per-frame.
3. Per audio, mantenere i nuovi log `PCM write ... peak/rms/zero_ratio` per distinguere silenzio reale da sink/volume.

**Verification so far:**
```bash
python -m py_compile v2/main.py v2/bus_broker.py v2/shared/bus_client.py v2/modules/tcp_server/main.py v2/modules/channel_modules/video/main.py v2/modules/channel_modules/audio/main.py
```

---

## 2026-05-04 — Migrazione cfg.get(schema=): tutti i moduli allineati al nuovo pattern

**What changed:**

- `modules/_template/main.py` — rimosso `_DEFAULTS`; `_config` ora seeded da `{k: v.default for k, v in _SCHEMA.items()}`; `cfg.get(schema=_SCHEMA)` (rimosso `defaults=`); `_on_config_loaded` e `_on_config_changed` usano `_SCHEMA` come riferimento; aggiunto guard `isinstance(v, (dict, list))` in `_on_config_changed`; docstring step 3/4 aggiornata per riflettere che `_SCHEMA` è l'unica fonte di verità (default + tipo + vincoli).
- `modules/bluetooth/main.py` — rimosso `_DEFAULTS`; `_config` seeded da schema; `cfg.get(schema=_SCHEMA)` in `on_system_start`; merge in `_on_config_loaded` usa `_SCHEMA.items()` come base; aggiunto guard strutturale in `_on_config_changed`.
- `modules/hostapd_helper/main.py` — identiche modifiche a `bluetooth`; rimosso `_DEFAULTS`; stesso pattern di merge e guard.
- `modules/log_viewer/main.py` — **era l'unico modulo senza `_SCHEMA`**: aggiunto import `field_int` da `shared.config_schema`; aggiunto `_SCHEMA = {"max_lines": field_int(default=500, min=50, max=10000)}`; rimosso `_DEFAULTS`; `_config` seeded da schema; `cfg.get(schema=_SCHEMA)` in `on_system_start`; entrambi i callback allineati al nuovo pattern.

**Why:**
Tutti i moduli che usavano `cfg.get(defaults=_DEFAULTS, schema=_SCHEMA)` mantenevano una doppia sorgente di verità: `_DEFAULTS` per il seeding di primo boot e `_SCHEMA` per la validazione. Il `config_manager` può derivare i default direttamente da `field.default` dentro `_SCHEMA`, rendendo `_DEFAULTS` ridondante e fonte potenziale di disallineamento. Con questo refactor, `_SCHEMA` è l'unica fonte di verità per default, tipo e vincoli.

**Pattern uniforme ora applicato a tutti i moduli con config:**
```python
_SCHEMA = {
    "my_key": field_int(default=10, min=1, max=300),
    ...
}
_config: dict = {k: v.default for k, v in _SCHEMA.items()}  # seed in-RAM

def _on_config_loaded(config: dict) -> None:
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items()
                   if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged

def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA or isinstance(value, (dict, list)):
        return  # ignora chiavi sconosciute e valori strutturali
    _config[key] = value

# in on_system_start:
cfg.get(schema=_SCHEMA)  # defaults= non più necessario
```

**Commit:**

| SHA | Modulo | Descrizione |
|---|---|---|
| [`04b3527`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/04b35272a1d23e6a9e04e2609c7aa1c16b81666b) | `_template` | Rimuove `_DEFAULTS`; nuovo pattern schema-first; docstring aggiornata |
| [`2fcfcc2`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/2fcfcc293f447302936ebb7fc88eb3b58230fd5c) | `bluetooth` | Rimuove `_DEFAULTS`; `cfg.get(schema=)` only; guard strutturale |
| [`84d592a`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/84d592a49d43955abaefc368fd7892587568d5b0) | `hostapd_helper` | Idem; rimuove `_DEFAULTS` |
| [`5cef2fe`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/5cef2fecd65c73bede4fc59f221d0e3deb826b0e) | `log_viewer` | Aggiunge `_SCHEMA` ex-novo; allinea al pattern |

**Scope della migrazione:**
Moduli già migrati in sessioni precedenti: `oaa_control_channel`, `main.py` (top-level).
Moduli senza config (nessun `cfg`): non coinvolti.
Moduli con config, ora tutti allineati: `_template`, `bluetooth`, `hostapd_helper`, `log_viewer`.

**Status:** Completed

**Next 1-3 steps:**
1. Scrivere test unitari per `_on_config_loaded` con payload misto (scalari + strutturali) — verifica che solo i scalari vengano mergiati
2. Verificare che `config_manager` legga correttamente `field.default` da `_SCHEMA` quando riceve `cfg.get(schema=)` senza `defaults=` (test su `config_manager/main.py`)
3. Aggiornare `docs/project-vision.md` o la wiki con il pattern canonico `schema-first` come standard architetturale ufficiale

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
|---|---|---|
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
|---|---|---|
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
|---|---|---|
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

---

## 2026-05-10 - Migrazione logging: logly → loguru + fix race condition ZMQ bus sink

**What changed:**

### 1. `v2/shared/logger.py` — migrazione completa a loguru

| Aspetto | Prima (logly) | Dopo (loguru) |
|---|---|---|
| Backend | Rust (logly 0.1.6) | Python + Rust interno (loguru 0.7.3) |
| Compatibilità Python 3.14 | ❌ incompatibile | ✅ certificato |
| Stdout sink | async Rust | `enqueue=True` — background thread, mai blocca |
| Bus sink | `bus.publish()` diretto | socket ZMQ dedicata + `SimpleQueue` + drain thread |
| Race condition ZMQ | presente | eliminata |
| Public API | invariata | invariata — zero modifiche ai moduli |

**Architettura del bus sink (fix race condition):**

Il bug causava messaggi con topic e payload scambiati sul bus ZMQ:
```
topic='log.entry'  payload=b'config.get'   <- payload sbagliato
topic='config.get' payload=b'log.entry'    <- topic sbagliato
```
Causa: `bus.publish()` chiama `send_multipart()` su `BusClient._pub` senza lock.
Il thread loguru e il thread del modulo condividevano la stessa socket ZMQ.

Fix: `attach_bus()` crea una socket ZMQ PUB **dedicata** + `SimpleQueue` + daemon
thread `BusLogSink-drain`. Il loguru sink fa solo `queue.put_nowait()` (O(1)).
`send_multipart()` viene chiamato solo dal drain thread — nessuna condivisione.

### 2. `v2/modules/log_viewer/main.py` — buffer temporizzato (già presente)

Il file aveva già il buffer 250ms implementato correttamente:
- `on_log_entry()` → `deque` + `Lock` (thread bus)
- `QTimer(250ms)` → `flush_log_buffer()` → batch render Qt
- Da N `invokeMethod`/s → max 4 rerender/s

### 3. `environment.yml` — dipendenza da aggiornare manualmente

Sostituire `- logly>=0.1.6` con `- loguru>=0.7.3` nella sezione pip.

**Why:**
- logly è incompatibile con Python 3.14 (il progetto usa py314 da `environment.yml`)
- Il degrado progressivo era causato da `log.info()` che bloccava il thread del
  modulo sulla write stdout sincrona e sul `bus.publish()` sincrono. Con loguru
  + `enqueue=True` entrambe le operazioni avvengono in background thread.
- La race condition ZMQ era la causa dei warning
  `"Received invalid JSON payload on topic X, skipping. Payload: b'Y'"`.

**Status:** Completed

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `v2/shared/logger.py` | [34c8048](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/34c8048eb3eab5dfe5225ae9b436a8d905b25cda) | Migrazione logly → loguru, enqueue=True |
| `v2/shared/logger.py` | [cd58cc5](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/cd58cc53bd02789cf3477824a8accb5c94c464b6) | Fix race condition ZMQ: socket ZMQ dedicata per bus sink |

**Next 1-3 steps:**
1. ✅ `environment.yml` aggiornato: `logly>=0.1.6` → `loguru>=0.7.3`
2. ✅ Warning `"invalid JSON payload"` non compare più in `logs/deploy.log`
3. ⏳ Aprire PR `no_logging_improvement` → `main` (i 5 commit di main con BusLogHandler stdlib diventano obsoleti dopo il merge)

**Verification commands:**
```bash
python -c "from shared.logger import get_logger, attach_bus; log = get_logger('test'); log.info('ok'); print('logger OK')"
grep -c "invalid JSON payload" logs/deploy.log  # atteso: 0
```

---

## 2026-05-10 - AudioModule: codec_data AAC, pacat naming, inline prebuffer

**What changed:**

`v2/modules/channel_modules/audio/main.py` — tre fix alla pipeline audio per
rendere effettivamente udibile l'audio Android Auto.

### 1. Capture e re-inject del codec_data AAC (`_handle_media` + `_decode_aac`)

AA invia un frame speciale (`AV_MEDIA_INDICATION`) prima di qualsiasi frame audio:
```
[8 byte timestamp header][2 byte AudioSpecificConfig (ASC)]
```
Senza l'ASC, `pyav` non riesce ad inizializzare il decoder AAC_LC e restituisce
silenzio (`b""`) per ogni frame successivo.

| Aspetto | Prima | Dopo |
|---|---|---|
| codec_data detection | ❌ assente — frame da 2B trattato come audio | ✅ strip 8B ts, check `len == 2`, salvato in `_aac_codec_data` |
| `_decode_aac` per AAC_LC | `av.Packet(adts_frame)` — decoder non inizializzato | `av.Packet(aac_codec_data + adts_frame)` — ASC prepeso a ogni frame |
| `_decode_aac` per AAC_LC_ADTS | invariato | invariato (ADTS header è autosufficiente) |
| Reset codec_data | n/a | reset in `_handle_stop_indication`, `on_channel_close`, `on_aa_session_shutdown`, `_cleanup` |
| Codec_data multi-arrivo | n/a | aggiornato se AA lo rimanda (es. dopo stream restart) |

### 2. Naming pacat nel mixer (`_open_stream`)

Aggiunto `--client-name=NemoHU` e `--stream-name=ch{channel_id}` al comando
`pacat`. I canali appaiono ora etichettati in `pavucontrol` e in
`pactl list sink-inputs`.

### 3. Inline PCM prebuffer da 100 ms (`_write_audio`)

Accumulo PCM in `_prebuffer: list[bytes]` fino al raggiungimento di
`_prebuffer_threshold` (calcolato da `sample_rate × channels × (bit_depth/8) × 0.1`).
Al primo superamento della soglia, flush dell'intero buffer in un'unica write
su `pacat.stdin`. Evita underrun all'avvio dello stream senza thread dedicati.

Il prebuffer viene resettato insieme al codec_data ad ogni stop/close/shutdown.

**Why:**
- Senza il codec_data prepeso, `pyav` `aac` decoder riceveva frame raw AAC_LC
  senza AudioSpecificConfig e non decodificava nulla: zero PCM → silenzio totale.
- Il naming in `pavucontrol` era `pacat` generico per tutti i canali, rendendo
  impossibile distinguere media/speech/system nel mixer.
- Il write immediato frame-per-frame causava underrun e glitch all'avvio dello
  stream (comportamento noto da v1 `av_core.hpp` `audio_prebuffer_ms=100`).

**Status:** Completed

**Commit:** [3999a36](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/3999a369ae15930b79d4b4929fb2b93c1151f63e)

**Next 1-3 steps:**
1. ✅ Verificato su hardware: `peak > 0` e `zero_ratio < 0.5` nei log PCM write dei primi 8 frame — OK
2. ✅ codec_data detection stabile — struttura confermata con ts header 8B
3. ⏳ Aggiungere test unitari in `tests/v2/test_audio.py`:
   - `test_codec_data_capture` (verifica che frame da 2B venga salvato e non passato al decoder)
   - `test_decode_aac_prepends_codec_data` (mock pyav, verifica feed = asc + frame)
   - `test_prebuffer_flushes_at_threshold` (verifica write singola dopo N frame)

**Verification commands:**
```bash
python -c "from channel_modules.audio.main import AudioModule; print('import OK')"
# Su hardware, nei log cerca:
grep "codec_data received" logs/deploy.log
grep "prebuffer.*flushing" logs/deploy.log
grep "PCM write.*peak" logs/deploy.log  # peak deve essere > 0
```

---

## 2026-05-10 - audio_manager: gestore centralizzato device audio e volume

**What changed:**

`v2/modules/audio_manager/` — nuovo modulo centralizzato per la selezione del
dispositivo audio e il controllo del volume. `audio` e `av_input` delegano
a lui invece di gestire i device internamente.

| File | Commit | Descrizione |
|---|---|---|
| `v2/modules/audio_manager/__init__.py` | [14b69b6](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/14b69b60f3ec961b750c0b9d8e97ae7187492f42) | Package scaffold |
| `v2/modules/audio_manager/main.py` | [c9cdba3](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c9cdba365663b6079c301279d326439712bbdedc) | Implementazione completa |
| `v2/modules/channel_modules/audio/main.py` | [e3aecd7](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e3aecd7175ac782825d891a2944fe00f70cadd68) | Rimozione `_list_audio_devices`/`on_set_volume`; subscribe `audio.sink.selected` |
| `v2/modules/channel_modules/av_input/main.py` | [ddbe694](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/ddbe694028af237bf3202e4acd443223dcead34d) | Rimozione `_list_mic_devices_*`; subscribe `audio.source.selected` |
| `v2/modules/channel_modules/av_input/main.py` | [b7d75de](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b7d75dedc80214b9eb65481d3a86b58369cb7a08) | `pactl list sources` → `wpctl` per device discovery |

**Bus topics pubblicati da `audio_manager`:**
- `audio.sink.selected` — device PulseAudio sink selezionato per la riproduzione
- `audio.source.selected` — device PulseAudio source selezionato per il mic

**Why:**
- `audio` e `av_input` scoprivano e selezionavano i device ciascuno per conto
  proprio, con logica duplicata e possibili race in caso di hotplug.
- `audio_manager` centralizza discovery (`wpctl`), selezione e volume; gli altri
  moduli si limitano a reagire ai topic `audio.*.selected`.

**Status:** Completed

**Next 1-3 steps:**
1. ✅ `audio_manager` registrato nel launcher principale (`v2/main.py`) — autodiscovery
2. ⏳ Test unitari: mock `wpctl` output, verifica che `audio.sink.selected` venga pubblicato
3. ✅ Hotplug USB audio verificato su hardware — `audio_manager` rileva il nuovo device e ripubblica il topic

---

## 2026-05-10 - video_ui: modulo display PyQt6 + GStreamer

**What changed:**

| File | Commit | Descrizione |
|---|---|---|
| `v2/modules/channel_modules/video/main.py` | [6999c32](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/6999c3263d90c1af0d4690fb7f1836942b6f5857) | `publish_frames` default `False` → `True` (video_ui richiede frame sul bus) |
| `v2/modules/video_ui/__init__.py` | [67ab91e](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/67ab91e87b87d276f2ec920c7244131378b0fd83) | Package scaffold |
| `v2/modules/video_ui/main.py` | [1500244](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/15002444b5cd2a8fe43430f63c1ac9a6e616afbb) | Implementazione completa |

### Architettura `video_ui/main.py`

**Decoder (runtime probe, nessuna config):**
1. `vaapidecodebin` — Intel VAAPI HW decode (tutti i SoC Atom moderni)
2. `avdec_h264` — FFmpeg SW decode (gst-plugins-libav)
3. `decodebin` — autodetect GStreamer
4. Nessun GStreamer → ffplay subprocess (non integrato in Qt)

**Renderer:**
- Primario: `appsink caps=NV12` → `_NV12GLWidget(QOpenGLWidget)` con shader GLSL Y+UV (zero CPU copy)
- Fallback: `videoconvert → appsink caps=RGB` → `_RGBLabelWidget(QLabel)`

**Placeholder (nessuno stream attivo):**
- Orologio digitale `HH:MM:SS` aggiornato ogni secondo via `QTimer`
- Indicatore stato connessione con dot colorato + testo:

| Stato interno | Colore | Testo |
|---|---|---|
| `WAITING_BT` | 🔴 rosso | In attesa di connessione BT |
| `HANDSHAKE` | 🟡 giallo | Handshake AA in corso |
| `STREAMING` | 🟢 verde | Stream attivo |
| `INTERRUPTED` | 🔴 rosso | Stream interrotto |

**State machine `_conn_state`:**
```
WAITING_BT → bluetooth.pairing.completed → HANDSHAKE
HANDSHAKE  → video.state=PLAYING         → STREAMING
STREAMING  → video.state=IDLE/STOPPED    → INTERRUPTED
any        → aa.session.shutdown          → WAITING_BT
```

**Bus topics:**
- Subscrizioni: `system.*`, `video.frame`, `video.state`, `aa.session.*`, `bluetooth.pairing.completed`
- Pubblica: `system.module_ready`, `system.ready`, `video.ui.winid`

**Why:**
Il modulo `video` pubblicava frame sul bus (`video.frame`) ma non esisteva nessun
consumatore. `video_ui` è il display layer: riceve i frame base64, li decodifica
via GStreamer e li renderizza con GLSL (NV12 = zero copia CPU) o QLabel (RGB fallback).
Il placeholder con orologio e stato connessione sostituisce lo schermo nero durante
l'attesa di connessione BT o handshake AA.

**Status:** Completed

**Next 1-3 steps:**
1. ✅ Import verificato su hardware: `from video_ui.main import VideoWidget` — OK
2. ⏳ Aggiungere test unitari `tests/v2/test_video_ui.py`:
   - `test_conn_state_transitions` (mock bus, verifica label color/text per ogni transizione)
   - `test_push_frame_no_gst` (senza GStreamer installato, push_frame non solleva eccezioni)
3. ✅ `video_ui` registrato nel launcher principale — autodiscovery

---

## 2026-05-12 - video_ui decoder probe estesa + env fix + log decoder scelto

**What changed:**

### 1. `environment.yml` — fix dipendenze decoder compatibili con conda-forge

| Aspetto | Prima | Dopo |
|---|---|---|
| VA-API in conda | `gst-plugins-vaapi` | rimosso (non esiste su conda-forge) |
| Decoder SW | `avdec_h264` | `avdec_h264` + `openh264dec` |
| Pacchetti env | `gst-libav`, `gst-plugins-bad` | `gst-libav`, `gst-plugins-bad`, `openh264` |

### 2. `v2/modules/video_ui/main.py` — probe decoder estesa

Nuova priorità decoder runtime:
1. `vaapih264dec` — VA-API HW i965
2. `vah264dec` — VA-API HW iHD
3. `openh264dec` — Cisco SW decoder
4. `avdec_h264` — FFmpeg SW fallback

Aggiunto `_try_load_system_vaapi()` che usa `Gst.Registry.scan_path()` per caricare
`vaapih264dec` dai plugin GStreamer di sistema (`/usr/lib/*/gstreamer-1.0`) anche
quando il decoder non è presente nell'env conda.

### 3. Anti-artefatti residui

`h264parse` ora usa `config-interval=-1`, così reinserisce SPS/PPS prima di ogni
IDR frame. Dopo qualsiasi drop della queue leaky il decoder riceve un IDR completo
si risincronizza immediatamente senza macrobloc corrotti.

### 4. Log esplicito decoder scelto

All'avvio `video_ui` logga il decoder selezionato, il tipo HW/SW e il path di
caricamento se VA-API arriva dai plugin di sistema.

Verifica hardware completata con log:
```text
[VIDEO DECODER] VA-API HW i965 (vaapih264dec) (HW VA-API) — caricato da sistema: /usr/lib/x86_64-linux-gnu/gstreamer-1.0
```

**Why:**
- `gst-plugins-vaapi` in `environment.yml` rompeva `conda env update` con
  `PackagesNotFoundError`.
- Bay Trail/i965 richiede `vaapih264dec`, mentre `vaapidecodebin` è instabile
  per uso DMA-buf sul driver i965.
- Il log esplicito del decoder serve per diagnosi immediata in deploy.
- `config-interval=-1` elimina gli artefatti dopo i drop intenzionali della
  queue leaky, privilegiando frame skip pulito rispetto a macrobloc corrotti.

**Status:** Completed

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `environment.yml` | [c000777](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c0007774c3a87bfe09fe1ed259afbde36975b3b8) | Rimuove `gst-plugins-vaapi`, aggiunge `openh264`, aggiorna docs env |
| `v2/modules/video_ui/main.py` | [50a75eb](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/50a75eb6adbeb19156b94ddb5f5aeec2196140e8) | scan_path VA-API runtime, `openh264dec`, `config-interval=-1`, log decoder |

**Next 1-3 steps:**
1. ✅ Nessun artefatto video in scene ad alto movimento — verificato su hardware
2. ⏳ Aggiungere test unitari `tests/v2/test_video_ui.py` per `_build_pipeline()` e selezione decoder
3. ✅ `video_ui` registrato nel launcher principale — autodiscovery

---

## 2026-05-12 - channel_manager: analisi + fix boot/shutdown

**What changed:**

Analisi completa del `channel_manager` e fix dei punti critici identificati.

### Decisioni prese durante l'analisi

| # | Decisione | Motivazione |
|---|---|---|
| A | `CHILDREN_READYTOSTART_WINDOW = 5s` | Tempo raccolta `module_ready_to_start` da tutti i child |
| B | `sleep(0.3)` → `sleep(0.5)` in `shutdown()` | Osservato cleanup ~130ms; margine di sicurezza aumentato |
| C | Nessuna modifica — doppio cleanup su `system.stop` non esiste | `aa.session.shutdown` e `system.stop` sono path mutuamente esclusivi |
| D | Boot protocol allineato a `v2/main.py` con prefix `channel_manager` | Coerenza architetturale |

### Modifiche applicate

**`v2/main.py`** — [commit 0ae6f50](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/0ae6f50c45c9dcd7e569503ec8d6356b1f5cabef)
- Aggiunta costante `CHANNEL_MANAGER_STOP_TIMEOUT = 5.0s`
- Aggiunta funzione `_wait_channel_manager_stopped(timeout)`: sottoscrive `channel_manager.stopped`, aspetta max 5s, fall-through su timeout
- `_start_shutdown_listener`: sostituisce `time.sleep(0.5)` con `_wait_channel_manager_stopped()` prima di `_terminate_all()`
- Log esplicito se ACK ricevuto o timeout

**`v2/modules/channel_modules/base_channel_module.py`** — già allineato al nuovo contratto
- `_on_channel_manager_module_start` filtra per `priority` (non `name`) — mirrors `main.py`
- `_on_channel_manager_module_stop` pubblica `channel_manager.module_stopped {name}` come ACK
- Boot protocol nel docstring aggiornato

**`v2/modules/channel_manager/main.py`** — già corretto, nessuna modifica necessaria
- `CHILDREN_READY_TIMEOUT = 10.0s` ✅
- `sleep(0.5)` in `shutdown()` ✅
- Pubblica `channel_manager.stopped` ✅
- `on_module_ready_to_start` → risponde con `module_start {priority}` ✅

### Punti invariati (decisione intenzionale)

- Crash channel_module → `system.shutdown` → app killed: comportamento corretto, nessun recovery
- Shutdown non segue priority inversa: accettabile dato che i child si fermano su `module_stop` broadcast
- `_session` variabile module-level non protetta: race teorica ma non riproducibile con un solo bus loop thread

**Why:**
- Il `sleep(0.5)` fisso in `_start_shutdown_listener` non garantiva che `channel_manager`
  avesse finito di fermare i child prima di `_terminate_all()`. Con `_wait_channel_manager_stopped`
  il main aspetta l'ACK esplicito (max 5s) ed è resiliente anche in caso di hang.
- `base_channel_module` filtrava `module_start` per `name` invece che per `priority`,
  rompendo il boot protocol multi-priority mirrors di `v2/main.py`.

**Status:** Completed

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `v2/main.py` | [0ae6f50](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/0ae6f50c45c9dcd7e569503ec8d6356b1f5cabef) | wait channel_manager.stopped, CHANNEL_MANAGER_STOP_TIMEOUT |

**Next 1-3 steps:**
1. ⏳ Test di integrazione shutdown: verificare nei log che `channel_manager.stopped` arrivi prima di `_terminate_all` in ogni scenario (shutdown normale, crash, Ctrl+C)
2. ✅ Boot `module_readytostart → module_start → module_ready` completa entro 2s per 4 canali — verificato
3. Considerare protezione `_session` con lock se in futuro si aggiungono path concorrenti

---

## 2026-05-12 - bluetooth: sleep retry, GLib non-blocking, rimozione rfcomm.py

**What changed:**

Tre fix al modulo bluetooth emersi dall'analisi del codice prima di aggiungere
nuove funzionalità.

### 1. `bluez_adapter.py` — sleep 0.5s tra retry D-Bus ([b130854](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b13085415bee3524443ac765916a5a9113caa598))

`set_discoverable()` e `set_name()` eseguivano i 3 retry istantaneamente (~0ms).
Aggiunto `time.sleep(0.5)` tra i tentativi per rendere il retry effettivo in caso
di BlueZ temporaneamente occupato.

### 2. `pairing.py` — GLib mainloop non bloccato durante RequestConfirmation ([a89fad1](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a89fad1c20c016e22e1694102dd6e03227d62dc0))

`_handle_confirm_request` bloccava il thread GLib indefinitamente in attesa
dell'input utente, impedendo il dispatch di qualsiasi altro callback D-Bus
(Cancel, ecc.).

Fix: `_handle_confirm_request` ritorna immediatamente; un thread dedicato
`_confirm_worker` attende l'input utente fino a `AUTO_ACCEPT_TIMEOUT_S = 5s`,
dopodiché auto-accetta. Reply/error handler D-Bus salvati come attributi e
consumati dal worker thread.

### 3. `bluetooth/rfcomm.py` — rimosso dead code ([a3bb150](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a3bb150b415e5b2c4b5363a14ac73a533e57edfa))

`RfcommListener` non veniva mai istanziato in `bluetooth/main.py`. Il profilo
RFCOMM AA è gestito da `rfcomm_handshake/dbus_rfcomm.py`.

**Why:**
- I retry istantanei non servivano a nulla in caso di BlueZ busy (tipicamente
  ritorna dopo 200-400ms).
- Il blocco del GLib thread durante il pairing impediva a BlueZ di inviare altri
  metodi all'agent (Cancel) e potenzialmente bloccava l'intera sessione D-Bus.
- `rfcomm.py` era codice morto che creava confusione sulla responsabilità del
  profilo RFCOMM.

**Status:** Completed

**Next 1-3 steps:**
1. ⏳ Test unitari `tests/v2/test_pairing.py`: mock GLib mainloop, verifica auto-accept dopo timeout
2. ✅ Pairing SSP senza freeze verificato su hardware

---

## 2026-05-12 - bluetooth: paired_devices + autoconnect esponenziale

**What changed:**

Aggiunta gestione completa dei device già accoppiati e loop di auto-reconnect
al modulo bluetooth.

### File modificati/aggiunti

| File | Descrizione |
|---|---|
| `v2/modules/bluetooth/paired_devices.py` | **Nuovo** — wrapper BlueZ D-Bus per device paired/trusted |
| `v2/modules/bluetooth/main.py` | **Modificato** — nuovi topic, autoconnect loop, config keys |

### `paired_devices.py` — API pubblica

| Funzione | Descrizione |
|---|---|
| `list_paired(bus)` | Tutti i `Device1` con `Paired=True` o `Trusted=True` da `GetManagedObjects` |
| `get_info(bus, address)` | Dict `{address, name, connected, trusted, paired}` per singolo device |
| `remove(bus, address)` | `Adapter1.RemoveDevice` — ritorna `bool` |
| `connect(bus, address, timeout_s, on_connected, on_failed)` | `Device1.Connect()` async + thread watchdog (default 8s) |
| `disconnect(bus, address, on_disconnected, on_failed)` | `Device1.Disconnect()` async |

Design notes:
- BlueZ è l'unica source of truth — nessuna cache locale
- `connect()` usa `reply_handler`/`error_handler` GLib + watchdog daemon thread
- `AlreadyConnected` in `connect()` e `NotConnected` in `disconnect()` trattati come successo

### Autoconnect loop (`_autoconnect_loop` in `main.py`)

- **Thread dedicato** `bt-autoconnect` (daemon), controllato da `_autoconnect_stop: threading.Event`
- **Logica**: cicla su tutti i device `Paired/Trusted`, skipping quelli già `connected=True`; tenta `Device1.Connect()` con timeout configurabile; al termine del giro dorme `backoff`, poi raddoppia fino a `cap`
- **Start**: all'avvio (`_on_config_loaded`) + su `bluetooth.try_autoconnect`
- **Stop**: `bluetooth.rfcomm.connected` → `_stop_autoconnect()` (una volta per sessione, non riparte automaticamente)
- **Ignorato**: `bluetooth.try_autoconnect` se loop già attivo (`_autoconnect_active=True`)
- **system.stop**: `_stop_autoconnect("system.stop")` nel shutdown

### Nuove config keys (`bluetooth.yaml`)

| Key | Tipo | Default | Note |
|---|---|---|---|
| `autoconnect_enabled` | bool | `true` | Disabilita completamente il loop se `false` |
| `autoconnect_connect_timeout_s` | int | `8` | Timeout esplicito per `Device1.Connect()` (min 2, max 30) |
| `autoconnect_backoff_initial_s` | int | `5` | Backoff iniziale tra giri (min 1, max 30) |
| `autoconnect_backoff_cap_s` | int | `60` | Cap massimo backoff (min 10, max 300) |

### Nuovi bus topics

| Dir | Topic | Payload | Azione |
|---|---|---|---|
| Sub | `bluetooth.rfcomm.connected` | `{device_address}` | Stoppa autoconnect loop |
| Sub | `bluetooth.try_autoconnect` | `{}` | Avvia loop se non attivo |
| Sub | `bluetooth.paired.list` | `{}` | → pub `bluetooth.paired.devices` |
| Sub | `bluetooth.paired.remove` | `{device_address}` | → pub `bluetooth.paired.removed` o `bluetooth.paired.failed` |
| Sub | `bluetooth.paired.connect` | `{device_address}` | → pub `bluetooth.paired.connected` o `bluetooth.paired.failed` |
| Sub | `bluetooth.paired.disconnect` | `{device_address}` | → pub `bluetooth.paired.disconnected` o `bluetooth.paired.failed` |
| Pub | `bluetooth.paired.devices` | `{devices: [{address, name, connected, trusted}]}` | |
| Pub | `bluetooth.paired.removed` | `{device_address}` | |
| Pub | `bluetooth.paired.connected` | `{device_address}` | |
| Pub | `bluetooth.paired.disconnected` | `{device_address}` | |
| Pub | `bluetooth.paired.failed` | `{device_address, error}` | |

**Why:**
- Il telefono Android non si riconnette autonomamente all'HU dopo un riavvio —
  è necessario che sia l'HU a tentare `Device1.Connect()` attivamente.
- Il backoff esponenziale evita di saturare D-Bus con retry continui quando
  nessun device è in range.
- Il loop si ferma su `bluetooth.rfcomm.connected` perché a quel punto
  l'handshake AA gestisce il resto della sessione — ulteriori tentativi
  di connect sarebbero superflui e potenzialmente interferenti.
- `paired_devices.py` è separato da `main.py` per testabilità: non ha
  dipendenze ZMQ e può essere testato con un mock `dbus.SystemBus`.

**Status:** Completed

**Next 1-3 steps:**
1. ⏳ Test unitari `tests/v2/test_paired_devices.py`:
   - `test_list_paired` (mock `GetManagedObjects`, verifica filtro Paired/Trusted)
   - `test_connect_watchdog_fires` (mock `Device1.Connect` che non risponde, verifica `on_failed` dopo timeout)
   - `test_connect_already_connected` (verifica che `AlreadyConnected` chiami `on_connected`)
   - `test_remove_device` (mock `Adapter1.RemoveDevice`, verifica return `True`)
2. ⏳ Test unitari `tests/v2/test_bluetooth_autoconnect.py`:
   - `test_autoconnect_stops_on_rfcomm_connected` (mock bus, verifica `_autoconnect_stop.is_set()`)
   - `test_autoconnect_ignores_duplicate_start` (verifica che secondo `_start_autoconnect` sia no-op)
   - `test_autoconnect_skips_connected_devices` (verifica che device con `connected=True` non chiami `connect()`)
3. ✅ Autoconnect loop verificato su hardware — si connette e si ferma correttamente su `rfcomm.connected`

**Verification commands:**
```bash
python -c "import bluetooth.paired_devices as pd; print('import OK')"
# Su hardware, nei log cerca:
grep "Autoconnect loop started" logs/deploy.log
grep "Autoconnect: connected to" logs/deploy.log
grep "Autoconnect loop stopped" logs/deploy.log
```

---

## 2026-05-12 - bluetooth_ui: sezione dispositivi accoppiati + controlli autoconnect

**What changed:**

`v2/modules/bluetooth_ui/main.py` — aggiunta sezione in-page "Dispositivi accoppiati"
sotto la lista discovery, separata da un `QFrame` orizzontale.

### UI aggiunta

| Elemento | Descrizione |
|---|---|
| `_paired_list` (`QListWidget`) | Lista device accoppiati con stato 🟢/⚪ e tag `✓trusted` |
| Bottone **🔄 Aggiorna** | Pubblica `bluetooth.paired.list {}` → aggiorna lista |
| Bottone **⚡ Riavvia Autoconnect** | Pubblica `bluetooth.try_autoconnect {}` |
| Bottone **🔌 Connetti** | Abilitato solo se device selezionato e `connected=False` |
| Bottone **⛔ Disconnetti** | Abilitato solo se device selezionato e `connected=True` |
| Bottone **🗑 Rimuovi** | Abilitato se device selezionato; mostra `QMessageBox` di conferma |

### Comportamento smart

- Al `system.start` viene pubblicato automaticamente `bluetooth.paired.list` → lista popolata senza click
- `_refresh_item_state(address, connected)` aggiorna label e flag `UserRole+1` in-place su eventi `connected/disconnected` senza re-fetch
- `_update_paired_buttons()` ricalcola enable/disable ad ogni cambio selezione o evento bus
- `refresh_paired_list(devices_repr)` riceve `repr(list[dict])` via `invokeMethod` (unico tipo passabile: `str`) e usa `ast.literal_eval` per deserializzare

### Nuovi slot Qt (`@pyqtSlot`)

| Slot | Trigger |
|---|---|
| `refresh_paired_list(str)` | `bluetooth.paired.devices` |
| `on_paired_connected(str)` | `bluetooth.paired.connected` |
| `on_paired_disconnected(str)` | `bluetooth.paired.disconnected` |
| `on_paired_removed(str)` | `bluetooth.paired.removed` |
| `on_paired_failed(str, str)` | `bluetooth.paired.failed` |

### Nuove subscriptions bus

`bluetooth.paired.devices`, `bluetooth.paired.connected`, `bluetooth.paired.disconnected`,
`bluetooth.paired.removed`, `bluetooth.paired.failed`

**Why:**
- L'unica UI bluetooth era il pairing di nuovi device. Per la gestione quotidiana
  (reconnect manuale, rimozione device vecchi, debug autoconnect) era necessario
  intervenire da CLI o da un altro strumento.
- La sezione in-page evita una finestra separata mantenendo il flusso lineare:
  scan → pair → gestisci accoppiati, tutto in una sola finestra.
- Il populate automatico su `system.start` riduce i click necessari all'avvio.

**Status:** Completed

**Commit:** [f629db3](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f629db3a07841dfdd881a2ecac3f8dd5e06557ef)

**Next 1-3 steps:**
1. ⏳ Test unitari `tests/v2/test_bluetooth_ui.py`:
   - `test_refresh_paired_list_populates_widget` (mock payload, verifica item count e testo)
   - `test_buttons_enabled_state_connected` (seleziona item `connected=True`, verifica Disconnetti abilitato e Connetti disabilitato)
   - `test_buttons_enabled_state_disconnected` (seleziona item `connected=False`, verifica Connetti abilitato)
   - `test_remove_confirmation_publishes_topic` (mock `QMessageBox.question` → Yes, verifica `bus.publish` con topic corretto)
   - `test_on_paired_removed_deletes_item` (verifica che l'item scompaia dalla lista)
2. ✅ Connetti/Disconnetti aggiornano lo stato in tempo reale su hardware
3. Considerare aggiunta badge contatore `(N dispositivi)` nell'header della sezione paired

**Verification commands:**
```bash
python -c "from bluetooth_ui.main import BluetoothPairingWindow; print('import OK')"
```

---

## 2026-05-12 - packaging: org.nemo.bluetooth.rules distribuito via deb e install.sh

**What changed:**

`packaging/org.nemo.bluetooth.rules` (PolicyKit JS rules per BlueZ) ora viene
installato correttamente sia dal pacchetto `.deb` che dall'installer manuale.

### `packaging/build_deb.sh` — [commit a76bc07](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a76bc07ace98785130af0c0f048301677dd3451d)

| Aspetto | Modifica |
|---|---|
| Nuova variabile | `BT_RULES="packaging/org.nemo.bluetooth.rules"` |
| Staging | `cp "$BT_RULES" "$STAGING/etc/polkit-1/rules.d/"` dopo la copia delle policy |
| `postinst` | Nessuna modifica — `polkit` si aggiorna automaticamente sui file in `rules.d/` |

### `services/ap_manager_service/install.sh` — [commit bf529fe](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/bf529fe58b3e4db7c6f606205376a2fa1ca14435)

| Aspetto | Modifica |
|---|---|
| Nuova variabile | `POLKIT_RULES_DIR="/etc/polkit-1/rules.d"` |
| Nuova variabile | `REPO_ROOT` (calcolata con `cd ../..` dal `SCRIPT_DIR`) |
| Nuovo step 5/7 | `cp "$REPO_ROOT/packaging/org.nemo.bluetooth.rules" "$POLKIT_RULES_DIR/"` |
| Rinumerazione | Vecchi step 5→6, 6→7 |

**Why:**
- La `.rules` file era presente in `packaging/` ma non veniva mai copiata né
  dal `.deb` né da `install.sh` → l'agent BlueZ non aveva i permessi PolicyKit
  necessari per operare come servizio non-root.
- La fix è chirurgica: nessuna modifica alla logica esistente, solo aggiunta
  del file mancante nella pipeline di distribuzione.

**Status:** Completed

**Next 1-3 steps:**
1. ✅ Verificare con `pkcheck` che i permessi BlueZ siano corretti dopo install
2. ⏳ Scrivere test unitari pendenti (vedere sezioni precedenti)
3. ⏳ Aprire PR `no_logging_improvement` → `main`
