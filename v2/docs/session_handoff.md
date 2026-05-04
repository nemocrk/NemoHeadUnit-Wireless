# Session Handoff Log

Registro delle sessioni di sviluppo, modifiche apportate e prossimi step.

---

## 2026-05-04 — Config integration: service_discovery ora legge da config_manager

**What changed:**
- `oaa_control_channel/service_discovery.py` — rimossi `HU_NAME`, `HU_MAKE`, `HU_MODEL`, `HU_SW_VERSION` come costanti globali; aggiunto `DEFAULTS: dict` con tutte le chiavi namespace (es. `hu.name`, `video.dpi`, `nav.min_interval_ms`); `build_service_discovery_response()` ora accetta `cfg: dict` al posto delle costanti; tutti i builder che usano valori configurabili accettano `cfg: dict`; i valori di protocollo (channel_id, keycodes, bit_depth, ecc.) restano hardcoded con commento `# protocol constant`
- `oaa_control_channel/handshake.py` — aggiunto `cfg: dict | None = None` a `ControlChannelHandshake.__init__`; `_cfg` salvato come `self._cfg`; passato `cfg=self._cfg` a `build_service_discovery_response` in `_on_service_discovery_request`
- `oaa_control_channel/main.py` — aggiunto `_cfg: dict` (inizializzato con `DEFAULTS`) e `_cfg_loaded: bool`; `on_system_start()` pubblica `config.get` con defaults (first-boot seeding); `on_config_response()` aggiorna `_cfg` e pubblica `system.ready` solo dopo la risposta; `on_config_changed()` aggiorna `_cfg`, azzera `_handshake`, pubblica `aa.session.shutdown` + `aa.session.restart`; aggiunte subscribe per `config.response` e `config.changed`; `_make_handshake()` passa `cfg=_cfg`

**Why:**
Tutte le impostazioni utente (nome HU, risoluzione video, DPI, sample rate audio, dimensioni touch, intervallo navigazione, dimensioni immagini nav) erano hardcoded in `service_discovery.py`. Ora sono persistite in `config/oaa_control_channel.yaml` tramite `config_manager`. Al boot il modulo chiede la config prima di pubblicare `system.ready` (pattern sincrono tramite bus). Se un valore cambia a runtime, la sessione attiva viene chiusa e `aa.session.restart` segnala a `tcp_server` di forzare la riconnessione, cosicché il prossimo handshake usi i nuovi valori.

**Nuovi messaggi bus:**

| Messaggio | Da | A | Payload |
|---|---|---|---|
| `config.get` | `oaa_control_channel` | `config_manager` | `{module, requester, defaults}` |
| `config.response` | `config_manager` | `oaa_control_channel` | `{module, config, requester}` |
| `config.changed` | `config_manager` | `oaa_control_channel` | `{module, key, value}` |
| `aa.session.restart` | `oaa_control_channel` | `tcp_server` | `{}` |

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

**Next 1-3 steps:**
1. Aggiungere `on_aa_session_restart` in `tcp_server/main.py` per chiudere la connessione TCP attiva quando riceve `aa.session.restart`
2. Aggiornare i test di `oaa_control_channel/handshake.py` per passare `cfg={}` al costruttore
3. Aggiungere test unitari per `on_config_response` e `on_config_changed` in `oaa_control_channel/main.py`

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
In precedenza `AACryptor` viveva in `oaa_control_channel`, che gestiva autonomamente sia la negoziazione TLS che il decrypt dei frame post-handshake. Questo impediva a `tcp_server` di decriptare i frame cifrati su canali diversi da ch0 senza dover delegare ogni tempo. Ora `tcp_server` possiede il cryptor, decripta i frame in ingresso prima di pubblicarli sul bus (payload sempre in chiaro per i subscriber), e gestisce il loop TLS tramite messaggi bus con `oaa_control_channel`.

**Nuovi messaggi bus introdotti:**

| Messaggio | Da | A | Payload |
|---|---|---|---|
| `aa.handshake.start_tls` | `oaa_control_channel` | `tcp_server` | `{}` |
| `aa.handshake.feed_input` | `oaa_control_channel` | `tcp_server` | `{payload_hex}` |
| `tcp.server.tls_handshake` | `tcp_server` | `oaa_control_channel` | `{outgoing_hex}` |
| `tcp.server.tls_handshake_completed` | `tcp_server` | `oaa_control_channel` | `{}` |

**Status:** Completed

**Next 1-3 steps:**
1. Aggiungere test unitari per `on_handshake_start_tls` e `on_handshake_feed_input` in `tcp_server`
2. Aggiornare i test esistenti di `oaa_control_channel/handshake.py` (costruttore ora richiede `publish_fn`)
3. Verificare che nessun altro modulo importi `AACryptor` da `oaa_control_channel`
