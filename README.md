# NemoHeadUnit-Wireless

![Coverage](docs/badges/coverage.svg)

Stack software per head unit Android Auto Wireless, scritto in Python 3.14.
Implementa il protocollo OAA (Open Android Auto) su trasporto Wi-Fi/TCP, con gestione canali audio, video, input, sensori e Bluetooth.

---

## Architettura

```
.
├── modules/                   # Moduli principali (channel_manager, tcp_server, audio_manager, ...)
├── modules/channel_modules/   # Canali OAA (audio, video, input, sensor, bluetooth, wifi, av_input)
├── modules/oaa_control_channel/ # Handshake, service discovery, serializer
├── shared/                    # Utility condivise (bus_client, logger, proto_utils, config_client, ...)
├── services/                  # Servizi di sistema (ap_manager_service, ...)
└── tests/                     # Suite di test (unit, integration, e2e, fuzz, performance)
```

Riferimento architetturale completo: [`docs/project-vision.md`](docs/project-vision.md)

---

## Test Suite

La CI è **solo on-demand** — nessun trigger automatico su push o PR.

### Avviare la CI

1. Vai su **Actions → Test Suite → Run workflow**
2. Scegli la suite dal dropdown:

| Suite | Descrizione | Blocca merge |
|---|---|---|
| `unit-integration` | Test unit + integration, coverage ≥ 80% | ✅ |
| `e2e-smoke` | Smoke E2E (BT connect → handshake → canali) | ✅ |
| `fuzz` | Fuzz test Hypothesis (wire format, proto, bus) | no |
| `performance` | Benchmark latenza/throughput/memoria | no |
| `e2e-full` | Sessione AA completa + recovery | no |
| `all` | Tutte e 5 le suite in parallelo | — |

### Coverage badge

Il badge `coverage.svg` in cima a questo file viene aggiornato automaticamente
ad ogni esecuzione di `unit-integration` o `all` e committato in `docs/badges/`.

### Eseguire i test localmente

```bash
# Installa dipendenze
pip install -e ".[test]"

# Unit + integration (gate 80%)
pytest -m "unit or integration" --cov=. --cov-report=term-missing --cov-fail-under=80

# Solo unit
pytest -m unit -v

# E2E smoke
pytest -m e2e_smoke -v

# Fuzz
pytest -m fuzz -v

# Performance
pytest -m performance -v

# Tutto
pytest -v --cov=.
```

---

## Documentazione

| File | Contenuto |
|---|---|
| [`docs/project-vision.md`](docs/project-vision.md) | Principi architetturali e design |
| [`docs/roadmap-current.md`](docs/roadmap-current.md) | Priorità e sequenza feature |
| [`docs/session_handoff.md`](docs/session_handoff.md) | Storico sessioni di sviluppo |
| [`docs/TEST_SUITE_ARCHITECTURE.md`](docs/TEST_SUITE_ARCHITECTURE.md) | Architettura test suite |
| [`docs/KNOWN_PRODUCTION_BUGS.md`](docs/KNOWN_PRODUCTION_BUGS.md) | Bug di produzione noti/risolti |
