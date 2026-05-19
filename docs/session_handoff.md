# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-19 — refactor/promote-v2-to-root, tutti gli step completati — pronto per PR

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: tutti gli step 1-8 completati. Prossima azione: aprire PR verso `main`.**

---

## 2026-05-19 — refactor/promote-v2-to-root (completato)

**Cosa cambiato:**

- Branch `refactor/promote-v2-to-root` creato da `main`
- **Step 3** — `docs/roadmap-current.md` e `docs/project-vision.md` aggiornati (rimossi tutti i riferimenti `v2/`)
- **Step 4** — Eliminati: `bus_broker.py` root v1, `app/` completo, `services/media_renderer.py`, `services/wireless_daemon.py`, `tests/` v1
- **Step 5** — Promossi in root: `main.py`, `bus_broker.py`, `shared/`, `modules/`, `config/`, `protos/`, `pyproject.toml`
- **Step 6** — Fix import paths e test paths:
  - `tests/conftest.py`: `_V2 = _HERE.parent` → `_ROOT = _HERE.parent`, tutti i commenti e path aggiornati — commit [`b13c0c8`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b13c0c84305591c0b296cad4610ddb73ae966935)
  - `pyproject.toml`: `testpaths = ["v2/tests"]` → `["tests"]`, `source = ["v2"]` → `["."]`, rimosso `"*/app/*"` da omit, aggiornata description — commit [`53d2793`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/53d27935fa65267de8d4e80dcfc1a32082691c07)
- **Step 7** — `README.md`: rimossi `v2/` dall'albero, `pip install -e v2/` → `pip install ".[test]"`, `--cov=v2` → `--cov=.`, rimosso `requirements-test.txt` deprecato — commit [`46da636`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/46da636bc2d9f458930a3b42357fd25b1adf44a7)
- **Step 8** — `.github/workflows/test-suite.yml`: in tutti e 5 i job rimossi `pip install "v2/[test]"` → `".[test]"`, `-c v2/pyproject.toml`, `v2/tests/` come argomento pytest esplicito — commit [`0d82c12`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/0d82c127ccd4182766c8fd7ab1d869ee9c4b76ff)

**Struttura root finale (confermata):**
```
/
├── main.py               ✅
├── bus_broker.py         ✅
├── pyproject.toml        ✅ (testpaths=["tests"], source=["."])
├── environment.yml       ✅
├── shared/               ✅
├── modules/              ✅
├── config/               ✅
├── protos/               ✅
├── services/             ✅ (solo ap_manager_service)
├── tests/                ✅ (unit/, integration/, e2e/, fuzz/, performance/)
├── docs/                 ✅
├── packaging/            ✅ (non modificato, compatibile)
├── scripts/              ✅ (non modificato, compatibile)
└── .github/workflows/    ✅ (test-suite.yml aggiornato)
```

**Residui "v2" benigni (non richiedono azione):**
- `version = "2.0.0"` in `pyproject.toml` — versione del progetto
- Entry storiche in `docs/session_handoff.md` (questo file)
- `docs/TEST_SUITE_ARCHITECTURE.md` — documento di riferimento, da aggiornare in follow-up non bloccante

**Piano completo (stato finale):**

| Step | Azione | Stato |
|---|---|---|
| 1 | Crea branch `refactor/promote-v2-to-root` | ✅ |
| 2 | Tag `v2-pre-promotion` su `main` | ✅ |
| 3 | Aggiorna `project-vision`, `roadmap-current`, `session-handoff` | ✅ |
| 4 | Elimina `app/`, `services/` v1, `tests/` v1, `bus_broker.py` root v1 | ✅ |
| 5 | Promuovi `v2/` → root | ✅ |
| 6 | Fix import paths (`conftest.py`) e test paths (`pyproject.toml`) | ✅ |
| 7 | Aggiorna `README.md` | ✅ |
| 8 | Aggiorna `.github/` CI workflow | ✅ |
| 9 | PR verso `main` | ⏳ prossimo |

**Status:** Pronto per PR 🚀

**Prossimi 3 passi:**
1. **Apri PR** `refactor/promote-v2-to-root` → `main` con descrizione del refactor
2. **Esegui CI** (`unit-integration`) sul branch per verifica finale prima del merge
3. **Follow-up post-merge** (non bloccante): aggiornare `docs/TEST_SUITE_ARCHITECTURE.md` con path senza `v2/`

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

- Bug #1 — `AudioModule._prebuffer_bytes` non resettato dopo flush
- Bug #2 — `ServiceDiscovery` `audio_type` perso per ch 5/6 — commit [`987ffb3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/987ffb3cf61281ebb559e25564f1f9490fd106a6)
- Bug #3 — `Logger.Popen` fuori dal `try` — già corretto, nessuna modifica
- Bug #4 — `Logger.exception()` con `sys.exc_info()`
- Bug #5 — `ChannelManager` sessione vuota in timeout
- `docs/KNOWN_PRODUCTION_BUGS.md` aggiornato — commit [`9ab40b7`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ab40b7778864bb4f7d87bfee51fe24fc7045262)

**Status:** Completato ✅

---

## Comandi Utili

```bash
# Coverage report
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
