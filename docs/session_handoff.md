# Session Handoff — NemoHeadUnit-Wireless

> **Scopo**: documento di continuità per sessioni AI successive.
> **Aggiornato**: 2026-05-19 14:30 — lista completa residui `v2/` rilevati con grep sul branch

---

## Stato Corrente in Una Frase

**Branch `refactor/promote-v2-to-root`: file critici fixati ✅. Rimane una lista precisa di file con `v2/` da aggiornare, suddivisi per priorità.**

---

## 2026-05-19 — Fix import `v2/` nei file critici

**Cosa cambiato:**

| File | Commit | Note |
|---|---|---|
| `shared/proto_utils.py` | [`e200d81`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e200d817f2e1791d4c78eda322186842d0771677) | `parents[2]`→`parents[1]`, path `protos/` |
| `shared/proto_explorer.py` | [`e200d81`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e200d817f2e1791d4c78eda322186842d0771677) | stesso commit |
| `modules/oaa_control_channel/handshake.py` | [`a14fa32`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a14fa3262f4ba4de3d1c27db09339de9db5cecd0) | depth 4→3, tutti `from protos...` |
| `modules/oaa_control_channel/service_discovery.py` | [`036f668`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/036f6689020197a731dab25abe5774ade6eb937f) | depth 4→3, ~20 import proto |
| `modules/oaa_control_channel/serializer.py` | [`c92269a`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c92269ac4dac79c2d9d8db3c662f28c3d90dcf22) | solo `main()` example |
| `modules/channel_manager/registry.py` | [`60c0a0f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/60c0a0f3c72ef0adaf851772ad5a296219a926e1) | depth 4→3, docstring |
| `scripts/run_channel_modules.py` | [`a2f699c`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/a2f699c565e4b252bbe6ab913d0ed1c309d90647) | rimosso `_V2`, fix `sdr_hex` |
| `scripts/compile_protos.sh` | [`e6a9765`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/e6a9765e95ff98bf440b493f9b817f289e26bf25) | `PROTO_OUT` → `protos/` |

**Status:** Completato ✅

---

## Residui `v2/` rilevati — lista completa (grep sul branch)

> Rilevati con: `grep -rn "v2/" --include="*.py|*.sh|*.yml|*.md" . | grep -v .git/ | grep -v ^./protos/ | grep -v ^./v2/protos/ | grep -v session_handoff | grep -v "v2\.0" | grep -v "version.*2\." | sort`

### 🔴 Priorità ALTA — bootstrap path runtime (causano ImportError a runtime)

Pattern da sostituire: `_V2 = _MODULES.parent` + `_PROTOS = _V2 / "protos"` o `_V2 = _MODULES.parent` nei `sys.path`.

| File | Righe | Fix |
|---|---|---|
| `modules/channel_modules/_template/main.py` | 92-96 | `_REPO_ROOT = _CHANNEL_MODS.parent.parent`, `_PROTOS = _REPO_ROOT / "protos"` |
| `modules/channel_modules/bluetooth/main.py` | 47-51 | idem |
| `modules/channel_modules/wifi/main.py` | 54-58 | idem |
| `modules/channel_modules/base_channel_module.py` | 86-88 | `_REPO_ROOT = _HERE.parent.parent`, `sys.path` senza `_V2` |
| `modules/channel_manager/launcher.py` | 26-28 | `_REPO_ROOT = _MODULES.parent`, rimuovere `_V2` |
| `modules/channel_manager/main.py` | 54-56 | idem |
| `modules/_template/main.py` | 83-85 | idem |
| `modules/audio_manager/main.py` | 62-64 | idem |
| `modules/bluetooth_manager/main.py` | 58-60 | idem |
| `modules/config_manager/main.py` | 68-70 | idem |
| `modules/hostapd_helper/main.py` | 51-53 | idem |

> Nota: `modules/channel_modules/audio/`, `video/`, `input/`, `sensor/`, `av_input/` non compaiono nell'output grep — probabilmente usano già il pattern corretto o non hanno `_V2`. Verificare.

### 🟡 Priorità MEDIA — commenti/docstring in file Python (non bloccano runtime)

| File | Righe | Contenuto |
|---|---|---|
| `modules/_template/main.py` | 5, 20-21, 66, 73-76 | Istruzioni `cp -r v2/modules/...`, `python v2/...` |
| `modules/channel_manager/launcher.py` | 5 | Docstring path `v2/modules/channel_modules/...` |
| `modules/channel_modules/base_channel_module.py` | 4, 11 | Docstring `v2/modules/channel_modules/` |
| `main.py` | 18 | Commento `v2/modules/` |
| `tests/conftest.py` | 38 | Commento storico |
| `tests/unit/modules/rfcomm_handshake/test_packet_unit.py` | 20 | Commento `# Ensure v2/ is on sys.path` |
| `tests/unit/oaa_control_channel/test_rfcomm_and_channel_manager.py` | 14 | Commento `cd v2/tests` |
| `tests/unit/oaa_control_channel/test_service_discovery.py` | 4 | Commento `v2/protos/oaa/` |
| `tests/e2e/helpers/frame_sequences.py` | 22 | URL GitHub con `/main/v2/modules/...` |
| `modules/bluetooth_manager/main.py` | 36 | Config key comment `v2/config/...` |
| `modules/hostapd_helper/main.py` | 21 | Config key comment `v2/config/...` |
| `modules/zmq_trace/main.py` | 8 | Docstring `v2/modules/zmq_trace/main.py` |

### 🟡 Priorità MEDIA — docs da aggiornare

| File | Note |
|---|---|
| `docs/KNOWN_PRODUCTION_BUGS.md` | Path `v2/modules/...` e `v2/shared/...` in 5 righe |
| `docs/roadmap-current.md` | Sezione con lista `v2/X → X` ancora presente (storica, ok tenerla) |
| `docs/TEST_SUITE_ARCHITECTURE.md` | Intero documento scritto per `v2/` — aggiornamento non bloccante |

### 🟠 Priorità BASSA — script infrastruttura (non usati in dev corrente)

| File | Righe | Note |
|---|---|---|
| `packaging/build_deb.sh` | 15, 154, 186 | Copia `v2/` nel deb, path `APP_OPT/v2/` |
| `packaging/nemo-headunit.sh` | 8 | `APP_MAIN="/opt/nemo-headunit/v2/main.py"` |
| `scripts/deploy_remote.sh` | 167-175 | Rsync `v2/` su remote |

---

## Prossimi 3 passi

1. **Fix 🔴 ALTA** — bootstrap path negli 11 file `modules/*/main.py` + `base_channel_module.py` + `launcher.py`
2. **Fix 🟡 MEDIA** — commenti/docstring + docs (non bloccanti, ma da fare prima della PR)
3. **Fix 🟠 BASSA** — `packaging/` e `scripts/deploy_remote.sh` (separati, post-merge)

---

## 2026-05-19 — refactor/promote-v2-to-root (step 1-8 completati)

**Cosa cambiato:**

- Branch `refactor/promote-v2-to-root` creato da `main`
- **Step 3** — `docs/roadmap-current.md` e `docs/project-vision.md` aggiornati
- **Step 4** — Eliminati: `bus_broker.py` root v1, `app/`, `services/` v1, `tests/` v1
- **Step 5** — Promossi in root: `main.py`, `bus_broker.py`, `shared/`, `modules/`, `config/`, `protos/`, `pyproject.toml`
- **Step 6** — `tests/conftest.py` + `pyproject.toml` — commit [`b13c0c8`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/b13c0c84305591c0b296cad4610ddb73ae966935), [`53d2793`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/53d27935fa65267de8d4e80dcfc1a32082691c07)
- **Step 7** — `README.md` — commit [`46da636`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/46da636bc2d9f458930a3b42357fd25b1adf44a7)
- **Step 8** — `.github/workflows/test-suite.yml` — commit [`0d82c12`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/0d82c127ccd4182766c8fd7ab1d869ee9c4b76ff)

**Status:** Completato ✅

---

## 2026-05-19 — ap_manager_service: join-network mode

- `services/ap_manager_service/ap_manager_service.py` — commit [`23ee37d`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/23ee37d7074e11d16bdbd5005455b5908f65e89a)
- `services/ap_manager_service/tests/test_ap_manager_service.py` — commit [`c7ac71f`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/c7ac71f11bc742d4c156476dc31b2defbc6418ba)

**Status:** Completato ✅

---

## 2026-05-15 — Fix bug di produzione

- Bug #2 — `audio_type` perso ch 5/6 — commit [`987ffb3`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/987ffb3cf61281ebb559e25564f1f9490fd106a6)
- `docs/KNOWN_PRODUCTION_BUGS.md` — commit [`9ab40b7`](https://github.com/nemocrk/NemoHeadUnit-Wireless/commit/9ab40b7778864bb4f7d87bfee51fe24fc7045262)

**Status:** Completato ✅

---

## Comandi Utili

```bash
# Verifica residui v2/ sul branch
git checkout refactor/promote-v2-to-root && \
grep -rn "v2/" \
  --include="*.py" --include="*.sh" --include="*.yml" --include="*.md" \
  . \
  | grep -v ".git/" | grep -v "^./protos/" | grep -v "^./v2/protos/" \
  | grep -v "docs/session_handoff.md" \
  | grep -v '"v2\.0' | grep -v 'version.*2\.' | sort

# Coverage report
pytest --cov=. --cov-report=html --cov-report=term-missing --ignore=services

# Unit + integration
pytest -m "unit or integration" --cov=. --cov-fail-under=80

# Test ap_manager_service
python -m pytest services/ap_manager_service/tests/test_ap_manager_service.py -v
```

---

## Pattern Architetturali Stabiliti

### BusClient
- `publish()` inietta `_trace: {src_module, topic, seq, ts_ns}` nel payload wire
- `publish()` ritorna `bool`; `BUS_HWM` = 5000
- `BusTracer` va **sempre mockato** nei test unit

### Bootstrap path — pattern post-promozione
```python
# Moduli in modules/<nome>/
_HERE      = Path(__file__).parent   # modules/<nome>/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root
for _p in (_REPO_ROOT, _REPO_ROOT / "protos"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Moduli in modules/channel_modules/<nome>/
_HERE         = Path(__file__).parent   # modules/channel_modules/<nome>/
_CHANNEL_MODS = _HERE.parent            # modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent    # modules/
_REPO_ROOT    = _MODULES.parent         # root
for _p in (_REPO_ROOT, _REPO_ROOT / "protos"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
```

### ap_manager_service — join-network mode
- `_detect_existing_wifi()` usa `nmcli -t -f active,ssid,bssid,device,security,type device wifi`
- Fallback ad AP mode se: nessuna rete, enterprise (802.1X), no IP, no PSK in NM
- In join mode: **zero teardown** su `stop()`
- Test: stub `dbus`/`gi`/`GLib` via `sys.modules`

### Performance — pattern consolidato
```python
@pytest.mark.performance
class TestXxx:
    # Soglie via env: PERF_P50_MS, PERF_P95_MS, PERF_P99_MS
    # Output JSON: tests/reports/perf-{scenario}.json
```

### Fuzz — pattern consolidato
```python
@pytest.mark.fuzz
class TestXxxFuzz:
    # Motore: hypothesis
    # @given(st.binary() | st.text() | st.integers() | ...)
    # @settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
```
