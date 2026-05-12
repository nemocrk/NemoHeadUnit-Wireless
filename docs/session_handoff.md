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
1. Testare su hardware: verificare che `peak > 0` e `zero_ratio < 0.5` nei log PCM write dei primi 8 frame
2. Se il codec_data non arriva o arriva con struttura diversa (es. no ts header), aggiustare la detection in `_handle_media`
3. Aggiungere test unitari in `tests/v2/test_audio.py`:
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
1. Registrare `audio_manager` nel launcher principale (`v2/main.py`)
2. Test unitari: mock `wpctl` output, verifica che `audio.sink.selected` venga pubblicato
3. Verificare hotplug USB audio: `audio_manager` deve rilevare il nuovo device e ripubblicare il topic

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
1. Verificare import su hardware: `python -c "from video_ui.main import VideoWidget; print('OK')"`
2. Aggiungere test unitari `tests/v2/test_video_ui.py`:
   - `test_conn_state_transitions` (mock bus, verifica label color/text per ogni transizione)
   - `test_push_frame_no_gst` (senza GStreamer installato, push_frame non solleva eccezioni)
3. Registrare `video_ui` nel launcher principale accanto a `video`, `audio`, `av_input`

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
1. Verificare su hardware che non compaiano più artefatti video in scene ad alto movimento
2. Aggiungere test unitari `tests/v2/test_video_ui.py` per `_build_pipeline()` e selezione decoder
3. Registrare `video_ui` nel launcher principale se non ancora presente

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
1. Test di integrazione shutdown: verificare nei log che `channel_manager.stopped` arrivi prima di `_terminate_all` in ogni scenario (shutdown normale, crash, Ctrl+C)
2. Verificare su hardware che il boot `module_readytostart → module_start → module_ready` completi entro 2s per 4 canali
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
1. ← **Prossima sessione**: aggiungere nuova funzionalità al modulo bluetooth
2. Test unitari `tests/v2/test_pairing.py`: mock GLib mainloop, verifica auto-accept dopo timeout
3. Test su hardware: verifica pairing SSP senza freeze del processo in attesa conferma utente

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
1. Test unitari `tests/v2/test_paired_devices.py`:
   - `test_list_paired` (mock `GetManagedObjects`, verifica filtro Paired/Trusted)
   - `test_connect_watchdog_fires` (mock `Device1.Connect` che non risponde, verifica `on_failed` dopo timeout)
   - `test_connect_already_connected` (verifica che `AlreadyConnected` chiami `on_connected`)
   - `test_remove_device` (mock `Adapter1.RemoveDevice`, verifica return `True`)
2. Test unitari `tests/v2/test_bluetooth_autoconnect.py`:
   - `test_autoconnect_stops_on_rfcomm_connected` (mock bus, verifica `_autoconnect_stop.is_set()`)
   - `test_autoconnect_ignores_duplicate_start` (verifica che secondo `_start_autoconnect` sia no-op)
   - `test_autoconnect_skips_connected_devices` (verifica che device con `connected=True` non chiami `connect()`)
3. Gestire `bluetooth.try_autoconnect` dalla UI (prossima feature)

**Verification commands:**
```bash
python -c "import bluetooth.paired_devices as pd; print('import OK')"
# Su hardware, nei log cerca:
grep "Autoconnect loop started" logs/deploy.log
grep "Autoconnect: connected to" logs/deploy.log
grep "Autoconnect loop stopped" logs/deploy.log
```
