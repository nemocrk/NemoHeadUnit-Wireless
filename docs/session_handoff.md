# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-19 — refactor/promote-v2-to-root, step 4+5 completati

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: step 3, 4, 5 completati. Prossimo: step 6 (verifica import paths + test paths), step 7 (README.md), step 8 (PR verso main).**

---

## 2026-05-19 — refactor/promote-v2-to-root

**Cosa cambiato:**

- Branch `refactor/promote-v2-to-root` creato da `main` (commit `d118e85`)
- **`docs/roadmap-current.md`** — riscritto per documentare attività in corso — commit [`d12caa9`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/d12caa978dc883de23e115edd6424776f1553ae6)
- **`docs/project-vision.md`** — rimossi tutti i riferimenti a `v2/`; aggiornate sezioni 4.1, 4.3, 6.3, 11, 15 — commit [`60881d3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/60881d33ec343c0ea25b00827cea16a0a851bea3)
- **Step 4 — Eliminazione v1**: `bus_broker.py` root, `app/` (completo), `services/media_renderer.py`, `services/wireless_daemon.py`, `services/__init__.py`, `tests/` root (v1: test_base_interface, test_logger, test_main, test_wireless) — commit [`ecf1554`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/ecf1554301d8892e9ccd53c728f0f8f2d70dd43f) → [`5a91679`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/5a916796ad323886c380fcb7bff9233a7110c226)
- **Step 5 — Promozione v2 → root**: `main.py`, `bus_broker.py`, `shared/`, `modules/`, `config/`, `protos/`, `pyproject.toml` promossi in root — commit [`b341ed7`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b341ed72f5740235036b08e6b5fa0754b6e3a55f)

**Struttura root attuale (confermata):**
```
/
├── main.py               ✅ v2
├── bus_broker.py         ✅ v2
├── pyproject.toml        ✅ v2
├── environment.yml       ✅ (da riconciliare con pyproject.toml)
├── shared/               ✅ v2
├── modules/              ✅ v2
├── config/               ✅ v2
├── protos/               ✅ v2
├── services/             ✅ solo ap_manager_service
├── tests/                ⚠️  da verificare path (ancora tests/v2/ o promosso?)
├── docs/                 ✅
├── packaging/            ⚠️  da verificare compatibilità v2
├── scripts/              ⚠️  da verificare compatibilità v2
├── third_party/          ⚠️  da verificare se ancora necessario
└── .github/              ⚠️  da verificare CI path
```

**Piano completo (stato attuale):**

| Step | Azione | Stato |
|---|---|---|
| 1 | Crea branch `refactor/promote-v2-to-root` | ✅ |
| 2 | Tag `v2-pre-promotion` su `main` | ✅ |
| 3 | Aggiorna `project-vision`, `roadmap-current`, `session-handoff` | ✅ |
| 4 | Elimina `app/`, `services/` v1, `tests/` v1, `bus_broker.py` root v1 | ✅ |
| 5 | Promuovi `v2/` → root | ✅ |
| 6 | Verifica import paths in `modules/` e test paths in `tests/` | ⏳ prossimo |
| 7 | Aggiorna `README.md` | ⏳ |
| 8 | Verifica `.github/` CI workflow (path references) | ⏳ |
| 9 | PR verso `main` | ⏳ |

**Status:** In Corso 🔄 — step 6 è il prossimo

**Prossimi 3 passi:**
1. **Step 6** — Analizzare import paths nei moduli e verificare `tests/` (struttura, conftest, pytest.ini)
2. **Step 7** — Aggiornare `README.md` (rimuovere riferimenti `v2/`, aggiornare quickstart)
3. **Step 8** — Verificare `.github/` CI workflow per path references → poi PR

---

## 2026-05-19 — ap_manager_service: join-network mode

**Cosa cambiato:**

- **`services/ap_manager_service/ap_manager_service.py`** — commit [`23ee37d`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/23ee37d7074e11d16bdbd5005455b5908f65e89a)
  - Aggiunti 3 helper puri: `_detect_existing_wifi()`, `_get_iface_ip()`, `_get_wifi_psk()`
  - `_APRunner` esteso con campo `_mode` (`"ap"` | `"join"` | `None`)
  - `start()` sceglie automaticamente join-network vs ap-mode
  - `stop()` in modalità `"join"`: solo reset stato, zero teardown
  - `get_mode()` esposto per log e test

- **`services/ap_manager_service/tests/test_ap_manager_service.py`** — commit [`c7ac71f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c7ac71f11bc742d4c156476dc31b2defbc6418ba)
  - 26 test case, copertura ≥ 80%

**Status:** Completato ✅

---

## 2026-05-15 — Fix tutti i bug di produzione

**Cosa cambiato:**

- Bug #1 — `AudioModule._prebuffer_bytes` non resettato dopo flush — commit `dea274a`
- Bug #2 — `ServiceDiscovery` `audio_type` perso per ch 5/6 — commit [`987ffb3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/987ffb3cf61281ebb559e25564f1f9490fd106a6)
- Bug #3 — `Logger.Popen` fuori dal `try` — già corretto, nessuna modifica
- Bug #4 — `Logger.exception()` con `sys.exc_info()` — commit `1f4f227`
- Bug #5 — `ChannelManager` sessione vuota in timeout — commit `fb5d2a3`
- `docs/KNOWN_PRODUCTION_BUGS.md` aggiornato — commit [`9ab40b7`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ab40b7778864bb4f7d87bfee51fe24fc7045262)

**Status:** Completato ✅

---

## Comandi Utili

```bash
# Coverage report (post-promozione)
pytest --cov=. --cov-report=html --cov-report=term-missing --ignore=services

# Unit + integration (blocca merge)
pytest -m "unit or integration" --cov=. --cov-fail-under=80

# Test ap_manager_service (no D-Bus richiesto)
python -m pytest services/ap_manager_service/tests/test_ap_manager_service.py -v

# Fuzz
pytest -m fuzz -v

# Performance
pytest -m performance -v

# Tutto
pytest -v
```

---

## Pattern Architetturali Stabiliti

### BusClient
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### ap_manager_service — join-network mode
- `_detect_existing_wifi()` usa `nmcli -t -f active,ssid,bssid,device,security,type device wifi`
- Fallback automatico ad AP mode se: nessuna rete, enterprise (802.1X), no IP, no PSK in NM
- In join mode: **zero teardown** su `stop()` — non toccare l'interfaccia né NM
- Test: stub `dbus`/`gi`/`GLib` via `sys.modules` per eseguire senza D-Bus di sistema

### Performance — pattern consolidato
```python
@pytest.mark.performance
class TestXxx:
    # Soglie via env: PERF_P50_MS, PERF_P95_MS, PERF_P99_MS
    # Output JSON: tests/reports/perf-{scenario}.json
    # Baseline regression: tests/reports/perf-baseline.json
```

### Fuzz — pattern consolidato
```python
@pytest.mark.fuzz
class TestXxxFuzz:
    # Motore: hypothesis
    # @given(st.binary() | st.text() | st.integers() | ...)
    # @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
    # Mai assert su valori specifici: assert su proprietà (no crash, no hang)
```
