# Roadmap Attuale — NemoHeadUnit-Wireless

> Documento di pianificazione attività corrente.
> **Ultimo aggiornamento: 2026-05-26**

---

## Attività in Corso

### refactor/promote-v2-to-root

**Obiettivo**: Promuovere il contenuto di `v2/` in root del progetto, eliminando il vecchio codice v1 e allineando tutta la documentazione.

**Branch**: `refactor/promote-v2-to-root` → PR verso `main`

**Piano di esecuzione:**

| Step | Azione | Stato |
|---|---|---|
| 1 | Crea branch `refactor/promote-v2-to-root` | ✅ |
| 2 | Tag di sicurezza `v2-pre-promotion` su `main` | ⬜ |
| 3 | Aggiorna `project-vision.md`, `roadmap-current.md`, `session-handoff.md` | 🔄 in corso |
| 4 | Elimina vecchio codice v1: `app/`, `services/` (tranne `ap_manager_service`), `tests/` root, `bus_broker.py` root | ⬜ |
| 5 | Promuovi `v2/` → root (chirurgico, file per file) | ⬜ |
| 6 | Aggiorna `docs/` — tutti i riferimenti `v2/` → root | ⬜ |
| 7 | Aggiorna `README.md` | ⬜ |
| 8 | Verifica import paths e test paths | ⬜ |

---

### feature/ui-module — Implementazione UI PyQt6

**Obiettivo**: Realizzare il layer UI completo secondo l'architettura e il design system definiti.

**Documenti di riferimento**:
- [`docs/UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) — struttura moduli, screen stack, bus topic contract
- [`docs/UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md) — token colore, tipografia, componenti, motion, note PyQt6

**Piano di esecuzione:**

| Step | Azione | Stato |
|---|---|---|
| 1 | Definire architettura UI — `docs/UI_ARCHITECTURE.md` | ✅ |
| 2 | Definire design system — `docs/UI_DESIGN_SYSTEM.md` | ✅ |
| 3 | Implementare `modules/ui_shell/` — layout engine + `input_trap` | ✅ |
| 4 | Implementare `modules/navbar_ui/` — primo widget concreto (proof-of-concept) | ✅ |
| 5 | Implementare `modules/floating_menu_ui/` — arc-shaped on_request launcher | ✅ |
| 6 | Implementare `modules/video_ui/` — `QtBusBridge` + rendering H.264 (`VideoScreen`) | ⬜ |
| 7 | Implementare `modules/bt_ui/` — pannello floating Bluetooth | ⬜ |
| 8 | Implementare `modules/config_ui/` — pannello impostazioni | ⬜ |
| 9 | Test di integrazione UI — copertura ≥80% per ogni modulo UI | ⬜ |

**Widget priority convention:**

| Priority | Moduli | Motivo |
|---|---|---|
| 0 | `config_manager` | Prima di tutto: config disponibile per tutti |
| 1 | `bluetooth_manager`, `tcp_server`, `audio_manager` | Servizi di sistema |
| 2 | `ui_shell` | Layout engine + input_trap; deve essere operativo e aver pubblicato `ui.shell.ready` prima dei widget |
| 3 | `floating_menu_ui` | Deve registrarsi dopo `ui_shell` ma prima dei widget `on_request` (priority 4) per poterli scoprire all'avvio |
| 4 | `navbar_ui`, `video_ui`, `bt_ui`, `config_ui` | Widget UI: priority 4 garantisce che `ui_shell` e `floating_menu_ui` abbiano già completato `system.ready` |

**Design tokens chiave** (estratto da `UI_DESIGN_SYSTEM.md`):

| Token | Valore | Ruolo |
|---|---|---|
| `--color-bg` | `#141414` | Sfondo principale |
| `--color-surface` | `#1c1c1c` | Background widget |
| `--color-text` | `#f0ece4` | Testo primario warm-white |
| `--color-accent` | `#c8b89a` | Icone attive, clock |
| `--font-display` | DM Sans 300 | Clock, titoli panel |
| Navbar height | 60px | Fisso |
| Touch target min | 44×44px | Obbligatorio |
| Arc radius base | 120px | floating_menu_ui |
| Arc icon size | 52px | floating_menu_ui |

---

## Contesto

La test suite v2 è **completa** (57 file, ~3120 test, Fasi 0–5).
Il codice v2 è pronto per essere la struttura principale del repository.

### File da eliminare (v1 legacy)
- `bus_broker.py` (root)
- `app/` (intero albero)
- `services/` — tranne `ap_manager_service` che va conservato
- `tests/` (root, v1)

### File da promuovere (v2 → root)
- `v2/main.py` → `main.py`
- `v2/bus_broker.py` → `bus_broker.py`
- `v2/pyproject.toml` → `pyproject.toml`
- `v2/config/` → `config/`
- `v2/shared/` → `shared/`
- `v2/protos/` → `protos/`
- `v2/modules/` → `modules/`
- `v2/tests/` → `tests/`

---

## Storico Roadmap Precedente

> La roadmap test suite (Fasi 0–5) è stata completata il 2026-05-13.
> Vedi `docs/session_handoff-old.md` per lo storico dettagliato delle sessioni precedenti.

---

*Roadmap Version: 5.2*
*Aggiornato: 2026-05-26*
