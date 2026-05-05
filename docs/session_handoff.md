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
  direttamente (non wrappato in `{branch: value}`).
- **`_OptionalMessageWidget`**: checkbox "attivo" che mostra/nasconde il sotto-form.
- **`validate()`** aggiunto a `_OptionalMessageWidget`.

### 2-5. form_builder, list_editor, module_tab, main — vedi entry precedente

**Status:** Completed

---

## 2026-05-04 - video module — AA video channel handler

**Status:** Completed

**Commit:** [285a76a](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/285a76a2ebefddc093d9f3cfc892503cf832a1ac)

---

## 2026-05-04 - input module — AA input channel handler

**Status:** Completed

---

## 2026-05-04 - sensor module — AA sensor channel handler

**Status:** Completed

**Commit:** [9ed34d0](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ed34d08891e13f3070ff1de808bff4f842ee824)

---

## 2026-05-04 - channel_manager registry rewrite + service_discovery fix

**Status:** Completed

**Commit map:**

| File | Commit | Descrizione |
|---|---|---|
| `v2/modules/oaa_control_channel/service_discovery.py` | [81cc925](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/81cc9256f2ccfefa8f2bf62e8193255458e3def6) | Espone `audio_type` in `channels_from_sdr_bytes` |
| `v2/modules/channel_manager/registry.py` | [bbfee3a](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/bbfee3aea79792f152c0c56d513d39b67616475b) | Riscrittura completa |
| `v2/modules/channel_manager/main.py` | [b062ee4](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b062ee41f767ea76ca1eb5bdc3451fe2f51e59fc) | Cattura `SkipChannel` con warning |

---

## 2026-05-05 - channel_modules refactor: discovery dinamica → CLI (--channel-id)

**Status:** Completed

| Modulo | Commit |
|---|---|
| `input/main.py` | [182047e](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/182047e99727418cbaaaaae90aaec2bc43b010f6) |
| `sensor/main.py` | [9aad7dc](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9aad7dcc8ba92ce6fc5fbf6068c25579c50dee5d) |

---

## 2026-05-05 - video/main.py refactor + proto_utils shared frame helpers

**What changed:**
- `proto_utils.py`: aggiunti `encode_aa_frame`, `decode_aa_frame`, `_FLAG_*` costanti
- `video/main.py`: rimossi `_encode_frame`/`_decode_frame` locali, importa da `proto_utils`

**Status:** Completed

**Commit map:**

| File | Commit |
|---|---|
| `v2/shared/proto_utils.py` | [1b20dd6](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/1b20dd66ffdc0f02f9b13cd7de3c6fb8b86c28c9) |
| `v2/modules/channel_modules/video/main.py` | [1b69a16](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/1b69a16af54f749855a6a4f4908b678ba7f4ef86) |

---

## 2026-05-05 - sensor/main.py align to shared encode_aa_frame/decode_aa_frame

**What changed:**

- Rimossi `_encode_frame` e `_decode_frame` come `@staticmethod` di classe
- Rimossi `_FLAG_FIRST`, `_FLAG_LAST`, `_FLAG_ENCRYPTED`, `_FLAG_FULL` module-level locali
- Importati `encode_aa_frame`, `decode_aa_frame` da `shared.proto_utils`
- `on_frame()` ora usa `decode_aa_frame`
- Tutti gli handler (`_handle_channel_open_request`, `_handle_sensor_start_request`,
  `_send_sensor_event`) ora usano `encode_aa_frame`
- Rimossa la moltiplicazione GPS (lat * 1e7, speed * 1e3, ecc.): i valori
  vengono passati as-is in `int()` — responsabilità di scaling delegata al publisher
- `_build_default_sensor_batch` estratta a module-level (non più `@staticmethod`)
- `_set_state` ora ha guard `if self._state == new_state: return` (evita publish
  ridondanti sullo stesso stato)
- `import time` rimosso (non più usato dopo la rimozione dello scaling timestamp)

**Why:**
- Allineamento a `video/main.py` e `input/main.py` che usano già le funzioni condivise
- La moltiplicazione GPS era un'assunzione non documentata: meglio delegare
  la conversione a chi pubblica `sensor.gps` (veicolo integration layer)
- Il guard su `_set_state` è un pattern difensivo già presente in video

**Status:** Completed

**Commit:** [f6d05f6](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f6d05f6e5714c2ff08800e94616a7f12257b4407)

**Next 1-3 steps:**
1. Allineare `audio/main.py` allo stesso pattern (verificare se usa ancora
   `_encode_frame`/`_decode_frame` locali)
2. Aggiungere test unitari in `tests/v2/test_sensor.py` per
   `_handle_channel_open_request`, `_handle_sensor_start_request`,
   `on_sensor_driving_status`, `on_sensor_night_mode`, `on_sensor_gps`
3. Aggiungere test unitari in `tests/v2/test_proto_utils.py` per
   `encode_aa_frame` e `decode_aa_frame` (round-trip)

**Verification commands:**
```bash
python -c "from v2.modules.channel_modules.sensor.main import SensorModule; print('OK')"
python -c "from shared.proto_utils import encode_aa_frame, decode_aa_frame; print('OK')"
python -m pytest tests/v2/ -v
```

---

## 2026-05-05 - channel_modules/_template — Reference implementation

**What changed:**

Creato `v2/modules/channel_modules/_template/main.py` come modulo di riferimento
per le future implementazioni di channel module.

**Why:**
- `audio/main.py` e `video/main.py` sono ora stabili e usano i proto reali:
  fonte di verità ideale per estrarre i pattern comuni.
- Avere un template esplicito riduce il rischio di deviazioni architetturali
  nei prossimi moduli (es. handshake AVChannel non standard, ACK mancante,
  session_id non inizializzato).

**Cosa include il template:**

| Sezione | Dettaglio |
|---|---|
| sys.path bootstrap | Identico a `audio` / `video` |
| Proto import | Blocco completo AV shared (`AVChannelSetupResponse`, `ChannelOpenResponse`, `AVChannelStartIndication`, `AVMediaAckIndication`) |
| `_MSG_*` aliases | Tutti i message ID comuni + slot `# TODO` per quelli channel-specific |
| Handshake completo | `_handle_setup_request` → `_handle_open_request` → `_handle_start_indication` → `_handle_stop_indication` |
| `_send_media_ack()` | Pattern fire-and-forget identico a `audio` / `video` |
| `_is_ready()` | Gate lazy readiness su risorsa esterna (documentato) |
| `on_config_loaded()` | Note sul race condition async bus (vedi `audio`) |
| `_handle_media_with_timestamp()` | Guard `session_id == 0` copiato da `video` |
| `_set_state()` | Guard `if self._state == new_state` + publish su bus |
| `run()` | Override con `aa.session.shutdown` + slot per subscriptions aggiuntive |

**Status:** Completed

**Commit:** [a91cd4d](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a91cd4d869cc6af3241c0ef48b5b6e320f42a7bd)

**Next 1-3 steps:**
1. Usare `_template` come base per il prossimo channel module da implementare
2. Verificare che `audio/main.py` non usi ancora `_encode_frame`/`_decode_frame` locali
   (allineamento pendente da entry precedente)
3. Aggiungere un test smoke in `tests/v2/test_template.py` che verifica
   l'importabilità e l'istanziazione di `TemplateModule` senza bus attivo
