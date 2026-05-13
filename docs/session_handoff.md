# Session Handoff — NemoHeadUnit-Wireless v2 Test Suite

> **Scopo**: documento di continuità per sessioni AI successive.  
> Contiene lo stato esatto della test suite, i file prodotti, le decisioni prese e il prossimo passo immediato.  
> **Aggiornato**: 2026-05-13 — commit `b024893`

---

## Stato Corrente in Una Frase

Fase 0 completata (infrastruttura), Fase 1 §1.1 + §1.2 + §1.4 parzialmente completata: **15 file di test, 955 test** scritti e pushati su `main`. Nessun test è ancora stato eseguito sull’hardware reale.

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
| `v2/tests/unit/modules/bluetooth/test_bluetooth_main.py` | `b024893` | 96 | boot×8, config×5, discover×5, pair×3, confirm/reject×5, rfcomm×1, autoconnect×5, paired CRUD×12, internal callbacks×5, autoconnect loop×5 |

**Totale: 955 test in 15 file di test + 3 file infrastruttura.**

---

## Pattern Architetturali Stabiliti

### Fixture `bt` (per bluetooth/main.py — dbus + gi headless)

`bluetooth/main.py` importa `dbus`, `dbus.mainloop.glib` e `gi.repository.GLib` all’import-time via `bluez_adapter.py` (`_setup_glib_mainloop()` è chiamata al modulo-level). Gli stub devono essere in `sys.modules` **prima** del primo import del modulo.

```python
def _install_dbus_stubs():
    dbus_mod = types.ModuleType("dbus")
    dbus_mod.SystemBus  = MagicMock
    dbus_mod.Interface  = MagicMock
    dbus_mod.Boolean    = lambda v: v
    sys.modules.setdefault("dbus", dbus_mod)
    sys.modules.setdefault("dbus.mainloop", types.ModuleType("dbus.mainloop"))
    dbus_ml_glib = types.ModuleType("dbus.mainloop.glib")
    dbus_ml_glib.DBusGMainLoop = MagicMock()
    sys.modules.setdefault("dbus.mainloop.glib", dbus_ml_glib)
    # gi.repository.GLib
    sys.modules.setdefault("gi", ...)
    sys.modules.setdefault("gi.repository.GLib", MagicMock())

_install_dbus_stubs()   # livello di file
```

**Regola**: BluezAdapter, DiscoverySession, PairingAgent, paired_devices vengono patchati con `patch("bluetooth.X")` nella fixture `bt`, prima del reload.

**Regola**: `mod._config` viene resettato manualmente ai default dopo reload (i field_bool/int/string sono MagicMock con `.default` fissato).

**Regola**: `_start_glib_mainloop` e `_stop_glib_mainloop` vengono patchati con `patch.object(mod, ...)` nei test di boot per evitare avvio thread GLib reali.

**Regola**: i test su `_start_autoconnect` usano `patch("threading.Thread")` per verificare che il thread venga o non venga lanciato, senza eseguire `_autoconnect_loop`.

### Fixture `vu` (per video_ui/main.py — PyQt6 + GStreamer headless)
Vedi handoff v2.6.

### Fixture `am` (per audio_manager/main.py)
`subprocess.run` patchato a stringa vuota di default.

### Fixture `ts` (per tcp_server/main.py)
Reset esplicito di 7 singleton + `_shutdown_ack_event.clear()`.

### Fixture `cm` (per channel_manager/main.py)
`session._launcher = mock_launcher_instance` inject diretto.

### Helper riutilizzabili

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
| **1 — Unit Test §1.3 Channel specifici** | 🟡 Parziale | audio, video ✅; altri ❌ |
| **1 — Unit Test §1.4 Standalone** | 🟡 Parziale | oaa_cc, tcp_server, audio_manager, video_ui, bluetooth/main ✅; **`test_bluez_adapter.py`** ← **PROSSIMO**, config_manager ❌, altri ❌ |
| **2–5** | ❌ Non iniziata | |

---

## Prossimo Passo Immediato

**`test_bluez_adapter.py`** — `BluezAdapter` class in `v2/modules/bluetooth/bluez_adapter.py`.

Ordine suggerito per completare bluetooth:
1. ~~`test_bluetooth_main.py`~~ ✅ (96 test)
2. `test_bluez_adapter.py` ← **PROSSIMO** — init, _find_adapter_path, register_profiles, set_discoverable, set_name, get_adapter_address, is_discovering, reset, shutdown
3. `test_discovery.py` — DiscoverySession
4. `test_pairing.py` — PairingAgent state machine
5. `test_paired_devices.py` — list_paired, connect, disconnect, remove

---

## Decisioni Tecniche Prese

| Decisione | Rationale |
|---|---|
| `sys.modules` injection dbus+gi a livello di file | `_setup_glib_mainloop()` è chiamata all’import-time di bluez_adapter.py |
| `sys.modules.setdefault()` | Evita conflitti tra test files |
| `patch("bluetooth.X")` invece di `patch.object` | Le classi vengono risolte alla riga `from bluetooth.X import X` nel reload |
| `patch.object(mod, "_start_glib_mainloop")` | Evita thread GLib reali nei test |
| `patch("threading.Thread")` in test autoconnect | Verifica lancio thread senza eseguire `_autoconnect_loop` |

---

*Handoff Version: 2.7*  
*Aggiornato: 2026-05-13*  
*Commit head: `b024893`*  
*Test totali scritti: 955*
