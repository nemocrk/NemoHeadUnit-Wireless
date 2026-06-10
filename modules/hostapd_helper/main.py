"""
NemoHeadUnit-Wireless — hostapd_helper module

Module contract:
  Name        : hostapd_helper
  Priority    : 1  (service level)
  Subscribes  : system.readytostart
                system.start
                system.stop
                bluetooth_manager.rfcomm.connected   {device_address: str}
                config.response              (filtered by module=hostapd_helper)
                config.changed               (filtered by module=hostapd_helper)
  Publishes   : system.module_ready          {name, priority}
                system.ready                 {name, priority}
                hostapd.starting             {ssid, interface}
                hostapd.ready                {ssid, key, bssid, interface,
                                              gateway_ip, security_mode, ap_type}
                hostapd.failed               {error: str}
                hostapd.stopped              {}

Configuration keys (config/hostapd_helper.yaml):
  interface         str    default: wlan0
  ssid              str    default: AndroidAutoAP
  hw_mode           enum   default: a  (a=5GHz, g=2.4GHz)
  channel           int    default: 36  (min=1, max=196)
  ap_password       str    default: "" (empty = random per session, chosen by service)
  subnet            str    default: 10.0.0
  gateway_ip        str    default: 10.0.0.1
  dhcp_range_start  str    default: 10.0.0.10
  dhcp_range_end    str    default: 10.0.0.50
  country_code      str    default: IT
  monitor_timeout   int    default: 30  (min=5, max=120)

Flow:
  1. bluetooth_manager.rfcomm.connected  → call Start() on D-Bus ap_manager_service
  2. ap_manager_service emits APStarted signal → publish hostapd.ready
  3. rfcomm_handshake module reads hostapd.ready and proceeds
  4. On system.stop (or APFailed signal) → call Stop() on D-Bus ap_manager_service

Backend:
  All privileged operations (hostapd, dnsmasq, ip, rfkill, nmcli, systemctl)
  are executed by org.nemo.APManager D-Bus system service (runs as root).
  This module communicates with it over the system bus without any sudo.
"""

import sys
from pathlib import Path
import time
import threading

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent   # modules/hostapd_helper/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

import dbus                                                  # noqa: E402
import dbus.mainloop.glib                                    # noqa: E402
from gi.repository import GLib                               # noqa: E402

from shared.bus_client import BusClient                      # noqa: E402
from shared.logger import get_logger                         # noqa: E402
from shared.config_client import ConfigClient                # noqa: E402
from shared.config_schema import field_enum, field_int, field_string  # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "hostapd_helper"
PRIORITY    = 1

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# D-Bus service constants
# ---------------------------------------------------------------------------

_DBUS_BUS_NAME    = "org.nemo.APManager"
_DBUS_OBJECT_PATH = "/org/nemo/APManager"
_DBUS_INTERFACE   = "org.nemo.APManager"

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "interface":        field_string(default="wlan0"),
    "ssid":             field_string(default="AndroidAutoAP"),
    "hw_mode":          field_enum(default="a", choices=["a", "g"]),
    "channel":          field_int(default=36, min=1, max=196),
    "ap_password":      field_string(default=""),
    "subnet":           field_string(default="10.0.0"),
    "gateway_ip":       field_string(default="10.0.0.1"),
    "dhcp_range_start": field_string(default="10.0.0.10"),
    "dhcp_range_end":   field_string(default="10.0.0.50"),
    "country_code":     field_string(default="IT"),
    "monitor_timeout":  field_int(default=30, min=5, max=120),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_ap_ready_params: dict | None = None
_glib_loop: GLib.MainLoop | None = None
_glib_thread: threading.Thread | None = None

# ---------------------------------------------------------------------------
# D-Bus AP client
# ---------------------------------------------------------------------------

class _DBusAPClient:
    """
    Thin wrapper around the org.nemo.APManager D-Bus service.
    """

    def __init__(self, system_bus: dbus.SystemBus):
        self._bus = system_bus

    def _proxy(self) -> dbus.Interface:
        obj = self._bus.get_object(
            _DBUS_BUS_NAME, _DBUS_OBJECT_PATH, introspect=False
        )
        return dbus.Interface(obj, _DBUS_INTERFACE)

    def start(self, config: dict) -> tuple[bool, str]:
        dbus_config = dbus.Dictionary(
            {
                k: dbus.String(str(v)) if not isinstance(v, int) else dbus.Int32(v)
                for k, v in config.items()
            },
            signature="sv",
        )
        return self._proxy().Start(dbus_config, dbus_interface=_DBUS_INTERFACE,
                                   signature="a{sv}")

    def stop(self) -> tuple[bool, str]:
        return self._proxy().Stop(dbus_interface=_DBUS_INTERFACE, signature="")

    def status(self) -> dict:
        state, ssid, bssid, gateway_ip, key, dhcp_clients = self._proxy().Status(
            dbus_interface=_DBUS_INTERFACE, signature=""
        )
        return {
            "state":        str(state),
            "ssid":         str(ssid),
            "bssid":        str(bssid),
            "gateway_ip":   str(gateway_ip),
            "key":          str(key),
            "dhcp_clients": int(dhcp_clients),
        }

    def is_running(self) -> bool:
        try:
            return self.status()["state"] == "running"
        except dbus.DBusException:
            return False


_dbus_system_bus: dbus.SystemBus | None = None
_ap_client: _DBusAPClient | None = None


def _ensure_dbus() -> _DBusAPClient:
    global _dbus_system_bus, _ap_client, _glib_loop, _glib_thread
    if _ap_client is not None:
        return _ap_client
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    _dbus_system_bus = dbus.SystemBus()
    _ap_client = _DBusAPClient(_dbus_system_bus)
    _glib_loop = GLib.MainLoop()
    _glib_thread = threading.Thread(target=_glib_loop.run, name="glib-dbus-loop", daemon=True)
    _glib_thread.start()
    log.debug("GLib main loop started for D-Bus signals")
    return _ap_client


# ---------------------------------------------------------------------------
# D-Bus signal handlers
# ---------------------------------------------------------------------------

def _on_dbus_ap_started(params: dict) -> None:
    global _ap_ready_params
    log.info(f"APStarted signal received: {dict(params)}")
    try:
        status = _ap_client.status()
        full_params = {
            "ssid":          status["ssid"],
            "key":           status["key"],
            "bssid":         status["bssid"],
            "interface":     str(params.get("interface", _config["interface"])),
            "gateway_ip":    status["gateway_ip"],
            "security_mode": 8,
            "ap_type":       1,
        }
        _ap_ready_params = full_params
        log.info(f"AP ready: ssid={full_params['ssid']} bssid={full_params['bssid']}")
        bus.publish("hostapd.ready", full_params)
    except dbus.DBusException as e:
        log.error(f"Could not retrieve AP status after APStarted: {e}")
        bus.publish("hostapd.failed", {"error": str(e)})


def _on_dbus_ap_stopped() -> None:
    global _ap_ready_params
    log.info("APStopped signal received")
    _ap_ready_params = None
    bus.publish("hostapd.stopped", {})


def _on_dbus_ap_failed(reason: str) -> None:
    global _ap_ready_params
    log.error(f"APFailed signal received: {reason}")
    _ap_ready_params = None
    bus.publish("hostapd.failed", {"error": str(reason)})


def _subscribe_dbus_signals() -> None:
    _dbus_system_bus.add_signal_receiver(
        _on_dbus_ap_started,
        signal_name="APStarted",
        dbus_interface=_DBUS_INTERFACE,
        bus_name=_DBUS_BUS_NAME,
        path=_DBUS_OBJECT_PATH,
    )
    _dbus_system_bus.add_signal_receiver(
        _on_dbus_ap_stopped,
        signal_name="APStopped",
        dbus_interface=_DBUS_INTERFACE,
        bus_name=_DBUS_BUS_NAME,
        path=_DBUS_OBJECT_PATH,
    )
    _dbus_system_bus.add_signal_receiver(
        _on_dbus_ap_failed,
        signal_name="APFailed",
        dbus_interface=_DBUS_INTERFACE,
        bus_name=_DBUS_BUS_NAME,
        path=_DBUS_OBJECT_PATH,
    )
    log.debug("D-Bus signal receivers registered")


# ---------------------------------------------------------------------------
# ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        log.info("No persisted config found — defaults seeded by config_manager.")
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged
    log.info(f"Config loaded: {_config}")

    # Initialise D-Bus connection and subscribe to AP signals
    _ensure_dbus()
    _subscribe_dbus_signals()
    log.info("D-Bus connection to ap_manager_service established")

    bus.subscribe("bluetooth_manager.rfcomm.connected", on_rfcomm_connected)

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — hostapd_helper online")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    if isinstance(value, (dict, list)):
        log.warning(f"config.changed: structural value for '{key}' rejected")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")


def _build_dbus_config() -> dict:
    return {
        "interface":        str(_config["interface"]),
        "ssid":             str(_config["ssid"]),
        "key":              str(_config["ap_password"]),
        "hw_mode":          str(_config["hw_mode"]),
        "channel":          int(_config["channel"]),
        "subnet":           str(_config["subnet"]),
        "gateway_ip":       str(_config["gateway_ip"]),
        "dhcp_range_start": str(_config["dhcp_range_start"]),
        "dhcp_range_end":   str(_config["dhcp_range_end"]),
        "country_code":     str(_config["country_code"]),
    }


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — initialising hostapd_helper")
    cfg.get(schema=_SCHEMA)


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — tearing down AP")
    _teardown()
    if _glib_loop and _glib_loop.is_running():
        _glib_loop.quit()
    bus.stop()


# ---------------------------------------------------------------------------
# bluetooth_manager.rfcomm.connected → AP lifecycle
# ---------------------------------------------------------------------------

def on_rfcomm_connected(topic: str, payload: dict) -> None:
    device_address = payload.get("device_address", "unknown")
    log.info(f"RFCOMM connected from {device_address} — starting AP via D-Bus service")

    client = _ensure_dbus()

    if client.is_running():
        params = _ap_ready_params
        if params:
            log.info(f"AP already running for RFCOMM reconnect from {device_address} — reusing it")
            bus.publish("hostapd.ready", params)
        else:
            try:
                status = client.status()
                log.info(f"AP already running, fetched status: {status}")
                bus.publish("hostapd.ready", {
                    "ssid":          status["ssid"],
                    "key":           status["key"],
                    "bssid":         status["bssid"],
                    "interface":     str(_config["interface"]),
                    "gateway_ip":    status["gateway_ip"],
                    "security_mode": 8,
                    "ap_type":       1,
                })
            except dbus.DBusException as e:
                log.error(f"Could not fetch AP status on reconnect: {e}")
                bus.publish("hostapd.failed", {"error": str(e)})
        return

    ap_dbus_config = _build_dbus_config()
    bus.publish("hostapd.starting", {"ssid": ap_dbus_config["ssid"], "interface": ap_dbus_config["interface"]})

    try:
        client.start(ap_dbus_config)
        log.info("Start() called on ap_manager_service — waiting for APStarted signal")
    except dbus.DBusException as e:
        error_name = e.get_dbus_name()
        error_msg  = str(e)
        if error_name == "org.nemo.APManager.Error.AlreadyRunning":
            log.warning("ap_manager_service reports AP already running — fetching status")
            on_rfcomm_connected(topic, payload)
        else:
            log.error(f"ap_manager_service Start() failed [{error_name}]: {error_msg}")
            bus.publish("hostapd.failed", {"error": error_msg})


# ---------------------------------------------------------------------------
# Teardown helper
# ---------------------------------------------------------------------------

def _teardown() -> None:
    global _ap_ready_params
    log.info("Tearing down AP via D-Bus service")
    _ap_ready_params = None
    if _ap_client is None:
        return
    try:
        if _ap_client.is_running():
            _ap_client.stop()
            log.info("Stop() called on ap_manager_service")
        else:
            log.info("AP was not running — nothing to stop")
    except dbus.DBusException as e:
        log.warning(f"Stop() on ap_manager_service failed: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    bus.subscribe("system.readytostart",                  on_system_readytostart)
    bus.subscribe("system.start",                         on_system_start)
    bus.subscribe("system.stop",                          on_system_stop)

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
