"""
ap_manager.py — WiFi Access Point lifecycle via hostapd and dnsmasq.

Responsibilities:
  - Write hostapd.conf and dnsmasq.conf dynamically
  - Start/stop hostapd and dnsmasq subprocesses
  - Expose AP parameters (ssid, key, bssid, interface) to caller

No ZMQ dependency — caller (main.py) handles publishing.
"""

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("hostapd_helper.ap_manager")

# Defaults — override via APConfig
DEFAULT_INTERFACE  = "wlan0"
DEFAULT_SSID       = "AndroidAutoAP"
DEFAULT_CHANNEL    = 6
DEFAULT_SUBNET     = "192.168.50"
GATEWAY_IP         = "192.168.50.1"
DHCP_RANGE_START   = "192.168.50.10"
DHCP_RANGE_END     = "192.168.50.50"
DHCP_LEASE_TIME    = "12h"
WPA2_SECURITY_MODE = 8   # WPA2_PERSONAL constant for RFCOMM handshake
AP_TYPE_DYNAMIC    = 1


@dataclass
class APConfig:
    interface: str = DEFAULT_INTERFACE
    ssid: str = DEFAULT_SSID
    key: str = ""            # generated on start() if empty
    channel: int = DEFAULT_CHANNEL
    subnet: str = DEFAULT_SUBNET


class APManager:
    """
    Manages a WiFi AP using hostapd and dnsmasq.

    Usage:
        cfg = APConfig(interface="wlan0", ssid="MyAP", key="secret123")
        mgr = APManager(cfg)
        ok = mgr.start()     # writes configs, starts subprocesses
        params = mgr.get_params()  # {ssid, key, bssid, interface, ...}
        mgr.stop()
    """

    def __init__(self, config: Optional[APConfig] = None):
        self._cfg = config or APConfig()
        self._hostapd_proc: Optional[subprocess.Popen] = None
        self._dnsmasq_proc: Optional[subprocess.Popen] = None
        self._hostapd_conf: Optional[str] = None  # temp file path
        self._dnsmasq_conf: Optional[str] = None
        self._bssid: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Configure interface, write configs, start hostapd + dnsmasq."""
        if not self._cfg.key:
            self._cfg.key = self._generate_key()

        self._bssid = self._get_interface_mac(self._cfg.interface)
        log.info(f"Starting AP: ssid={self._cfg.ssid} iface={self._cfg.interface} bssid={self._bssid}")

        if not self._configure_interface():
            return False

        self._hostapd_conf = self._write_hostapd_conf()
        self._dnsmasq_conf = self._write_dnsmasq_conf()

        if not self._start_hostapd():
            return False

        if not self._start_dnsmasq():
            self.stop()
            return False

        return True

    def stop(self) -> None:
        """Terminate hostapd and dnsmasq, restore interface."""
        self._kill(self._hostapd_proc, "hostapd")
        self._kill(self._dnsmasq_proc, "dnsmasq")
        self._hostapd_proc = None
        self._dnsmasq_proc = None
        self._cleanup_conf(self._hostapd_conf)
        self._cleanup_conf(self._dnsmasq_conf)
        self._restore_interface()
        log.info("AP stopped")

    def is_running(self) -> bool:
        """Return True if both subprocesses are still alive."""
        hp = self._hostapd_proc and self._hostapd_proc.poll() is None
        dp = self._dnsmasq_proc and self._dnsmasq_proc.poll() is None
        return bool(hp and dp)

    def get_params(self) -> dict:
        """Return AP parameters needed for the RFCOMM handshake."""
        return {
            "ssid":          self._cfg.ssid,
            "key":           self._cfg.key,
            "bssid":         self._bssid,
            "interface":     self._cfg.interface,
            "gateway_ip":    GATEWAY_IP,
            "security_mode": WPA2_SECURITY_MODE,
            "ap_type":       AP_TYPE_DYNAMIC,
        }

    # ------------------------------------------------------------------
    # Config writers
    # ------------------------------------------------------------------

    def _write_hostapd_conf(self) -> str:
        cfg = self._cfg
        content = (
            f"interface={cfg.interface}\n"
            f"driver=nl80211\n"
            f"ssid={cfg.ssid}\n"
            f"hw_mode=g\n"
            f"channel={cfg.channel}\n"
            f"wmm_enabled=0\n"
            f"macaddr_acl=0\n"
            f"auth_algs=1\n"
            f"ignore_broadcast_ssid=0\n"
            f"wpa=2\n"
            f"wpa_passphrase={cfg.key}\n"
            f"wpa_key_mgmt=WPA-PSK\n"
            f"wpa_pairwise=TKIP\n"
            f"rsn_pairwise=CCMP\n"
        )
        return self._write_temp("hostapd_", ".conf", content)

    def _write_dnsmasq_conf(self) -> str:
        cfg = self._cfg
        content = (
            f"interface={cfg.interface}\n"
            f"dhcp-range={DHCP_RANGE_START},{DHCP_RANGE_END},{DHCP_LEASE_TIME}\n"
            f"dhcp-option=3,{GATEWAY_IP}\n"
            f"dhcp-option=6,{GATEWAY_IP}\n"
            f"server=8.8.8.8\n"
            f"log-queries\n"
            f"log-dhcp\n"
            f"listen-address={GATEWAY_IP}\n"
            f"bind-interfaces\n"
        )
        return self._write_temp("dnsmasq_", ".conf", content)

    # ------------------------------------------------------------------
    # Subprocess helpers
    # ------------------------------------------------------------------

    def _start_hostapd(self) -> bool:
        try:
            self._hostapd_proc = subprocess.Popen(
                ["hostapd", self._hostapd_conf],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.info(f"hostapd started (pid={self._hostapd_proc.pid})")
            return True
        except FileNotFoundError:
            log.error("hostapd not found — install hostapd")
            return False
        except Exception as e:
            log.error(f"hostapd start failed: {e}")
            return False

    def _start_dnsmasq(self) -> bool:
        try:
            self._dnsmasq_proc = subprocess.Popen(
                ["dnsmasq", f"--conf-file={self._dnsmasq_conf}", "--no-daemon"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.info(f"dnsmasq started (pid={self._dnsmasq_proc.pid})")
            return True
        except FileNotFoundError:
            log.error("dnsmasq not found — install dnsmasq")
            return False
        except Exception as e:
            log.error(f"dnsmasq start failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Interface helpers
    # ------------------------------------------------------------------

    def _configure_interface(self) -> bool:
        """Assign gateway IP to interface."""
        iface = self._cfg.interface
        try:
            subprocess.run(["ip", "link", "set", iface, "up"], check=True)
            subprocess.run(
                ["ip", "addr", "flush", "dev", iface], check=True
            )
            subprocess.run(
                ["ip", "addr", "add", f"{GATEWAY_IP}/24", "dev", iface],
                check=True,
            )
            log.info(f"Interface {iface} configured with {GATEWAY_IP}/24")
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Interface config failed: {e}")
            return False

    def _restore_interface(self) -> None:
        iface = self._cfg.interface
        try:
            subprocess.run(["ip", "addr", "flush", "dev", iface], check=False)
            log.info(f"Interface {iface} flushed")
        except Exception as e:
            log.warning(f"_restore_interface: {e}")

    def _get_interface_mac(self, iface: str) -> str:
        try:
            path = f"/sys/class/net/{iface}/address"
            with open(path) as f:
                return f.read().strip().upper()
        except Exception:
            return "00:00:00:00:00:00"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_key(length: int = 12) -> str:
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def _write_temp(prefix: str, suffix: str, content: str) -> str:
        fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
        with os.fdopen(fd, "w") as f:
            f.write(content)
        log.debug(f"Wrote config: {path}")
        return path

    @staticmethod
    def _cleanup_conf(path: Optional[str]) -> None:
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except Exception:
                pass

    @staticmethod
    def _kill(proc: Optional[subprocess.Popen], name: str) -> None:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.info(f"{name} terminated")
