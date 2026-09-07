"""
test_bluez_hfp.py — Tests for BlueZ HFP AT commands and telephony state machine.
"""

import pytest
from unittest.mock import patch, MagicMock
from backend.shared.hardware.bluez_hfp import (
    BlueZHFClient,
    CallState,
    parse_clcc_response,
    parse_ciev_response,
    parse_cops_response,
    parse_csq_response,
    parse_cbc_response,
    parse_cind_response,
    parse_clip_response,
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


def test_parse_at_telemetry_commands():
    # 1. Carrier / Operator Name (+COPS)
    assert parse_cops_response('+COPS: 0,0,"Vodafone IT",7') == "Vodafone IT"
    assert parse_cops_response('+COPS: 0,2,"TIM"') == "TIM"
    assert parse_cops_response("+COPS: 0") is None

    # 2. Signal Quality (+CSQ: 0-31 RSSI scale)
    assert parse_csq_response("+CSQ: 31,99") == 5
    assert parse_csq_response("+CSQ: 15,0") == 2
    assert parse_csq_response("+CSQ: 0,0") == 0
    assert parse_csq_response("+CSQ: 99,99") is None

    # 3. Battery Level (+CBC: percentage 0-100)
    assert parse_cbc_response("+CBC: 0,85") == 85
    assert parse_cbc_response("+CBC: 1,100") == 100

    # 4. Indicators (+CIND)
    cind = parse_cind_response("+CIND: 1,0,0,0,4,0,5")
    assert cind["service"] == 1
    assert cind["signal"] == 4
    assert cind["battchg"] == 5

    # 5. Caller ID (+CLIP)
    clip = parse_clip_response('+CLIP: "+393401234567",145,,,,0')
    assert clip["number"] == "+393401234567"

    # 6. Formatters
    assert format_at_cops_query() == "AT+COPS?\r"
    assert format_at_csq_query() == "AT+CSQ\r"
    assert format_at_cind_query() == "AT+CIND?\r"
    assert format_at_cbc_query() == "AT+CBC\r"
    assert format_at_clcc_query() == "AT+CLCC\r"


def test_bluez_hf_client_process_at_line():
    client = BlueZHFClient()

    # Inbound COPS sets carrier
    assert client.process_at_line('+COPS: 0,0,"Verizon Wireless",7') is True
    assert client.get_state()["carrier"] == "Verizon Wireless"

    # Inbound CSQ sets signal
    assert client.process_at_line("+CSQ: 28,0") is True
    assert client.get_state()["signal_bars"] == 5

    # Inbound CBC sets battery
    assert client.process_at_line("+CBC: 0,72") is True
    assert client.get_state()["battery_pct"] == 72

    # Inbound CLIP sets incoming call
    assert client.process_at_line('+CLIP: "+15551234567",145,"",,"John Doe"') is True
    assert client.get_state()["is_in_call"] is True
    assert client.get_state()["call_state"] == CallState.RINGING
    assert client.get_state()["phone_number"] == "+15551234567"
    assert client.get_state()["caller_name"] == "John Doe"

    # Remote hangup / disconnect
    assert client.process_at_line("NO CARRIER") is True
    assert client.get_state()["is_in_call"] is False
    assert client.get_state()["call_state"] == CallState.IDLE


def test_bluez_hf_client_session_bus_telephony():
    from unittest.mock import MagicMock

    mock_bus = MagicMock()
    mock_gateway = MagicMock()
    mock_call_mgr = MagicMock()
    mock_transport = MagicMock()
    mock_obj_mgr = MagicMock()
    mock_props = MagicMock()

    mock_obj_mgr.GetManagedObjects.return_value = {
        "/org/pipewire/Telephony/ag1": {
            "org.pipewire.Telephony.AudioGateway1": {"Address": "C8:2A:DD:8C:40:44"},
            "org.ofono.VoiceCallManager": {},
            "org.pipewire.Telephony.AudioGatewayTransport1": {},
        }
    }

    def fake_interface(obj, iface):
        if iface == "org.freedesktop.DBus.ObjectManager":
            return mock_obj_mgr
        elif iface == "org.pipewire.Telephony.AudioGateway1":
            return mock_gateway
        elif iface == "org.ofono.VoiceCallManager":
            return mock_call_mgr
        elif iface == "org.pipewire.Telephony.AudioGatewayTransport1":
            return mock_transport
        elif iface == "org.freedesktop.DBus.Properties":
            return mock_props
        return MagicMock()

    with patch("dbus.Interface", side_effect=fake_interface), \
         patch("dbus.Byte", side_effect=lambda v: v):
        client = BlueZHFClient(session_bus=mock_bus)
        assert client._gateway_path == "/org/pipewire/Telephony/ag1"

        # 1. Dial calls AudioGateway1.Dial and activates transport
        assert client.dial("+123456789") is True
        mock_gateway.Dial.assert_called_with("+123456789")
        mock_transport.Activate.assert_called()
        assert client.get_state()["call_state"] == CallState.DIALING

        # 2. Answer calls HoldAndAnswer
        assert client.answer() is True
        mock_gateway.HoldAndAnswer.assert_called()
        assert client.get_state()["call_state"] == CallState.ACTIVE

        # 3. DTMF calls SendTones
        assert client.send_dtmf("5") is True
        mock_gateway.SendTones.assert_called_with("5")

        # 4. Volume sync: 80% -> 12/15
        assert client.set_volume(80) is True
        mock_props.Set.assert_called_with("org.pipewire.Telephony.AudioGateway1", "SpeakerVolume", 12)

        # 5. Hangup calls HangupAll
        assert client.hangup() is True
        mock_gateway.HangupAll.assert_called()
        assert client.get_state()["call_state"] == CallState.IDLE


def test_bluez_hf_client_dbus_signals_lifecycle():
    from unittest.mock import MagicMock

    mock_bus = MagicMock()
    mock_obj_mgr = MagicMock()
    mock_obj_mgr.GetManagedObjects.return_value = {}

    with patch("dbus.Interface", return_value=mock_obj_mgr):
        state_changes = []
        client = BlueZHFClient(session_bus=mock_bus, on_state_changed=lambda st: state_changes.append(st))

        # Simulate incoming call via D-Bus signal
        client._on_dbus_call_added(
            call_path="/org/pipewire/Telephony/ag1/voicecall01",
            properties={
                "LineIdentification": "+393401234567",
                "Name": "Alice",
                "State": "incoming",
            },
            path="/org/pipewire/Telephony/ag1"
        )
        assert client.get_state()["is_in_call"] is True
        assert client.get_state()["call_state"] == CallState.RINGING
        assert client.get_state()["phone_number"] == "+393401234567"
        assert client.get_state()["caller_name"] == "Alice"
        assert len(state_changes) >= 1

        # Simulate remote hangup via CallRemoved signal
        client._on_dbus_call_removed(
            call_path="/org/pipewire/Telephony/ag1/voicecall01",
            path="/org/pipewire/Telephony/ag1"
        )
        assert client.get_state()["is_in_call"] is False
        assert client.get_state()["call_state"] == CallState.IDLE
        assert client.get_state()["phone_number"] == ""

