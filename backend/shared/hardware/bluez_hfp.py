"""
bluez_hfp.py — Standalone Bluetooth Hands-Free Profile (HFP) AT command & call management.
Handles dial, answer, hangup, mute, DTMF tones, and call state tracking.
"""

import enum
import re
from typing import Optional, Dict, Any, Callable
from shared.logger import get_logger

log = get_logger("hardware.bluez_hfp")


class CallState(str, enum.Enum):
    IDLE = "IDLE"
    DIALING = "DIALING"
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    HELD = "HELD"


def format_at_dial(number: str) -> str:
    # Ensure semicolon at end for voice call dial command in GSM 07.07 / 3GPP 27.007
    num = number.strip()
    return f"ATD{num};\r"


def format_at_answer() -> str:
    return "ATA\r"


def format_at_hangup() -> str:
    return "AT+CHUP\r"


def format_at_dtmf(key: str) -> str:
    return f"AT+VTS={key}\r"


def format_at_mute(muted: bool) -> str:
    return f"AT+CMUT={1 if muted else 0}\r"


def format_at_cops_query() -> str:
    return "AT+COPS?\r"


def format_at_csq_query() -> str:
    return "AT+CSQ\r"


def format_at_cind_query() -> str:
    return "AT+CIND?\r"


def format_at_cbc_query() -> str:
    return "AT+CBC\r"


def format_at_clcc_query() -> str:
    return "AT+CLCC\r"


def parse_clcc_response(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a 3GPP +CLCC line:
    +CLCC: <idx>,<dir>,<stat>,<mode>,<mpty>[,"<number>",<type>]
    stat: 0=active, 1=held, 2=dialing, 3=alerting, 4=incoming, 5=waiting
    dir: 0=outgoing, 1=incoming
    """
    line = line.strip()
    match = re.match(r'^\+CLCC:\s*(\d+),(\d+),(\d+),(\d+),(\d+)(?:,"([^"]*)",(\d+))?', line)
    if not match:
        return None

    idx = int(match.group(1))
    direction = "incoming" if int(match.group(2)) == 1 else "outgoing"
    stat_code = int(match.group(3))
    number = match.group(6) or ""

    state_map = {
        0: CallState.ACTIVE,
        1: CallState.HELD,
        2: CallState.DIALING,
        3: CallState.DIALING,  # Alerting remote
        4: CallState.RINGING,  # Incoming ringing
        5: CallState.HELD,
    }
    state = state_map.get(stat_code, CallState.IDLE)

    return {
        "idx": idx,
        "direction": direction,
        "state": state,
        "number": number,
    }


def parse_ciev_response(line: str, ind_map: Dict[int, str]) -> Optional[Dict[str, Any]]:
    """
    Parse a standard +CIEV indicator event:
    +CIEV: <ind>,<val>
    """
    line = line.strip()
    match = re.match(r'^\+CIEV:\s*(\d+),(\d+)', line)
    if not match:
        return None

    ind_idx = int(match.group(1))
    val = int(match.group(2))
    name = ind_map.get(ind_idx, f"indicator_{ind_idx}")

    return {
        "indicator": name,
        "value": val,
    }


def parse_cops_response(line: str) -> Optional[str]:
    """
    Parse 3GPP +COPS operator response:
    +COPS: <mode>[,<format>,"<operator>"[,<act>]]
    """
    line = line.strip()
    match = re.search(r'\+COPS:\s*[^,]+,[^,]+,"([^"]+)"', line)
    if match:
        return match.group(1).strip()
    return None


def parse_csq_response(line: str) -> Optional[int]:
    """
    Parse 3GPP +CSQ signal quality response:
    +CSQ: <rssi>,<ber>
    Maps 0..31 RSSI scale to 0..5 signal bars (99 = unknown).
    """
    line = line.strip()
    match = re.match(r'^\+CSQ:\s*(\d+),', line)
    if not match:
        return None
    rssi = int(match.group(1))
    if rssi == 99 or rssi < 0:
        return None
    return max(0, min(5, round(rssi * 5 / 31)))


def parse_cbc_response(line: str) -> Optional[int]:
    """
    Parse 3GPP +CBC battery level response:
    +CBC: <bcs>,<bcl>
    Returns battery charge percentage 0..100.
    """
    line = line.strip()
    match = re.match(r'^\+CBC:\s*\d+,(\d+)', line)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def parse_cind_response(line: str, ind_names: Optional[list[str]] = None) -> Dict[str, int]:
    """
    Parse 3GPP +CIND indicator status response:
    +CIND: <val1>,<val2>,<val3>,...
    """
    line = line.strip()
    match = re.match(r'^\+CIND:\s*([\d,]+)', line)
    if not match:
        return {}
    vals = [int(v.strip()) for v in match.group(1).split(",") if v.strip().isdigit()]
    names = ind_names or ["service", "call", "callsetup", "callheld", "signal", "roam", "battchg"]
    res: Dict[str, int] = {}
    for idx, name in enumerate(names):
        if idx < len(vals):
            res[name] = vals[idx]
    return res


def parse_clip_response(line: str) -> Optional[Dict[str, str]]:
    """
    Parse 3GPP +CLIP caller identification notification:
    +CLIP: "<number>",<type>[,"<subaddr>",<satype>[,[<alpha>][,<cli_validity>]]]
    """
    line = line.strip()
    match = re.match(r'^\+CLIP:\s*"([^"]+)",\s*(\d+)(?:,[^,]*,\s*[^,]*(?:,\s*"([^"]*)")?)?', line)
    if not match:
        return None
    number = match.group(1).strip()
    name = (match.group(3) or "").strip()
    return {"number": number, "name": name or number}


class BlueZHFClient:
    """
    Hands-Free Client managing AT command sequence, call lifecycle, and audio loopback sync.
    Can operate directly with an open RFCOMM socket/file descriptor or in mock/standalone mode.
    """

    DEFAULT_INDICATOR_MAP = {
        1: "service",
        2: "call",
        3: "callsetup",
        4: "callheld",
        5: "signal",
        6: "roam",
        7: "battchg",
    }

    def __init__(
        self,
        rfcomm_fd: Optional[int] = None,
        on_state_changed: Optional[Callable[[Dict[str, Any]], None]] = None,
        session_bus: Optional[Any] = None,
        auto_connect_session_bus: bool = True,
    ):
        self._rfcomm_fd = rfcomm_fd
        self._on_state_changed = on_state_changed
        self._call_state: CallState = CallState.IDLE
        self._phone_number: str = ""
        self._caller_name: str = ""
        self._muted: bool = False
        self._carrier: str = ""
        self._signal_bars: int = -1
        self._battery_pct: int = -1
        self._is_roaming: bool = False
        self._indicator_map = dict(self.DEFAULT_INDICATOR_MAP)

        # WirePlumber / PipeWire D-Bus SessionBus Telephony State
        self._session_bus = session_bus
        self._gateway_path: Optional[str] = None
        self._gateway_proxy = None
        self._call_manager_proxy = None
        self._transport_proxy = None
        self._bound_device_address: str = ""

        if self._session_bus is not None:
            self._setup_dbus_signals()
            self._discover_gateway()
        elif auto_connect_session_bus:
            self._init_session_bus()

    def _init_session_bus(self) -> None:
        try:
            import dbus
            self._session_bus = dbus.SessionBus()
            self._setup_dbus_signals()
            self._discover_gateway()
            log.info("Initialized D-Bus SessionBus telephony connection for org.pipewire.Telephony")
        except Exception as e:
            log.debug(f"PipeWire Telephony SessionBus initialization skipped or unavailable: {e}")

    def _setup_dbus_signals(self) -> None:
        if not self._session_bus:
            return
        try:
            # Subscribe to org.ofono.VoiceCallManager signals emitted by WirePlumber
            self._session_bus.add_signal_receiver(
                self._on_dbus_call_added,
                signal_name="CallAdded",
                dbus_interface="org.ofono.VoiceCallManager",
                bus_name="org.pipewire.Telephony",
                path_keyword="path",
            )
            self._session_bus.add_signal_receiver(
                self._on_dbus_call_removed,
                signal_name="CallRemoved",
                dbus_interface="org.ofono.VoiceCallManager",
                bus_name="org.pipewire.Telephony",
                path_keyword="path",
            )
            # Subscribe to ObjectManager to detect gateway addition / removal
            self._session_bus.add_signal_receiver(
                self._on_dbus_interfaces_added,
                signal_name="InterfacesAdded",
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                bus_name="org.pipewire.Telephony",
            )
            self._session_bus.add_signal_receiver(
                self._on_dbus_interfaces_removed,
                signal_name="InterfacesRemoved",
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                bus_name="org.pipewire.Telephony",
            )
            log.info("Subscribed to org.pipewire.Telephony D-Bus signals on SessionBus")
        except Exception as e:
            log.debug(f"Could not subscribe to D-Bus signals on SessionBus: {e}")

    def _discover_gateway(self) -> None:
        if not self._session_bus:
            return
        try:
            import dbus
            manager = dbus.Interface(
                self._session_bus.get_object("org.pipewire.Telephony", "/org/pipewire/Telephony"),
                "org.freedesktop.DBus.ObjectManager",
            )
            objects = manager.GetManagedObjects()
            target_path = None
            for path, ifaces in objects.items():
                if "org.pipewire.Telephony.AudioGateway1" in ifaces:
                    addr = str(ifaces["org.pipewire.Telephony.AudioGateway1"].get("Address", "")).upper()
                    if not self._bound_device_address or addr == self._bound_device_address:
                        target_path = str(path)
                        break
            if target_path:
                self._bind_gateway_path(target_path)
            else:
                self._unbind_gateway()
        except Exception as e:
            log.debug(f"_discover_gateway notice: {e}")

    def _bind_gateway_path(self, path: str) -> None:
        if not self._session_bus:
            return
        try:
            import dbus
            self._gateway_path = path
            obj = self._session_bus.get_object("org.pipewire.Telephony", path)
            self._gateway_proxy = dbus.Interface(obj, "org.pipewire.Telephony.AudioGateway1")
            self._call_manager_proxy = dbus.Interface(obj, "org.ofono.VoiceCallManager")
            self._transport_proxy = dbus.Interface(obj, "org.pipewire.Telephony.AudioGatewayTransport1")
            log.info(f"Bound BlueZHFClient to PipeWire Telephony Gateway at {path}")
        except Exception as e:
            log.warning(f"Failed to bind gateway path {path}: {e}")

    def _unbind_gateway(self) -> None:
        self._gateway_path = None
        self._gateway_proxy = None
        self._call_manager_proxy = None
        self._transport_proxy = None

    def bind_device(self, address: str) -> None:
        self._bound_device_address = address.upper().strip()
        self._discover_gateway()

    def unbind_device(self) -> None:
        self._bound_device_address = ""
        self._unbind_gateway()
        self._call_state = CallState.IDLE
        self._phone_number = ""
        self._caller_name = ""
        self._notify()

    def _on_dbus_interfaces_added(self, path: Any, interfaces: Dict[str, Any]) -> None:
        if "org.pipewire.Telephony.AudioGateway1" in interfaces:
            addr = str(interfaces["org.pipewire.Telephony.AudioGateway1"].get("Address", "")).upper()
            if not self._bound_device_address or addr == self._bound_device_address:
                self._bind_gateway_path(str(path))

    def _on_dbus_interfaces_removed(self, path: Any, interfaces: list) -> None:
        if str(path) == self._gateway_path:
            self._unbind_gateway()

    def _on_dbus_call_added(self, call_path: Any, properties: Dict[str, Any], path: Optional[str] = None) -> None:
        log.info(f"🔔 D-Bus CallAdded on {path}: {properties}")
        line_id = str(properties.get("LineIdentification", "")).strip()
        name = str(properties.get("Name", "")).strip()
        state_str = str(properties.get("State", "")).lower()

        if "incoming" in state_str or "waiting" in state_str:
            self._call_state = CallState.RINGING
        elif "dialing" in state_str or "alerting" in state_str:
            self._call_state = CallState.DIALING
        elif "active" in state_str:
            self._call_state = CallState.ACTIVE
        elif "held" in state_str:
            self._call_state = CallState.HELD
        else:
            self._call_state = CallState.ACTIVE

        if line_id:
            self._phone_number = line_id
        if name:
            self._caller_name = name
        elif not self._caller_name and self._phone_number:
            self._caller_name = self._phone_number

        if self._transport_proxy:
            try:
                self._transport_proxy.Activate()
            except Exception as e:
                log.debug(f"AudioGatewayTransport1.Activate notice: {e}")

        self._notify()

    def _on_dbus_call_removed(self, call_path: Any, path: Optional[str] = None) -> None:
        log.info(f"🔕 D-Bus CallRemoved on {path}: {call_path}")
        self._call_state = CallState.IDLE
        self._phone_number = ""
        self._caller_name = ""
        self._notify()

    def get_state(self) -> Dict[str, Any]:
        return {
            "source": "bluetooth_hfp",
            "is_in_call": self._call_state != CallState.IDLE,
            "call_state": self._call_state.value,
            "phone_number": self._phone_number,
            "caller_name": self._caller_name,
            "muted": self._muted,
            "carrier": self._carrier,
            "signal_bars": self._signal_bars,
            "battery_pct": self._battery_pct,
            "is_roaming": self._is_roaming,
        }

    def _notify(self) -> None:
        if self._on_state_changed:
            try:
                self._on_state_changed(self.get_state())
            except Exception as e:
                log.warning(f"Error in on_state_changed callback: {e}")

    def send_at_cmd(self, cmd: str) -> bool:
        log.info(f"📤 HFP Sending AT Command: {repr(cmd)}")
        if self._rfcomm_fd is not None:
            try:
                import os
                os.write(self._rfcomm_fd, cmd.encode("ascii"))
                return True
            except Exception as e:
                log.warning(f"Failed to write AT command to RFCOMM fd: {e}")
                return False
        return True

    def dial(self, number: str) -> bool:
        clean_num = number.strip()
        if not clean_num:
            return False
        self._phone_number = clean_num
        self._call_state = CallState.DIALING
        self._muted = False

        # Attempt native WirePlumber D-Bus session call first
        if self._gateway_proxy is not None:
            try:
                log.info(f"Invoking AudioGateway1.Dial('{clean_num}') via SessionBus")
                self._gateway_proxy.Dial(clean_num)
                if self._transport_proxy:
                    try:
                        self._transport_proxy.Activate()
                    except Exception:
                        pass
                self._notify()
                return True
            except Exception as e:
                log.warning(f"AudioGateway1.Dial failed ({e}), falling back to AT command dispatch")

        cmd = format_at_dial(clean_num)
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def answer(self) -> bool:
        if self._call_state != CallState.RINGING:
            log.debug(f"answer called but call_state is {self._call_state}")
        self._call_state = CallState.ACTIVE

        if self._gateway_proxy is not None:
            try:
                log.info("Invoking AudioGateway1.HoldAndAnswer() via SessionBus")
                self._gateway_proxy.HoldAndAnswer()
                if self._transport_proxy:
                    try:
                        self._transport_proxy.Activate()
                    except Exception:
                        pass
                self._notify()
                return True
            except Exception as e:
                log.warning(f"AudioGateway1.HoldAndAnswer failed ({e}), falling back to AT command dispatch")

        cmd = format_at_answer()
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def hangup(self) -> bool:
        self._call_state = CallState.IDLE
        self._phone_number = ""
        self._caller_name = ""
        self._muted = False

        if self._gateway_proxy is not None:
            try:
                log.info("Invoking AudioGateway1.HangupAll() via SessionBus")
                self._gateway_proxy.HangupAll()
                self._notify()
                return True
            except Exception as e:
                log.warning(f"AudioGateway1.HangupAll failed ({e}), falling back to AT command dispatch")

        cmd = format_at_hangup()
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def send_dtmf(self, key: str) -> bool:
        k = str(key).strip()
        if not k:
            return False

        if self._gateway_proxy is not None:
            try:
                log.info(f"Invoking AudioGateway1.SendTones('{k}') via SessionBus")
                self._gateway_proxy.SendTones(k)
                return True
            except Exception as e:
                log.warning(f"AudioGateway1.SendTones failed ({e}), falling back to AT command dispatch")

        cmd = format_at_dtmf(k)
        return self.send_at_cmd(cmd)

    def set_mute(self, muted: bool) -> bool:
        self._muted = muted
        cmd = format_at_mute(muted)
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def set_volume(self, pct: int) -> bool:
        """
        Synchronize head unit volume to phone HFP SpeakerVolume (0..15 scale).
        """
        v = max(0, min(15, round(pct * 15 / 100)))
        if self._gateway_path and self._session_bus:
            try:
                import dbus
                props = dbus.Interface(
                    self._session_bus.get_object("org.pipewire.Telephony", self._gateway_path),
                    "org.freedesktop.DBus.Properties",
                )
                props.Set("org.pipewire.Telephony.AudioGateway1", "SpeakerVolume", dbus.Byte(v))
                log.info(f"Synchronized HFP SpeakerVolume to {v} (from {pct}%)")
                return True
            except Exception as e:
                log.debug(f"Failed to set SpeakerVolume on D-Bus: {e}")
        return False

    def handle_call_active(self) -> None:
        self._call_state = CallState.ACTIVE
        self._notify()

    def handle_incoming_call(self, number: str, name: str = "") -> None:
        self._phone_number = number
        self._caller_name = name or number
        self._call_state = CallState.RINGING
        self._notify()

    def update_telemetry(self, battery_pct: int = -1, signal_bars: int = -1, carrier: str = "", is_roaming: Optional[bool] = None) -> None:
        if battery_pct >= 0:
            self._battery_pct = max(0, min(100, battery_pct))
        if signal_bars >= 0:
            self._signal_bars = max(0, min(5, signal_bars))
        if carrier:
            self._carrier = carrier
        if is_roaming is not None:
            self._is_roaming = is_roaming
        self._notify()

    def set_rfcomm_fd(self, fd: Optional[int]) -> None:
        self._rfcomm_fd = fd

    def set_indicator_map(self, ind_map: Dict[int, str]) -> None:
        self._indicator_map = dict(ind_map)

    def process_at_line(self, line: str) -> bool:
        """
        Process an inbound AT response/unsolicited notification line from phone/modem.
        Updates internal state and triggers notification callback if changed.
        """
        raw = line.strip()
        if not raw:
            return False

        updated = False

        # 1. Operator / Carrier: +COPS: ...,"CarrierName"
        if raw.startswith("+COPS:"):
            cops = parse_cops_response(raw)
            if cops:
                self._carrier = cops
                updated = True

        # 2. Signal Quality: +CSQ: <rssi>,<ber>
        elif raw.startswith("+CSQ:"):
            csq = parse_csq_response(raw)
            if csq is not None:
                self._signal_bars = csq
                updated = True

        # 3. Battery Level: +CBC: <bcs>,<bcl>
        elif raw.startswith("+CBC:"):
            cbc = parse_cbc_response(raw)
            if cbc is not None:
                self._battery_pct = cbc
                updated = True

        # 4. Indicators Status: +CIND: <val1>,<val2>,...
        elif raw.startswith("+CIND:"):
            cind = parse_cind_response(raw)
            if "signal" in cind:
                self._signal_bars = max(0, min(5, cind["signal"]))
                updated = True
            if "battchg" in cind:
                self._battery_pct = max(0, min(100, cind["battchg"] * 20))
                updated = True
            if "roam" in cind:
                self._is_roaming = bool(cind["roam"])
                updated = True
            if "call" in cind:
                if cind["call"] == 1:
                    self._call_state = CallState.ACTIVE
                    updated = True
                elif cind.get("callsetup", 0) == 0 and self._call_state != CallState.IDLE:
                    self._call_state = CallState.IDLE
                    self._phone_number = ""
                    self._caller_name = ""
                    updated = True
            if "callsetup" in cind:
                cs = cind["callsetup"]
                if cs == 1:
                    self._call_state = CallState.RINGING
                    updated = True
                elif cs in (2, 3):
                    self._call_state = CallState.DIALING
                    updated = True

        # 5. Indicator Event: +CIEV: <ind>,<val>
        elif raw.startswith("+CIEV:"):
            ciev = parse_ciev_response(raw, self._indicator_map)
            if ciev:
                ind = ciev["indicator"]
                val = ciev["value"]
                if ind == "signal":
                    self._signal_bars = max(0, min(5, val))
                    updated = True
                elif ind == "battchg":
                    self._battery_pct = max(0, min(100, val * 20))
                    updated = True
                elif ind == "roam":
                    self._is_roaming = bool(val)
                    updated = True
                elif ind == "call":
                    if val == 1:
                        self._call_state = CallState.ACTIVE
                        updated = True
                    elif val == 0:
                        self._call_state = CallState.IDLE
                        self._phone_number = ""
                        self._caller_name = ""
                        updated = True
                elif ind == "callsetup":
                    if val == 1:
                        self._call_state = CallState.RINGING
                        updated = True
                    elif val in (2, 3):
                        self._call_state = CallState.DIALING
                        updated = True
                    elif val == 0 and self._call_state not in (CallState.ACTIVE, CallState.HELD):
                        self._call_state = CallState.IDLE
                        updated = True

        # 6. Incoming Ring & Caller ID
        elif raw == "RING":
            if self._call_state != CallState.ACTIVE:
                self._call_state = CallState.RINGING
                updated = True

        elif raw.startswith("+CLIP:"):
            clip = parse_clip_response(raw)
            if clip:
                self._phone_number = clip["number"]
                self._caller_name = clip["name"]
                self._call_state = CallState.RINGING
                updated = True

        # 7. Call List / Active Call: +CLCC: ...
        elif raw.startswith("+CLCC:"):
            clcc = parse_clcc_response(raw)
            if clcc:
                self._call_state = clcc["state"]
                if clcc.get("number"):
                    self._phone_number = clcc["number"]
                    if not self._caller_name:
                        self._caller_name = clcc["number"]
                updated = True

        # 8. Call Ended result codes
        elif raw in ("NO CARRIER", "BUSY"):
            self._call_state = CallState.IDLE
            self._phone_number = ""
            self._caller_name = ""
            updated = True

        if updated:
            self._notify()

        return updated
