#!/usr/bin/env python3
"""
ap_manager_service.py — D-Bus system service for WiFi AP lifecycle.

Runs as root (via systemd).  Exposes:
  Bus name   : org.nemo.APManager
  Object path: /org/nemo/APManager
  Interface  : org.nemo.APManager

Methods:
  Start(config: a{sv}) -> (success: b, error: s)
  Stop()               -> (success: b, error: s)
  Status()             -> (state: s, ssid: s, bssid: s,
                            gateway_ip: s, dhcp_clients: i)

Signals:
  APStarted(params: a{sv})   — ssid, bssid, interface, gateway_ip (NO key)
  APStopped()
  APFailed(reason: s)

Errors:
  org.nemo.APManager.Error.AlreadyRunning
  org.nemo.APManager.Error.NotRunning
  org.nemo.APManager.Error.StartFailed
  org.nemo.APManager.Error.InvalidConfig

Access control: callers must belong to group 'ap_manager' (enforced by PolicyKit).
"""

import os
import sys
import signal
import logging
import subprocess
import tempfile
import time
import secrets
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ap_manager_service] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ap_manager_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUS_NAME    = "org.nemo.APManager"
OBJECT_PATH = "/org/nemo/APManager"
INTERFACE   = "org.nemo.APManager"

POLKIT_ACTION_START  = "org.nemo.apmanager.start"
POLKIT_ACTION_STOP   = "org.nemo.apmanager.stop"
POLKIT_ACTION_STATUS = "org.nemo.apmanager.status"

WPA2_SECURITY_MODE = 8
AP_TYPE_DYNAMIC    = 1
DHCP_LEASE_TIME    = "12h"

DNSMASQ_LEASES_FILE = "/var/lib/misc/dnsmasq.leases"

# ---------------------------------------------------------------------------
# APConfig
# ---------------------------------------------------------------------------

@dataclass
class APConfig:
    interface:        str = "wlan0"
    ssid:             str = "AndroidAutoAP"
    key:              str = ""
    hw_mode:          str = "a"
    channel:          int = 36
    subnet:           str = "10.0.0"
    gateway_ip:       str = "10.0.0.1"
    dhcp_range_start: str = "10.0.0.10"
    dhcp_range_end:   str = "10.0.0.50"
    country_code:     str = "IT"


def _config_from_dbus_dict(d: dict) -> APConfig:
    """Build APConfig from a D-Bus a{sv} dict. Raises ValueError on bad values."""
    cfg = APConfig()
    str_fields = {"interface", "ssid", "key", "hw_mode", "subnet",
                  "gateway_ip", "dhcp_range_start", "dhcp_range_end", "country_code"}
    int_fields = {"channel"}
    for k, v in d.items():
        if k in str_fields:
            setattr(cfg, k, str(v))
        elif k in int_fields:
            setattr(cfg, k, int(v))
        else:
            log.warning(f"Unknown config key '{k}' — ignored")
    if cfg.hw_mode not in ("a", "g"):
        raise ValueError(f"hw_mode must be 'a' or 'g', got '{cfg.hw_mode}'")
    if not (1 <= cfg.channel <= 196):
        raise ValueError(f"channel must be 1-196, got {cfg.channel}")
    return cfg

# ---------------------------------------------------------------------------
# PolicyKit helper
# ---------------------------------------------------------------------------

def _polkit_check(sender: str, action_id: str, system_bus: dbus.SystemBus) -> None:
    """
    Ask PolicyKit whether `sender` is authorised for `action_id`.
    Raises dbus.DBusException with error name org.nemo.APManager.Error.NotAuthorized
    if the check fails or the caller is not in the ap_manager group.
    """
    try:
        # introspect=False: prevents dbus-python from calling Introspect() on
        # the polkitd proxy before CheckAuthorization(). polkitd (uid=981)
        # rejects Introspect calls from root (uid=0) with AccessDenied.
        polkit = system_bus.get_object(
            "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority",
            introspect=False,
        )
        authority = dbus.Interface(polkit, "org.freedesktop.PolicyKit1.Authority")

        subject = ("system-bus-name", {"name": dbus.String(sender)})
        # Must be an explicitly typed dbus.Dictionary — passing a plain Python {}
        # causes dbus-python to raise "Unable to guess signature from an empty dict".
        details = dbus.Dictionary({}, signature="sv")
        flags   = dbus.UInt32(0)
        cancel  = ""

        (is_auth, _is_challenge, _details) = authority.CheckAuthorization(
            subject, action_id, details, flags, cancel
        )
        if not is_auth:
            raise dbus.DBusException(
                f"Not authorised for action '{action_id}'",
                name="org.nemo.APManager.Error.NotAuthorized",
            )
    except dbus.DBusException:
        raise
    except Exception as e:
        log.warning(f"PolicyKit check failed unexpectedly: {e} — denying")
        raise dbus.DBusException(
            "PolicyKit unavailable",
            name="org.nemo.APManager.Error.NotAuthorized",
        )

# ---------------------------------------------------------------------------
# Low-level subprocess helpers (all run as root — no sudo needed)
# ---------------------------------------------------------------------------

def _run(args: list, check: bool = False, timeout: int = 10) -> subprocess.CompletedProcess:
    log.debug(f"run: {' '.join(str(a) for a in args)}")
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _write_temp(prefix: str, suffix: str, content: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    log.debug(f"Wrote config: {path}")
    return path


def _cleanup_file(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass


def _kill_proc(proc: Optional[subprocess.Popen], name: str) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.info(f"{name} terminated")


def _generate_key(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _get_mac(iface: str) -> str:
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            return f.read().strip().upper()
    except Exception:
        return "00:00:00:00:00:00"


def _count_dhcp_clients(leases_file: str = DNSMASQ_LEASES_FILE) -> int:
    try:
        with open(leases_file) as f:
            return sum(1 for line in f if line.strip())
    except FileNotFoundError:
        return 0
    except Exception as e:
        log.warning(f"Could not read DHCP leases: {e}")
        return 0

# ---------------------------------------------------------------------------
# AP lifecycle (runs as root — no sudo prefix)
# ---------------------------------------------------------------------------

class _APRunner:
    """Internal: manages hostapd + dnsmasq subprocesses."""

    def __init__(self):
        self._cfg: Optional[APConfig] = None
        self._bssid: str = ""
        self._hostapd_proc: Optional[subprocess.Popen] = None
        self._dnsmasq_proc: Optional[subprocess.Popen] = None
        self._hostapd_conf: Optional[str] = None
        self._dnsmasq_conf: Optional[str] = None

    # -- public ---------------------------------------------------------------

    def start(self, cfg: APConfig) -> None:
        """Start AP. Raises RuntimeError on failure."""
        if not cfg.key:
            cfg.key = _generate_key()
        self._cfg = cfg

        self._stop_conflicting_hostapd_service()
        self._cleanup_stale_daemons()
        self._bssid = _get_mac(cfg.interface)

        log.info(
            f"Starting AP: ssid={cfg.ssid} iface={cfg.interface} "
            f"bssid={self._bssid} channel={cfg.channel} gw={cfg.gateway_ip}"
        )

        self._release_interface()
        self._configure_interface()

        self._hostapd_conf = self._write_hostapd_conf()
        self._dnsmasq_conf = self._write_dnsmasq_conf()

        self._start_hostapd()
        self._start_dnsmasq()

    def stop(self) -> None:
        _kill_proc(self._hostapd_proc, "hostapd")
        _kill_proc(self._dnsmasq_proc, "dnsmasq")
        self._hostapd_proc = None
        self._dnsmasq_proc = None
        _cleanup_file(self._hostapd_conf)
        _cleanup_file(self._dnsmasq_conf)
        self._restore_interface()
        self._set_nm_managed(True)
        self._nm_reconnect()
        log.info("AP stopped")

    def is_running(self) -> bool:
        hp = self._hostapd_proc and self._hostapd_proc.poll() is None
        dp = self._dnsmasq_proc and self._dnsmasq_proc.poll() is None
        return bool(hp and dp)

    def get_params(self) -> dict:
        """Returns AP params WITHOUT key."""
        if not self._cfg:
            return {}
        return {
            "ssid":          self._cfg.ssid,
            "bssid":         self._bssid,
            "interface":     self._cfg.interface,
            "gateway_ip":    self._cfg.gateway_ip,
            "security_mode": WPA2_SECURITY_MODE,
            "ap_type":       AP_TYPE_DYNAMIC,
        }

    def get_key(self) -> str:
        return self._cfg.key if self._cfg else ""

    # -- interface helpers ----------------------------------------------------

    def _configure_interface(self) -> None:
        iface = self._cfg.interface
        gw    = self._cfg.gateway_ip
        cmds  = [
            ["rfkill", "unblock", "wifi"],
            ["ip", "link", "set", iface, "down"],
            ["ip", "addr", "flush", "dev", iface],
            ["iw", "dev", iface, "set", "type", "__ap"],
            ["ip", "link", "set", iface, "up"],
            ["ip", "addr", "add", f"{gw}/24", "dev", iface],
        ]
        for cmd in cmds:
            r = _run(cmd)
            time.sleep(0.5)
            if r.returncode != 0:
                log.warning(f"cmd {cmd[0]} returned {r.returncode}: {r.stderr.strip()}")

    def _restore_interface(self) -> None:
        if not self._cfg:
            return
        _run(["ip", "addr", "flush", "dev", self._cfg.interface])
        time.sleep(0.2)

    def _release_interface(self) -> None:
        iface = self._cfg.interface
        _run(["nmcli", "device", "disconnect", iface], timeout=5)
        time.sleep(0.5)
        self._set_nm_managed(False)
        time.sleep(0.5)

    def _set_nm_managed(self, managed: bool) -> None:
        if not self._cfg:
            return
        value = "yes" if managed else "no"
        _run(["nmcli", "device", "set", self._cfg.interface, "managed", value], timeout=5)

    def _nm_reconnect(self) -> None:
        time.sleep(0.5)
        _run(["systemctl", "restart", "NetworkManager"])

    # -- daemon helpers -------------------------------------------------------

    def _write_hostapd_conf(self) -> str:
        cfg = self._cfg
        content = (
            f"interface={cfg.interface}\n"
            f"driver=nl80211\n"
            f"country_code={cfg.country_code}\n"
            f"ieee80211d=1\n"
            f"ssid={cfg.ssid}\n"
            f"hw_mode={cfg.hw_mode}\n"
            f"channel={cfg.channel}\n"
            f"ieee80211n=1\n"
            f"wmm_enabled=1\n"
            f"macaddr_acl=0\n"
            f"auth_algs=1\n"
            f"ignore_broadcast_ssid=0\n"
            f"wpa=2\n"
            f"wpa_passphrase={cfg.key}\n"
            f"wpa_key_mgmt=WPA-PSK\n"
            f"rsn_pairwise=CCMP\n"
        )
        if cfg.hw_mode == "a":
            content += "ht_capab=[HT40+][SHORT-GI-20][SHORT-GI-40]\n"
        return _write_temp("hostapd_", ".conf", content)

    def _write_dnsmasq_conf(self) -> str:
        cfg = self._cfg
        content = (
            f"interface={cfg.interface}\n"
            f"dhcp-range={cfg.dhcp_range_start},{cfg.dhcp_range_end},{DHCP_LEASE_TIME}\n"
            f"dhcp-option=3,{cfg.gateway_ip}\n"
            f"dhcp-option=6,{cfg.gateway_ip}\n"
            f"server=8.8.8.8\n"
            f"log-queries\n"
            f"log-dhcp\n"
            f"listen-address={cfg.gateway_ip}\n"
            f"bind-interfaces\n"
        )
        return _write_temp("dnsmasq_", ".conf", content)

    def _start_hostapd(self) -> None:
        time.sleep(0.2)
        try:
            self._hostapd_proc = subprocess.Popen(
                ["hostapd", self._hostapd_conf],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            log.info(f"hostapd started (pid={self._hostapd_proc.pid})")
        except FileNotFoundError:
            raise RuntimeError("hostapd not found — install hostapd")
        time.sleep(0.3)
        if self._hostapd_proc.poll() is not None:
            _, stderr = self._hostapd_proc.communicate()
            raise RuntimeError(f"hostapd exited immediately: {stderr.strip()}")

    def _start_dnsmasq(self) -> None:
        try:
            self._dnsmasq_proc = subprocess.Popen(
                ["dnsmasq", f"--conf-file={self._dnsmasq_conf}", "--no-daemon"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            log.info(f"dnsmasq started (pid={self._dnsmasq_proc.pid})")
        except FileNotFoundError:
            raise RuntimeError("dnsmasq not found — install dnsmasq")
        time.sleep(0.3)
        if self._dnsmasq_proc.poll() is not None:
            _, stderr = self._dnsmasq_proc.communicate()
            raise RuntimeError(f"dnsmasq exited immediately: {stderr.strip()}")

    def _stop_conflicting_hostapd_service(self) -> None:
        r = _run(["systemctl", "is-active", "--quiet", "hostapd"])
        if r.returncode != 0:
            return
        log.warning("Stopping active hostapd.service")
        _run(["systemctl", "stop", "hostapd"], timeout=10)

    def _cleanup_stale_daemons(self) -> None:
        for name, pattern in [
            ("hostapd", "hostapd /tmp/hostapd_"),
            ("dnsmasq", "dnsmasq --conf-file=/tmp/dnsmasq_"),
        ]:
            found = _run(["pgrep", "-af", pattern])
            if not (found.stdout or "").strip():
                continue
            log.warning(f"Killing stale {name}")
            _run(["pkill", "-TERM", "-f", pattern])
            time.sleep(0.2)
            _run(["pkill", "-KILL", "-f", pattern])

# ---------------------------------------------------------------------------
# D-Bus service object
# ---------------------------------------------------------------------------

class APManagerService(dbus.service.Object):

    def __init__(self, system_bus: dbus.SystemBus):
        bus_name = dbus.service.BusName(BUS_NAME, bus=system_bus)
        super().__init__(bus_name, OBJECT_PATH)
        self._system_bus = system_bus
        self._runner = _APRunner()
        log.info(f"Service ready on {BUS_NAME}{OBJECT_PATH}")

    # -- Methods --------------------------------------------------------------

    @dbus.service.method(
        dbus_interface=INTERFACE,
        in_signature="a{sv}",
        out_signature="bs",
        sender_keyword="sender",
    )
    def Start(self, config: dict, sender: str = None) -> tuple:
        _polkit_check(sender, POLKIT_ACTION_START, self._system_bus)

        if self._runner.is_running():
            raise dbus.DBusException(
                "AP is already running",
                name="org.nemo.APManager.Error.AlreadyRunning",
            )

        try:
            ap_cfg = _config_from_dbus_dict(config)
        except ValueError as e:
            raise dbus.DBusException(str(e), name="org.nemo.APManager.Error.InvalidConfig")

        try:
            self._runner.start(ap_cfg)
        except RuntimeError as e:
            error_msg = str(e)
            log.error(f"Start failed: {error_msg}")
            try:
                self._runner.stop()
            except Exception:
                pass
            self.APFailed(error_msg)
            raise dbus.DBusException(error_msg, name="org.nemo.APManager.Error.StartFailed")

        params = self._runner.get_params()
        log.info(f"AP started: {params}")
        self.APStarted({k: dbus.String(str(v)) for k, v in params.items()})
        return (True, "")

    @dbus.service.method(
        dbus_interface=INTERFACE,
        in_signature="",
        out_signature="bs",
        sender_keyword="sender",
    )
    def Stop(self, sender: str = None) -> tuple:
        _polkit_check(sender, POLKIT_ACTION_STOP, self._system_bus)

        if not self._runner.is_running():
            raise dbus.DBusException(
                "AP is not running",
                name="org.nemo.APManager.Error.NotRunning",
            )

        self._runner.stop()
        self.APStopped()
        return (True, "")

    @dbus.service.method(
        dbus_interface=INTERFACE,
        in_signature="",
        out_signature="sssssi",
        sender_keyword="sender",
    )
    def Status(self, sender: str = None) -> tuple:
        _polkit_check(sender, POLKIT_ACTION_STATUS, self._system_bus)

        running = self._runner.is_running()
        state   = "running" if running else "stopped"

        if not running:
            return (state, "", "", "", "", 0)

        params  = self._runner.get_params()
        key     = self._runner.get_key()
        clients = _count_dhcp_clients()

        return (
            state,
            params.get("ssid", ""),
            params.get("bssid", ""),
            params.get("gateway_ip", ""),
            key,
            clients,
        )

    # -- Signals --------------------------------------------------------------

    @dbus.service.signal(dbus_interface=INTERFACE, signature="a{sv}")
    def APStarted(self, params: dict):
        """Emitted when the AP starts. Contains ssid, bssid, interface, gateway_ip. No key."""
        pass

    @dbus.service.signal(dbus_interface=INTERFACE, signature="")
    def APStopped(self):
        """Emitted when the AP stops cleanly."""
        pass

    @dbus.service.signal(dbus_interface=INTERFACE, signature="s")
    def APFailed(self, reason: str):
        """Emitted when start or runtime failure occurs."""
        pass

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    
    try:
        system_bus = dbus.SystemBus()
    except dbus.exceptions.DBusException as e:
        log.error(f"Failed to connect to system D-Bus: {e}")
        log.error("Ensure D-Bus daemon is running and accessible.")
        log.error(f"DBUS_SYSTEM_BUS_ADDRESS: {os.environ.get('DBUS_SYSTEM_BUS_ADDRESS', '(not set)')}")
        raise SystemExit(1)
    
    service    = APManagerService(system_bus)  # noqa: F841

    loop = GLib.MainLoop()

    def _on_sigterm(*_):
        log.info("SIGTERM received — stopping service")
        if service._runner.is_running():
            service._runner.stop()
        loop.quit()

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT,  _on_sigterm)

    log.info("Entering main loop")
    loop.run()


if __name__ == "__main__":
    main()
