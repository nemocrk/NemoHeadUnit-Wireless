# Session Handoff — NemoHeadUnit-Wireless v2

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-19 — refactor/promote-v2-to-root in corso

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root` aperto: step 3 (docs) completato, step 4 (eliminazione v1) da fare.** Prossimo: eliminare `app/`, `services/` (tranne `ap_manager_service`), `tests/` root, `bus_broker.py` root.

---

## 2026-05-19 — refactor/promote-v2-to-root (in corso)

**Cosa cambiato:**

- Branch `refactor/promote-v2-to-root` creato da `main` (commit `d118e85`)
- **`docs/roadmap-current.md`** — riscritto per documentare attività in corso (promozione v2 → root) — commit [`d12caa9`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/d12caa978dc883de23e115edd6424776f1553ae6)
- **`docs/project-vision.md`** — rimozione tutti i riferimenti a `v2/`; aggiornate sezioni 4.1, 4.3, 6.3, 11, 15; versione 3.3 → 3.4 — commit [`60881d3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/60881d33ec343c0ea25b00827cea16a0a851bea3)
- **`docs/session_handoff.md`** — questa entry

**Piano completo (stato attuale):**

| Step | Azione | Stato |
|---|---|---|
| 1 | Crea branch `refactor/promote-v2-to-root` | ✅ |
| 2 | Tag `v2-pre-promotion` su `main` | ⬜ da fare |
| 3 | Aggiorna `project-vision`, `roadmap-current`, `session-handoff` | ✅ |
| 4 | Elimina `app/`, `services/` (tranne `ap_manager_service`), `tests/` root, `bus_broker.py` root | ⬜ da fare |
| 5 | Promuovi `v2/` → root (chirurgico, file per file) | ⬜ da fare |
| 6 | Aggiorna `docs/` — riferimenti `v2/` → root | ⬜ da fare |
| 7 | Aggiorna `README.md` | ⬜ da fare |
| 8 | Verifica import paths e test paths | ⬜ da fare |

**Perché:** Promuovere l'implementazione v2 a struttura principale del repository, eliminando il codice legacy v1 dalla root.

**Status:** In Corso 🔄

**Prossimi 3 passi:**
1. Creare tag `v2-pre-promotion` su `main` (rollback point)
2. Eliminare chirurgicamente i file v1 (step 4)
3. Promuovere i file v2 → root (step 5)

---

## 2026-05-19 — ap_manager_service: join-network mode

**Cosa cambiato:**

- **`services/ap_manager_service/ap_manager_service.py`** — commit [`23ee37d`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/23ee37d7074e11d16bdbd5005455b5908f65e89a)
  - Aggiunti 3 helper puri: `_detect_existing_wifi()`, `_get_iface_ip()`, `_get_wifi_psk()`
  - `_APRunner` esteso con campo `_mode` (`"ap"` | `"join"` | `None`)
  - `start()` ora sceglie automaticamente il percorso:
    - **join-network**: HU già connessa con PSK disponibile → nessun daemon, nessun cambio interfaccia
    - **ap-mode**: comportamento precedente invariato
  - `stop()` in modalità `"join"`: solo reset stato, zero teardown (no `ip flush`, no NM restart)
  - `is_running()` in modalità `"join"`: `True` finché `_cfg is not None`
  - `get_mode()` esposto per log e test
  - Fallback trasparente ad AP mode se: nessuna rete WiFi attiva, PSK non recuperabile, IP non assegnato, rete enterprise (802.1X)

- **`services/ap_manager_service/tests/test_ap_manager_service.py`** — commit [`c7ac71f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c7ac71f11bc742d4c156476dc31b2defbc6418ba)
  - 26 test case, copertura ≥ 80% del codice nuovo

**Status:** Completato ✅

---

## 2026-05-15 — Fix tutti i bug di produzione

**Cosa cambiato:**

- **Bug #1 — `AudioModule._prebuffer_bytes`** non resettato dopo flush — commit `dea274a`
- **Bug #2 — `ServiceDiscovery` `audio_type` perso per ch 5/6** — commit [`987ffb3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/987ffb3cf61281ebb559e25564f1f9490fd106a6)
- **Bug #3 — `Logger.Popen` fuori dal `try`** — già corretto, nessuna modifica
- **Bug #4 — `Logger.exception()` con `sys.exc_info()`** — commit `1f4f227`
- **Bug #5 — `ChannelManager` sessione vuota in timeout** — commit `fb5d2a3`
- **`docs/KNOWN_PRODUCTION_BUGS.md`** aggiornato — commit [`9ab40b7`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ab40b7778864bb4f7d87bfee51fe24fc7045262)

**Status:** Completato ✅

---

## Handoff precedenti (sommario)

| Data | Cosa | Status |
|---|---|---|
| 2026-05-19 | refactor/promote-v2-to-root — docs aggiornati | 🔄 in corso |
| 2026-05-19 | `ap_manager_service` join-network mode + 26 test | ✅ |
| 2026-05-15 | Fix 5 bug produzione da KNOWN_PRODUCTION_BUGS.md | ✅ |
| 2026-05-13 | Fase 5 §2/§3 (chiude test suite) | ✅ |
| 2026-05-13 | Fase 4 §5/§6 + Fase 5 §1 | ✅ |
| 2026-05-13 | Fase 4 §2/§3/§4 | ✅ |
| 2026-05-13 | Fase 3 Full + Fase 4 §1 | ✅ |
| 2026-05-13 | Fase 3 Smoke §2/§3 | ✅ |
| 2026-05-13 | Unit rfcomm_handshake + channel_manager | ✅ |
| 2026-05-13 | E2E Helpers | ✅ |

---

## Comandi Utili

```bash
# Coverage report (post-promozione)
pytest --cov=. --cov-report=html --cov-report=term-missing --ignore=services

# Fuzz (tutti)
pytest -m fuzz -v

# Performance
pytest -m performance -v

# Smoke CI
pytest -m e2e_smoke -v

# Unit + integration (blocca merge)
pytest -m "unit or integration" --cov=. --cov-fail-under=80

# Tutto
pytest -v

# Test ap_manager_service (no D-Bus richiesto)
python -m pytest services/ap_manager_service/tests/test_ap_manager_service.py -v
```

---

## Pattern Architetturali Stabiliti

### BusClient (commit ddd7142)
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### ap_manager_service — join-network mode (2026-05-19)
- `_detect_existing_wifi()` usa `nmcli -t -f active,ssid,bssid,device,security,type device wifi`
- Fallback automatico ad AP mode se: nessuna rete, enterprise (802.1X), no IP, no PSK in NM
- In join mode: **zero teardown** su `stop()` — non toccare l'interfaccia né NM
- Test: stub `dbus`/`gi`/`GLib` via `sys.modules` per eseguire senza D-Bus di sistema

### Performance — pattern consolidato (Fase 4)
```python
@pytest.mark.performance
class TestXxx:
    # Soglie via env: PERF_P50_MS, PERF_P95_MS, PERF_P99_MS
    # Output JSON: tests/reports/perf-{scenario}.json
    # Baseline regression: tests/reports/perf-baseline.json
```

### Fuzz — pattern consolidato (Fase 5)
```python
@pytest.mark.fuzz
class TestXxxFuzz:
    # Motore: hypothesis
    # @given(st.binary() | st.text() | st.integers() | ...)
    # @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    # Mai assert su valori specifici: assert su proprietà (no crash, no hang)
```
