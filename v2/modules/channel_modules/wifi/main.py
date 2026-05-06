"""
NemoHeadUnit-Wireless v2 — channel_modules/wifi

Module contract:
  Name        : wifi
  Priority    : 1
  Channel ID  : parsed by BaseChannelModule from --channel-id CLI
  SDR bytes   : parsed by base into self.channel_config

  Subscribes  : channel_manager.module_start
                channel_manager.module_stop
                config.response      (auto via ConfigClient — reads hostapd_helper config)
                config.changed       (auto via ConfigClient)
                aa.channel.open     {channel_id, ...}
                aa.channel.close    {channel_id}
                aa.frame.ch<ch_id>  raw bytes
                hostapd.ready       {ssid, key, ...}  (live update of credentials)

  Publishes   : channel_manager.module_ready  {name, priority}
                wifi.credentials_sent          {ssid}

  Config keys : ssid        (str)  — read from hostapd_helper config
                ap_password (str)  — read from hostapd_helper config

  Hardcoded   : security_mode     = WPA2_PERSONAL
                access_point_type = STATIC

---
AA WiFi channel — NON-AV channel:
  This channel does NOT use the AV handshake flow (no AVChannelSetupRequest,
  no session_id, no media ACK).  on_frame() dispatches directly by message_id
  using WifiChannelMessage IDs.

Message flow:
  CREDENTIALS_REQUEST  → parse WifiSecurityRequest (body may be empty)
                        → respond CREDENTIALS_RESPONSE with ssid+key+security_mode+ap_type

Credentials source:
  SSID and password are read from the hostapd_helper config namespace via ConfigClient.
  They are also updated in real-time via the hostapd.ready bus event, which is published
  by hostapd_helper when the AP becomes active (with the final bssid and actual key).
  This ensures the module always sends the latest active credentials to the phone.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent          # v2/modules/channel_modules/wifi/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/
_PROTOS       = _V2 / "protos"                 # v2/protos/

for _p in (_V2, _MODULES, _CHANNEL_MODS, _PROTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
from shared.proto_utils import decode_aa_frame                              # noqa: E402
from shared.config_schema import field_string                               # noqa: E402
from shared.config_client import ConfigClient                               # noqa: E402
from channel_modules.base_channel_module import BaseChannelModule           # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports — WiFi channel
# ---------------------------------------------------------------------------
from oaa.wifi.WifiChannelMessageIdsEnum_pb2  import WifiChannelMessage      # noqa: E402
from oaa.wifi.WifiSecurityResponseMessage_pb2 import WifiSecurityResponse   # noqa: E402

# ---------------------------------------------------------------------------
# AA message ID aliases
# ---------------------------------------------------------------------------
_MSG_CREDENTIALS_REQUEST  = WifiChannelMessage.Enum.CREDENTIALS_REQUEST
_MSG_CREDENTIALS_RESPONSE = WifiChannelMessage.Enum.CREDENTIALS_RESPONSE

# ---------------------------------------------------------------------------
# Hardcoded WiFi parameters (per project decision)
# ---------------------------------------------------------------------------
_SECURITY_MODE     = WifiSecurityResponse.SecurityMode.WPA2_PERSONAL
_ACCESS_POINT_TYPE = WifiSecurityResponse.AccessPointType.STATIC


# ---------------------------------------------------------------------------
# WiFiModule
# ---------------------------------------------------------------------------

class WiFiModule(BaseChannelModule):
    """
    AA WiFi channel module.

    Non-AV channel: bypasses the entire AV handshake flow.
    on_frame() dispatches directly by WifiChannelMessage ID.

    Credentials (ssid + key) are sourced from:
      1. hostapd_helper config (via ConfigClient at startup)
      2. hostapd.ready bus event (live update when AP becomes active)

    security_mode and access_point_type are hardcoded to
    WPA2_PERSONAL and STATIC respectively.
    """

    MODULE_NAME: str = "wifi"
    CHANNEL_ID:  int = -1   # always overridden by --channel-id
    PRIORITY:    int = 1

    # ------------------------------------------------------------------
    # Config schema — reads ssid and ap_password from hostapd_helper namespace
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        """
        Declare config keys sourced from the hostapd_helper config namespace.
        ConfigClient is pointed at module_name="hostapd_helper" (see _init_hostapd_cfg).
        """
        return {
            "ssid":        field_string(default="AndroidAutoAP"),
            "ap_password": field_string(default=""),
        }

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()

        # Live credentials — updated by config load and hostapd.ready
        self._ssid:     str = "AndroidAutoAP"
        self._password: str = ""

        # Separate ConfigClient pointed at hostapd_helper to read its config
        self._hostapd_cfg: ConfigClient = ConfigClient(
            bus=self.bus, module_name="hostapd_helper"
        )
        self._hostapd_cfg.on_config_loaded  = self._on_hostapd_config_loaded
        self._hostapd_cfg.on_config_changed = self._on_hostapd_config_changed

    # ------------------------------------------------------------------
    # Readiness gate — no external resource to check
    # ------------------------------------------------------------------

    def _is_ready(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """
        Request hostapd_helper config at startup so we have credentials
        before the first CREDENTIALS_REQUEST arrives.
        """
        self._hostapd_cfg.register()
        self._hostapd_cfg.get(schema=self.get_schema())
        self.log.info(
            "_init: requesting hostapd_helper config for ssid+ap_password"
        )

    def _cleanup(self) -> None:
        self._ssid     = "AndroidAutoAP"
        self._password = ""
        self.log.info("_cleanup: credentials cleared")

    # ------------------------------------------------------------------
    # hostapd_helper config callbacks
    # ------------------------------------------------------------------

    def _on_hostapd_config_loaded(self, config: dict) -> None:
        """Called when hostapd_helper persisted config is delivered."""
        schema = self.get_schema()
        if config:
            merged = {k: v.default for k, v in schema.items()}
            merged.update({
                k: v for k, v in config.items()
                if k in schema and not isinstance(v, (dict, list))
            })
            self._ssid     = str(merged["ssid"])
            self._password = str(merged["ap_password"])
            self.log.info(
                "hostapd config loaded: ssid=%r ap_password=[REDACTED]", self._ssid
            )
        else:
            self.log.info(
                "hostapd config not found — using defaults: ssid=%r", self._ssid
            )
        # Mirror config_loaded flag so BaseChannelModule readiness gate is satisfied
        self._config_loaded = True
        self._try_publish_ready()

    def _on_hostapd_config_changed(self, key: str, value: Any) -> None:
        """Called when a hostapd_helper config key changes at runtime."""
        if key == "ssid":
            self._ssid = str(value)
            self.log.info("ssid updated at runtime: %r", self._ssid)
        elif key == "ap_password":
            self._password = str(value)
            self.log.info("ap_password updated at runtime (value redacted)")

    # ------------------------------------------------------------------
    # hostapd.ready — live credential update from active AP
    # ------------------------------------------------------------------

    def on_hostapd_ready(self, topic: str, payload: dict) -> None:
        """
        Fired by hostapd_helper when the AP is confirmed active.
        Contains the final ssid, key and bssid as actually configured.
        These override the persisted config values to ensure the phone
        receives the credentials that match the currently running AP.
        """
        ssid = payload.get("ssid")
        key  = payload.get("key")
        if ssid is not None:
            self._ssid = str(ssid)
        if key is not None:
            self._password = str(key)
        self.log.info(
            "hostapd.ready received — credentials updated: ssid=%r", self._ssid
        )

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self.log.info("WiFi channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        self.log.info("WiFi channel %d closed", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """
        Entry point for every raw binary frame on the WiFi channel.

        This is a NON-AV channel: no AVChannelSetupRequest, no session_id,
        no media ACK.  Decode the frame header and dispatch by message_id
        using WifiChannelMessage IDs directly.
        """
        result = decode_aa_frame(data)
        if result is None:
            self.log.error(
                "on_frame: malformed payload on ch=%d — dropping", channel_id
            )
            return

        message_id, body = result

        if message_id == _MSG_CREDENTIALS_REQUEST:
            self._handle_credentials_request(body)
        else:
            self.log.debug(
                "Unhandled msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(body),
            )

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _handle_credentials_request(self, body: bytes) -> None:
        """
        Handle CREDENTIALS_REQUEST (WifiSecurityRequest).

        The request body may be empty — the phone is simply asking for
        our AP credentials.  Respond with the current ssid + password
        plus hardcoded security_mode=WPA2_PERSONAL, access_point_type=STATIC.
        """
        self.log.info(
            "CREDENTIALS_REQUEST ch=%d — sending credentials for ssid=%r",
            self.CHANNEL_ID, self._ssid,
        )

        resp = WifiSecurityResponse()
        resp.ssid              = self._ssid
        resp.key               = self._password
        resp.security_mode     = _SECURITY_MODE
        resp.access_point_type = _ACCESS_POINT_TYPE

        self.send_frame(_MSG_CREDENTIALS_RESPONSE, resp.SerializeToString())
        self.log.info(
            "CREDENTIALS_RESPONSE sent ch=%d ssid=%r security_mode=WPA2_PERSONAL ap_type=STATIC",
            self.CHANNEL_ID, self._ssid,
        )
        self.bus.publish("wifi.credentials_sent", {"ssid": self._ssid})

    # ------------------------------------------------------------------
    # run() override — subscribe to hostapd.ready for live credential updates
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("hostapd.ready", self.on_hostapd_ready)
        super().run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    WiFiModule().run()
