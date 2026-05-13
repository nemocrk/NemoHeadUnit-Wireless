# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `acb6dce`

---

## Stato Corrente in Una Frase

Fase 0 completata (infrastruttura), Fase 1 §1.1 + §1.2 + §1.4 parzialmente completata: **13 file di test, 767 test** scritti e pushati su `main`. Nessun test è ancora stato eseguito sull'hardware reale.

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
| `v2/tests/unit/modules/audio_manager/test_audio_manager.py` | `acb6dce` | 88 | enum helpers×16, enumerate fallback×4, _build_schema×5, sink_input_index×3, volume_control×5, _refresh_devices×4, config_loaded×5, config_changed×8, volume_set×6, ch_volume_set×4, boot×6 |

**Totale: 767 test in 13 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti (da rispettare)

### Fixture `am` (per audio_manager/main.py — subprocess + bus + cfg mockati)

`audio_manager/main.py` usa `subprocess.run` per wpctl/pactl. La fixture `am` patcha `subprocess.run` a livello di modulo e resetta esplicitamente lo stato:

```python
@pytest.fixture()
def am():
    mock_run = MagicMock(return_value=_make_run_result(""))
    with patch("shared.bus_client.BusClient", return_value=mock_bus), \
         patch("subprocess.run", mock_run):
        import modules.audio_manager.main as mod
        importlib.reload(mod)
        mod._sinks = ["default"]; mod._sources = ["default"]
        mod._config = {...defaults...}
        yield mod, mock_bus, mock_cfg, mock_run
```

**Regola**: i test che verificano un comando specifico (es. wpctl/pactl) usano `patch.object(mod, "enumerate_sinks")` oppure un `patch("subprocess.run")` locale dentro il test, sovrascrivendo il default della fixture.

**Pattern helper utile**: `_payload(mock_bus, topic)` per leggere il primo payload di una publish.

### Fixture `ts` (per tcp_server/main.py)
Reset esplicito di 7 singleton + `_shutdown_ack_event.clear()`. `FrameAssembler` reale su `mod._assembler` nei test di `_on_raw_frame`.

### Fixture `cm` (per channel_manager/main.py)
`session._launcher = mock_launcher_instance` inject diretto post-`__init__`.

### Fixture `scope="module"` per moduli stateless
`frame_codec.py`, `registry.py`, `serializer.py`, `service_discovery.py` — nessun singleton mutabile.

### Helper di asserzione riutilizzabili

```python
def _topics(mock_bus) -> list[str]:
    return [c.args[0] for c in mock_bus.publish.call_args_list]

def _payload(mock_bus, topic: str) -> dict:
    for c in mock_bus.publish.call_args_list:
        if c.args[0] == topic:
            return c.args[1]
    return {}
```

---

## Stato Roadmap per Fase

| Fase | Stato | Note |
|---|---|---|
| **0 — Infrastruttura** | ✅ Completa | `conftest.py`, `pytest.ini`, `requirements-test.txt` |
| **1 — Unit Test §1.1 Shared** | 🟡 Parziale | `test_proto_utils.py` ✅, `test_bus_client.py` ✅, `test_config_client.py` ✅, `test_logger.py` ❌ mancante |
| **1 — Unit Test §1.2 Base** | ✅ Completa | `test_base_channel_module.py` ✅ |
| **1 — Unit Test §1.3 Channel specifici** | 🟡 Parziale | `test_audio_module.py` ✅, `test_video_module.py` ✅, `av_input/bluetooth/input/sensor/wifi` ❌ mancanti |
| **1 — Unit Test §1.4 Standalone** | 🟡 Parziale | `oaa_cc_main` ✅, `handshake` ✅, `serializer` ✅, `service_discovery` ✅, `channel_manager` ✅, `tcp_server` ✅, `audio_manager` ✅, `video_ui` ❌ **PROSSIMO**, `bluetooth/*` ❌, `config_manager` ❌, altri ❌ |
| **2 — Integration** | ❌ Non iniziata | — |
| **3 — E2E** | ❌ Non iniziata | — |
| **4 — Performance** | ❌ Non iniziata | — |
| **5 — Fuzz** | ❌ Non iniziata | — |

---

## Prossimo Passo Immediato

**`test_video_ui.py`** — `v2/modules/video_ui/main.py`.
Gestisce la visualizzazione del video AA su un'interfaccia Qt/GStreamer.

Ordine suggerito per completare §1.4:
1. ~~`test_serializer.py`~~ ✅
2. ~~`test_service_discovery.py`~~ ✅
3. ~~`test_channel_manager.py`~~ ✅
4. ~~`test_tcp_server.py`~~ ✅
5. ~~`test_audio_manager.py`~~ ✅ (enum×20 + schema×5 + config×13 + handlers×16 + boot×6 = 88 test)
6. `test_video_ui.py` ← **PROSSIMO**
7. `test_bluetooth_*.py` (×3)
8. `test_config_manager.py`
9. Moduli secondari: `rfcomm_handshake`, `hostapd_helper`, `config_ui`, `log_viewer`

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| `importlib.reload()` per ogni test di `main.py` | I singleton globali devono essere resettati per l'isolamento |
| `subprocess.run` patchato a livello di fixture + override locale | Default sicuro (restituisce stringa vuota); test specifici iniettano stdout controllato |
| `patch.object(mod, "enumerate_sinks")` nei test di `_refresh_devices` | Più pulito che patchare subprocess indirettamente per funzioni di alto livello |
| `patch.object(mod, "_set_global_volume")` nei test di config | Evita dependency su wpctl nei test di logica pure (config merge, publish) |
| `_make_run_result(stdout)` helper | Crea MagicMock con `.stdout` e `.returncode` in una riga |
| `autouse=True` su `_patch_module` in altri moduli | Garantisce isolamento automatico |
| `scope="module"` per moduli stateless | `frame_codec.py`, `registry.py`, `serializer.py`, `service_discovery.py` |
| `session._launcher = mock` inject diretto | Più affidabile di `patch()` su `__init__` dopo `importlib.reload()` |
| Reset esplicito singleton in `ts` e `am` | Moduli con molti singleton — più sicuro del solo reload |

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

*Handoff Version: 2.5*  
*Aggiornato: 2026-05-13*  
*Commit head: `acb6dce`*  
*Test totali scritti: 767*
