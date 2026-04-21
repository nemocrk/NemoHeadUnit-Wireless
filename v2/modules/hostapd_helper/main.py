"""
NemoHeadUnit-Wireless v2 — hostapd_helper module

Module contract:
  Name        : hostapd_helper
  Subscribes  : system.stop
                bluetooth.rfcomm.connected   {device_address: str}
                config.response              (filtered by module=hostapd_helper)
                config.changed               (filtered by module=hostapd_helper)
  Publishes   : hostapd.starting             {ssid, interface}
                hostapd.ready                {ssid, key, bssid, interface,
                                              gateway_ip, security_mode, ap_type}
                hostapd.failed               {error: str}
                hostapd.stopped              {}
                config.get                   (on startup, to load persisted config)
                config.set                   (when writing defaults for the first time)

Configuration keys (v2/config/hostapd_helper.yaml):
  interface         str    default: wlan0
  ssid              str    default: AndroidAutoAP
  channel           int    default: 6
  ap_password       str    default: "" (empty = random per session)
  subnet            str    default: 192.168.50
  gateway_ip        str    default: 192.168.50.1
  dhcp_range_start  str    default: 192.168.50.10
  dhcp_range_end    str    default: 192.168.50.50
  monitor_timeout   int    default: 30

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

_HERE    = Path(__file__).parent   # v2/modules/hostapd_helper/
_MODULES = _HERE.parent            # v2/modules/
_V2      = _MODULES.parent         # v2/

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient        # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402

from hostapd_helper.ap_manager import APManager, APConfig   # noqa: E402
from hostapd_helper.ap_monitor import APMonitor             # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "hostapd_helper"

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "interface":        "wlan0",
    "ssid":             "AndroidAutoAP",
    "channel":          6,
    "ap_password":      "",
    "subnet":           "192.168.50",
    "gateway_ip":       "192.168.50.1",
    "dhcp_range_start": "192.168.50.10",
    "dhcp_range_end":   "192.168.50.50",
    "monitor_timeout":  30,
}

_config: dict = dict(_DEFAULTS)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_ap_manager: APManager | None = None
_ap_monitor: APMonitor | None = None

# ---------------------------------------------------------------------------
# ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        # First boot — no YAML exists yet. Persist defaults so config_ui
        # (and future runs) can read them from config_manager.
        log.info("No persisted config found — writing defaults.")
        for key, value in _DEFAULTS.items():
            cfg.set(key, value)
        # _config stays as _DEFAULTS (already set)
        return

    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in config.items() if k in _DEFAULTS})
    _config = merged
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _DEFAULTS:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")
    # Note: AP config changes take effect on next bluetooth.rfcomm.connected


def _build_ap_config() -> APConfig:
    """Build an APConfig from the current live _config dict."""
    return APConfig(
        interface=        str(_config["interface"]),
        ssid=             str(_config["ssid"]),
        key=              str(_config["ap_password"]),
        channel=          int(_config["channel"]),
        subnet=           str(_config["subnet"]),
        gateway_ip=       str(_config["gateway_ip"]),
        dhcp_range_start= str(_config["dhcp_range_start"]),
        dhcp_range_end=   str(_config["dhcp_range_end"]),
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def on_rfcomm_connected(topic: str, payload: dict) -> None:
    global _ap_manager, _ap_monitor

    device_address = payload.get("device_address", "unknown")
    log.info(f"RFCOMM connected from {device_address} — starting AP")

    ap_cfg = _build_ap_config()
    _ap_manager = APManager(ap_cfg)

    bus.publish("hostapd.starting", {
        "ssid":      ap_cfg.ssid,
        "interface": ap_cfg.interface,
    })

    if not _ap_manager.start():
        _ap_manager = None
        bus.publish("hostapd.failed", {"error": "APManager.start() failed"})
        return

    _ap_monitor = APMonitor(
        ap_manager=_ap_manager,
        on_ready_cb=_on_ap_ready,
        on_failed_cb=_on_ap_failed,
        timeout=float(_config["monitor_timeout"]),
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
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("bluetooth.rfcomm.connected", on_rfcomm_connected)
    bus.subscribe("system.stop",               on_system_stop)

    # Request persisted config; _DEFAULTS are used until response arrives
    cfg.get()

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
