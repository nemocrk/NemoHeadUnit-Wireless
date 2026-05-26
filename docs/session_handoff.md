# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-26 12:57 — navbar_ui implementato

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: `ui_shell` e `navbar_ui` implementati e testati ✅. Prossimo step: aggiornare roadmap + test integrazione headless.**

---

## 2026-05-26 — navbar_ui: frosted-glass bottom bar, priority 4

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `modules/navbar_ui/main.py` | [`637abcf`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/637abcf876c0dbf83db0b2f37e2e3103c3466a17) | Transparent frameless PyQt6 window, frosted glass bottom bar, prev/play-pause/next + BT indicator, input routing, boot protocol |
| `tests/unit/modules/navbar_ui/test_navbar_ui.py` | [`fb230d5`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/fb230d511df16f5868d15b247eebbeacac6f347b) | Unit test completi: ≥80% coverage su boot, registration, geometry, state handlers, config, NavbarWindow headless (layout, hit-test, tap, touch-target) |

**Architettura navbar_ui:**
- **Priority 4** (ui_shell priority 2 è garantito già operativo al momento di avvio navbar_ui)
- Attende `ui.shell.ready` per registrarsi — race guard: se `_shell_ready` è già `True` al momento di `system.start`, si registra immediatamente
- Finestra Qt: transparent + FramelessWindowHint + Tool (no taskbar); mai chiama `setGeometry()` autonomamente — posizione esclusivamente da `ui.widget.geometry`
- Non chiama `show()` fino a ricezione di `ui.widget.geometry` — window non visibile finché la geometria non è confermata
- **Input headless**: `handle_input()` riceve payload da `input.event.navbar_ui` (bus), costruisce logica press/release in pure Python senza eventi Qt diretti
- **Design tokens** (da `UI_DESIGN_SYSTEM.md`): `#1c1c1c` frosted bg, `#f0ece4` text, `#c8b89a` accent, DM Sans 14px
- Al `system.stop`: pubblica `ui.widget.unregister` + quit Qt + stop bus

**Bus topics:**
- Subscribe: `ui.shell.ready`, `ui.widget.geometry`, `input.event.navbar_ui`, `media.state`, `bt.state`
- Publish: `ui.widget.register`, `ui.widget.unregister`, `media.command {action: play_pause|prev|next}`

**Perché priority 4 e non 2:**
Con priority 4 il sistema di boot garantisce che `ui_shell` (priority 2) abbia completato `system.ready` e pubblicato `ui.shell.ready` prima che `navbar_ui` parta. Con priority 2 (come da doc) il timing dipende dall'ordine di startup parallelo, richiedendo una race guard più fragile.

**Status:** Completato ✅

**Prossimi 3 step:**
1. Aggiornare `docs/UI_ARCHITECTURE.md` — annotare priority 4 come scelta deliberata per `navbar_ui` (e futuri widget UI)
2. Aggiornare `docs/roadmap-current.md` step 3 e 4 a `✅`
3. Test integrazione headless `ui_shell ↔ navbar_ui` sul bus reale

---

## 2026-05-26 — ui_shell: layout engine + widget registry + input_trap

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `modules/ui_shell/main.py` | [`2c3e912`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/2c3e9126354ffa6a12047a607ebcb25fde08a3a8) | Layout engine completo: `_reflow()`, `_compute_geometry()`, `_hit_test()`, widget registry, input_trap PyQt6, boot protocol |
| `tests/unit/modules/ui_shell/test_ui_shell.py` | [`adf04fa`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/adf04fa93ffd03b12be123bee22bc72f6a83ecb1) | Unit test completi: ≥80% coverage |

**Status:** Completato ✅

---

## 2026-05-26 — UI Architecture & Design System

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `docs/UI_ARCHITECTURE.md` | [`f99be80`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f99be807e05bfd17bb54dde851054c3e14b46903) | Architettura UI completa |
| `docs/UI_DESIGN_SYSTEM.md` | [`f99be80`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f99be807e05bfd17bb54dde851054c3e14b46903) | Design system Scandinavian minimalist dark |

**Status:** Completato ✅

---

## Residui `v2/` rimanenti

### 🟠 Priorità BASSA — script infrastruttura

| File | Righe | Note |
|---|---|---|
| `packaging/build_deb.sh` | 15, 154, 186 | Copia `v2/` nel deb |
| `packaging/nemo-headunit.sh` | 8 | `APP_MAIN="/opt/nemo-headunit/v2/main.py"` |
| `scripts/deploy_remote.sh` | 167-175 | Rsync `v2/` su remote |

---

## Comandi Utili

```bash
# Coverage navbar_ui
pytest tests/unit/modules/navbar_ui/ -v --cov=modules/navbar_ui --cov-report=term-missing

# Coverage ui_shell
pytest tests/unit/modules/ui_shell/ -v --cov=modules/ui_shell --cov-report=term-missing

# Coverage generale
pytest --cov=. --cov-report=html --cov-report=term-missing --ignore=services
```

---

## Pattern Architetturali Stabiliti

### Widget UI — priority convention
```
Priority 0  config_manager
Priority 1  bluetooth_manager, tcp_server, audio_manager
Priority 2  ui_shell     ← layout engine + input_trap
Priority 4  navbar_ui, video_ui, bt_ui, config_ui   ← widget processes
            (priority 4 garantisce ui_shell già operativo e ui.shell.ready già pubblicato)
```

### Widget registration contract
```python
# Widget pubblica dopo aver ricevuto ui.shell.ready:
bus.publish("ui.widget.register", {
    "name":       MODULE_NAME,
    "z_order":    2,
    "dock":       "bottom",   # o top|left|right|center|...
    "height":     60,
    "min_height": 48,
    "max_height": 80,
})
# ui_shell risponde con:
# ui.widget.geometry {name, x, y, w, h}
# Il widget chiama setGeometry(x, y, w, h) + show() solo a questo punto.
```

### Bootstrap path — pattern post-promozione
```python
_HERE      = Path(__file__).parent   # modules/<nome>/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

### BusClient
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit
