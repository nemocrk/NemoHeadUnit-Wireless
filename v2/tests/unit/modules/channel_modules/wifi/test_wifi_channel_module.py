"""
Unit tests for channel_modules/wifi/main.py

Strategy:
  WiFiModule è un canale NON-AV: on_frame() decodifica raw bytes via decode_aa_frame()
  e fa dispatch per WifiChannelMessage ID.
  Tutti i proto e shared sono patchati pre-import con MagicMock.
  ConfigClient è patchato nella fixture per isolare i callback.

Covers:
  Section 1  — costanti: msg IDs, _SECURITY_MODE, _ACCESS_POINT_TYPE
  Section 2  — __init__: _ssid default, _password default, schema
  Section 3  — _init(): chiama _hostapd_cfg.register() e .get()
  Section 4  — _cleanup(): resetta _ssid e _password ai default
  Section 5  — _on_hostapd_config_loaded(): con config valida, con config vuota,
               aggiorna _ssid/_password, setta _config_loaded=True,
               chiama _try_publish_ready
  Section 6  — _on_hostapd_config_changed(): chiave ssid, chiave ap_password,
               chiave sconosciuta ignorata
  Section 7  — on_hostapd_ready(): aggiorna ssid e key, solo ssid, solo key,
               payload vuoto = nessun aggiornamento
  Section 8  — on_channel_open / on_channel_close
  Section 9  — on_frame(): malformed → log.error + drop;
               dispatch CREDENTIALS_REQUEST, CHANNEL_OPEN_REQUEST, unknown
  Section 10 — _handle_open_request(): invia CHANNEL_OPEN_RESPONSE
  Section 11 — _handle_credentials_request(): invia CREDENTIALS_RESPONSE
               con ssid e key corretti, pubblica wifi.credentials_sent
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Stub proto + shared imports before module load
# ---------------------------------------------------------------------------

def _stub_all():
    stubs = [
        "oaa", "oaa.control", "oaa.wifi", "oaa.common",
        "oaa.control.ControlMessageIdsEnum_pb2",
        "oaa.control.ChannelOpenResponseMessage_pb2",
        "oaa.common.StatusEnum_pb2",
        "oaa.wifi.WifiChannelMessageIdsEnum_pb2",
        "oaa.wifi.WifiSecurityResponseMessage_pb2",
        "shared.proto_utils",
        "shared.config_schema",
    ]
    for name in stubs:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

_stub_all()

for _k in list(sys.modules.keys()):
    if "channel_modules.wifi" in _k and "test" not in _k:
        del sys.modules[_k]

_config_client_mock_cls = MagicMock()

with patch("shared.config_client.ConfigClient", _config_client_mock_cls), \
     patch("shared.logger.get_logger", return_value=MagicMock()), \
     patch("shared.bus_client.BusClient", MagicMock()):
    from channel_modules.wifi.main import (
        WiFiModule,
        _MSG_CHANNEL_OPEN_REQUEST, _MSG_CHANNEL_OPEN_RESPONSE,
        _MSG_CREDENTIALS_REQUEST, _MSG_CREDENTIALS_RESPONSE,
    )

import channel_modules.wifi.main as _wifi_mod


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def wm():
    _config_client_mock_cls.reset_mock()
    mod = WiFiModule()
    mod.bus = MagicMock()
    mod.log = MagicMock()
    mod.CHANNEL_ID = 9
    # Replace the ConfigClient instance with a fresh mock
    mod._hostapd_cfg = MagicMock()
    # Provide a mock _config dict and _try_publish_ready
    mod._config = {}
    mod._try_publish_ready = MagicMock()
    return mod


# ===========================================================================
# Section 1 — Constants
# ===========================================================================

class TestConstants:

    @pytest.mark.unit
    def test_msg_credentials_request_defined(self):
        assert _MSG_CREDENTIALS_REQUEST is not None

    @pytest.mark.unit
    def test_msg_credentials_response_defined(self):
        assert _MSG_CREDENTIALS_RESPONSE is not None

    @pytest.mark.unit
    def test_msg_channel_open_request_defined(self):
        assert _MSG_CHANNEL_OPEN_REQUEST is not None

    @pytest.mark.unit
    def test_msg_channel_open_response_defined(self):
        assert _MSG_CHANNEL_OPEN_RESPONSE is not None


# ===========================================================================
# Section 2 — __init__ / schema
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_default_ssid(self, wm):
        assert wm._ssid == "AndroidAutoAP"

    @pytest.mark.unit
    def test_default_password_empty(self, wm):
        assert wm._password == ""

    @pytest.mark.unit
    def test_schema_has_ssid_key(self, wm):
        assert "ssid" in wm.get_schema()

    @pytest.mark.unit
    def test_schema_has_ap_password_key(self, wm):
        assert "ap_password" in wm.get_schema()

    @pytest.mark.unit
    def test_module_name(self, wm):
        assert wm.MODULE_NAME == "wifi"

    @pytest.mark.unit
    def test_is_ready_true(self, wm):
        assert wm._is_ready() is True


# ===========================================================================
# Section 3 — _init()
# ===========================================================================

class TestInitHook:

    @pytest.mark.unit
    def test_calls_hostapd_cfg_register(self, wm):
        wm._init()
        wm._hostapd_cfg.register.assert_called_once()

    @pytest.mark.unit
    def test_calls_hostapd_cfg_get(self, wm):
        wm._init()
        wm._hostapd_cfg.get.assert_called_once()

    @pytest.mark.unit
    def test_logs_info(self, wm):
        wm._init()
        wm.log.info.assert_called()


# ===========================================================================
# Section 4 — _cleanup()
# ===========================================================================

class TestCleanup:

    @pytest.mark.unit
    def test_resets_ssid_to_default(self, wm):
        wm._ssid = "MyAP"
        wm._cleanup()
        assert wm._ssid == "AndroidAutoAP"

    @pytest.mark.unit
    def test_resets_password_to_empty(self, wm):
        wm._password = "secret"
        wm._cleanup()
        assert wm._password == ""

    @pytest.mark.unit
    def test_logs_info(self, wm):
        wm._cleanup()
        wm.log.info.assert_called()


# ===========================================================================
# Section 5 — _on_hostapd_config_loaded()
# ===========================================================================

class TestOnHostapdConfigLoaded:

    @pytest.mark.unit
    def test_updates_ssid_from_config(self, wm):
        wm._on_hostapd_config_loaded({"ssid": "MyNetwork", "ap_password": "pw123"})
        assert wm._ssid == "MyNetwork"

    @pytest.mark.unit
    def test_updates_password_from_config(self, wm):
        wm._on_hostapd_config_loaded({"ssid": "MyNetwork", "ap_password": "pw123"})
        assert wm._password == "pw123"

    @pytest.mark.unit
    def test_empty_config_keeps_defaults(self, wm):
        wm._on_hostapd_config_loaded({})
        # empty → defaults from schema are used
        assert wm._ssid == "AndroidAutoAP"
        assert wm._password == ""

    @pytest.mark.unit
    def test_sets_config_loaded_flag(self, wm):
        wm._on_hostapd_config_loaded({})
        assert wm._config_loaded is True

    @pytest.mark.unit
    def test_calls_try_publish_ready(self, wm):
        wm._on_hostapd_config_loaded({})
        wm._try_publish_ready.assert_called_once()

    @pytest.mark.unit
    def test_logs_info(self, wm):
        wm._on_hostapd_config_loaded({"ssid": "X", "ap_password": "Y"})
        wm.log.info.assert_called()


# ===========================================================================
# Section 6 — _on_hostapd_config_changed()
# ===========================================================================

class TestOnHostapdConfigChanged:

    @pytest.mark.unit
    def test_updates_ssid(self, wm):
        wm._on_hostapd_config_changed("ssid", "NewSSID")
        assert wm._ssid == "NewSSID"

    @pytest.mark.unit
    def test_updates_ap_password(self, wm):
        wm._on_hostapd_config_changed("ap_password", "newpw")
        assert wm._password == "newpw"

    @pytest.mark.unit
    def test_unknown_key_ignored(self, wm):
        old_ssid = wm._ssid
        wm._on_hostapd_config_changed("unknown_key", "value")
        assert wm._ssid == old_ssid

    @pytest.mark.unit
    def test_logs_info_on_ssid_change(self, wm):
        wm._on_hostapd_config_changed("ssid", "X")
        wm.log.info.assert_called()


# ===========================================================================
# Section 7 — on_hostapd_ready()
# ===========================================================================

class TestOnHostapdReady:

    @pytest.mark.unit
    def test_updates_ssid_and_key(self, wm):
        wm.on_hostapd_ready("", {"ssid": "LiveAP", "key": "livekey"})
        assert wm._ssid == "LiveAP"
        assert wm._password == "livekey"

    @pytest.mark.unit
    def test_updates_only_ssid(self, wm):
        wm._password = "oldpw"
        wm.on_hostapd_ready("", {"ssid": "OnlySSID"})
        assert wm._ssid == "OnlySSID"
        assert wm._password == "oldpw"

    @pytest.mark.unit
    def test_updates_only_key(self, wm):
        wm._ssid = "MyAP"
        wm.on_hostapd_ready("", {"key": "newkey"})
        assert wm._ssid == "MyAP"
        assert wm._password == "newkey"

    @pytest.mark.unit
    def test_empty_payload_no_change(self, wm):
        wm._ssid = "MyAP"
        wm._password = "pw"
        wm.on_hostapd_ready("", {})
        assert wm._ssid == "MyAP"
        assert wm._password == "pw"

    @pytest.mark.unit
    def test_logs_info(self, wm):
        wm.on_hostapd_ready("", {"ssid": "X", "key": "Y"})
        wm.log.info.assert_called()


# ===========================================================================
# Section 8 — on_channel_open / on_channel_close
# ===========================================================================

class TestChannelOpenClose:

    @pytest.mark.unit
    def test_channel_open_logs(self, wm):
        wm.on_channel_open(9, {})
        wm.log.info.assert_called()

    @pytest.mark.unit
    def test_channel_close_logs(self, wm):
        wm.on_channel_close(9)
        wm.log.info.assert_called()


# ===========================================================================
# Section 9 — on_frame() dispatch
# ===========================================================================

class TestOnFrame:

    @pytest.mark.unit
    def test_malformed_frame_logs_error(self, wm):
        with patch.object(_wifi_mod, "decode_aa_frame", return_value=None):
            wm.on_frame(9, b"bad")
        wm.log.error.assert_called()

    @pytest.mark.unit
    def test_malformed_frame_does_not_call_handlers(self, wm):
        with patch.object(_wifi_mod, "decode_aa_frame", return_value=None), \
             patch.object(wm, "_handle_credentials_request") as mock_h:
            wm.on_frame(9, b"bad")
        mock_h.assert_not_called()

    @pytest.mark.unit
    def test_dispatch_credentials_request(self, wm):
        with patch.object(_wifi_mod, "decode_aa_frame", return_value=(_MSG_CREDENTIALS_REQUEST, b"")), \
             patch.object(wm, "_handle_credentials_request") as mock_h:
            wm.on_frame(9, b"data")
        mock_h.assert_called_once_with(b"")

    @pytest.mark.unit
    def test_dispatch_channel_open_request(self, wm):
        with patch.object(_wifi_mod, "decode_aa_frame", return_value=(_MSG_CHANNEL_OPEN_REQUEST, b"")), \
             patch.object(wm, "_handle_open_request") as mock_h:
            wm.on_frame(9, b"data")
        mock_h.assert_called_once_with(b"")

    @pytest.mark.unit
    def test_unknown_msg_id_logs_debug(self, wm):
        with patch.object(_wifi_mod, "decode_aa_frame", return_value=(0xFFFF, b"")):
            wm.on_frame(9, b"data")
        wm.log.debug.assert_called()


# ===========================================================================
# Section 10 — _handle_open_request()
# ===========================================================================

class TestHandleOpenRequest:

    @pytest.mark.unit
    def test_sends_channel_open_response(self, wm):
        with patch.object(wm, "send_frame") as mock_sf:
            wm._handle_open_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_CHANNEL_OPEN_RESPONSE

    @pytest.mark.unit
    def test_logs_info(self, wm):
        with patch.object(wm, "send_frame"):
            wm._handle_open_request(b"")
        wm.log.info.assert_called()


# ===========================================================================
# Section 11 — _handle_credentials_request()
# ===========================================================================

class TestHandleCredentialsRequest:

    @pytest.mark.unit
    def test_sends_credentials_response(self, wm):
        wm._ssid = "TestAP"
        wm._password = "testpw"
        resp_mock = MagicMock()
        with patch.object(_wifi_mod, "WifiSecurityResponse", return_value=resp_mock), \
             patch.object(wm, "send_frame") as mock_sf:
            wm._handle_credentials_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_CREDENTIALS_RESPONSE

    @pytest.mark.unit
    def test_sets_ssid_on_response(self, wm):
        wm._ssid = "MyAP"
        resp_mock = MagicMock()
        with patch.object(_wifi_mod, "WifiSecurityResponse", return_value=resp_mock), \
             patch.object(wm, "send_frame"):
            wm._handle_credentials_request(b"")
        assert resp_mock.ssid == "MyAP"

    @pytest.mark.unit
    def test_sets_key_on_response(self, wm):
        wm._password = "mypw"
        resp_mock = MagicMock()
        with patch.object(_wifi_mod, "WifiSecurityResponse", return_value=resp_mock), \
             patch.object(wm, "send_frame"):
            wm._handle_credentials_request(b"")
        assert resp_mock.key == "mypw"

    @pytest.mark.unit
    def test_publishes_wifi_credentials_sent(self, wm):
        wm._ssid = "TestAP"
        resp_mock = MagicMock()
        with patch.object(_wifi_mod, "WifiSecurityResponse", return_value=resp_mock), \
             patch.object(wm, "send_frame"):
            wm._handle_credentials_request(b"")
        wm.bus.publish.assert_called_with("wifi.credentials_sent", {"ssid": "TestAP"})

    @pytest.mark.unit
    def test_logs_info(self, wm):
        resp_mock = MagicMock()
        with patch.object(_wifi_mod, "WifiSecurityResponse", return_value=resp_mock), \
             patch.object(wm, "send_frame"):
            wm._handle_credentials_request(b"")
        wm.log.info.assert_called()
