# Roadmap Attuale — NemoHeadUnit-Wireless

> Documento di pianificazione attività corrente.
> **Ultimo aggiornamento: 2026-05-19**

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

*Roadmap Version: 4.0*
*Aggiornato: 2026-05-19*
