# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-26 15:40 — floating_menu_ui testato + docs aggiornati

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: `ui_shell`, `navbar_ui`, `floating_menu_ui` implementati e testati ✅. Prossimo step: `video_ui` oppure test integrazione headless.**

---

## 2026-05-26 — floating_menu_ui: test + docs aggiornati

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `tests/unit/modules/floating_menu_ui/test_floating_menu_ui.py` | [`f31fc7b`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f31fc7b3af94c15df8af795002ef86e12057eea1) | 35 test: on_request discovery, unregister, mutual exclusivity, home close, settings toggle, dpi_factor, arc geometry, visible count, sorted entries, boot protocol |
| `docs/roadmap-current.md` | [`a5ed35e`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a5ed35e320b01ce11c3ed759c60c82abb38df755) | Step 5 floating_menu_ui ✅, priority 3 aggiunto alla convention table, bump v5.2 |
| `docs/UI_ARCHITECTURE.md` | [`02c98d1`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/02c98d1dd7fb7a8d67834a4813f0d196c8b2650e) | floating_menu_ui: process map, boot sequence (priority 3), bus topics (ui.settings.toggle, ui.home.pressed, ui.module.open/close), Arc Geometry section, module naming table |
| `docs/UI_DESIGN_SYSTEM.md` | [`bb30324`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/bb3032448532740ede9e2b9a5bedd1aedca07588) | Arc component spec (geometry, icon states, scroll hint, motion rows, PyQt6 paint pattern), anti-patterns aggiornati |

**Architettura floating_menu_ui:**
- **Priority 3** — dopo `ui_shell` (priority 2), prima dei widget `on_request` (priority 4) per garantire che sia già in ascolto su `ui.widget.register` quando `bt_ui`/`config_ui` si annunciano
- **Arc geometry**: quarter-circle 90° ancorato al bottom-right. Radius base 120px × dpi_factor. Max 8 icone visibili; drag tangenziale per scrollare oltre 8
- **Mutual exclusivity**: apertura di un modulo chiude automaticamente quello precedentemente aperto via `ui.module.close`
- **Visibility driven by `ui.settings.toggle`**: la finestra è `0×0` (hidden) fino al toggle; `ui.home.pressed` chiude tutto e azzera lo stato
- **Scroll hint**: 5 dot indicator sul bordo destro del bounding box quando N > 8
- **Headless safe**: PyQt6 opzionale — se non disponibile il modulo gira senza finestra

**Bus topics:**
- Subscribe: `ui.shell.ready`, `ui.widget.register`, `ui.widget.unregister`, `ui.widget.geometry`, `input.event.floating_menu_ui`, `ui.settings.toggle`, `ui.home.pressed`
- Publish: `ui.widget.register`, `ui.widget.update`, `ui.widget.unregister`, `ui.module.open`, `ui.module.close`, `system.module_ready`, `system.ready`

**Status:** Completato ✅

**Prossimi 3 step:**
1. Implementare `modules/video_ui/` — step 6 della roadmap
2. Test di integrazione headless `ui_shell ⇔ navbar_ui ⇔ floating_menu_ui` sul bus reale
3. Implementare `modules/bt_ui/` (on_request, menu_order=1)

---

## 2026-05-26 — navbar_ui: frosted-glass bottom bar, priority 4

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `modules/navbar_ui/main.py` | [`637abcf`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/637abcf876c0dbf83db0b2f37e2e3103c3466a17) | Transparent frameless PyQt6 window, frosted glass bottom bar, prev/play-pause/next + BT indicator, input routing, boot protocol |
| `tests/unit/modules/navbar_ui/test_navbar_ui.py` | [`fb230d5`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/fb230d511df16f5868d15b247eebbeacac6f347b) | Unit test completi: ≥80% coverage su boot, registration, geometry, state handlers, config, NavbarWindow headless (layout, hit-test, tap, touch-target) |

**Status:** Completato ✅

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
# Coverage floating_menu_ui
pytest tests/unit/modules/floating_menu_ui/ -v --cov=modules/floating_menu_ui --cov-report=term-missing

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
Priority 2  ui_shell            ← layout engine + input_trap
Priority 3  floating_menu_ui    ← on_request launcher (deve scoprire i widget priority 4)
Priority 4  navbar_ui, video_ui, bt_ui, config_ui   ← widget processes
            (priority 4 garantisce ui_shell + floating_menu_ui già operativi)
```

### Widget registration contract
```python
# Widget always-visible (navbar, video) — pubblica dopo aver ricevuto ui.shell.ready:
bus.publish("ui.widget.register", {
    "name":       MODULE_NAME,
    "z_order":    2,
    "dock":       "bottom",   # o top|left|right|center|...
    "height":     60,
    "min_height": 48,
    "max_height": 80,
})

# Widget on_request (bt_ui, config_ui) — stessi campi + campi menu:
bus.publish("ui.widget.register", {
    "name":       MODULE_NAME,
    "z_order":    2,
    "dock":       "center",
    "width":      400,
    "height":     500,
    "on_request": True,
    "menu_order": 1,           # posizione nell'arco
    "icon":       "🦷",         # glyph Unicode o nome Lucide
})
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
