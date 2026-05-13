# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `ffe6314`

---

## Stato Corrente in Una Frase

Fase 0 completata (infrastruttura), Fase 1 §1.1 + §1.2 + §1.4 parzialmente completata: **9 file di test, 445 test** scritti e pushati su `main`. Nessun test è ancora stato eseguito sull'hardware reale.

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

**Totale: 445 test in 9 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti (da rispettare)

### Fixture `_patch_module` (per moduli con singleton a livello di modulo)

I moduli `main.py` (es. `oaa_control_channel/main.py`) usano singleton globali (`bus`, `log`, `cfg`, `_handshake`). Il pattern usato è:

```python
@pytest.fixture(autouse=True)
def _patch_module(monkeypatch):
    mock_bus = MagicMock()
    # ...
    with patch("shared.bus_client.BusClient", return_value=mock_bus), ...:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import oaa_control_channel.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus
        mod._handshake = None
        yield mod, mock_bus, mock_cfg
```

**Regola**: ogni test riceve un modulo fresco via `importlib.reload()` + `autouse=True`. I singleton vengono iniettati direttamente nel namespace del modulo ricaricato.

### Fixture `hs_factory` (per state machine pura)

Per testare `ControlChannelHandshake` senza protobuf reali:

```python
@pytest.fixture()
def hs_factory():
    sys_patch = _build_sys_modules_patch()  # stub tutti i proto pb2
    with patch.dict("sys.modules", sys_patch), ...:
        import oaa_control_channel.handshake as hs_mod
        importlib.reload(hs_mod)
        hs_mod.decode_proto  = mock_decode   # inject diretto
        hs_mod.encode_proto  = mock_encode
        hs_mod.build_from_schema_cfg = mock_build
        yield _factory_fn
```

**Regola**: tutti i protobuf sono sostituiti con `types.SimpleNamespace` o `MagicMock`. La state machine è testata in isolamento completo.

### Fixture `ser_mod` (per moduli senza singleton — import diretto)

Per moduli puri come `serializer.py` (nessun singleton globale, nessun bus), si usa import diretto con scope `module`:

```python
@pytest.fixture(scope="module")
def ser_mod():
    if _MODULE_PATH in sys.modules:
        del sys.modules[_MODULE_PATH]
    import oaa_control_channel.serializer as mod
    importlib.reload(mod)
    return mod
```

**Regola**: usare `scope="module"` quando il modulo è stateless (nessun singleton che cambia tra test).

### Helper di asserzione riutilizzabili

```python
def _published(bus, topic) -> list[dict]:
    return [c.args[1] for c in bus.publish.call_args_list if c.args[0] == topic]

def _sent_msg_ids(send_fn) -> list[int]:
    return [c.args[0] for c in send_fn.call_args_list]

def _published_topics(publish_fn) -> list[str]:
    return [c.args[0] for c in publish_fn.call_args_list]
```

---

## Stato Roadmap per Fase

| Fase | Stato | Note |
|---|---|---|
| **0 — Infrastruttura** | ✅ Completa | `conftest.py`, `pytest.ini`, `requirements-test.txt` |
| **1 — Unit Test §1.1 Shared** | 🟡 Parziale | `test_proto_utils.py` ✅, `test_bus_client.py` ✅, `test_config_client.py` ✅, `test_logger.py` ❌ mancante |
| **1 — Unit Test §1.2 Base** | ✅ Completa | `test_base_channel_module.py` ✅ |
| **1 — Unit Test §1.3 Channel specifici** | 🟡 Parziale | `test_audio_module.py` ✅, `test_video_module.py` ✅, `av_input/bluetooth/input/sensor/wifi` ❌ mancanti |
| **1 — Unit Test §1.4 Standalone** | 🟡 Parziale | `test_oaa_control_channel_main.py` ✅, `test_handshake.py` ✅, `test_serializer.py` ✅, `test_service_discovery.py` ❌ **PROSSIMO**, `channel_manager` ❌, `audio_manager` ❌, `video_ui` ❌, `bluetooth/*` ❌, `tcp_server` ❌, `config_manager` ❌, altri ❌ |
| **2 — Integration** | ❌ Non iniziata | — |
| **3 — E2E** | ❌ Non iniziata | — |
| **4 — Performance** | ❌ Non iniziata | — |
| **5 — Fuzz** | ❌ Non iniziata | — |

---

## Prossimo Passo Immediato

**`test_service_discovery.py`** — builder del Service Discovery Response in `oaa_control_channel/service_discovery.py` (16 KB).  
Logica critica per la negoziazione dei canali; usata da `handshake.py` in stato `SDR`.

Ordine suggerito per completare §1.4:
1. ~~`test_serializer.py`~~ ✅
2. `test_service_discovery.py`
3. `test_channel_manager.py`
4. `test_tcp_server.py`
5. `test_audio_manager.py`
6. `test_video_ui.py`
7. `test_bluetooth_*.py` (×3)
8. `test_config_manager.py`
9. Moduli secondari: `rfcomm_handshake`, `hostapd_helper`, `config_ui`, `log_viewer`

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| `importlib.reload()` per ogni test di `main.py` | I singleton globali devono essere resettati per l'isolamento |
| Protobuf stubbed con `types.SimpleNamespace` + `MagicMock` | I `.pb2` generati non sono presenti nell'ambiente di test CI |
| `_FakeEnum` con `__eq__` custom | I valori proto enum non sono istanze standard di Python `Enum` |
| Inject diretto su `mod.decode_proto` etc. | Più affidabile di `patch()` dopo `importlib.reload()` su moduli con import at module level |
| `autouse=True` su `_patch_module` | Garantisce isolamento automatico senza dimenticare la fixture |
| `scope="module"` per `ser_mod` in `test_serializer.py` | `serializer.py` è stateless — nessun singleton, riuso sicuro tra test nella stessa sessione |
| Helper `_parse_bulk_frame` / `_parse_first_frame` | Evita parsing duplicato in ogni test — rende le asserzioni leggibili |

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

*Handoff Version: 2.1*  
*Aggiornato: 2026-05-13*  
*Commit head: `ffe6314`*  
*Test totali scritti: 445*
