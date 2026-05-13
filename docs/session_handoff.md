# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `6a83677`

---

## Stato Corrente in Una Frase

**Bluetooth completamente coperto (5 file, ~368 test).** Totale: **19 file di test, 1267 test** su `main`. Nessun test è ancora stato eseguito sull’hardware reale.

---

## File Prodotti

| File | Commit | Test | Note |
|---|---|---|---|
| `v2/tests/conftest.py` | `0ff487e` | — | Fixture globali |
| `v2/tests/pytest.ini` | `0ff487e` | — | Marker e config pytest |
| `v2/tests/requirements-test.txt` | `0ff487e` | — | Dipendenze test |
| `v2/tests/unit/shared/test_proto_utils.py` | precedente | 47 | encode/decode round-trip |
| `v2/tests/unit/modules/channel_modules/audio/test_audio_module.py` | precedente | 42 | codec, prebuffer |
| `v2/tests/unit/modules/channel_modules/video/test_video_module.py` | precedente | 38 | frame decode |
| `v2/tests/unit/modules/channel_modules/test_base_channel_module.py` | precedente | 44 | state machine lifecycle |
| `v2/tests/unit/shared/test_bus_client.py` | precedente | 52 | connect/disconnect, publish |
| `v2/tests/unit/shared/test_config_client.py` | precedente | 38 | config load, merge, callback |
| `v2/tests/unit/oaa_control_channel/test_oaa_control_channel_main.py` | `c3d7a4a` | 54 | boot, session lifecycle |
| `v2/tests/unit/oaa_control_channel/test_handshake.py` | `6c99b41` | 62 | state machine completa |
| `v2/tests/unit/oaa_control_channel/test_serializer.py` | `ffe6314` | 68 | FrameHeader, FrameSerializer |
| `v2/tests/unit/oaa_control_channel/test_service_discovery.py` | `4e7a28d` | 72 | SDR, channels, message |
| `v2/tests/unit/modules/channel_manager/test_channel_manager.py` | `4f90f8a` | 78 | registry, session lifecycle |
| `v2/tests/unit/modules/tcp_server/test_tcp_server.py` | `412541a` | 84 | frame_codec, TLS, session |
| `v2/tests/unit/modules/audio_manager/test_audio_manager.py` | `acb6dce` | 88 | enum, sink, volume, config |
| `v2/tests/unit/modules/video_ui/test_video_ui.py` | `83973cb` | 92 | state machine, handlers, GL widget |
| `v2/tests/unit/modules/bluetooth/test_bluetooth_main.py` | `b024893` | 96 | boot, config, discover, pair, paired CRUD, autoconnect |
| `v2/tests/unit/modules/bluetooth/test_bluez_adapter.py` | `17c7c4d` | 88 | __init__, init(), find_adapter, profiles, controls, reset, shutdown |
| `v2/tests/unit/modules/bluetooth/test_discovery.py` | `a1b5156` | 72 | init, is_running, start, stop, _start_discovery, _stop_discovery, _poll_devices, _run |
| `v2/tests/unit/modules/bluetooth/test_pairing.py` | `d4973f8` | 84 | __init__, register, unregister, pair, reply/error handlers, confirm, reject, pin_code, confirm_request, confirm_worker, cancel, helpers |
| `v2/tests/unit/modules/bluetooth/test_paired_devices.py` | `6a83677` | 68 | _device_to_dict, _get_managed_objects, _resolve_*path, list_paired, get_info, remove, connect, disconnect |

**Totale: 1267 test in 19 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti

### Bluetooth: pattern comune per tutti i file

- **Import una volta sola** a livello di file del modulo con `patch("shared.logger.get_logger")` + `importlib.reload()`
- **`patch.dict(sys.modules, {"dbus": mock_dbus})`** per ogni test che esegue metodi con lazy dbus import
- **`_make_dbus_mocks()`** / **`_make_bus()`** helper che costruiscono mock coerenti con `Interface.side_effect` per distinguere interfacce D-Bus
- **`iface_factory`** pattern locale nei test complessi: `dbus.Interface.side_effect = lambda obj, iface: {iface_name: mock}[iface]`
- **`patch.object(agent/session, "method")`** per isolare unit senza eseguire side-effect reali (thread, D-Bus)
- **`patch("threading.Thread")`** per verificare launch senza eseguire il loop

### Fixture `ba_init` (ba + già inizializzato)

```python
@pytest.fixture()
def ba_init():
    adapter = BluezAdapter()
    mock_dbus = _make_dbus_mocks()[0]
    with patch.dict(sys.modules, {"dbus": mock_dbus}):
        adapter.init()
    return adapter, mock_dbus, mock_props_iface, mock_adapter_iface
```

### Helper `_make_objects()` (paired_devices / discovery)

```python
def _make_objects(devices=None, adapters=None):
    objects = {}
    for path, props in (devices or []):
        objects[path] = {"org.bluez.Device1": props}
    for path in (adapters or []):
        objects[path] = {"org.bluez.Adapter1": {}}
    return objects
```

### Helper riutilizzabili (bus-based modules)

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
| **0 — Infrastruttura** | ✅ Completa | |
| **1 — Unit Test §1.1 Shared** | 🟡 Parziale | `test_logger.py` ❌ mancante |
| **1 — Unit Test §1.2 Base** | ✅ Completa | |
| **1 — Unit Test §1.3 Channel** | 🟡 Parziale | audio, video ✅; altri ❌ |
| **1 — Unit Test §1.4 Standalone** | 🟡 Parziale | oaa_cc, tcp_server, audio_manager, video_ui, bluetooth ✅✅; **`config_manager`** ← PROSSIMO |
| **2–5** | ❌ Non iniziata | |

---

## Prossimo Passo Immediato

**`test_config_manager.py`** — `ConfigManager` in `v2/modules/config_manager/` (o `v2/shared/`).

Ordine suggerito post-bluetooth:
1. ~~bluetooth (5 file)~~ ✅
2. `test_config_manager.py` ← **PROSSIMO**
3. `test_logger.py` (shared)
4. Channel modules mancanti (input, sensor, navigation, …)

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| `patch.dict(sys.modules, {"dbus": mock_dbus})` per ogni metodo | dbus è importato lazy inside i metodi |
| `_make_dbus_mocks()` / `_make_bus()` con `Interface.side_effect` | Evita ambiguità: ogni interfaccia D-Bus diversa ritorna mock distinto |
| Import modulo una volta sola a livello di file | Nessun singleton globale nei moduli bluetooth |
| `patch("time.sleep")` nei test retry | Evita attese reali |
| `patch.object(adapter, "register_profiles")` nel test reset | Isola il test reset |
| `connect()` usa `_replied` Event come flag idempotente | Evita double-call tra watchdog e D-Bus reply |

---

*Handoff Version: 2.9*  
*Aggiornato: 2026-05-13*  
*Commit head: `6a83677`*  
*Test totali scritti: 1267*
