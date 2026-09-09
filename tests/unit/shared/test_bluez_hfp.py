"""
test_bluez_hfp.py — Unit tests for BlueZ Hands-Free Profile (HFP) AT command generation,
response parsing, and call state lifecycle in shared.hardware.bluez_hfp.
"""

from unittest.mock import MagicMock
import pytest

from shared.hardware.bluez_hfp import (
    CallState,
    format_at_dial,
    format_at_answer,
    format_at_hangup,
    format_at_dtmf,
    format_at_mute,
    format_at_cops_query,
    format_at_csq_query,
    format_at_cind_query,
    format_at_cbc_query,
    format_at_clcc_query,
    parse_clcc_response,
    parse_ciev_response,
    parse_cops_response,
    parse_csq_response,
    parse_cbc_response,
    parse_cind_response,
    parse_clip_response,
    BlueZHFClient,
)

pytestmark = pytest.mark.unit


def test_at_command_formatters():
    assert format_at_dial("+15551234567") == "ATD+15551234567;\r"
    assert format_at_answer() == "ATA\r"
    assert format_at_hangup() == "AT+CHUP\r"
    assert format_at_dtmf("5") == "AT+VTS=5\r"
    assert format_at_mute(True) == "AT+CMUT=1\r"
    assert format_at_mute(False) == "AT+CMUT=0\r"
    assert format_at_cops_query() == "AT+COPS?\r"
    assert format_at_csq_query() == "AT+CSQ\r"
    assert format_at_cind_query() == "AT+CIND?\r"
    assert format_at_cbc_query() == "AT+CBC\r"
    assert format_at_clcc_query() == "AT+CLCC\r"


def test_parse_clcc_response():
    # Active incoming call
    res1 = parse_clcc_response('+CLCC: 1,1,0,0,0,"+15551234",145')
    assert res1 is not None
    assert res1["idx"] == 1
    assert res1["direction"] == "incoming"
    assert res1["state"] == CallState.ACTIVE
    assert res1["number"] == "+15551234"

    # Dialing outgoing call
    res2 = parse_clcc_response('+CLCC: 2,0,2,0,0,"+15559999",145')
    assert res2 is not None
    assert res2["direction"] == "outgoing"
    assert res2["state"] == CallState.DIALING

    # Ringing incoming call
    res3 = parse_clcc_response('+CLCC: 1,1,4,0,0,"+15550000",145')
    assert res3 is not None
    assert res3["state"] == CallState.RINGING

    # Held call
    res4 = parse_clcc_response('+CLCC: 1,0,1,0,0,"+15551111",145')
    assert res4 is not None
    assert res4["state"] == CallState.HELD

    # Malformed
    assert parse_clcc_response("INVALID_LINE") is None


def test_parse_ciev_response():
    ind_map = {1: "service", 2: "call", 5: "signal"}
    res = parse_ciev_response("+CIEV: 2,1", ind_map)
    assert res is not None
    assert res["indicator"] == "call"
    assert res["value"] == 1

    res_unknown = parse_ciev_response("+CIEV: 99,0", ind_map)
    assert res_unknown is not None
    assert res_unknown["indicator"] == "indicator_99"

    assert parse_ciev_response("+BAD: 1,1", ind_map) is None


def test_parse_cops_response():
    assert parse_cops_response('+COPS: 0,0,"Verizon"') == "Verizon"
    assert parse_cops_response('+COPS: 0,0,"Vodafone IT",7') == "Vodafone IT"
    assert parse_cops_response('+COPS: 0') is None


def test_parse_csq_response():
    # RSSI 31 maps to 5 bars
    assert parse_csq_response("+CSQ: 31,99") == 5
    # RSSI 0 maps to 0 bars
    assert parse_csq_response("+CSQ: 0,99") == 0
    # RSSI 16 maps to round(16 * 5 / 31) = round(2.58) = 3
    assert parse_csq_response("+CSQ: 16,99") == 3
    # Unknown (99)
    assert parse_csq_response("+CSQ: 99,99") is None
    assert parse_csq_response("BAD_CSQ") is None


def test_parse_cbc_response():
    assert parse_cbc_response("+CBC: 0,85") == 85
    assert parse_cbc_response("+CBC: 0,100") == 100
    assert parse_cbc_response("+CBC: 0,0") == 0
    assert parse_cbc_response("BAD_CBC") is None


def test_parse_cind_response():
    ind_data = "+CIND: 1,0,0,0,4,0,5"
    res = parse_cind_response(ind_data)
    assert res["service"] == 1
    assert res["call"] == 0
    assert res["signal"] == 4
    assert res["battchg"] == 5
    assert parse_cind_response("BAD") == {}


def test_parse_clip_response():
    clip = parse_clip_response('+CLIP: "+15551234",145,,,"Alice"')
    assert clip is not None
    assert clip["number"] == "+15551234"
    assert clip["name"] == "Alice"

    clip_no_name = parse_clip_response('+CLIP: "+15559999",145')
    assert clip_no_name is not None
    assert clip_no_name["number"] == "+15559999"
    assert clip_no_name["name"] == "+15559999"

    assert parse_clip_response("BAD_CLIP") is None


def test_bluez_hf_client_lifecycle_and_actions():
    state_updates = []
    def _on_state_changed(state_dict):
        state_updates.append(state_dict)

    client = BlueZHFClient(
        on_state_changed=_on_state_changed,
        auto_connect_session_bus=False
    )

    status = client.get_state()
    assert status["call_state"] == "IDLE"
    assert status["muted"] is False

    # Dial
    client.dial("+15554321")
    assert client.get_state()["call_state"] == "DIALING"
    assert client.get_state()["phone_number"] == "+15554321"

    # Answer
    client.answer()
    assert client.get_state()["call_state"] == "ACTIVE"

    # DTMF
    assert client.send_dtmf("5") is True
    assert client.send_dtmf("") is False

    # Mute
    client.set_mute(True)
    assert client.get_state()["muted"] is True
    client.set_mute(False)
    assert client.get_state()["muted"] is False

    # Hangup
    client.hangup()
    assert client.get_state()["call_state"] == "IDLE"
    assert client.get_state()["phone_number"] == ""

    # Bind and unbind
    client.bind_device("00:11:22:33:44:55")
    assert client._bound_device_address == "00:11:22:33:44:55"
    client.unbind_device()
    assert client._bound_device_address == ""


def test_bluez_hf_client_handle_at_responses():
    client = BlueZHFClient(auto_connect_session_bus=False)

    # Handle incoming caller ID (+CLIP)
    client.process_at_line('+CLIP: "+15558888",145,,,"Bob"')
    assert client.get_state()["phone_number"] == "+15558888"
    assert client.get_state()["caller_name"] == "Bob"

    # Handle operator (+COPS)
    client.process_at_line('+COPS: 0,0,"AT&T"')
    assert client.get_state()["carrier"] == "AT&T"

    # Handle signal (+CSQ)
    client.process_at_line("+CSQ: 25,99")
    assert client.get_state()["signal_bars"] == 4

    # Handle battery (+CBC)
    client.process_at_line("+CBC: 0,90")
    assert client.get_state()["battery_pct"] == 90

    # Handle indicator (+CIEV callsetup incoming)
    client.process_at_line("+CIEV: 3,1")
    assert client.get_state()["call_state"] == "RINGING"

    # Handle indicator (+CIEV call active)
    client.process_at_line("+CIEV: 2,1")
    assert client.get_state()["call_state"] == "ACTIVE"

    # Handle indicator (+CIEV call disconnected)
    client.process_at_line("+CIEV: 2,0")
    assert client.get_state()["call_state"] == "IDLE"

    # Handle CIND initial indicators (call=0, callsetup=0, signal=4, battchg=5)
    client.process_at_line("+CIND: 0,0,0,0,4,0,5")
    assert client.get_state()["signal_bars"] == 4
    assert client.get_state()["battery_pct"] == 100
    assert client.get_state()["call_state"] == "IDLE"

    # Handle RING
    client.process_at_line("RING")
    assert client.get_state()["call_state"] == "RINGING"

    # Handle CLCC active call
    client.process_at_line('+CLCC: 1,0,0,0,0,"+15550000",145')
    assert client.get_state()["call_state"] == "ACTIVE"
    assert client.get_state()["phone_number"] == "+15550000"

    # Handle NO CARRIER
    client.process_at_line("NO CARRIER")
    assert client.get_state()["call_state"] == "IDLE"

    # Handle BUSY
    client.process_at_line("RING")
    assert client.get_state()["call_state"] == "RINGING"
    client.process_at_line("BUSY")
    assert client.get_state()["call_state"] == "IDLE"


def test_bluez_hf_client_dbus_call_events():
    client = BlueZHFClient(auto_connect_session_bus=False)

    # 1. Call Added (incoming / ringing)
    client._on_dbus_call_added(
        call_path="/org/ofono/voicecall01",
        properties={
            "LineIdentification": "+15559999",
            "Name": "Emergency",
            "State": "incoming",
        },
    )
    assert client.get_state()["call_state"] == "RINGING"
    assert client.get_state()["phone_number"] == "+15559999"
    assert client.get_state()["caller_name"] == "Emergency"

    # 2. Call Removed
    client._on_dbus_call_removed(call_path="/org/ofono/voicecall01")
    assert client.get_state()["call_state"] == "IDLE"
    assert client.get_state()["phone_number"] == ""


def test_bluez_hf_client_send_at_command():
    import os
    client = BlueZHFClient(auto_connect_session_bus=False)
    r_fd, w_fd = os.pipe()
    client._rfcomm_fd = w_fd

    client.send_at_cmd("AT+CIND=?\r")
    out = os.read(r_fd, 64)
    assert out == b"AT+CIND=?\r"

    os.close(r_fd)
    os.close(w_fd)



