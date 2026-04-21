"""
NemoHeadUnit-Wireless v2 — hostapd_helper module

Module contract:
  Name        : hostapd_helper
  Subscribes  : system.stop
                bluetooth.rfcomm.connected   {device_address: str}
  Publishes   : hostapd.starting             {ssid, interface}
                hostapd.ready                {ssid, key, bssid, interface,
                                              gateway_ip, security_mode, ap_type}
                hostapd.failed               {error: str}
                hostapd.stopped              {}

Flow:
  1. bluetooth.rfcomm.connected  → start APManager (create AP)
  2. APMonitor polls until AP is confirmed active
  3. On confirmed → publish hostapd.ready with full params
  4. rfcomm_handshake module reads hostapd.ready and proceeds
  5. On system.stop (or AP failure) → APManager.stop()

Internal helpers (no ZMQ):
  ap_manager.py  — writes hostapd.conf + dnsmasq.conf, starts subprocesses
  ap_monitor.py  — polls interface until AP mode confirmed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from shared.bus_client import BusClient   # noqa: E402
from shared.logger import get_logger      # noqa: E402

from hostapd_helper.ap_manager import APManager, APConfig   # noqa: E402
from hostapd_helper.ap_monitor import APMonitor             # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "hostapd_helper"

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_ap_manager: APManager | None = None
_ap_monitor: APMonitor | None = None

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def on_rfcomm_connected(topic: str, payload: dict) -> None:
    """
    Triggered when the phone opens the RFCOMM channel.
    Starts the AP and begins monitoring.
    """
    global _ap_manager, _ap_monitor

    device_address = payload.get("device_address", "unknown")
    log.info(f"RFCOMM connected from {device_address} — starting AP")

    # Build config (defaults are fine; extend here for custom ssid/iface)
    cfg = APConfig()
    _ap_manager = APManager(cfg)

    bus.publish("hostapd.starting", {
        "ssid":      cfg.ssid,
        "interface": cfg.interface,
    })

    if not _ap_manager.start():
        _ap_manager = None
        bus.publish("hostapd.failed", {"error": "APManager.start() failed"})
        return

    _ap_monitor = APMonitor(
        ap_manager=_ap_manager,
        on_ready_cb=_on_ap_ready,
        on_failed_cb=_on_ap_failed,
    )
    _ap_monitor.start()


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — tearing down AP")
    _teardown()
    bus.stop()

# ---------------------------------------------------------------------------
# APMonitor callbacks → bus events
# ---------------------------------------------------------------------------

def _on_ap_ready(params: dict) -> None:
    """
    Called by APMonitor once the AP is confirmed active.
    Publishes hostapd.ready with all params needed by rfcomm_handshake.
    """
    log.info(f"AP ready: ssid={params.get('ssid')} bssid={params.get('bssid')}")
    bus.publish("hostapd.ready", params)


def _on_ap_failed(reason: str) -> None:
    log.error(f"AP failed: {reason}")
    _teardown()
    bus.publish("hostapd.failed", {"error": reason})

# ---------------------------------------------------------------------------
# Teardown helper
# ---------------------------------------------------------------------------

def _teardown() -> None:
    global _ap_manager, _ap_monitor
    if _ap_monitor:
        _ap_monitor.stop()
        _ap_monitor = None
    if _ap_manager:
        _ap_manager.stop()
        _ap_manager = None
    bus.publish("hostapd.stopped", {})

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("bluetooth.rfcomm.connected", on_rfcomm_connected)
    bus.subscribe("system.stop",               on_system_stop)

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
