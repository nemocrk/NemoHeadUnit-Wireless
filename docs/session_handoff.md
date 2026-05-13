# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `9ab6c2e` (unit tests rfcomm_handshake + channel_manager)

---

## Stato Corrente in Una Frase

**38 file di test, ~2546 test + 3 helper E2E** su `main`. Fase 0, 1 e **2 completamente chiuse**. Prerequisito Fase 3 (helpers) ✅. Test unitari aggiuntivi per `rfcomm_handshake` e `channel_manager` aggiunti a `v2/tests/`. **Prossimo: primo smoke test E2E** — `test_bt_connect_to_handshake.py`.

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
| `v2/tests/test_rfcomm_and_channel_manager.py` | `9ab6c2e` | ~51 | **NUOVO** — unit rfcomm_handshake + channel_manager |

**Totale: ~2546 test in 38 file di test + 3 helper E2E + 3 file infrastruttura.**

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

### _FakeSocket — aggiunto getsockopt()
I test unit di BusClient richiedono che `_FakeSocket` implementi `getsockopt()` (usato da BusTracer emit calls).

### BusTracer test pattern
```python
def _make_tracer(enabled=True, module_name="test"):
    from shared.bus_trace import BusTracer
    with patch("shared.bus_trace.TRACE_ENABLED", enabled):
        with patch.object(BusTracer, "_drain_loop", return_value=None):
            return BusTracer(module_name=module_name)
```

### zmq_trace test pattern
```python
def _load_module():
    # Stub shared.bus_client, config_client, logger, config_schema
    # Reload zmq_trace.main per test isolation
    # Attach mock_bus as mod._mock_bus for assertions
```

### Canali NON-AV (bluetooth, wifi channel modules)
1. `decode_aa_frame()` patchato via `patch.object(_module, "decode_aa_frame")` nei test di dispatch
2. `return_value=None` testa il path malformed-frame
3. `return_value=(msg_id, body)` testa dispatch per specifico messaggio
4. Proto MagicMock iniettato in sys.modules pre-import

### AVInput (threading + subprocess)
1. `subprocess.Popen` patchato per isolare spawn
2. `threading.Thread` patchato per evitare thread reali nei test
3. `_start_stream` / `_stop_stream` patchati con `patch.object`
4. `_send_queue` manipolato direttamente

### Integration Tests — Pattern BusClient in-process
1. `_make_client(in_process_broker, name)` — monkey-patcha BROKER_PUB_ADDR/BROKER_SUB_ADDR **+ BusTracer mock**
2. `_start_client(client)` — avvia `client.run()` in thread separato, ritorna il thread
3. `_wait(timeout)` — `Event.wait()` con timeout fisso; i test aspettano topic specifici entro 3s
4. Ogni test usa `importlib.reload()` per garantire stato modulo fresco

### E2E Helpers — design decisions (commit 637631d, bab116c, 5c74859)
1. **`PhoneMock`** gira in daemon thread, espone `.wait_done(timeout)` e `.completed` — no blocking nei test
2. **`TcpPhoneClient.recv_frames_until(predicate, timeout, max_frames)`** usa deadline assoluta, non timeout ricorsivo
3. **`FullHandshakeSequence.as_bus_payloads()`** restituisce `List[dict]` pronti per `on_frame_ch0()` senza socket reali
4. **`StackLauncher`** inietta stub hardware (GLib, D-Bus, BlueZ, GStreamer, PyQt6) prima dell'import dei moduli
5. **`e2e_stack()`** context manager pubblica `system.readytostart` e attende `wait_all_ready()` prima di cedere il controllo

### boot_shutdown — FakeModule pattern
```python
class FakeModule:
    """Modulo fittizio che risponde al bus esattamente come un modulo v2 reale.
    Usato in test_boot_shutdown per simulare 3..N moduli nell'orchestrazione."""
    def on_start(self): self.bus.publish("system.module_ready", {"module": self.name})
    def on_stop(self): self.bus.publish("system.module_stopped", {"module": self.name})
```

### rfcomm_handshake / channel_manager — pattern unit test (commit 9ab6c2e)
- **`importlib.reload(mod)`** all'inizio di ogni metodo di test per stato globale pulito
- **`mod.bus = MagicMock()`** sostituisce il BusClient modulo-level senza ZMQ
- **Socket mock**: `MagicMock(spec=socket.socket)` con `recv.side_effect` che simula un byte-stream pre-caricato
- **`_make_socket_with_responses(*packets_raw)`** helper locale: flattens raw packets in un stream, serve via `recv(n)`
- **`patch("rfcomm_handshake.main.DbusRfcommListener")`** per isolare D-Bus
- **`patch("rfcomm_handshake.main._start_glib_mainloop")`** per evitare GLib reale
- `RfcommHandshakeEventLoop` skippato automaticamente se `google.protobuf` non è installato

---

## 2026-05-13 — Unit Tests rfcomm_handshake + channel_manager

**Cosa cambiato:**
Creato `v2/tests/test_rfcomm_and_channel_manager.py` con 4 sezioni:
- **Section 1** — `packet.py`: encode/decode roundtrip, truncated buffer, repr
- **Section 2** — `handshake.py / RfcommHandshake`: 6 test dell'event loop (happy path, ack opzionale, socket chiuso, sendall fallisce, msg_id sconosciuti, loop esaurito)
- **Section 3** — `rfcomm_handshake/main.py`: 7 test bus-level (boot, priority guard, handshake trigger, duplicate reject, credentials store, stop)
- **Section 4** — `channel_manager/main.py`: 13 test (ChannelManagerSession lifecycle + boot handlers)

**Perché:**
Aggiunto per raggiungere coverage ≥80% sui due moduli prima del primo smoke test E2E, che li usa entrambi nel path critico RFCOMM→AA.

**Status:** Completato ✅

**Prossimi 3 passi:**
1. **IMMEDIATO** — Creare `v2/tests/e2e/smoke/test_bt_connect_to_handshake.py` usando `PhoneMock` + `e2e_stack()`
2. Creare `v2/tests/e2e/smoke/test_channel_manager_boot.py`
3. Creare `v2/tests/e2e/smoke/test_audio_path_smoke.py`

| Verificare vision alignment | `grep -A 100 "# Project Vision: NemoHeadUnit-Wireless" docs/project-vision.md` |

---

## 2026-05-13 — E2E Helpers Completati

**Cosa cambiato:**
Creati i tre helper E2E in `v2/tests/e2e/helpers/`:
- `phone_mock.py` — `PhoneMock` (RFCOMM 4-step handshake responder in daemon thread) + `TcpPhoneClient` (AA TCP client con `send_frame`, `recv_frame`, `recv_frames_until`)
- `frame_sequences.py` — 8 classi builder per frame AA: `VersionSequence`, `AuthSequence`, `ServiceDiscoverySeq`, `ChannelOpenSeq`, `PingSequence`, `MediaSequence`, `ShutdownSequence`, `FullHandshakeSequence`
- `stack_launcher.py` — `StackLauncher` (orchestratore in-process con stub hardware) + `e2e_stack()` context manager

**Perché:**
Prerequisito architetturale della Fase 3 E2E.

**Status:** Completato ✅

---

## Contesto per Sessione Successiva

### Come usare gli E2E helpers

```python
# test_bt_connect_to_handshake.py — struttura di base
import pytest
from tests.e2e.helpers.phone_mock import PhoneMock, TcpPhoneClient
from tests.e2e.helpers.frame_sequences import VersionSequence, ServiceDiscoverySeq, ChannelOpenSeq
from tests.e2e.helpers.stack_launcher import e2e_stack

@pytest.mark.e2e_smoke
def test_bt_connect_triggers_aa_handshake(in_process_broker):
    with e2e_stack(in_process_broker, modules=["rfcomm_handshake", "tcp_server", "oaa_control_channel"]) as stack:
        # 1. Simula RFCOMM connect
        import socket
        hu_sock, phone_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        mock = PhoneMock(phone_sock).start()
        stack.publish("bluetooth.rfcomm.connected", {"fd": hu_sock.fileno()})

        # 2. Attendi handshake completato
        assert mock.wait_done(timeout=5.0)
        assert mock.completed

        # 3. Verifica AA TCP attivo
        client = TcpPhoneClient.connect("127.0.0.1", 5288, timeout=5.0)
        client.send_frame(0, VersionSequence.request_frame())
        frames = client.recv_frames_until(lambda f: f[2] == MSG_VERSION_RESPONSE, timeout=3.0)
        assert frames
        client.close()
```

### Comandi utili

```bash
# Eseguire solo gli smoke tests
pytest -m e2e_smoke -v

# Verificare che gli helpers siano importabili
python -c "from v2.tests.e2e.helpers.phone_mock import PhoneMock, TcpPhoneClient; print('OK')"

# Run unit + integration (come in CI)
pytest -m "unit or integration" --cov=v2 --cov-fail-under=80
```
