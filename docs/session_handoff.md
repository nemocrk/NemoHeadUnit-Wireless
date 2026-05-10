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
1. Aggiungere test unitari in `tests/v2/test_av_input.py`:
   - `test_build_media_with_timestamp` (round-trip con `parse_media_with_timestamp`)
   - `test_handle_setup_request` (verifica payload `AVChannelSetupResponse`)
   - `test_handle_input_open_request_start` / `_stop` (mock sounddevice)
   - `test_mic_callback` (verifica che `send_frame` venga chiamato con payload corretto)
2. Verificare compatibilità wire format con `openauto-prodigy` su hardware
3. Aggiungere `av_input` al registry del `channel_manager`

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
1. Test unitari per i 4 nuovi handler in `tests/v2/test_handshake.py`
2. Test end-to-end su hardware wireless
3. Verificare che `PING_REQUEST` non causi doppia callback `on_active`

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

**Status:** Completed (manca solo aggiornamento manuale `environment.yml`)

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `v2/shared/logger.py` | [34c8048](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/34c8048eb3eab5dfe5225ae9b436a8d905b25cda) | Migrazione logly → loguru, enqueue=True |
| `v2/shared/logger.py` | [cd58cc5](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/cd58cc53bd02789cf3477824a8accb5c94c464b6) | Fix race condition ZMQ: socket ZMQ dedicata per bus sink |

**Next 1-3 steps:**
1. Aggiornare `environment.yml`: sostituire `logly>=0.1.6` con `loguru>=0.7.3`
2. Verificare su hardware che il warning `"invalid JSON payload"` non compaia più in `logs/deploy.log`
3. Aprire PR `no_logging_improvement` → `main` (i 5 commit di main con BusLogHandler stdlib diventano obsoleti dopo il merge)

**Verification commands:**
```bash
python -c "from shared.logger import get_logger, attach_bus; log = get_logger('test'); log.info('ok'); print('logger OK')"
grep -c "invalid JSON payload" logs/deploy.log  # atteso: 0
```
