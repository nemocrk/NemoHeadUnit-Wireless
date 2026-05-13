# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — Fase 3 Smoke completata (~27 test in 3 file)

---

## Stato Corrente in Una Frase

**45 file di test, ~2777 test + 3 helper E2E** su `main`. Fase 0, 1, 2 e **3 Smoke completamente chiuse**. **Prossimo: Fase 3 Full — `test_full_aa_session.py`** (nightly, `@pytest.mark.e2e_full`).

---

## File Prodotti

| File | Commit | Test | Note |
|---|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | — | Fixture globali |
| `v2/tests/pytest.ini` | `0ff487e` | — | Marker e config pytest |
| `v2/tests/requirements-test.txt` | `0ff487e` | — | Dipendenze test |
| `v2/tests/unit/shared/test_proto_utils.py` | precedente | 47 | |
| `v2/tests/unit/shared/test_bus_client.py` | `db85dc8` | ~75 | AGGIORNATO — fix _trace, BusTracer mock, nuovi test |
| `v2/tests/unit/shared/test_config_client.py` | precedente | 38 | |
| `v2/tests/unit/shared/test_logger.py` | `38a2885` | 88 | |
| `v2/tests/unit/shared/test_bus_trace.py` | `be3068e` | 72 | BusTracer |
| `v2/tests/unit/modules/channel_modules/audio/test_audio_module.py` | precedente | 42 | |
| `v2/tests/unit/modules/channel_modules/video/test_video_module.py` | precedente | 38 | |
| `v2/tests/unit/modules/channel_modules/test_base_channel_module.py` | precedente | 44 | |
| `v2/tests/unit/modules/channel_modules/input/test_input_module.py` | `5aaa396` | 88 | |
| `v2/tests/unit/modules/channel_modules/sensor/test_sensor_module.py` | `d6f8a78` | 84 | |
| `v2/tests/unit/modules/channel_modules/bluetooth/test_bluetooth_channel_module.py` | `7791347` | 72 | |
| `v2/tests/unit/modules/channel_modules/wifi/test_wifi_channel_module.py` | `a1fa970` | 72 | |
| `v2/tests/unit/modules/channel_modules/av_input/test_av_input_module.py` | `0328b84` | 96 | |
| `v2/tests/unit/oaa_control_channel/test_oaa_control_channel_main.py` | `c3d7a4a` | 54 | |
| `v2/tests/unit/oaa_control_channel/test_handshake.py` | `6c99b41` | 62 | |
| `v2/tests/unit/oaa_control_channel/test_serializer.py` | `ffe6314` | 68 | |
| `v2/tests/unit/oaa_control_channel/test_service_discovery.py` | `4e7a28d` | 72 | |
| `v2/tests/unit/modules/channel_manager/test_channel_manager.py` | `4f90f8a` | 78 | |
| `v2/tests/unit/modules/tcp_server/test_tcp_server.py` | `412541a` | 84 | |
| `v2/tests/unit/modules/audio_manager/test_audio_manager.py` | `acb6dce` | 88 | |
| `v2/tests/unit/modules/video_ui/test_video_ui.py` | `83973cb` | 92 | |
| `v2/tests/unit/modules/bluetooth/test_bluetooth_main.py` | `b024893` | 96 | |
| `v2/tests/unit/modules/bluetooth/test_bluez_adapter.py` | `17c7c4d` | 88 | |
| `v2/tests/unit/modules/bluetooth/test_discovery.py` | `a1b5156` | 72 | |
| `v2/tests/unit/modules/bluetooth/test_pairing.py` | `d4973f8` | 84 | |
| `v2/tests/unit/modules/bluetooth/test_paired_devices.py` | `6a83677` | 68 | |
| `v2/tests/unit/modules/config_manager/test_config_manager.py` | `e1c0847` | 96 | |
| `v2/tests/unit/modules/zmq_trace/test_zmq_trace.py` | `cdc9e6f` | 68 | zmq_trace module |
| `v2/tests/integration/test_bus_broker.py` | `bd326e5` | 84 | Fase 2 §1 |
| `v2/tests/integration/test_channel_lifecycle.py` | `1734764` | 88 | Fase 2 §2 |
| `v2/tests/integration/test_audio_manager.py` | `7e1d9be` | ~47 | Fase 2 §3 |
| `v2/tests/integration/test_config_manager.py` | `9f08aa6` | ~50 | Fase 2 §4 |
| `v2/tests/integration/test_video_pipeline.py` | `2d8d861` | ~60 | Fase 2 §5 |
| `v2/tests/integration/test_bluetooth_flow.py` | `dbbc2b4` | ~60 | Fase 2 §6 |
| `v2/tests/integration/test_boot_shutdown.py` | `2fe07ef` | ~65 | Fase 2 §7 |
| `v2/tests/e2e/helpers/phone_mock.py` | `5c74859` | — | PhoneMock + TcpPhoneClient |
| `v2/tests/e2e/helpers/frame_sequences.py` | `bab116c` | — | 8 classi frame builder |
| `v2/tests/e2e/helpers/stack_launcher.py` | `637631d` | — | StackLauncher + e2e_stack() |
| `v2/tests/test_rfcomm_and_channel_manager.py` | `9ab6c2e` | ~51 | unit rfcomm_handshake + channel_manager |
| `v2/tests/e2e/smoke/test_bt_connect_to_handshake.py` | — | 10 | **Fase 3 Smoke §1** |
| `v2/tests/e2e/smoke/test_channel_manager_boot.py` | — | 9 | **Fase 3 Smoke §2** |
| `v2/tests/e2e/smoke/test_audio_path_smoke.py` | — | 8 | **Fase 3 Smoke §3** |

**Totale: ~2777 test in 45 file di test + 3 helper E2E + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti

### BusClient — importante cambio (commit ddd7142)
- `publish()` ora inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `_handle_received_message()` rimuove `_trace` prima di consegnare all'handler
- `publish()` ritorna `bool`: `True` = ok, `False` = dropped
- `BUS_HWM` alzato a 5000
- `BusTracer` istanziato in `__init__` — **va sempre mockato** nei test unit con `patch("shared.bus_client.BusTracer", return_value=MagicMock())`

### Pattern mock BusTracer nei test unit
```python
mock_tracer = MagicMock()
with patch("shared.bus_client.zmq.Context", return_value=ctx):
    with patch("shared.bus_client.BusTracer", return_value=mock_tracer):
        client = BusClient(module_name="test_unit")
```

### E2E Smoke — pattern consolidato (Fase 3 Smoke)
```python
@pytest.mark.e2e_smoke
class TestXxx:
    def test_yyy(self, in_process_broker):
        with e2e_stack(in_process_broker, modules=[...]) as stack:
            # stack.publish() per eventi bus
            # stack.wait_topic("some.topic", timeout=5) per assertions
            # PhoneMock(sock).start() + mock.wait_done(5) per RFCOMM
            # TcpPhoneClient.connect(...) per AA TCP
            ...
```

**Timeout standard smoke**: 5s per handshake, 3s per frame exchange, 2s per topic bus.

### rfcomm_handshake / channel_manager — pattern unit test (commit 9ab6c2e)
- **`importlib.reload(mod)`** all'inizio di ogni metodo di test per stato globale pulito
- **`mod.bus = MagicMock()`** sostituisce il BusClient modulo-level senza ZMQ
- **Socket mock**: `MagicMock(spec=socket.socket)` con `recv.side_effect` che simula un byte-stream pre-caricato
- **`_make_socket_with_responses(*packets_raw)`** helper locale: flattens raw packets in un stream, serve via `recv(n)`
- **`patch("rfcomm_handshake.main.DbusRfcommListener")`** per isolare D-Bus
- **`patch("rfcomm_handshake.main._start_glib_mainloop")`** per evitare GLib reale
- `RfcommHandshakeEventLoop` skippato automaticamente se `google.protobuf` non è installato

### Integration Tests — Pattern BusClient in-process
1. `_make_client(in_process_broker, name)` — monkey-patcha BROKER_PUB_ADDR/BROKER_SUB_ADDR **+ BusTracer mock**
2. `_start_client(client)` — avvia `client.run()` in thread separato, ritorna il thread
3. `_wait(timeout)` — `Event.wait()` con timeout fisso; i test aspettano topic specifici entro 3s
4. Ogni test usa `importlib.reload()` per garantire stato modulo fresco

### boot_shutdown — FakeModule pattern
```python
class FakeModule:
    def on_start(self): self.bus.publish("system.module_ready", {"module": self.name})
    def on_stop(self): self.bus.publish("system.module_stopped", {"module": self.name})
```

---

## 2026-05-13 — Fase 3 Smoke Completata

**Cosa cambiato:**
Creati i 3 file smoke E2E in `v2/tests/e2e/smoke/`:

- **`test_bt_connect_to_handshake.py`** (10 test) — `TestRfcommToTcpSmoke` e `TestTcpServerAvailableAfterHandshake` e `TestFullAaSmoke`. Copre: RFCOMM handshake completato, WiFi credentials non vuote, `rfcomm.handshake.completed` sul bus, no-ack variant, socket chiuso mid-handshake, duplicate connection rejected, TCP 5288 aperto, version request/response, version exchange completo, shutdown sequence.

- **`test_channel_manager_boot.py`** (9 test) — `TestChannelManagerBootSmoke`. Copre: boot sequence completa con `channel_manager` + `tcp_server` + `oaa_control_channel`, tutti i canali ready, `channel_manager.all_channels_ready` pubblicato, restart dopo session shutdown, `aa.session.shutdown` tear-down ordinato, canali non aperti prima di `system.start`, boot con moduli parziali (subset channels), stop pulito, timeout canale mancante.

- **`test_audio_path_smoke.py`** (8 test) — `TestAudioPathSmoke`. Copre: `audio_manager` ready dopo boot, topic `audio.focus.acquired`, `audio.focus.released`, routing DA/verso AA, `audio_manager` + `channel_manager` + media channel integrati, focus preemption da chiamata, recovery dopo interruzione, stop senza audio attivo.

**Perché:**
Completare la Fase 3 Smoke richiesta da roadmap. Tutti e 3 i file usano `e2e_stack()` + `PhoneMock` + `TcpPhoneClient` dal layer helper già consolidato.

**Status:** Completato ✅

**Prossimi 3 passi:**
1. **IMMEDIATO** — `v2/tests/e2e/full_session/test_full_aa_session.py` (`@pytest.mark.e2e_full`)
2. `v2/tests/e2e/full_session/test_session_recovery.py`
3. `v2/tests/performance/test_bus_latency.py` (Fase 4, non bloccante)

---

## 2026-05-13 — Unit Tests rfcomm_handshake + channel_manager

**Cosa cambiato:**
Creato `v2/tests/test_rfcomm_and_channel_manager.py` con 4 sezioni:
- **Section 1** — `packet.py`: encode/decode roundtrip, truncated buffer, repr
- **Section 2** — `handshake.py / RfcommHandshake`: 6 test dell'event loop
- **Section 3** — `rfcomm_handshake/main.py`: 7 test bus-level
- **Section 4** — `channel_manager/main.py`: 13 test

**Status:** Completato ✅

---

## 2026-05-13 — E2E Helpers Completati

**Cosa cambiato:**
Creati i tre helper E2E in `v2/tests/e2e/helpers/`:
- `phone_mock.py` — `PhoneMock` + `TcpPhoneClient`
- `frame_sequences.py` — 8 classi builder per frame AA
- `stack_launcher.py` — `StackLauncher` + `e2e_stack()` context manager

**Status:** Completato ✅

---

## Contesto per Sessione Successiva

### Struttura `test_full_aa_session.py` attesa

```python
@pytest.mark.e2e_full
class TestFullAaSession:
    """Sessione AA completa: boot → RFCOMM handshake → TCP AA →
    version exchange → service discovery → tutti i canali aperti →
    media → ping/pong → shutdown ordinato."""

    def test_full_session_happy_path(self, in_process_broker): ...
    def test_session_with_audio_focus(self, in_process_broker): ...
    def test_session_with_video_frame(self, in_process_broker): ...
    def test_session_with_sensor_events(self, in_process_broker): ...
    def test_session_shutdown_from_phone(self, in_process_broker): ...
    def test_session_shutdown_from_hu(self, in_process_broker): ...
    # Timeout più lunghi: 30s per session completa
```

### Comandi utili

```bash
# Solo smoke
pytest -m e2e_smoke -v

# Solo full (nightly)
pytest -m e2e_full -v

# Unit + integration (CI)
pytest -m "unit or integration" --cov=v2 --cov-fail-under=80

# Tutto
pytest -v --cov=v2
```
