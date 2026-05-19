# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-19 15:00 — refactor/promote-v2-to-root, file critici fixati, channel_modules da completare

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: 8 file critici fixati. Rimangono 8 `main.py` in `modules/channel_modules/` con path `v2/` da fixare, poi PR.**

---

## 2026-05-19 — Fix import `v2/` nei file critici

**Cosa cambiato:**

Rimossi tutti i riferimenti `v2/protos` e `v2/modules` dai file core:

| File | Commit | Note |
|---|---|---|
| `shared/proto_utils.py` | [`e200d81`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e200d817f2e1791d4c78eda322186842d0771677) | `parents[2]`→`parents[1]`, path `protos/` |
| `shared/proto_explorer.py` | [`e200d81`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e200d817f2e1791d4c78eda322186842d0771677) | stesso commit |
| `modules/oaa_control_channel/handshake.py` | [`a14fa32`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a14fa3262f4ba4de3d1c27db09339de9db5cecd0) | depth 4→3, tutti `from protos...` |
| `modules/oaa_control_channel/service_discovery.py` | [`036f668`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/036f6689020197a731dab25abe5774ade6eb937f) | depth 4→3, ~20 import proto |
| `modules/oaa_control_channel/serializer.py` | [`c92269a`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c92269ac4dac79c2d9d8db3c662f28c3d90dcf22) | solo `main()` example |
| `modules/channel_manager/registry.py` | [`60c0a0f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/60c0a0f3c72ef0adaf851772ad5a296219a926e1) | depth 4→3, docstring |
| `scripts/run_channel_modules.py` | [`a2f699c`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a2f699c565e4b252bbe6ab913d0ed1c309d90647) | rimosso `_V2`, fix `sdr_hex = sdr_bytes.hex()` |
| `scripts/compile_protos.sh` | [`e6a9765`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e6a9765e95ff98bf440b493f9b817f289e26bf25) | `PROTO_OUT` → `protos/` |

**Residui rilevati (da fixare nella prossima sessione):**

Tutti i `main.py` in `modules/channel_modules/` hanno ancora il blocco bootstrap:
```python
_V2    = _MODULES.parent   # v2/
_PROTOS = _V2 / "protos"   # v2/protos/
```
Da sostituire con:
```python
_REPO_ROOT = _CHANNEL_MODS.parent.parent  # root
_PROTOS    = _REPO_ROOT / "protos"
```

File interessati (tutti in `modules/channel_modules/`):
- `_template/main.py`
- `audio/main.py`
- `video/main.py`
- `input/main.py`
- `sensor/main.py`
- `bluetooth/main.py`
- `wifi/main.py`
- `av_input/main.py`

> **Nota**: i file `v2/modules/channel_modules/*/main.py` risultano ancora presenti nel repo (dead code pre-promozione). Andrebbero eliminati con un commit separato.

**Why:**
Il refactor `promote-v2-to-root` ha mosso tutto da `v2/` a root. I path bootstrap nei file devono riflettere la nuova profondità albero.

**Status:** In Progress — critici ✅, channel_modules ⏳

**Prossimi 3 passi:**
1. Fixare i bootstrap path in tutti gli 8 `main.py` di `modules/channel_modules/`
2. Eliminare i vecchi `v2/modules/channel_modules/*/main.py` (dead code)
3. Aprire PR `refactor/promote-v2-to-root` → `main`

---

## 2026-05-19 — refactor/promote-v2-to-root (step 1-8 completati)

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

**Status:** Completato ✅

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
