"""
test_bluez_hfp.py — Tests for BlueZ HFP AT commands and telephony state machine.
"""

import pytest
from backend.shared.hardware.bluez_hfp import (
    BlueZHFClient,
    CallState,
    parse_clcc_response,
    parse_ciev_response,
    format_at_dial,
    format_at_answer,
    format_at_hangup,
    format_at_dtmf,
    format_at_mute,
)


def test_at_command_formatters():
    assert format_at_dial("+123456789") == "ATD+123456789;\r"
    assert format_at_answer() == "ATA\r"
    assert format_at_hangup() == "AT+CHUP\r"
    assert format_at_dtmf("5") == "AT+VTS=5\r"
    assert format_at_dtmf("#") == "AT+VTS=#\r"
    assert format_at_mute(True) == "AT+CMUT=1\r"
    assert format_at_mute(False) == "AT+CMUT=0\r"


def test_parse_clcc_line():
    # Format: +CLCC: <id>,<dir>,<stat>,<mode>,<mpty>,"<number>",<type>
    # stat: 0=active, 1=held, 2=dialing, 3=alerting, 4=incoming, 5=waiting
    line = '+CLCC: 1,0,0,0,0,"+3901234567",145'
    call = parse_clcc_response(line)
    assert call is not None
    assert call["idx"] == 1
    assert call["direction"] == "outgoing"
    assert call["state"] == CallState.ACTIVE
    assert call["number"] == "+3901234567"

    # Incoming alerting
    line_in = '+CLCC: 2,1,4,0,0,"0289000",129'
    call_in = parse_clcc_response(line_in)
    assert call_in is not None
    assert call_in["idx"] == 2
    assert call_in["direction"] == "incoming"
    assert call_in["state"] == CallState.RINGING
    assert call_in["number"] == "0289000"

    # Invalid line returns None
    assert parse_clcc_response("+OTHER: 1,2,3") is None


def test_parse_ciev_line():
    # Standard indicators: signal, battery, call, callsetup
    # +CIEV: <ind>,<val>
    # ind map example: 2 -> call (0 or 1), 3 -> callsetup (0=idle, 1=incoming, 2=outgoing, 3=alerting), 5 -> signal (0-5), 7 -> battchg (0-5)
    ind_map = {2: "call", 3: "callsetup", 5: "signal", 7: "battery"}

    ev_signal = parse_ciev_response("+CIEV: 5,4", ind_map)
    assert ev_signal == {"indicator": "signal", "value": 4}

    ev_batt = parse_ciev_response("+CIEV: 7,5", ind_map)
    assert ev_batt == {"indicator": "battery", "value": 5}

    ev_invalid = parse_ciev_response("NO CARRIER", ind_map)
    assert ev_invalid is None


def test_bluez_hf_client_dial_lifecycle():
    client = BlueZHFClient()
    assert client.get_state()["call_state"] == CallState.IDLE
    assert client.get_state()["is_in_call"] is False

    # 1. Dial a number
    res = client.dial("+39333123456")
    assert res is True
    assert client.get_state()["call_state"] == CallState.DIALING
    assert client.get_state()["phone_number"] == "+39333123456"
    assert client.get_state()["is_in_call"] is True

    # 2. Remote answers
    client.handle_call_active()
    assert client.get_state()["call_state"] == CallState.ACTIVE

    # 3. DTMF tones
    assert client.send_dtmf("9") is True

    # 4. Mute toggle
    assert client.set_mute(True) is True
    assert client.get_state()["muted"] is True
    assert client.set_mute(False) is True
    assert client.get_state()["muted"] is False

    # 5. Hangup
    assert client.hangup() is True
    assert client.get_state()["call_state"] == CallState.IDLE
    assert client.get_state()["is_in_call"] is False


def test_bluez_hf_client_incoming_call_lifecycle():
    client = BlueZHFClient()

    # Incoming ringing
    client.handle_incoming_call(number="+18005551234", name="Alice")
    assert client.get_state()["call_state"] == CallState.RINGING
    assert client.get_state()["phone_number"] == "+18005551234"
    assert client.get_state()["caller_name"] == "Alice"
    assert client.get_state()["is_in_call"] is True

    # Answer call
    assert client.answer() is True
    assert client.get_state()["call_state"] == CallState.ACTIVE

    # Terminate call
    client.hangup()
    assert client.get_state()["call_state"] == CallState.IDLE
