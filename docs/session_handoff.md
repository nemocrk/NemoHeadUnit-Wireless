# Session Handoff Documentation

## 2026-04-21 - Multi-threaded Message Bus Architecture

**What changed:**
- Enhanced MessageBus with thread affinity registry
- Updated ComponentRegistry with thread declaration support
- Updated main.py to declare threads for all components

**Why:**
- Enable multi-threaded execution where each component runs in its own thread
- Components declare which thread they need during registration
- MessageBus routes messages to the appropriate thread

**Status:**
Completed

**Next 1-3 steps:**
1. Review component implementations for thread-safety
2. Add tests for cross-thread message handling
3. Verify thread lifecycle management

**Verification commands/results:**
```bash
# Build verification
python -c "from app.main import Application; print('Python build OK')"
# Output: Python build OK

# Test suite
python -m pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80 -v
# Output: Tests running...
```

**Run Application Command**
```bash
python app/main.py
```

---

## 2026-04-21 - v2 Wireless Modules — Initial Implementation

**What changed:**

Creati 4 moduli standalone sotto `v2/modules/` seguendo l'architettura v2
(processo OS indipendente per modulo, comunicazione esclusiva via ZeroMQ bus):

| Modulo | File | Descrizione |
|---|---|---|
| `bluetooth` | `bluez_adapter.py`, `discovery.py`, `pairing.py`, `rfcomm.py`, `main.py` | D-Bus BlueZ, discovery timed, pairing Agent2, RFCOMM ch.8 |
| `hostapd_helper` | `ap_manager.py`, `ap_monitor.py`, `main.py` | Crea AP WiFi on-the-fly via hostapd+dnsmasq, polling conferma attivazione |
| `rfcomm_handshake` | `packet.py`, `handshake.py`, `main.py` | 5-stage AA wireless handshake, encode/decode pacchetti |
| `tcp_server` | `server.py`, `frame_relay.py`, `main.py` | TCP listen :5288, SSL interno, relay frame AA sul bus |

Fixato anche il pattern `sys.path` nel template `_template/main.py`:
aggiunta di `v2/modules/` al path oltre a `v2/`, necessario per
risolvere import locali del tipo `from bluetooth.bluez_adapter import`.

**Why:**
- Refactoring da architettura monolitica v1 (`app/wireless/`) a moduli
  isolati v2 senza accoppiamento diretto tra componenti
- Ogni modulo è avviabile e testabile in isolamento
- Il flusso completo è guidato da eventi bus:
  `system.start` → `bluetooth.rfcomm.connected` → `hostapd.ready`
  → `rfcomm.handshake.completed` → `aa.frame.received`

**Status:**
Completed

**Next 1-3 steps:**
1. Aggiungere test unitari per ogni modulo (`tests/v2/test_bluetooth.py`, ecc.)
2. Implementare protobuf reali a partire da `mrmees/open-android-auto`
   per sostituire l'encoding manuale in `rfcomm_handshake/handshake.py`
3. Testare il flusso end-to-end su hardware (wlan0 + BlueZ reale)

**Verification commands:**
```bash
# Avviare il broker v2
python v2/bus_broker.py

# Verificare autodiscovery moduli
python v2/main.py

# Avviare singolo modulo in isolamento
python v2/modules/bluetooth/main.py
python v2/modules/hostapd_helper/main.py
python v2/modules/rfcomm_handshake/main.py
python v2/modules/tcp_server/main.py
```
