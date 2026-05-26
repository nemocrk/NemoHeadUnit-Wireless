# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-26 12:50 — ui_shell implementato

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: `modules/ui_shell/` implementato e testato ✅. Prossimo step: implementare `modules/navbar_ui/`.**

---

## 2026-05-26 — ui_shell: layout engine + widget registry + input_trap

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `modules/ui_shell/main.py` | [`2c3e912`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/2c3e9126354ffa6a12047a607ebcb25fde08a3a8) | Layout engine completo: `_reflow()`, `_compute_geometry()`, `_hit_test()`, widget registry, input_trap PyQt6, boot protocol |
| `tests/unit/modules/ui_shell/test_ui_shell.py` | [`adf04fa`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/adf04fa93ffd03b12be123bee22bc72f6a83ecb1) | Unit test completi: ≥80% coverage su _clamp, _resolve_size, _compute_geometry (tutti i dock), _reflow, register/update/unregister, _hit_test, on_input_raw, on_screen_resize, boot protocol |

**Architettura ui_shell:**
- `_registry: dict[str, WidgetRecord]` — registry thread-safe dei widget (lock per operazioni)
- `_reflow()` — algoritmo multi-layer: ordina per z_order, consuma spazio da bordi (top→bottom→left→right), center riempie resto
- `_compute_geometry()` — helper per singolo widget, gestisce aspect_ratio
- `_hit_test(x, y)` — ritorna il widget più in alto (z_order desc) che contiene il punto
- `on_input_raw` — routing: hit_test → pubblica `input.event.<name>` con coordinate relative
- `_run_qt()` — PyQt6 in thread dedicato: `ShellWindow` (opaque charcoal `#141414`) + `InputTrap` (transparent always-on-top, cattura tutti gli eventi raw)
- Headless graceful: se PyQt6 non è disponibile, warning + continua senza Qt

**Bus topics implementati:**
- Subscribe: `ui.widget.register`, `ui.widget.update`, `ui.widget.unregister`, `input.raw`
- Publish: `ui.widget.geometry`, `ui.shell.ready`, `system.module_ready`, `system.ready`, `input.event.<name>`

**Perché:**
ui_shell è il layout orchestrator centrale: tutti i widget si registrano qui per ricevere geometria calcolata. È anche l'unico processo con una finestra Qt visibile — tutti gli altri widget si posizionano sopra tramite geometria bus.

**Status:** Completato ✅

**Prossimi 3 step:**
1. Implementare `modules/navbar_ui/` — primo widget concreto (dock=bottom, h=60, frosted glass) come proof-of-concept
2. Aggiornare `docs/roadmap-current.md` step 3 a `✅`
3. Testare integrazione ui_shell ↔ navbar_ui sul bus reale (headless)

---

## 2026-05-26 — UI Architecture & Design System

**Cosa cambiato:**

| File | Commit | Contenuto |
|---|---|---|
| `docs/UI_ARCHITECTURE.md` | [`f99be80`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f99be807e05bfd17bb54dde851054c3e14b46903) | Architettura UI completa: process map, screen stack, bus topic contract, input routing, boot sequence |
| `docs/UI_DESIGN_SYSTEM.md` | [`f99be80`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/f99be807e05bfd17bb54dde851054c3e14b46903) | Design system Scandinavian minimalist dark: token colore, tipografia DM Sans, componenti, motion, PyQt6 notes |
| `docs/project-vision.md` | [`de6296b`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/de6296bc3f29156f36ec60e99806e62f367397ab) | Aggiunto `UI_ARCHITECTURE.md` e `UI_DESIGN_SYSTEM.md` in Key Source Files (4.3), Phase 2, Tech Stack Summary; v3.5 |
| `docs/roadmap-current.md` | [`fc8eee8`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/fc8eee8e3fe199d7d715ac2e909d998cc3481ed9) | Aggiunto blocco `feature/ui-module` con 8 step tracciati; v5.0 |

**Status:** Completato ✅

---

## 2026-05-19 — Fix 🟡 MEDIA: commenti/docstring v2/ in moduli e test

**Cosa cambiato:**

| File | Commit | Fix |
|---|---|---|
| `main.py` | [`5598fbb`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/5598fbb048349967351b28df7bd721620e33506e) | Docstring: remove `v2` dal titolo, `v2/modules/` → `modules/` |
| `modules/zmq_trace/main.py` | [`e1c55ec`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e1c55ecc35e08afcac222022d7af8d06753dd14f) | `_V2`→`_REPO_ROOT`, docstring path aggiornato |
| `tests/unit/modules/rfcomm_handshake/test_packet_unit.py` | [`96e9e6b`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/96e9e6b1d1f4a9a011f54e12fedc2279268615f4) | Bootstrap `_V2`→`_REPO_ROOT`, commento aggiornato |
| `tests/unit/oaa_control_channel/test_rfcomm_and_channel_manager.py` | [`3e11f32`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/3e11f3212775b65d6133cc584ea3a06f107804a7) | Docstring `cd v2/tests`→`cd tests` |
| `tests/e2e/helpers/frame_sequences.py` | [`820499f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/820499f6e358f5d7088e49589643d7efcc404876) | URL GitHub `/main/v2/modules/`→`/main/modules/` |
| `tests/unit/oaa_control_channel/test_service_discovery.py` | [`9eb5fe5`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9eb5fe525832817420c53b1a9b071805ce762616) | Commento + 8 import `from v2.protos.oaa…`→`from protos.oaa…` |

**Status:** Completato ✅

---

## Residui `v2/` rimanenti

### 🟠 Priorità BASSA — script infrastruttura (non usati in dev corrente)

| File | Righe | Note |
|---|---|---|
| `packaging/build_deb.sh` | 15, 154, 186 | Copia `v2/` nel deb, path `APP_OPT/v2/` |
| `packaging/nemo-headunit.sh` | 8 | `APP_MAIN="/opt/nemo-headunit/v2/main.py"` |
| `scripts/deploy_remote.sh` | 167-175 | Rsync `v2/` su remote |

### 🟡 Docs da aggiornare (non bloccanti)

| File | Note |
|---|---|
| `docs/KNOWN_PRODUCTION_BUGS.md` | Path `v2/modules/...` e `v2/shared/...` in 5 righe |
| `docs/TEST_SUITE_ARCHITECTURE.md` | Intero documento scritto per `v2/` — aggiornamento non bloccante |

---

## Comandi Utili

```bash
# Coverage ui_shell
pytest tests/unit/modules/ui_shell/ -v --cov=modules/ui_shell --cov-report=term-missing

# Coverage generale
pytest --cov=. --cov-report=html --cov-report=term-missing --ignore=services

# Unit + integration
pytest -m "unit or integration" --cov=. --cov-fail-under=80
```

---

## Pattern Architetturali Stabiliti

### BusClient
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### Bootstrap path — pattern post-promozione
```python
_HERE      = Path(__file__).parent   # modules/<nome>/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root
for _p in (_REPO_ROOT,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
```

### ui_shell — widget registration contract
```python
bus.publish("ui.widget.register", {
    "name":      "navbar",
    "z_order":   2,
    "dock":      "bottom",    # top|bottom|left|right|center|top-left|top-right|bottom-left|bottom-right
    "height":    60,
    # width, min_width, max_width, min_height, max_height, aspect_ratio — opzionali
})
# Risponde con: ui.widget.geometry {name, x, y, w, h}
```
