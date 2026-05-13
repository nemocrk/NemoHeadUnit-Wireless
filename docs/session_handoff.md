# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `412541a`

---

## Stato Corrente in Una Frase

Fase 0 completata (infrastruttura), Fase 1 §1.1 + §1.2 + §1.4 parzialmente completata: **12 file di test, 679 test** scritti e pushati su `main`. Nessun test è ancora stato eseguito sull'hardware reale.

---

## File Prodotti in Questa Sessione

| File | Commit | Test | Note |
|---|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | — | Fixture globali: `in_process_broker`, `bus_client`, `mock_bus`, `aa_frame_factory`, `mock_config`, `qt_app`, `dbus_session`, `audio_source`, `hardware_available()` |
| `v2/tests/pytest.ini` | `0ff487e` | — | Marker: `unit integration e2e e2e_smoke e2e_full performance hardware qt dbus slow fuzz`; `--strict-markers -q --tb=short` |
| `v2/tests/requirements-test.txt` | `0ff487e` | — | `pytest>=8.0`, `pytest-cov>=5.0`, `pytest-asyncio>=0.23`, `pytest-timeout>=2.3`, `hypothesis>=6.100`, `dbus-python>=1.3`, `pyyaml>=6.0` |
| `v2/tests/unit/shared/test_proto_utils.py` | precedente | 47 | encode/decode round-trip, malformed input, edge cases |
| `v2/tests/unit/modules/channel_modules/audio/test_audio_module.py` | precedente | 42 | codec, prebuffer, sink selection |
| `v2/tests/unit/modules/channel_modules/video/test_video_module.py` | precedente | 38 | frame decode, GStreamer mock |
| `v2/tests/unit/modules/channel_modules/test_base_channel_module.py` | precedente | 44 | state machine, setup/open/close lifecycle |
| `v2/tests/unit/shared/test_bus_client.py` | precedente | 52 | connect/disconnect, publish, subscribe, wait_for, reconnect |
| `v2/tests/unit/shared/test_config_client.py` | precedente | 38 | config load, merge, changed callback, unknown key |
| `v2/tests/unit/oaa_control_channel/test_oaa_control_channel_main.py` | `c3d7a4a` | 54 | boot protocol, config flow, session lifecycle, frame dispatch, TLS delegation, channel_manager integration |
| `v2/tests/unit/oaa_control_channel/test_handshake.py` | `6c99b41` | 62 | state machine completa: VERSION→TLS→AUTH→SDR→CHANNELS→ACTIVE→SHUTDOWN |
| `v2/tests/unit/oaa_control_channel/test_serializer.py` | `ffe6314` | 68 | FrameHeader serialize/parse/roundtrip, FrameSerializer BULK/multi-frame/raw, Messenger msg_type/enc_type/serialize_and_log |
| `v2/tests/unit/oaa_control_channel/test_service_discovery.py` | `4e7a28d` | 72 | SEMANTIC_DEFAULTS struct, _apply_defaults_to_schema, build_from_schema_cfg BT/WiFi inject, channels_from_sdr_bytes round-trip/av_type/audio_type, message_from_sdr_bytes |
| `v2/tests/unit/modules/channel_manager/test_channel_manager.py` | `4f90f8a` | 78 | registry resolve×16+module_name×5, ChannelManagerSession start/readiness/shutdown/crash, module-level handlers×14 |
| `v2/tests/unit/modules/tcp_server/test_tcp_server.py` | `412541a` | 84 | frame_codec helpers×9+encode×13, FrameAssembler×12, boot handlers×4, handshake_completed×3, frame_send×4, TLS handlers×7, _on_raw_frame×6, session restart×6 |

**Totale: 679 test in 12 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti (da rispettare)

### Fixture `_patch_module` (per moduli con singleton a livello di modulo)

I moduli `main.py` (es. `oaa_control_channel/main.py`) usano singleton globali (`bus`, `log`, `cfg`, `_handshake`). Il pattern usato è:

```python
@pytest.fixture(autouse=True)
def _patch_module(monkeypatch):
    mock_bus = MagicMock()
    with patch("shared.bus_client.BusClient", return_value=mock_bus), ...:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import oaa_control_channel.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus
        yield mod, mock_bus, mock_cfg
```

**Regola**: ogni test riceve un modulo fresco via `importlib.reload()` + `autouse=True`.

### Fixture `ts` (per tcp_server/main.py — molti singleton I/O)

`tcp_server/main.py` ha molti singleton (`_server`, `_relay`, `_cryptor`, `_assembler`, `_server_starting`, `_restart_pending`, `_shutdown_ack_event`). La fixture `ts` è per-test e li resetta tutti esplicitamente dopo il reload:

```python
@pytest.fixture()
def ts():
    with patch("tcp_server.server.TCPServer", mock_server_cls), \
         patch("tcp_server.frame_relay.FrameRelay", mock_relay_cls), \
         patch("tcp_server.aa_cryptor.AACryptor", mock_cryptor_cls), \
         patch("tcp_server.frame_codec.FrameAssembler", mock_assembler_cls):
        import modules.tcp_server.main as mod
        importlib.reload(mod)
        mod._server = None
        mod._relay = None
        mod._cryptor = None
        mod._assembler = None
        mod._server_starting = False
        mod._restart_pending = False
        mod._shutdown_ack_event.clear()
        yield mod, mock_bus, ...
```

**Regola**: `_on_raw_frame` viene testata usando `FrameAssembler` reale (non mockato) injettato direttamente su `mod._assembler` — più fedele alla logica di assemblaggio. TLS e socket sono sempre mockati.

### Fixture `cm` (per channel_manager/main.py — OOP + Launcher mock)

```python
def _make_session(mod, mock_launcher_instance):
    session = mod.ChannelManagerSession()
    session._launcher = mock_launcher_instance  # inject diretto
    return session
```

**Regola**: `ChannelManagerSession._launcher` injettato direttamente post-`__init__`.

### Fixture `scope="module"` per moduli stateless

`frame_codec.py` e `registry.py` sono stateless puri — `scope="module"`, import reale, nessuno stub.

### Helper di asserzione riutilizzabili

```python
def _published_topics(bus) -> list[str]:
    return [c.args[0] for c in bus.publish.call_args_list]
```

---

## Stato Roadmap per Fase

| Fase | Stato | Note |
|---|---|---|
| **0 — Infrastruttura** | ✅ Completa | `conftest.py`, `pytest.ini`, `requirements-test.txt` |
| **1 — Unit Test §1.1 Shared** | 🟡 Parziale | `test_proto_utils.py` ✅, `test_bus_client.py` ✅, `test_config_client.py` ✅, `test_logger.py` ❌ mancante |
| **1 — Unit Test §1.2 Base** | ✅ Completa | `test_base_channel_module.py` ✅ |
| **1 — Unit Test §1.3 Channel specifici** | 🟡 Parziale | `test_audio_module.py` ✅, `test_video_module.py` ✅, `av_input/bluetooth/input/sensor/wifi` ❌ mancanti |
| **1 — Unit Test §1.4 Standalone** | 🟡 Parziale | `oaa_cc_main` ✅, `handshake` ✅, `serializer` ✅, `service_discovery` ✅, `channel_manager` ✅, `tcp_server` ✅, `audio_manager` ❌ **PROSSIMO**, `video_ui` ❌, `bluetooth/*` ❌, `config_manager` ❌, altri ❌ |
| **2 — Integration** | ❌ Non iniziata | — |
| **3 — E2E** | ❌ Non iniziata | — |
| **4 — Performance** | ❌ Non iniziata | — |
| **5 — Fuzz** | ❌ Non iniziata | — |

---

## Prossimo Passo Immediato

**`test_audio_manager.py`** — `v2/modules/audio_manager/main.py`.
Gestisce la selezione del sink audio e il routing dei canali audio MEDIA/SPEECH/SYSTEM.

Ordine suggerito per completare §1.4:
1. ~~`test_serializer.py`~~ ✅
2. ~~`test_service_discovery.py`~~ ✅
3. ~~`test_channel_manager.py`~~ ✅
4. ~~`test_tcp_server.py`~~ ✅ (frame_codec×22 + FrameAssembler×12 + handlers×30 = 84 test)
5. `test_audio_manager.py` ← **PROSSIMO**
6. `test_video_ui.py`
7. `test_bluetooth_*.py` (×3)
8. `test_config_manager.py`
9. Moduli secondari: `rfcomm_handshake`, `hostapd_helper`, `config_ui`, `log_viewer`

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| `importlib.reload()` per ogni test di `main.py` | I singleton globali devono essere resettati per l'isolamento |
| Protobuf stubbed con `types.SimpleNamespace` + `MagicMock` in `test_handshake.py` | I `.pb2` generati potrebbero non essere disponibili in ambienti CI senza compilazione |
| Proto reali in `test_service_discovery.py` | `v2/protos/oaa/` è nel repo — import diretto più affidabile |
| `_FakeEnum` con `__eq__` custom | I valori proto enum non sono istanze standard di Python `Enum` |
| `autouse=True` su `_patch_module` | Garantisce isolamento automatico senza dimenticare la fixture |
| `scope="module"` per moduli stateless | `serializer.py`, `service_discovery.py`, `frame_codec.py`, `registry.py` |
| `session._launcher = mock` inject diretto | Più affidabile di `patch()` su `__init__` dopo `importlib.reload()` |
| `mod._assembler = FrameAssembler()` reale in `_on_raw_frame` tests | Testa la logica BULK/FIRST/LAST reale senza over-mocking |
| Reset esplicito di tutti i singleton in fixture `ts` | `tcp_server/main.py` ha 7 singleton — reset manuale più sicuro che affidarsi al reload |
| `_shutdown_ack_event.set()` pre-impostato nei test di restart | Evita wait reali da 3s, mantiene test < 1s |

---

## Contesto Progetto

- **Repository**: `nemocrk/NemoHeadUnit-Wireless`
- **Branch di lavoro**: `main`
- **Directory test**: `v2/tests/`
- **Architettura di riferimento**: `docs/TEST_SUITE_ARCHITECTURE.md`
- **Roadmap**: `docs/roadmap-current.md`
- **Vision**: `docs/project-vision.md`
- **Target coverage globale**: ≥ 80% (blocca merge in CI)
- **Marker pytest disponibili**: `unit`, `integration`, `e2e`, `e2e_smoke`, `e2e_full`, `performance`, `hardware`, `qt`, `dbus`, `slow`, `fuzz`

---

*Handoff Version: 2.4*  
*Aggiornato: 2026-05-13*  
*Commit head: `412541a`*  
*Test totali scritti: 679*
