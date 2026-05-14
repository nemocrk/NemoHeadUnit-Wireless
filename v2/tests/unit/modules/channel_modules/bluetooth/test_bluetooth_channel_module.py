"""
Unit tests for channel_modules/bluetooth/main.py

Strategy:
  BluetoothModule è un canale NON-AV: on_frame() riceve raw bytes e
  li decodifica con decode_aa_frame() prima di fare dispatch.
  Tutti i proto e shared sono patchati pre-import.

Covers:
  Section 1  — costanti msg ID
  Section 2  — __init__: stato iniziale, schema vuoto
  Section 3  — on_channel_open / on_channel_close
  Section 4  — on_frame(): malformed frame → log.error + drop;
               dispatch per PAIRING_REQUEST, CHANNEL_OPEN_REQUEST,
               AUTH_DATA, AUTH_RESULT, unknown
  Section 5  — _handle_open_request(): send_frame CHANNEL_OPEN_RESPONSE
  Section 6  — _handle_pairing_request(): parse OK, parse error,
               pubblica bluetooth_manager.pairing_request,
               invia PAIRING_RESPONSE already_paired=True
  Section 7  — _handle_auth_data(): pubblica bluetooth_manager.auth_data
  Section 8  — _handle_auth_result(): pubblica bluetooth_manager.auth_result
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
        "oaa", "oaa.control", "oaa.bluetooth", "oaa.common",
        "oaa.control.ControlMessageIdsEnum_pb2",
        "oaa.control.ChannelOpenResponseMessage_pb2",
        "oaa.common.StatusEnum_pb2",
        "oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2",
        "oaa.bluetooth.BluetoothPairingRequestMessage_pb2",
        "oaa.bluetooth.BluetoothPairingResponseMessage_pb2",
        "shared.proto_utils",
        "shared.config_schema",
        "shared.config_client",
    ]
    for name in stubs:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

_stub_all()

for _k in list(sys.modules.keys()):
    if "channel_modules.bluetooth" in _k and "test" not in _k:
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()), \
     patch("shared.bus_client.BusClient", MagicMock()):
    from channel_modules.bluetooth.main import (
        BluetoothModule,
        _MSG_CHANNEL_OPEN_REQUEST, _MSG_CHANNEL_OPEN_RESPONSE,
        _MSG_PAIRING_REQUEST, _MSG_PAIRING_RESPONSE,
        _MSG_AUTH_DATA, _MSG_AUTH_RESULT,
    )

import channel_modules.bluetooth.main as _bt_mod


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def bm():
    mod = BluetoothModule()
    mod.bus = MagicMock()
    mod.log = MagicMock()
    mod.CHANNEL_ID = 8
    return mod


# ===========================================================================
# Section 1 — Constants
# ===========================================================================

class TestConstants:

    @pytest.mark.unit
    def test_msg_pairing_request_defined(self):
        assert _MSG_PAIRING_REQUEST is not None

    @pytest.mark.unit
    def test_msg_pairing_response_defined(self):
        assert _MSG_PAIRING_RESPONSE is not None

    @pytest.mark.unit
    def test_msg_auth_data_defined(self):
        assert _MSG_AUTH_DATA is not None

    @pytest.mark.unit
    def test_msg_auth_result_defined(self):
        assert _MSG_AUTH_RESULT is not None


# ===========================================================================
# Section 2 — __init__ / schema
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_schema_is_empty_dict(self, bm):
        assert bm.get_schema() == {}

    @pytest.mark.unit
    def test_module_name_bluetooth(self, bm):
        assert bm.MODULE_NAME == "bluetooth"

    @pytest.mark.unit
    def test_priority_is_1(self, bm):
        assert bm.PRIORITY == 1


# ===========================================================================
# Section 3 — on_channel_open / on_channel_close
# ===========================================================================

class TestChannelOpenClose:

    @pytest.mark.unit
    def test_channel_open_logs(self, bm):
        bm.on_channel_open(8, {})
        bm.log.info.assert_called()

    @pytest.mark.unit
    def test_channel_close_logs(self, bm):
        bm.on_channel_close(8)
        bm.log.info.assert_called()


# ===========================================================================
# Section 4 — on_frame() dispatch
# ===========================================================================

class TestOnFrame:

    @pytest.mark.unit
    def test_malformed_frame_logs_error_and_drops(self, bm):
        with patch.object(_bt_mod, "decode_aa_frame", return_value=None):
            bm.on_frame(8, b"garbage")
        bm.log.error.assert_called()

    @pytest.mark.unit
    def test_dispatch_pairing_request(self, bm):
        with patch.object(_bt_mod, "decode_aa_frame", return_value=(_MSG_PAIRING_REQUEST, b"")), \
             patch.object(bm, "_handle_pairing_request") as mock_h:
            bm.on_frame(8, b"data")
        mock_h.assert_called_once_with(b"")

    @pytest.mark.unit
    def test_dispatch_channel_open_request(self, bm):
        with patch.object(_bt_mod, "decode_aa_frame", return_value=(_MSG_CHANNEL_OPEN_REQUEST, b"")), \
             patch.object(bm, "_handle_open_request") as mock_h:
            bm.on_frame(8, b"data")
        mock_h.assert_called_once_with(b"")

    @pytest.mark.unit
    def test_dispatch_auth_data(self, bm):
        with patch.object(_bt_mod, "decode_aa_frame", return_value=(_MSG_AUTH_DATA, b"abc")), \
             patch.object(bm, "_handle_auth_data") as mock_h:
            bm.on_frame(8, b"data")
        mock_h.assert_called_once_with(b"abc")

    @pytest.mark.unit
    def test_dispatch_auth_result(self, bm):
        with patch.object(_bt_mod, "decode_aa_frame", return_value=(_MSG_AUTH_RESULT, b"xyz")), \
             patch.object(bm, "_handle_auth_result") as mock_h:
            bm.on_frame(8, b"data")
        mock_h.assert_called_once_with(b"xyz")

    @pytest.mark.unit
    def test_unknown_msg_id_logs_debug(self, bm):
        with patch.object(_bt_mod, "decode_aa_frame", return_value=(0xFFFF, b"")):
            bm.on_frame(8, b"data")
        bm.log.debug.assert_called()


# ===========================================================================
# Section 5 — _handle_open_request()
# ===========================================================================

class TestHandleOpenRequest:

    @pytest.mark.unit
    def test_sends_channel_open_response(self, bm):
        with patch.object(bm, "send_frame") as mock_sf:
            bm._handle_open_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_CHANNEL_OPEN_RESPONSE

    @pytest.mark.unit
    def test_logs_info(self, bm):
        with patch.object(bm, "send_frame"):
            bm._handle_open_request(b"")
        bm.log.info.assert_called()


# ===========================================================================
# Section 6 — _handle_pairing_request()
# ===========================================================================

class TestHandlePairingRequest:

    def _mock_req(self, address="aa:bb:cc", name="TestPhone", method=1):
        req = MagicMock()
        req.phone_address = address
        req.phone_name    = name
        req.pairing_method = method
        req.HasField = lambda f: True
        return req

    @pytest.mark.unit
    def test_publishes_pairing_request_event(self, bm):
        req = self._mock_req()
        with patch.object(_bt_mod, "BluetoothPairingRequest", return_value=req), \
             patch.object(bm, "send_frame"):
            bm._handle_pairing_request(b"")
        bm.bus.publish.assert_any_call(
            "bluetooth.pairing_request",
            {"phone_address": "aa:bb:cc", "phone_name": "TestPhone", "pairing_method": 1},
        )

    @pytest.mark.unit
    def test_sends_pairing_response(self, bm):
        req = self._mock_req()
        with patch.object(_bt_mod, "BluetoothPairingRequest", return_value=req), \
             patch.object(bm, "send_frame") as mock_sf:
            bm._handle_pairing_request(b"")
        assert mock_sf.call_args[0][0] == _MSG_PAIRING_RESPONSE

    @pytest.mark.unit
    def test_parse_error_still_sends_response(self, bm):
        req_class = MagicMock()
        req_class.return_value.ParseFromString.side_effect = Exception("bad proto")
        with patch.object(_bt_mod, "BluetoothPairingRequest", req_class), \
             patch.object(bm, "send_frame") as mock_sf:
            bm._handle_pairing_request(b"garbage")
        mock_sf.assert_called_once()

    @pytest.mark.unit
    def test_logs_warning_on_parse_error(self, bm):
        req_class = MagicMock()
        req_class.return_value.ParseFromString.side_effect = Exception("bad proto")
        with patch.object(_bt_mod, "BluetoothPairingRequest", req_class), \
             patch.object(bm, "send_frame"):
            bm._handle_pairing_request(b"garbage")
        bm.log.warning.assert_called()


# ===========================================================================
# Section 7 — _handle_auth_data()
# ===========================================================================

class TestHandleAuthData:

    @pytest.mark.unit
    def test_publishes_auth_data(self, bm):
        bm._handle_auth_data(b"\x01\x02\x03")
        bm.bus.publish.assert_called_with(
            "bluetooth.auth_data", {"data_hex": b"\x01\x02\x03".hex()}
        )

    @pytest.mark.unit
    def test_logs_debug(self, bm):
        bm._handle_auth_data(b"\xAA")
        bm.log.debug.assert_called()


# ===========================================================================
# Section 8 — _handle_auth_result()
# ===========================================================================

class TestHandleAuthResult:

    @pytest.mark.unit
    def test_publishes_auth_result(self, bm):
        bm._handle_auth_result(b"\xFF")
        bm.bus.publish.assert_called_with(
            "bluetooth.auth_result", {"data_hex": b"\xFF".hex()}
        )

    @pytest.mark.unit
    def test_logs_debug(self, bm):
        bm._handle_auth_result(b"\x00")
        bm.log.debug.assert_called()
