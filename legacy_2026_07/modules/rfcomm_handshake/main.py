"""
NemoHeadUnit-Wireless v2 — rfcomm_handshake module

Module contract:
  Name        : rfcomm_handshake
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                hostapd.ready   {ssid, key, bssid, interface,
                                 gateway_ip, security_mode, ap_type}
  Publishes   : system.module_ready            {name, priority}
                system.ready                   {name, priority}
                bluetooth_manager.rfcomm.connected     {device_address}
                rfcomm.handshake.started       {device_address}
                rfcomm.handshake.completed     {device_address, phone_ip}
                rfcomm.handshake.failed        {device_address, error}

Flow:
  1. Waits for hostapd.ready — stores WiFi credentials
  2. Registers the AA RFCOMM Profile1 service with BlueZ D-Bus
  3. Receives the accepted RFCOMM fd through Profile1.NewConnection
  4. Publishes bluetooth_manager.rfcomm.connected so hostapd_helper starts the AP
  5. Runs the 5-stage handshake via RfcommHandshake
  6. On success → publishes rfcomm.handshake.completed {phone_ip}
     which triggers tcp_server to start listening

Internal helpers (no ZMQ):
  dbus_rfcomm.py — BlueZ Profile1 registration and fd handoff
  packet.py    — packet encode/decode
  handshake.py — 5-stage handshake state machine
"""

import sys
import socket
import threading
from pathlib import Path
import time
from typing import Optional

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_REPO_ROOT      = _MODULES.parent
_PROTO_ROOT = _REPO_ROOT / "protos"

if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROTO_ROOT))

from shared.bus_client import BusClient                      # noqa: E402
from shared.logger import get_logger             # noqa: E402
from rfcomm_handshake.dbus_rfcomm import DbusRfcommListener  # noqa: E402
from rfcomm_handshake.handshake import RfcommHandshake       # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "rfcomm_handshake"
PRIORITY    = 1  # service level

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_credentials: Optional[dict] = None
_device_address: Optional[str] = None
_pending_sock: Optional[socket.socket] = None
_rfcomm_listener: Optional[DbusRfcommListener] = None
_handshake_running = False
_state_lock = threading.Lock()
_glib_loop = None

# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    global _rfcomm_listener

    if payload.get("priority") != PRIORITY:
        return

    log.info(f"system.start priority={PRIORITY} — starting RFCOMM D-Bus listener")

    _start_glib_mainloop()
    _rfcomm_listener = DbusRfcommListener(on_connected_cb=_on_rfcomm_connected)
    if not _rfcomm_listener.start():
        log.error("RFCOMM D-Bus listener failed to start")
        bus.publish("rfcomm.handshake.failed", {
            "device_address": "",
            "error": "RFCOMM D-Bus listener failed to start",
        })

    bus.subscribe("hostapd.ready",              on_hostapd_ready)

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — rfcomm_handshake online")


def on_system_stop(topic: str, payload: dict) -> None:
    global _rfcomm_listener
    log.info("system.stop received")
    _close_pending_sock()
    if _rfcomm_listener:
        _rfcomm_listener.stop()
        _rfcomm_listener = None
    _stop_glib_mainloop()
    bus.stop()


def _start_glib_mainloop() -> None:
    global _glib_loop
    if _glib_loop is not None:
        return
    try:
        from gi.repository import GLib
        _glib_loop = GLib.MainLoop()
        t = threading.Thread(target=_glib_loop.run, daemon=True, name="glib-dbus")
        t.start()
        log.info("GLib mainloop started (thread: glib-dbus)")
    except Exception as e:
        log.error(f"Failed to start GLib mainloop — RFCOMM callbacks will not work: {e}")


def _stop_glib_mainloop() -> None:
    global _glib_loop
    if _glib_loop and _glib_loop.is_running():
        _glib_loop.quit()
        log.info("GLib mainloop stopped")
    _glib_loop = None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _on_rfcomm_connected(sock: socket.socket, device_address: str) -> None:
    global _credentials, _device_address, _pending_sock
    with _state_lock:
        if _handshake_running:
            log.warning(f"Ignoring RFCOMM connection from {device_address}: handshake already running")
            try:
                sock.close()
            except Exception:
                pass
            return
        if _pending_sock and _pending_sock is not sock:
            _close_pending_sock_locked()
        _credentials = None
        _device_address = device_address
        _pending_sock = sock

    log.info(f"RFCOMM connected from {device_address} — waiting for hostapd.ready")
    bus.publish("bluetooth_manager.rfcomm.connected", {"device_address": device_address})
    _try_start_handshake()


def on_hostapd_ready(topic: str, payload: dict) -> None:
    global _credentials
    _credentials = payload
    log.info(f"hostapd.ready received: ssid={payload.get('ssid')} — ready for handshake")
    _try_start_handshake()


# ---------------------------------------------------------------------------
# Handshake trigger
# ---------------------------------------------------------------------------

def _try_start_handshake() -> None:
    global _handshake_running
    with _state_lock:
        if _handshake_running or not _device_address or not _credentials or not _pending_sock:
            return
        _handshake_running = True
        device_address = _device_address

    log.info(f"Both conditions met — starting handshake with {device_address}")
    t = threading.Thread(target=_run_handshake, daemon=True, name="rfcomm-handshake")
    t.start()


def _run_handshake() -> None:
    global _handshake_running
    with _state_lock:
        device_address = _device_address
        sock = _pending_sock
        credentials = dict(_credentials or {})

    bus.publish("rfcomm.handshake.started", {"device_address": device_address})

    if sock is None:
        bus.publish("rfcomm.handshake.failed", {
            "device_address": device_address,
            "error": "RFCOMM socket unavailable",
        })
        with _state_lock:
            _handshake_running = False
        return

    hs = RfcommHandshake(
        sock=sock,
        credentials=credentials,
        on_stage_cb=lambda stage: log.info(f"Handshake stage: {stage}"),
    )
    result = hs.run()

    if result.success:
        log.info(f"Handshake OK — phone_ip={result.phone_ip}")
        bus.publish("rfcomm.handshake.completed", {
            "device_address": device_address,
            "phone_ip":       result.phone_ip,
        })
    else:
        log.error(f"Handshake failed: {result.error}")
        bus.publish("rfcomm.handshake.failed", {
            "device_address": device_address,
            "error":          result.error,
        })

    with _state_lock:
        _close_pending_sock_locked()
        _handshake_running = False


def _close_pending_sock() -> None:
    with _state_lock:
        _close_pending_sock_locked()


def _close_pending_sock_locked() -> None:
    global _pending_sock
    if _pending_sock:
        try:
            _pending_sock.close()
        except Exception:
            pass
        _pending_sock = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart",        on_system_readytostart)
    bus.subscribe("system.start",               on_system_start)
    bus.subscribe("system.stop",                on_system_stop)

    log.info("Module started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
